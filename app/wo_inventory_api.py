"""Saved-WO inventory facts. Shipping is explicit; completion is irrelevant."""
from collections import defaultdict
from datetime import datetime, timezone
from flask import jsonify, request, abort
from .models import db, SalesOrder


def register_wo_inventory_api(bp):
    @bp.get('/api/wo-quantities')
    def wo_quantities():
        numbers = request.args.getlist('sales_order')
        if len(numbers) > 500 or any(not number.strip() or len(number) > 80 for number in numbers):
            abort(400)
        query = db.session.query(SalesOrder.sales_order, SalesOrder.items, SalesOrder.is_shipped).filter(SalesOrder.record_source == 'mes')
        if numbers:
            query = query.filter(SalesOrder.sales_order.in_([number.strip() for number in numbers]))
        quantities = defaultdict(lambda: [0, 0])
        for number, items, is_shipped in query:
            for item in items or []:
                qty = max(0, int(item.get('quantity') or 0))
                if qty:
                    key = (str(number).strip(), str(item.get('item') or '').strip(),
                           str(item.get('inventory_site') or '').strip())
                    quantities[key][1 if is_shipped else 0] += qty
        return jsonify(schema_version=2, generated_at=datetime.now(timezone.utc).isoformat(),
                       quantities=[dict(sales_order=so, item=item, inventory_site=site,
                                        quantity=qty[0], shipped_quantity=qty[1])
                                   for (so, item, site), qty in sorted(quantities.items())])
