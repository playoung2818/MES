"""MES reads the shared owner; existing WOs remain records, never SO approvals."""
from collections import defaultdict
from copy import deepcopy
from .models import db, SalesOrder

MES = 'released_to_mes'
NONPRODUCTION = {'shipping charge', 'forwarding charge', 'tariff (estimation)', 'tariff charge', 'engineer services'}


def production_item(item):
    return str(item.get('item') or '').strip().casefold() not in NONPRODUCTION


def execution_source(approval, orders):
    source = approval.to_source()
    source['items'] = deepcopy([i for i in source['items'] or [] if production_item(i)])
    published, imported_shipped, mes_shipped, used = (defaultdict(int) for _ in range(4))
    for item in source['items']:
        published[item['item'].strip().casefold()] += int(item['quantity'])
    ordered = dict(approval.approved_order_signature or published)
    if approval.approved_order_signature is not None:
        for item in source['items']:
            if str(item.get('id', '')).startswith('manual-'):
                name = item['item'].strip().casefold()
                ordered[name] = ordered.get(name, 0) + int(item['quantity'])
    snapshot = approval.latest_snapshot or approval.previous_snapshot or {}
    for item in snapshot.get('items', []):
        imported_shipped[item['item'].strip().casefold()] += int(item.get('shipped_quantity', 0))
    for wo in orders:
        for item in wo.items or []:
            if not production_item(item):
                continue
            name = item['item'].strip().casefold()
            used[name] += int(item['quantity'])
            if wo.is_shipped:
                mes_shipped[name] += int(item['quantity'])
    external = {name: max(0, imported_shipped[name]-mes_shipped[name]) for name in ordered}
    limits = {name: qty if used[name] > max(0, qty-external[name]) else max(0, qty-external[name])
              for name, qty in ordered.items()}
    shipping_issues = [{'item': name, 'source_shipped': imported_shipped[name], 'mes_shipped': mes_shipped[name]}
                       for name, qty in ordered.items() if external[name] and max(0, qty-external[name]) < used[name] <= qty]
    # Distribute a product's effective requirement over its displayed source lines.
    occurrences = defaultdict(int)
    for item in source['items']:
        occurrences[item['item'].strip().casefold()] += 1
    remaining = dict(limits)
    for item in source['items']:
        name = item['item'].strip().casefold()
        occurrences[name] -= 1
        qty = remaining.get(name, 0) if not occurrences[name] else min(int(item.get('ordered_quantity', item['quantity'])), remaining.get(name, 0))
        item['quantity'] = qty
        remaining[name] = remaining.get(name, 0)-qty
    conflicts = [{'item': name, 'ordered': limits.get(name, 0), 'allocated': qty}
                 for name, qty in sorted(used.items()) if qty > limits.get(name, 0)]
    details = dict(approval.reconciliation_details or {})
    details['allocations'] = conflicts
    details['shipments'] = shipping_issues
    source.update(workflow_status=approval.workflow_status, source_missing=not approval.google_present,
                  reconciliation_required=bool(conflicts or shipping_issues or details.get('finished_goods')),
                  reconciliation_details=details)
    return source


def notice(approval, source=None):
    if approval is None:
        return 'SO approval unavailable — new work release is blocked.'
    if approval.workflow_status == 'closed':
        return 'SO closed/excluded — existing WO retained as a record; new releases and picking are blocked.'
    if approval.workflow_status != MES:
        return 'SO under MRP review — Returned from MES. Existing WO is read-only; new releases and additional picking are blocked.' if approval.returned_from_mes else 'SO is not MES-owned. Existing WO is read-only.'
    if not approval.google_present:
        return 'SO missing from current source — disposition required. New releases and picking are blocked.'
    if source and source.get('reconciliation_required'):
        return 'WO reconciliation required. Review affected allocations, shipment records or finished-goods status before releasing more work.'
    return ''


def refresh_reconciliation(approval):
    orders = SalesOrder.mes_query().filter_by(sales_order=approval.sales_order).all()
    source = execution_source(approval, orders)
    approval.reconciliation_details = source['reconciliation_details']
    approval.reconciliation_required = source['reconciliation_required']
