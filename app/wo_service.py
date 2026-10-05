"""Partial WO allocation and validation helpers."""
from collections import defaultdict
from copy import deepcopy
from types import SimpleNamespace
from .serial_pruner import _is_na, prune_and_sort_serials, normalize_numeric_serial
import re


def totals(items, normalized=False):
    result = defaultdict(int)
    for item in items:
        product = item['item'].strip().casefold() if normalized else item['item']
        result[product] += int(item.get('quantity', 0))
    return dict(result)


def allocated(orders, exclude_id=None, normalized=False):
    return totals([item for order in orders if order.id != exclude_id for item in order.items], normalized=normalized)


def new_order(source, orders):
    used = allocated(orders, normalized=True)
    items = []
    for n, row in enumerate(source['items'], 1):
        quantity = int(row['quantity'])
        product = row['item'].strip().casefold()
        consumed = min(quantity, used.get(product, 0))
        used[product] = used.get(product, 0) - consumed
        items.append(dict(id=n, item=row['item'], quantity=quantity-consumed, serials=[],
                          notes=row.get('configuration') or '', inventory_site=row.get('inventory_site') or ''))
    return SimpleNamespace(sales_order=source['sales_order'], customer=source.get('customer'),
                           customer_po=source.get('customer_po'), items=items)


def parse_items(form, original):
    by_id = {item['id']: deepcopy(item) for item in original}
    ids = [int(value) for value in form.get('item_order', '').split(',') if value.strip()]
    if len(ids) != len(by_id) or set(ids) != set(by_id):
        raise ValueError('Item list changed. Reload the WO before editing.')
    items = [by_id[i] for i in ids]
    for item in items:
        n = item['id']
        raw_qty = form.get(f'quantity_{n}', str(item['quantity'])).strip()
        if not re.fullmatch(r'[0-9]+', raw_qty):
            raise ValueError('Qty must be a whole number greater than or equal to zero.')
        item['quantity'] = int(raw_qty)
        item['notes'] = form.get(f'notes_{n}', item.get('notes', ''))
        existing = set(item.get('serials', []))
        serials = set()
        for token in re.split(r'[,\n;]+', form.get(f'serials_{n}', '')):
            token = token.strip()
            serials.update([token] if token in existing and not _is_na(token) else prune_and_sort_serials(token))
        item['serials'] = sorted(serials)
        if item['quantity'] == 0 and any(not _is_na(s) for s in serials):
            raise ValueError(f"{item['item']}: a zero-quantity line cannot contain serial numbers.")
    if not any(item['quantity'] > 0 for item in items):
        raise ValueError('A WO must contain at least one positive quantity.')
    return items


def validate(items, limits, sibling_orders, all_orders, exclude_id=None):
    used = allocated(sibling_orders, exclude_id, normalized=True)
    for product, quantity in (totals(items, normalized=True).items() if limits is not None else []):
        remaining = max(0, limits.get(product, 0) - used.get(product, 0))
        if quantity > remaining:
            raise ValueError(f'{product}: WO quantity {quantity} exceeds remaining quantity {remaining}.')
    seen = set()
    for item in items:
        for sn in item.get('serials', []):
            if _is_na(sn):
                continue
            key = normalize_numeric_serial(sn).upper()
            if key in seen:
                raise ValueError(f'Duplicate serial number: {sn}')
            seen.add(key)
    for order in all_orders:
        if order.id == exclude_id:
            continue
        for item in order.items:
            for sn in item.get('serials', []):
                if not _is_na(sn) and normalize_numeric_serial(sn).upper() in seen:
                    raise ValueError(f'Serial {sn} already belongs to {order.document_number or order.sales_order}.')
