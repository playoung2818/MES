"""Manual shipment status for whole saved WO releases."""
from datetime import datetime, timezone
from uuid import uuid4
from flask import current_app, request, session, abort, flash, redirect, url_for
from itsdangerous import URLSafeTimedSerializer, BadData
from sqlalchemy.exc import SQLAlchemyError
from .models import db, SalesOrder


def signer():
    return URLSafeTimedSerializer(current_app.secret_key, salt='wo-shipment-v1')


def shipment_context(orders):
    session.setdefault('planning_csrf', str(uuid4()))
    return dict(shipment_csrf=session['planning_csrf'], shipment_tokens={
        order.id: signer().dumps(dict(id=order.id, items=order.items, shipped=order.is_shipped))
        for order in orders})


def register_shipment_routes(bp):
    @bp.post('/generated/wo/<wo_id>/shipment')
    def set_wo_shipment(wo_id):
        from .planning_routes import _csrf_valid
        if not _csrf_valid():
            abort(403)
        target = request.form.get('is_shipped')
        if target not in ('0', '1'):
            abort(400)
        try:
            payload = signer().loads(request.form.get('shipment_token', ''), max_age=3600)
            order = SalesOrder.mes_query().filter_by(id=wo_id).populate_existing().with_for_update().first()
            if order is None:
                abort(404)
            if payload != dict(id=order.id, items=order.items, shipped=order.is_shipped):
                raise ValueError('This WO changed. Reload before updating shipment status.')
            order.is_shipped = target == '1'
            order.shipment_updated_at = datetime.now(timezone.utc)
            db.session.commit()
            flash('WO marked shipped. It will no longer count as WIP on the next MRP run.' if order.is_shipped else
                  'WO marked not shipped. Its quantities will count as WIP on the next MRP run.', 'success')
        except (BadData, ValueError) as exc:
            db.session.rollback()
            flash(str(exc), 'danger')
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception('WO shipment update failed')
            flash('Shipment status could not be saved.', 'danger')
        if request.form.get('return_to') == 'detail':
            return redirect(url_for('main.generated_detail', wo_id=wo_id))
        return redirect(url_for('main.generated_orders', q=request.form.get('q', '')))
