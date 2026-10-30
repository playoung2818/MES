"""Read-only, advisory checks of the exact WO preview (no Word/Sheet/Excel I/O)."""
from collections import Counter
from datetime import datetime, timezone
import re

from flask import current_app
from sqlalchemy import column, func, or_, select, table
from sqlalchemy.exc import SQLAlchemyError

from .models import db
from .serial_pruner import _is_na, normalize_numeric_serial, prune_and_sort_serials
from .so_workflow import production_item


def clean_text(value):
    return '' if value is None else str(value).strip()


def normalize_part(value):
    return clean_text(value).upper().replace('-', '').replace(' ', '')


def serial_key(value):
    return normalize_numeric_serial(clean_text(value)).upper()


def load_source_items(sales_order):
    """Use the current imported SO, not a historical approved snapshot."""
    source = table('open_sales_orders', column('QB Num'), column('Item'), column('Inventory Site'))
    # A failed optional read must not leave the enclosing WO transaction aborted.
    with db.session.begin_nested():
        rows = db.session.execute(select(source.c.Item, source.c['Inventory Site']).where(
            source.c['QB Num'] == sales_order)).mappings().all()
    return [dict(item=row['Item']) for row in rows
            if production_item({'item': row['Item']})
            and clean_text(row['Inventory Site']).casefold() != 'drop ship']


def load_receiving_parts(serials):
    """Batch lookups; latest entry date/id wins, matching the standalone checker.

    Match numeric leading-zero variants without converting long serials to numbers.
    No engine is constructed per serial and no receiving records are written.
    """
    serials = sorted({serial_key(sn) for sn in serials if clean_text(sn) and not _is_na(sn)})
    receiving = table('receiving_log', column('id'), column('entry_date'),
                      column('serial_number'), column('part_number'))
    found = {}
    with db.session.begin_nested():
        for start in range(0, len(serials), 400):
            batch = serials[start:start + 400]
            trimmed = func.trim(receiving.c.serial_number)
            filters = [func.upper(trimmed).in_(batch)]
            numeric = [sn.lstrip('0') for sn in batch if sn.isascii() and sn.isdigit()]
            if numeric:
                filters.append((trimmed != '') & func.ltrim(trimmed, '0').in_(numeric))
            rows = db.session.execute(select(receiving.c.serial_number, receiving.c.part_number).where(
                or_(*filters)).order_by(receiving.c.entry_date.desc().nulls_last(),
                                       receiving.c.id.desc())).mappings()
            for row in rows:
                found.setdefault(serial_key(row['serial_number']), clean_text(row['part_number']))
    return found


