"""Flask routes for the shared MRP planning module."""
from datetime import date, datetime, timezone
import hmac
from uuid import uuid4
from flask import current_app, jsonify, render_template, request, session, flash, redirect, url_for, abort
from sqlalchemy.exc import SQLAlchemyError
from .models import db, ProductionOverride, ApprovedSO
from .production_planning import PlanningDataError, load_orders, build_plan
from .production_schedule import upsert as _upsert, parse_production_date
from .material_readiness import load_material_readiness
import pandas as pd

REVIEW_STATUSES = [
    ('pending', 'Pending review'), ('all', 'All statuses'),
    ('legacy', 'Legacy — sync required'), ('unapproved', 'Unapproved'),
    ('changed', 'Changed'), ('removed', 'Removed — excluded'),
    ('approved', 'Approved'), ('retired', 'Retired'),
]


def review_status(row, changes):
    if row.synced_at is None:
        return 'legacy'
    if not row.google_present:
        return 'removed' if row.is_active or row.approved_at is None else 'retired'
    if row.approved_at is None or not row.is_active:
        return 'unapproved'
    return 'changed' if changes else 'approved'


def needs_review(row, changes=None):
    if row.synced_at is None:
        return True
    if not row.google_present:
        return False
    if row.approved_at is None or not row.is_active:
        return True
    if changes is None:
        from .approved_so import differences
        changes = differences(row.approved_snapshot, row.latest_snapshot)
    return bool(changes)


def review_lt_key(entry):
    value = pd.to_datetime(entry['source'].get('ship_date') or '', errors='coerce', format='mixed')
    return (pd.isna(value), value.date() if pd.notna(value) else date.max, entry['order'].sales_order)


def _csrf_valid():
    expected = session.get('planning_csrf')
    actual = request.headers.get('X-CSRFToken') or request.form.get('csrf_token', '')
    return bool(expected and hmac.compare_digest(expected.encode('utf-8'), actual.encode('utf-8')))


