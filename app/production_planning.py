"""MRP production planning, adapted to the MES app's existing database.

Source: playoung2818/MRP_System, commit f37fe90861187b8806cd6ac6fb3ea3fbb2694578.
GETs read only. Schedule saves write shared MRP overrides, never WO Details.
Picked status and quantities are derived exclusively from saved WO Details.
"""
from datetime import date, timedelta
import pandas as pd
from .models import db, ProductionOverride, SalesOrder
from .approved_so import approved_orders
from .planning_labor import (
    _build_first_wo_item_map, _build_production_order_row, _format_num,
    _summarize_labor_rows, _parse_float,
    WEEKLY_LABOR_CAPACITY_HOURS,
)


class PlanningDataError(Exception):
    pass


def load_orders():
    """Planning and WO generation share approved_work_orders data and item order."""
    rows = []
    for source in approved_orders().values():
        for item in source['items']:
            site = item.get('inventory_site') or source.get('inventory_site') or ''
            if site.strip().casefold() == 'drop ship':
                continue
            rows.append({'QB Num': source['sales_order'], 'Customer': source.get('customer') or '',
                         'Customer PO': source.get('customer_po') or '', 'Terms': source.get('terms') or '',
                         'Item': item['item'], 'Qty': item['quantity'],
                         'Lead Time': source.get('ship_date') or '', 'Inventory Site': site})
    frame = pd.DataFrame(rows, columns=['QB Num', 'Customer', 'Customer PO', 'Terms',
                                       'Item', 'Qty', 'Lead Time', 'Inventory Site'])
    frame['Lead Time'] = pd.to_datetime(frame['Lead Time'], errors='coerce', format='mixed')
    return frame


def saved_quantities():
    """Aggregate current persisted releases; revisions replace, not add to, quantities."""
    orders = {}
    for number, items in db.session.query(SalesOrder.sales_order, SalesOrder.items):
        products = orders.setdefault(str(number).strip().casefold(), {})
        for item in items or []:
            key = str(item.get('item') or '').strip().casefold()
            quantity = max(_parse_float(item.get('quantity'), 0.0), 0.0)
            products[key] = products.get(key, 0.0) + quantity
    return orders


def pick_totals(info, saved):
    """Match saved quantities by product, excluding accessory counts from system units.

    Clamp each product to its planned qty; calculate remaining labor per family,
    rather than applying a proportional estimate to mixed-family releases.
    """
    unit_rows = info.get('unit_rows') or []
    if not unit_rows:
        unit_rows = [{'item': info.get('item'), 'qty': info.get('qty'),
                      'family': 'Unknown', 'hours_per_unit': None}]
    products = {}
    for item in unit_rows:
        key = str(item.get('item') or '').strip().casefold()
        entry = products.setdefault(key, {'qty': 0.0, 'family': item['family'],
                                          'rate': item['hours_per_unit']})
        entry['qty'] += max(_parse_float(item.get('qty'), 0.0), 0.0)
    picked, remaining, hours = 0.0, 0.0, 0.0
    families = {}
    unknown = False
    for key, entry in products.items():
        done = min(saved.get(key, 0.0), entry['qty'])
        left = entry['qty'] - done
        picked += done
        remaining += left
        families[entry['family']] = families.get(entry['family'], 0.0) + left
        if entry['rate'] is None:
            unknown = unknown or left > 0
        else:
            hours += left * entry['rate']
    return dict(picked_qty=picked, picked_qty_str=_format_num(picked),
                remaining_units=remaining, remaining_units_str=_format_num(remaining),
                labor_hours=None if unknown else hours,
                labor_hours_str='Review' if unknown else _format_num(hours),
                family_units_detail=families, picked_qty_saved=False)


def labor_map(frame):
    return _build_first_wo_item_map(frame)


def build_plan(frame, today=None):
    today = today or date.today()
    overrides = {row.wo_number: row for row in ProductionOverride.query.all()}
    saved = saved_quantities()
    schedule = {key: row.production_date.isoformat() for key, row in overrides.items() if row.production_date}
    labor = labor_map(frame)
    statuses, quantities = {}, {}
    for number, info in labor.items():
        key = number.strip().casefold()
        statuses[number] = 'Picked' if key in saved else 'NA'
        quantities[number] = pick_totals(info, saved.get(key, {}))
    picked = {number: info['picked_qty'] for number, info in quantities.items()}
    assigned, finished, unassigned = {}, [], []
    for number, group in frame.groupby('QB Num', sort=True):
        lead = group.iloc[0]['Lead Time']
        lead_date = lead.date() if pd.notna(lead) else None
        placeholder = lead_date and (lead_date.month, lead_date.day) in ((7, 4), (12, 31))
        override = overrides.get(number)
        production_date = override.production_date if override else None
        if not production_date and lead_date and not placeholder:
            production_date = lead_date
        row = _build_production_order_row(
            number, group, labor_item_map=labor, wo_status_map=statuses,
            picked_qty_overrides=picked, production_schedule_overrides=schedule,
            production_date_str=production_date.isoformat() if production_date else '',
        )
        row.update(quantities[number])
        if (override and override.is_finished_goods) or (production_date and production_date < today):
            finished.append(row)
        elif not production_date or production_date.weekday() >= 5:
            row.update(lead_time=row['ship_date'], remaining_qty_str=row['remaining_units_str'])
            unassigned.append(row)
        else:
            assigned.setdefault(production_date, []).append(row)
    # Include upcoming weekday buckets; keep all future scheduled dates too.
    for offset in range(22):
        day = today + timedelta(days=offset)
        if day.weekday() < 5:
            assigned.setdefault(day, [])
    groups = []
    week_rows = {}
    for day, rows in sorted(assigned.items()):
        summary = _summarize_labor_rows(rows)
        groups.append({'date': day.isoformat(), 'orders': rows,
                       'total_units_str': _format_num(sum(row['remaining_units'] for row in rows)),
                       'labor_hours_str': summary['known_hours_str'],
                       'unknown_hours_count': summary['unknown_count']})
        week = day - timedelta(days=day.weekday())
        week_rows.setdefault(week, []).extend(rows)
    weeks = []
    for start, rows in sorted(week_rows.items()):
        summary = _summarize_labor_rows(rows)
        hours = summary['known_hours']
        remaining = WEEKLY_LABOR_CAPACITY_HOURS - hours
        review, large = [], []
        for row in rows:
            detail = dict(row, first_item=row['line'], total_units_str=row['qty_str'], family='Mixed / model')
            if row['labor_hours'] is None:
                review.append(detail)
            if row['qty'] > 20:
                large.append(detail)
        weeks.append({'week_start': start.isoformat(), 'week_end': (start + timedelta(days=6)).isoformat(),
                      'so_count': len(rows), 'capacity_hours_str': _format_num(WEEKLY_LABOR_CAPACITY_HOURS),
                      'used_hours_str': _format_num(hours), 'remaining_hours': remaining,
                      'remaining_hours_str': _format_num(remaining), 'unknown_count': summary['unknown_count'],
                      'family_counts': summary['family_counts'], 'large_sos': large, 'review_sos': review,
                      'used_pct': _format_num(min(hours / WEEKLY_LABOR_CAPACITY_HOURS * 100, 100)),
                      'status': 'over' if hours > WEEKLY_LABOR_CAPACITY_HOURS else 'tight' if hours >= 64 else 'ok'})
    return dict(capacity_weeks=weeks, date_groups=groups, passed_lt_orders=finished,
                passed_lt_summary=_summarize_labor_rows(finished), unassigned_lt_orders=unassigned,
                unassigned_lt_summary=_summarize_labor_rows(unassigned))
