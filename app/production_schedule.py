"""Shared production-date validation/writes for calendar and SO review."""
from datetime import date, datetime, timezone
from .models import db, ProductionOverride


def parse_production_date(raw):
    try:
        value = date.fromisoformat(raw)
        if value.isoformat() != raw:
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError('Production date must use YYYY-MM-DD.') from None
    if value < date.today() or value.weekday() >= 5:
        raise ValueError('Choose today or a future weekday for production.')
    return value


def upsert(model, values):
    if db.engine.dialect.name == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    statement = insert(model).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=['wo_number'],
        set_={key: getattr(statement.excluded, key) for key in values if key != 'wo_number'},
    )
    db.session.execute(statement)


def save_production_date(number, target, updated_by=None):
    """Stage schedule write in caller's transaction; do not commit separately."""
    upsert(ProductionOverride, dict(wo_number=number, production_date=target,
        is_finished_goods=False, schedule_updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
        schedule_updated_by=updated_by, finished_goods_updated_at=None, finished_goods_updated_by=None))
