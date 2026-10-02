"""Google snapshots -> reviewed SOs, using the approved_work_orders table only."""
from collections import defaultdict, deque
from copy import deepcopy
from datetime import datetime, timezone
from uuid import UUID, uuid4
from sqlalchemy import text
from .models import db, ApprovedSO, SalesOrder
from .production_schedule import parse_production_date, save_production_date

FIELDS = ('customer', 'customer_po', 'ship_date', 'terms', 'inventory_site')
ITEM_FIELDS = ('item', 'quantity', 'configuration', 'inventory_site')


def lock_review():
    if db.engine.dialect.name == 'postgresql':
        db.session.execute(text('SELECT pg_advisory_xact_lock(26091003)'))


def approved_orders():
    return {row.sales_order: row.to_source() for row in ApprovedSO.query.filter(
        ApprovedSO.approved_at.isnot(None), ApprovedSO.is_active.is_(True),
        ApprovedSO.google_present.is_(True)).all()}


def signature(item):
    return tuple(str(item.get(field) or '').strip().casefold()
                 for field in ('item', 'configuration', 'inventory_site'))


def snapshot(source, prior=None):
    result = dict(sales_order=source['sales_order'], **{key: source.get(key) or '' for key in FIELDS})
    pools = defaultdict(deque)
    for item in (prior or {}).get('items', []):
        try:
            identity = str(UUID(str(item.get('id'))))
        except (ValueError, TypeError, AttributeError):
            continue
        pools[signature(item)].append(identity)
    result['items'] = []
    used_ids = set()
    for item in source['items']:
        quantity = item['quantity']
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
            raise ValueError('Google quantities must be nonnegative whole numbers.')
        row = {field: item.get(field) or '' for field in ITEM_FIELDS}
        row['quantity'] = quantity
        row['id'] = pools[signature(item)].popleft() if pools[signature(item)] else None
        if row['id']:
            used_ids.add(row['id'])
        result['items'].append(row)
    # Preserve identity/order across configuration or site edits when matching
    # the unmatched product is unambiguous. Never guess across ambiguous lines.
    old_products, new_products = defaultdict(list), defaultdict(list)
    for item in (prior or {}).get('items', []):
        try:
            identity = str(UUID(str(item.get('id'))))
        except (ValueError, TypeError, AttributeError):
            continue
        if identity not in used_ids:
            old_products[str(item.get('item') or '').strip().casefold()].append(identity)
    for row in result['items']:
        if row['id'] is None:
            new_products[row['item'].strip().casefold()].append(row)
    for product, rows in new_products.items():
        candidates = old_products[product]
        if len(rows) == len(candidates) == 1:
            rows[0]['id'] = candidates[0]
        else:
            for row in rows:
                row['id'] = str(uuid4())
    return result


def differences(before, after):
    if before == after:
        return []
    if before is None:
        return ['New SO in Google Sheet'] if after is not None else []
    if after is None:
        return ['SO removed from Google Sheet — excluded from new WO generation automatically']
    changes = []
    for field in FIELDS:
        if before.get(field, '') != after.get(field, ''):
            changes.append(f"{field.replace('_', ' ').title()}: {before.get(field) or '—'} → {after.get(field) or '—'}")
    old = {item['id']: item for item in before.get('items', [])}
    new = {item['id']: item for item in after.get('items', [])}
    for key in old.keys() - new.keys():
        changes.append(f"Removed item: {old[key]['item']} (Qty {old[key]['quantity']})")
    for key in new.keys() - old.keys():
        changes.append(f"Added item: {new[key]['item']} (Qty {new[key]['quantity']})")
    for key in old.keys() & new.keys():
        for field in ITEM_FIELDS:
            if old[key].get(field, '') != new[key].get(field, ''):
                changes.append(f"{new[key]['item']} — {field}: {old[key].get(field) or '0'} → {new[key].get(field) or '0'}")
    old_order = [item['id'] for item in before.get('items', []) if item['id'] in new]
    new_order = [item['id'] for item in after.get('items', []) if item['id'] in old]
    if old_order != new_order:
        changes.append('Google item order changed (separate from item content)')
    return changes