def register_routes(bp):
    @bp.route('/production_planning')
    def production_planning():
        session.setdefault('planning_csrf', str(uuid4()))
        error = None
        status = 200
        try:
            plan = build_plan(load_orders())
        except (PlanningDataError, SQLAlchemyError) as exc:
            db.session.rollback()
            error = str(exc) if isinstance(exc, PlanningDataError) else 'Unable to read MRP planning data. Check database access.'
            current_app.logger.warning('Production planning data unavailable: %s', type(exc).__name__)
            plan = dict(capacity_weeks=[], date_groups=[], passed_lt_orders=[], passed_lt_summary={},
                        unassigned_lt_orders=[], unassigned_lt_summary={})
            status = 503
        pending = sum(1 for row in ApprovedSO.query.all() if needs_review(row))
        return render_template('production_planning.html', **plan, error=error, pending_so_count=pending,
                               planning_csrf=session['planning_csrf'],
                               loaded_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S')), status

    @bp.route('/production_planning/orders')
    def so_review_list():
        from .approved_so import differences
        session.setdefault('planning_csrf', str(uuid4()))
        q = request.args.get('q', '').strip().casefold()
        selected_status = request.args.get('status') or ('all' if request.args.get('all') == '1' else 'pending')
        labels = dict(REVIEW_STATUSES)
        if selected_status not in labels:
            abort(400)
        pending_only = selected_status == 'pending'
        rows = []
        for row in ApprovedSO.query.order_by(ApprovedSO.sales_order.desc()).all():
            source = row.latest_snapshot or row.to_source()
            if q and q not in ' '.join([row.sales_order, source.get('customer') or '', source.get('customer_po') or '']).casefold():
                continue
            pending_changes = differences(row.approved_snapshot, row.latest_snapshot)
            pending = needs_review(row, pending_changes)
            status = review_status(row, pending_changes)
            if (pending_only and not pending) or (selected_status not in ('all', 'pending') and status != selected_status):
                continue
            rows.append(dict(order=row, source=source, changes=pending_changes,
                             status=status, status_label=labels[status],
                             sync_changes=differences(row.previous_snapshot, row.latest_snapshot)))
        rows.sort(key=review_lt_key)
        return render_template('so_review_list.html', rows=rows, q=request.args.get('q', ''),
                               pending_only=pending_only, status_options=REVIEW_STATUSES,
                               selected_status=selected_status, summary_title=labels[selected_status],
                               planning_csrf=session['planning_csrf'])

    @bp.route('/production_planning/orders/<path:sales_order>', methods=['GET', 'POST'])
    def so_review_detail(sales_order):
        from collections import Counter
        from .approved_so import differences, review_items, approve, signature
        row = db.session.get(ApprovedSO, sales_order)
        if row is None:
            abort(404)
        session.setdefault('planning_csrf', str(uuid4()))
        status = 200
        if request.method == 'POST':
            if not _csrf_valid():
                abort(403)
            try:
                approve(row, request.form.get('item_order', '').split(',') if request.form.get('item_order') else [],
                        int(request.form['source_revision']), int(request.form['revision']),
                        retire=request.form.get('action') == 'retire',
                        production_date=request.form.get('production_date', '').strip() or None,
                        updated_by=request.remote_addr)
                flash('SO retired from new WO generation.' if not row.is_active else
                      'Approved SO published. Item order and any assigned production date are saved.', 'success')
                return redirect(url_for('main.so_review_list'))
            except (ValueError, KeyError) as exc:
                db.session.rollback()
                flash(str(exc), 'danger')
                status = 400
            except SQLAlchemyError:
                db.session.rollback()
                current_app.logger.exception('SO approval failed')
                flash('Approval failed. Approved data was not changed.', 'danger')
                status = 503
        items = review_items(row)
        duplicates = any(count > 1 for count in Counter(signature(item) for item in items).values())
        schedule = db.session.get(ProductionOverride, row.sales_order)
        selected_date = ''
        if schedule and not schedule.is_finished_goods and schedule.production_date:
            if schedule.production_date >= date.today() and schedule.production_date.weekday() < 5:
                selected_date = schedule.production_date.isoformat()
        return render_template('so_review_detail.html', order=row, items=items,
                               review_source=row.latest_snapshot or row.to_source(), schedule=schedule,
                               material_readiness=load_material_readiness(row.sales_order),
                               selected_date=request.form.get('production_date', selected_date),
                               today=date.today().isoformat(),
                               sync_changes=differences(row.previous_snapshot, row.latest_snapshot),
                               pending_changes=differences(row.approved_snapshot, row.latest_snapshot),
                               duplicates=duplicates, planning_csrf=session['planning_csrf']), status

    @bp.route('/api/production_schedule', methods=['POST'])
    def api_production_schedule():
        if not _csrf_valid():
            return jsonify(ok=False, error='Session expired. Reload Production Planning before saving.'), 403
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(ok=False, error='Invalid request.'), 400
        assignments = payload.get('assignments')
        if not isinstance(assignments, list) or not assignments or len(assignments) > 2000:
            return jsonify(ok=False, error='Provide a nonempty list of schedule changes (up to 2000).'), 400
        try:
            orders = load_orders()
            keys = set(orders['QB Num'])
            parsed, seen = [], set()
            for entry in assignments:
                if not isinstance(entry, dict):
                    return jsonify(ok=False, error='Invalid schedule assignment.'), 400
                number = str(entry.get('wo_number') or '').strip()
                if number not in keys or number in seen:
                    return jsonify(ok=False, error='Unknown or repeated SO / WO number.'), 400
                seen.add(number)
                area = entry.get('target_area', 'schedule')
                if area not in ('schedule', 'finished_goods'):
                    return jsonify(ok=False, error='Invalid target area.'), 400
                target = None
                if area == 'schedule':
                    try:
                        target = parse_production_date(entry.get('production_date'))
                    except ValueError as exc:
                        return jsonify(ok=False, error=str(exc)), 400
                parsed.append((number, area, target))
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            # Validate every assignment first; then commit the entire batch together.
            for number, area, target in parsed:
                values = {'wo_number': number}
                if area == 'finished_goods':
                    values.update(is_finished_goods=True, finished_goods_updated_at=now,
                                  finished_goods_updated_by=request.remote_addr)
                else:
                    values.update(production_date=target, is_finished_goods=False,
                                  schedule_updated_at=now, schedule_updated_by=request.remote_addr,
                                  finished_goods_updated_at=None, finished_goods_updated_by=None)
                _upsert(ProductionOverride, values)
            db.session.commit()
        except (PlanningDataError, SQLAlchemyError):
            db.session.rollback()
            current_app.logger.exception('Planning schedule save failed')
            return jsonify(ok=False, error='Unable to save the schedule. No changes were committed.'), 503
        return jsonify(ok=True, saved_count=len(parsed))

    @bp.route('/api/wo_picked_qty', methods=['POST'])
    def api_wo_picked_qty():
        if not _csrf_valid():
            return jsonify(ok=False, error='Session expired. Reload Production Planning before saving.'), 403
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(ok=False, error='Invalid request.'), 400
        return jsonify(ok=False, error='Picked Qty is calculated from WO Details. Revise the saved WOs to change it.'), 409
