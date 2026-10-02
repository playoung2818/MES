"""Read-only access to the existing MRP-managed material readiness table."""
from flask import current_app
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError
from .models import db


def load_material_readiness(sales_order):
    """Join by QB Num without creating a model/table or changing approval state."""
    try:
        # Separate connection: an unavailable MRP table must not poison the
        # session used for SO review/approval.
        with db.engine.connect() as connection:
            if not inspect(connection).has_table('so_material_readiness'):
                return {'date': None, 'message': 'Readiness data unavailable'}
            row = connection.execute(text('''
                SELECT earliest_material_ready_date
                FROM so_material_readiness
                WHERE "QB Num" = :sales_order
            '''), {'sales_order': sales_order}).mappings().first()
        if row is None:
            return {'date': None, 'message': 'No readiness record'}
        return {'date': row['earliest_material_ready_date'], 'message': 'Not determined'}
    except SQLAlchemyError as exc:
        current_app.logger.warning('Material readiness unavailable: %s', type(exc).__name__)
        return {'date': None, 'message': 'Readiness data unavailable'}
