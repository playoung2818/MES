import os
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for, abort
from itsdangerous import URLSafeTimedSerializer, BadData
from sqlalchemy import String, cast, or_
from .models import db, SalesOrder, ApprovedSO
from .document_numbers import lock_document_numbers, next_document_number
from .importer import read_google_sheet
from .wo_service import totals, allocated, new_order, parse_items, validate
from .word_export import word_context
from .approved_so import approved_orders, lock_review, differences

bp = Blueprint('main', __name__)
from .planning_routes import register_routes
register_routes(bp)


def _google_config():
    return (os.getenv('GOOGLE_CREDENTIALS_PATH', r'D:\OneDrive - neousys-tech\Desktop\ZC\pdfwo-466115-734096e1cef8.json'),
            os.getenv('GOOGLE_SHEET_NAME', 'PDF_WO'), os.getenv('GOOGLE_WORKSHEET_NAME') or None)


def _read_google_orders():
    return read_google_sheet(*_google_config())['orders']


def _read_approved_orders():
    return approved_orders()


def _signer():
    return URLSafeTimedSerializer(current_app.secret_key, salt='wo-preview')


def _search(query, q):
    if q:
        like = f'%{q}%'
        query = query.filter(or_(SalesOrder.sales_order.ilike(like), SalesOrder.document_number.ilike(like),
                                 SalesOrder.customer.ilike(like), SalesOrder.customer_po.ilike(like),
                                 cast(SalesOrder.items, String).ilike(like)))
    return query


@bp.route('/')
def index():
    q = request.args.get('q', '').strip()
    data = _read_approved_orders()
    sales_orders = []
    for source in data.values():
        text = ' '.join([source['sales_order'], source.get('customer') or '', source.get('customer_po') or '',
                         ' '.join(i['item'] for i in source['items'])])
        if not q or q.lower() in text.lower():
            sales_orders.append(SimpleNamespace(**source, part=source['items'][0]['item'] if source['items'] else ''))
    sales_orders = sorted(sales_orders, key=lambda so: so.sales_order, reverse=True)[:200]
    return render_template('index.html', sales_orders=sales_orders, q=q)


@bp.route('/wo/new', methods=['GET', 'POST'])
@bp.route('/wo/<wo_id>/manual', methods=['GET', 'POST'])
def manual_wo(wo_id=None):
    from .manual_wo import manual_editor
    return manual_editor(wo_id, _signer())


