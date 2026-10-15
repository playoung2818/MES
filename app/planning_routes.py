"""MES consumes MRP-approved orders; only scheduling is editable here."""
from datetime import datetime, timezone
import hmac
import os
from uuid import uuid4
from urllib.parse import quote
from flask import current_app, jsonify, render_template, request, session, redirect, abort
from sqlalchemy.exc import SQLAlchemyError
from .models import db, ProductionOverride
from .production_planning import PlanningDataError, load_orders, build_plan
from .production_schedule import upsert as _upsert, parse_production_date


def mrp_review_url(sales_order=None):
    base = current_app.config.get('MRP_BASE_URL') or os.getenv('MRP_BASE_URL', 'http://localhost:5002')
    return base.rstrip('/') + '/sales_orders' + ('/' + quote(sales_order, safe='') if sales_order else '')


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
        return render_template('production_planning.html', **plan, error=error,
                               planning_csrf=session['planning_csrf'],
                               loaded_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S')), status

    # Existing bookmarks point to MRP. Writes are deliberately not redirected:
    # a cached MES form must never approve or sync orders after the ownership move.
    @bp.route('/production_planning/orders')
    def so_review_list():
        return redirect(mrp_review_url())

    @bp.route('/production_planning/orders/<path:sales_order>', methods=['GET', 'POST'])
    def so_review_detail(sales_order):
        if request.method == 'POST':
            return jsonify(ok=False, error='Sales-order review moved to MRP.', review_url=mrp_review_url(sales_order)), 410
        return redirect(mrp_review_url(sales_order))

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
