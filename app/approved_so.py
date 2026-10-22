"""Read MES-owned source-present orders; MRP alone stages and approves SOs."""
from collections import defaultdict

from sqlalchemy import text
from .models import db, ApprovedSO, SalesOrder


def lock_review():
    if db.engine.dialect.name == 'postgresql':
        db.session.execute(text('SELECT pg_advisory_xact_lock(26091003)'))


def is_drop_ship_order(source):
    source = source or {}
    items = source.get('items') or []
    if not items:
        return str(source.get('inventory_site') or '').strip().casefold() == 'drop ship'
    return all(str(item.get('inventory_site') or source.get('inventory_site') or '').strip().casefold() == 'drop ship'
               for item in items)


def approved_orders():
    orders = {}
    from .so_workflow import execution_source
    saved = defaultdict(list)
    for wo in SalesOrder.mes_query().all():
        saved[wo.sales_order].append(wo)
    for row in ApprovedSO.query.filter(
            ApprovedSO.approved_at.isnot(None), ApprovedSO.workflow_status == 'released_to_mes',
            ApprovedSO.source_present.is_(True)).all():
        source = execution_source(row, saved[row.sales_order])
        snapshot = row.latest_snapshot or row.previous_snapshot or row.approved_snapshot or source
        if not is_drop_ship_order(snapshot) and not is_drop_ship_order(source):
            orders[row.sales_order] = source
    return orders