@bp.route('/so/<path:sales_order>', methods=['GET', 'POST'])
@bp.route('/wo/<wo_id>/edit', methods=['GET', 'POST'])
def so_detail(sales_order=None, wo_id=None):
    saved = db.session.get(SalesOrder, wo_id) if wo_id else None
    if wo_id and saved is None:
        abort(404)
    if saved and saved.is_manual:
        return redirect(url_for('main.manual_wo', wo_id=saved.id))
    if saved:
        sales_order = saved.sales_order
    siblings = SalesOrder.query.filter_by(sales_order=sales_order).order_by(SalesOrder.release_number).all()
    source = _read_approved_orders().get(sales_order)
    approval = db.session.get(ApprovedSO, sales_order)
    if not saved and source and approval and differences(approval.approved_snapshot, approval.latest_snapshot):
        flash('This SO has pending source changes. Reapprove it before creating a new work order or next release.', 'warning')
        return redirect(url_for('main.so_review_detail', sales_order=sales_order))
    if not source and not saved:
        flash('This SO is unapproved, inactive, or removed from the source; new WO generation is unavailable. Existing WOs can still be revised.', 'warning')
        return render_template('so_unavailable.html', sales_order=sales_order, releases=siblings), 503
    if saved:
        order = SimpleNamespace(sales_order=sales_order, customer=saved.customer, customer_po=saved.customer_po,
                                items=deepcopy(saved.items))
    else:
        order = new_order(source, siblings)
    limits = totals(source['items'], normalized=True) if source else allocated(siblings, normalized=True)
    used = allocated(siblings, saved.id if saved else None, normalized=True)
    remaining = {line['item']: max(0, limits.get(line['item'].strip().casefold(), 0)
                                 - used.get(line['item'].strip().casefold(), 0)) for line in order.items}
    release_number = saved.release_number if saved else max((o.release_number for o in siblings), default=0)+1
    target_id = saved.id if saved else str(uuid4())
    context = dict(sales_order=sales_order, order=order, lines=order.items, releases=siblings,
                   release_number=release_number, editing=saved, remaining=remaining,
                   ship_date=source.get('ship_date') if source else None, **word_context(saved))
    if request.method == 'POST':
        try:
            if request.form.get('action') == 'push':
                try:
                    payload = _signer().loads(request.form.get('preview_token', ''), max_age=3600)
                except BadData:
                    raise ValueError('Preview expired or missing. Generate the table again.')
                if payload['sales_order'] != sales_order or payload['edit_id'] != wo_id:
                    raise ValueError('Preview belongs to a different WO.')
                items = payload['items']
                # Ensure edits made after preview cannot silently change the checked table.
                if parse_items(request.form, order.items) != items:
                    raise ValueError('Inputs changed after preview. Generate the table again before pushing.')
                lock_review()
                db.session.expire_all()
                current_source = _read_approved_orders().get(sales_order)
                current_approval = db.session.get(ApprovedSO, sales_order)
                if not saved and current_approval and differences(current_approval.approved_snapshot, current_approval.latest_snapshot):
                    raise ValueError('This SO has pending source changes. Reapprove it before creating a new work order or next release.')
                if payload.get('source_revision') != (current_source or {}).get('revision'):
                    raise ValueError('Approved SO changed after preview. Reload and generate the table again.')
                if not saved and not current_source:
                    raise ValueError('SO is no longer approved and active.')
                limits = totals(current_source['items'], normalized=True) if current_source else limits
                lock_document_numbers()
                all_orders = SalesOrder.query.populate_existing().all()
                already = next((o for o in all_orders if o.id == payload['id']), None)
                if not saved and already:
                    return redirect(url_for('main.generated_detail', wo_id=already.id))
                siblings = [o for o in all_orders if o.sales_order == sales_order]
                current = next((o for o in siblings if o.id == wo_id), None) if wo_id else None
                if wo_id and current is None:
                    raise ValueError('WO no longer exists.')
                if current and str(current.pushed_at) != payload['version']:
                    raise ValueError('WO changed in another session. Reload before revising.')
                validate(items, limits, siblings, all_orders, wo_id)
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                if not current: #The code only generates a new document number when there is no existing WO being edited.
                    number = next_document_number(now)
                    current = SalesOrder(id=payload['id'], sales_order=sales_order,
                        release_number=max((o.release_number for o in siblings), default=0)+1,
                        generated_date=now, document_number=number)
                    db.session.add(current)
                current.customer = payload['customer']
                current.customer_po = payload['customer_po']
                current.items = items
                current.pushed_at = now
                db.session.commit()
                flash(f'{current.document_number} / P{current.release_number} pushed to DB.', 'success')
                return redirect(url_for('main.generated_detail', wo_id=current.id))
            items = parse_items(request.form, order.items)
            validate(items, limits, siblings, SalesOrder.query.all(), wo_id)
            payload = dict(id=target_id, edit_id=wo_id, sales_order=sales_order, items=items,
                           customer=order.customer, customer_po=order.customer_po,
                           version=str(saved.pushed_at) if saved else None,
                           source_revision=source.get('revision') if source else None)
            context.update(lines=items, show_table=True, preview=True, preview_token=_signer().dumps(payload),
                           **word_context(saved, preview=True))
            return render_template('so_detail.html', **context)
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'danger')
            return render_template('so_detail.html', **context), 400
        except Exception:
            db.session.rollback()
            current_app.logger.exception('Could not generate/push WO')
            flash('Could not push WO. Generate the preview again and retry.', 'danger')
            return render_template('so_detail.html', **context), 500
    return render_template('so_detail.html', **context)


@bp.route('/generated')
def generated_orders():
    q = request.args.get('q', '').strip()
    orders = _search(SalesOrder.query, q).order_by(SalesOrder.document_number.desc()).all()
    return render_template('generated.html', orders=orders, q=q)


@bp.route('/generated/wo/<wo_id>')
def generated_detail(wo_id):
    order = db.session.get(SalesOrder, wo_id)
    if order is None:
        abort(404)
    return render_template('generated_detail.html', order=order, lines=order.items,
                           sales_order=order.sales_order, **word_context(order))


@bp.route('/generated/<path:sales_order>')
def generated_so(sales_order):
    orders = SalesOrder.query.filter_by(sales_order=sales_order).order_by(SalesOrder.release_number).all()
    if not orders:
        abort(404)
    return render_template('generated.html', orders=orders, q=sales_order)


@bp.route('/sync-google', methods=['POST'])
def sync_google():
    from .planning_routes import _csrf_valid
    from .approved_so import sync_snapshots
    if not _csrf_valid():
        abort(403)
    try:
        changed = sync_snapshots(read_google_sheet(*_google_config()))
        flash(f'Google snapshots synced: {changed} SOs changed. Approved data and WOs were not overwritten.', 'success')
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'danger')
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Google Sheet sync failed')
        flash('Google Sheet sync failed. Existing snapshots and approved SOs were preserved.', 'danger')
    return redirect(url_for('main.so_review_list'))
