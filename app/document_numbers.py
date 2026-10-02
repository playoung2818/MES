"""Document identity allocation; caller commits with the order in one transaction."""
from sqlalchemy import text
from .models import db, SalesOrder


def lock_document_numbers():
    if db.session.get_bind().dialect.name == 'postgresql':
        # Serialize allocations (including first document of a new month).
        db.session.execute(text('SELECT pg_advisory_xact_lock(26090127)'))


def next_document_number(created_at):
    prefix = f"WO-{created_at:%y%m}-"
    numbers = db.session.query(SalesOrder.document_number).filter(
        SalesOrder.document_number.like(prefix + '%')).all()
    highest = max((int(value[len(prefix):]) for (value,) in numbers
                   if value and value[len(prefix):].isdigit()), default=0)
    return f'{prefix}{highest + 1:04d}'
