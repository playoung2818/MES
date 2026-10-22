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
    source['items'] = deepcopy([i for i in source['items'] or [] if production_item(i)
                               and str(i.get('inventory_site') or source.get('inventory_site') or '').strip().casefold() != 'drop ship'])
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
    # Invoicing is proof of shipment. Source shipments may belong to existing
    # WOs, so never treat an overlap as additional fulfillment outside the WO.
    # Retain existing allocations, but do not release work for invoiced units.
    external = {name: max(0, imported_shipped[name]-mes_shipped[name]) for name in ordered}
    limits = {name: min(qty, max(used[name], qty-external[name], 0))
              for name, qty in ordered.items()}
    shipping_issues = [{'item': name, 'ordered': qty,
                        'source_shipped': imported_shipped[name], 'mes_shipped': mes_shipped[name]}
                       for name, qty in ordered.items()
                       if max(imported_shipped[name], mes_shipped[name]) > qty]
    # An SO-level invoice does not identify which of multiple open releases
    # shipped. Keep that ambiguity explicit rather than guessing a release.
    for name, qty in ordered.items():
        open_releases = [wo for wo in orders if not wo.is_shipped and any(
            production_item(i) and i['item'].strip().casefold() == name and int(i['quantity']) > 0
            for i in wo.items or [])]
        if (len(open_releases) > 1 and imported_shipped[name] > mes_shipped[name]
                and imported_shipped[name] < qty):
            shipping_issues.append(dict(item=name, source_shipped=imported_shipped[name],
                                        mes_shipped=mes_shipped[name],
                                        reason='Assign invoiced shipments to the affected WO releases.'))
    fulfillment = [dict(item=name, ordered=qty, allocated=used[name],
                        shipped=max(imported_shipped[name], mes_shipped[name]),
                        remaining=max(0, qty-max(imported_shipped[name], mes_shipped[name])))
                   for name, qty in ordered.items()]
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
    conflicts = [{'item': name, 'ordered': ordered.get(name, 0), 'allocated': qty}
                 for name, qty in sorted(used.items()) if qty > ordered.get(name, 0)]
    details = dict(approval.reconciliation_details or {})
    details['allocations'] = conflicts
    details['shipments'] = shipping_issues
    source.update(workflow_status=approval.workflow_status, source_missing=not approval.source_present,
                  reconciliation_required=bool(conflicts or shipping_issues or details.get('finished_goods')),
                  reconciliation_details=details, fulfillment=fulfillment)
    return source


def notice(approval, source=None):
    if approval is None:
        return 'SO approval unavailable — new work release is blocked.'
    if approval.workflow_status == 'closed':
        return 'SO closed/excluded — existing WO retained as a record; new releases and picking are blocked.'
    if approval.workflow_status != MES:
        return 'SO under MRP review — Returned from MES. Existing WO is read-only; new releases and additional picking are blocked.' if approval.returned_from_mes else 'SO is not MES-owned. Existing WO is read-only.'
    if not approval.source_present:
        return 'SO missing from current source — disposition required. New releases and picking are blocked.'
    if source and source.get('reconciliation_required'):
        details = source.get('reconciliation_details') or {}
        if details.get('allocations'):
            return 'WO reconciliation required. Allocated WO quantities exceed the ordered quantities. Review affected allocations before releasing more work.'
        if details.get('shipments'):
            if any(issue.get('reason') for issue in details['shipments']):
                return 'WO reconciliation required. Assign invoiced shipments to the affected WO releases; the SO invoice totals do not identify which release shipped.'
            return 'WO reconciliation required. Shipped/invoiced quantities exceed the ordered quantities. Review shipment records before releasing more work.'
        return 'WO reconciliation required. Review finished-goods status before releasing more work.'
    return ''


def refresh_reconciliation(approval):
    orders = SalesOrder.mes_query().filter_by(sales_order=approval.sales_order).all()
    source = execution_source(approval, orders)
    approval.reconciliation_details = source['reconciliation_details']
    approval.reconciliation_required = source['reconciliation_required']