def sync_snapshots(result):
    """Explicit sync stages content and disables absent SOs; approved content stays intact."""
    orders = result.get('orders') or {}
    if not orders or result.get('skipped') or result.get('messages'):
        raise ValueError('Google read was empty or had invalid/skipped rows. Nothing was synced; existing SOs were preserved.')
    for key, source in orders.items():
        if key != source.get('sales_order') or not source.get('items') or len(key) > 80:
            raise ValueError('Invalid Google SO identity/items. Nothing was synced.')
        if len(source.get('customer') or '') > 200 or len(source.get('customer_po') or '') > 120:
            raise ValueError('Google customer/PO exceeds the database field length. Nothing was synced.')
    lock_review()
    existing = {row.sales_order: row for row in ApprovedSO.query.populate_existing().all()}
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    changed = 0
    for number in sorted(set(existing) | set(orders)):
        row = existing.get(number)
        if row is None:
            row = ApprovedSO(sales_order=number, items=[], metadata_fields={}, source_revision=0, revision=0)
            db.session.add(row)
        prior = row.latest_snapshot
        was_absent = row.synced_at is not None and not row.google_present
        incoming = snapshot(orders[number], prior or row.approved_snapshot or {'items': row.items or []}) if number in orders else None
        # Reappearing SOs need explicit reapproval, including rows removed under
        # the old manual-retirement policy. Never reactivate them during sync.
        if row.is_active and (incoming is None or was_absent):
            row.is_active = False
            row.revision = (row.revision or 0) + 1
        row.previous_snapshot = deepcopy(prior)
        row.latest_snapshot = incoming
        row.google_present = incoming is not None
        row.synced_at = now
        if prior != incoming:
            row.source_revision = (row.source_revision or 0) + 1
            changed += 1
    db.session.commit()
    return changed


def review_items(row):
    """Keep approved manual order while merging current Google content for review."""
    latest = deepcopy((row.latest_snapshot or {}).get('items', []))
    by_id = {item['id']: item for item in latest}
    ordered = [by_id.pop(item.get('id')) for item in row.items or [] if item.get('id') in by_id]
    return ordered + [item for item in latest if item['id'] in by_id]


def approve(row, order_ids, source_revision, revision, retire=False, production_date=None, updated_by=None):
    target = parse_production_date(production_date) if production_date is not None else None
    if retire and target is not None:
        raise ValueError('Cannot assign production to a retired SO.')
    lock_review()
    db.session.refresh(row)
    if row.source_revision != source_revision or row.revision != revision:
        raise ValueError('This SO changed in another session or sync. Reload and review again.')
    if row.synced_at is None:
        raise ValueError('Sync Google Sheet before approving this legacy SO.')
    if retire:
        if row.google_present:
            raise ValueError('This SO is still present in Google. Review it instead of retiring it.')
        row.is_active = False
    else:
        if not row.google_present or row.latest_snapshot is None:
            raise ValueError('SO no longer exists in Google and is excluded from new WO generation.')
        incoming = row.latest_snapshot
        by_id = {item['id']: item for item in incoming['items']}
        if len(order_ids) != len(by_id) or set(order_ids) != set(by_id):
            raise ValueError('Item list changed. Reload before approving.')
        # Never silently approve quantities below already saved WO allocations.
        saved = defaultdict(int)
        for wo in SalesOrder.query.filter_by(sales_order=row.sales_order):
            for item in wo.items:
                saved[item['item'].strip().casefold()] += int(item.get('quantity', 0))
        planned = defaultdict(int)
        for item in incoming['items']:
            planned[item['item'].strip().casefold()] += item['quantity']
        for product, quantity in saved.items():
            if quantity > planned.get(product, 0):
                raise ValueError(f'{product}: saved WOs allocate {quantity}, but Google now has {planned.get(product, 0)}. Resolve the saved WO quantities first.')
        row.customer = incoming['customer']
        row.customer_po = incoming['customer_po']
        row.items = [deepcopy(by_id[key]) for key in order_ids]
        row.metadata_fields = {key: incoming[key] for key in ('ship_date', 'terms', 'inventory_site')}
        row.is_active = True
    row.approved_snapshot = deepcopy(row.latest_snapshot)
    row.approved_at = datetime.now(timezone.utc).replace(tzinfo=None)
    row.revision += 1
    if target is not None:
        save_production_date(row.sales_order, target, updated_by=updated_by)
    db.session.commit()
