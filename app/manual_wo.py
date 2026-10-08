"""Blank WOs: operator-provided data, with no Google Sheet quantity limits."""
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4
from flask import request, render_template, flash, redirect, url_for, abort, current_app
from werkzeug.datastructures import MultiDict
from itsdangerous import BadData
from .models import db, SalesOrder
from .document_numbers import lock_document_numbers, next_document_number
from .wo_service import parse_items, validate
from .word_export import word_context


def parse_manual(form, existing):
    so = form.get('sales_order', '').strip()
    customer = form.get('customer', '').strip()
    po = form.get('customer_po', '').strip()
    if not so or len(so) > 80:
        raise ValueError('NTA Order ID is required (maximum 80 characters).')
    if not customer or len(customer) > 200 or len(po) > 120:
        raise ValueError('Customer is required; check Customer and PO field lengths.')
    columns = [form.getlist(field) for field in ('item', 'quantity', 'serials', 'notes')]
    if not columns[0] or len({len(values) for values in columns}) != 1:
        raise ValueError('Invalid item rows.')
    originals, normalized = [], MultiDict()
    for n, (item, qty, serials, notes) in enumerate(zip(*columns), 1):
        item = item.strip()
        if not item or len(item) > 200:
            raise ValueError('Every row needs an Item (maximum 200 characters). Remove unused rows.')
        originals.append(dict(id=n, item=item, quantity=0,
                              serials=existing[n-1].get('serials', []) if n <= len(existing) else [], notes=''))
        for key, value in [('quantity', qty), ('serials', serials), ('notes', notes)]:
            normalized[f'{key}_{n}'] = value
    normalized['item_order'] = ','.join(str(i['id']) for i in originals)
    return dict(sales_order=so, customer=customer, customer_po=po, items=parse_items(normalized, originals))


def manual_editor(wo_id, signer):
    saved = db.session.get(SalesOrder, wo_id) if wo_id else None
    if wo_id and (saved is None or not saved.is_manual or saved.record_source != 'mes'):
        abort(404)
    blank = dict(sales_order=request.args.get('sales_order', ''), customer='', customer_po='',
                 items=[dict(id=1, item='', quantity=1, serials=[], notes='')])
    data = dict(sales_order=saved.sales_order, customer=saved.customer, customer_po=saved.customer_po,
                items=deepcopy(saved.items)) if saved else blank
    context = dict(data=data, editing=saved, **word_context(saved))
    if request.method == 'POST':
        try:
            payload = None
            if request.form.get('action') == 'push':
                try:
                    payload = signer.loads(request.form.get('preview_token', ''), max_age=3600)
                except BadData:
                    raise ValueError('Preview expired or missing. Generate the table again.')
                if payload.get('mode') != 'manual' or payload.get('edit_id') != wo_id:
                    raise ValueError('Preview belongs to another WO.')
            data = parse_manual(request.form, payload['data']['items'] if payload else data['items'])
            if saved and data['sales_order'] != saved.sales_order:
                raise ValueError('Cannot change the SO reference of an existing WO.')
            context['data'] = data
            if payload and payload['data'] != data:
                raise ValueError('Inputs changed after preview. Generate the table again.')
            if payload:
                lock_document_numbers()
            all_orders = SalesOrder.mes_query().populate_existing().all()
            existing = next((o for o in all_orders if payload and o.id == payload['id']), None)
            if payload and not saved and existing:
                return redirect(url_for('main.generated_detail', wo_id=existing.id))
            if payload and saved and (existing is None or str(existing.pushed_at) != payload['version']):
                raise ValueError('WO changed in another session. Reload before revising.')
            validate(data['items'], None, [], all_orders, wo_id)
            if payload:
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                if not existing:
                    number = next_document_number(now)
                    releases = [o.release_number for o in all_orders if o.sales_order == data['sales_order']]
                    existing = SalesOrder(id=payload['id'], sales_order=data['sales_order'], is_manual=True,
                        release_number=max(releases, default=0)+1, generated_date=now, document_number=number)
                    db.session.add(existing)
                existing.customer = data['customer']
                existing.customer_po = data['customer_po']
                existing.items = data['items']
                existing.pushed_at = now
                db.session.commit()
                flash('Blank WO pushed to DB.', 'success')
                return redirect(url_for('main.generated_detail', wo_id=existing.id))
            token = signer.dumps(dict(mode='manual', id=saved.id if saved else str(uuid4()), edit_id=wo_id,
                                      version=str(saved.pushed_at) if saved else None, data=data))
            context.update(show_table=True, preview_token=token, **word_context(saved, preview=True))
            return render_template('manual_wo.html', **context)
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'danger')
        except Exception:
            db.session.rollback()
            current_app.logger.exception('Could not push manual WO')
            flash('Could not push WO. Please generate a new preview and retry.', 'danger')
        # Preserve operator inputs, including invalid values, for correction.
        context['data'] = dict(sales_order=request.form.get('sales_order', ''), customer=request.form.get('customer', ''),
            customer_po=request.form.get('customer_po', ''), items=[dict(item=i, quantity=q, serial_text=s, notes=n)
            for i, q, s, n in zip(*(request.form.getlist(k) for k in ('item', 'quantity', 'serials', 'notes')))])
        return render_template('manual_wo.html', **context), 400
    return render_template('manual_wo.html', **context)