def check_items(sales_order, items, source_items, receiving_parts, raw_serials=None):
    """Pure checker; None means a reference could not be checked, not a clean pass."""
    issues = []

    def add(severity, error_type, message, **details):
        issues.append(dict(severity=severity, error_type=error_type, message=message, **details))

    active = [(n, item) for n, item in enumerate(items, 1) if int(item.get('quantity', 0)) > 0]
    if source_items is None:
        add('WARNING', 'SOURCE_CHECK_UNAVAILABLE', 'Current imported sales-order data could not be checked.')
    elif not source_items:
        add('WARNING', 'SOURCE_NOT_FOUND',
            f'{sales_order} is not in the current production-scope import. Item/row comparison was not performed.')
    else:
        actual = Counter(normalize_part(item['item']) for _, item in active)
        expected = Counter(normalize_part(item['item']) for item in source_items)
        # A zero-quantity line is intentionally left out of a partial release.
        omitted = Counter(normalize_part(i['item']) for i in items if int(i.get('quantity', 0)) == 0)
        differences = actual != expected
        partial = differences and actual + omitted == expected
        severity = 'WARNING' if partial else 'CRITICAL'
        if len(active) != len(source_items):
            add(severity, 'ROW_COUNT_MISMATCH',
                f'Row count: preview {len(active)}, current SO {len(source_items)}.' +
                (' Zero-quantity lines are omitted from this release.' if partial else ''))
        for key, count in (actual - expected).items():
            name = next(i['item'] for _, i in active if normalize_part(i['item']) == key)
            add('CRITICAL', 'MISSING_IN_SOURCE', f'Preview item is not in the current SO ({count} row(s)).', item=name)
        for key, count in (expected - actual).items():
            name = next(i['item'] for i in source_items if normalize_part(i['item']) == key)
            add(severity, 'EXTRA_IN_SOURCE',
                f'Current SO item is not included in this preview ({count} row(s)).', item=name)
        if not differences:
            add('INFO', 'SOURCE_ITEMS_MATCH', f'Current SO row count and item names match ({len(active)} rows).')

    if receiving_parts is None:
        add('WARNING', 'RECEIVING_CHECK_UNAVAILABLE',
            'Receiving log could not be checked. Serial existence and part matches are unverified.')
    seen = set()
    checked = matched = 0
    for row_index, item in active:
        qty, name = int(item['quantity']), item['item']
        serials = [clean_text(sn) for sn in item.get('serials', []) if clean_text(sn) and not _is_na(sn)]
        details = dict(row_index=row_index, item=name)
        if serials and len(serials) != qty:
            add('CRITICAL', 'QTY_MISMATCH', f'Quantity {qty}, serial count {len(serials)}.', **details)
        if not serials:
            add('WARNING', 'NO_SN', f'No serial numbers listed; quantity {qty}.', **details)
        elif any(_is_na(sn) for sn in item.get('serials', [])):
            add('WARNING', 'PARTIAL_SN', 'Some units use NA; review pre-installed/unserialized quantities.', **details)
        entered = serials
        if raw_serials is not None:
            raw = raw_serials.get(item.get('id'), '')
            entered = [sn for token in re.split(r'[,\n;]+', raw)
                       for sn in prune_and_sort_serials(token) if not _is_na(sn)]
        for sn in entered:
            key = serial_key(sn)
            if key in seen:
                add('WARNING', 'DUPLICATE_SN_IN_WO', 'Serial appears more than once in the entered WO.',
                    serial_number=sn, **details)
            seen.add(key)
        for sn in serials:
            if receiving_parts is None:
                continue
            checked += 1
            part = receiving_parts.get(serial_key(sn))
            if not part:
                add('CRITICAL', 'SERIAL_NOT_FOUND', 'Serial not found with a part number in the receiving log.',
                    serial_number=sn, **details)
            elif normalize_part(part) != normalize_part(name):
                add('CRITICAL', 'PART_MISMATCH', f'Receiving-log part: {part}.',
                    serial_number=sn, db_part=part, **details)
            else:
                matched += 1
                add('INFO', 'MATCH', 'Serial part matches receiving log.', serial_number=sn, **details)
    counts = Counter(issue['severity'] for issue in issues)
    return dict(sales_order=sales_order, checked_at=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),
                issues=issues, critical_count=counts['CRITICAL'], warning_count=counts['WARNING'],
                checked_serials=checked, matched_serials=matched,
                findings=[i for i in issues if i['severity'] != 'INFO'],
                matches=[i for i in issues if i['severity'] == 'INFO'])


def preview_checks(sales_order, items, raw_serials=None):
    source_items = receiving_parts = None
    try:
        source_items = load_source_items(sales_order)
    except SQLAlchemyError:
        current_app.logger.warning('WO preview source check unavailable', exc_info=True)
    serials = [sn for item in items if int(item.get('quantity', 0)) > 0
               for sn in item.get('serials', []) if clean_text(sn) and not _is_na(sn)]
    try:
        receiving_parts = load_receiving_parts(serials) if serials else {}
    except SQLAlchemyError:
        current_app.logger.warning('WO preview receiving check unavailable', exc_info=True)
    return check_items(sales_order, items, source_items, receiving_parts, raw_serials)
