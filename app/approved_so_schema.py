"""Safely rename approved-order tables and add review columns; back up first."""
import json
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import inspect, text
from .models import db

TABLE = 'approved_work_orders'
LEGACY_TABLES = ('sales_order', 'approved_sales_orders')
COLUMNS = {
    'metadata_fields': "JSON NOT NULL DEFAULT '{}'",
    'previous_snapshot': 'JSON', 'latest_snapshot': 'JSON', 'approved_snapshot': 'JSON',
    'google_present': 'BOOLEAN NOT NULL DEFAULT FALSE',
    'is_active': 'BOOLEAN NOT NULL DEFAULT FALSE',
    'synced_at': 'TIMESTAMP', 'approved_at': 'TIMESTAMP',
    'source_revision': 'INTEGER NOT NULL DEFAULT 0', 'revision': 'INTEGER NOT NULL DEFAULT 0',
}


def _lock(connection):
    if db.engine.dialect.name == 'postgresql':
        connection.execute(text('SELECT pg_advisory_xact_lock(26091002)'))


def _backup(connection, table, reason, backup_dir):
    rows = [dict(row) for row in connection.execute(text(f'SELECT * FROM "{table}"')).mappings()]
    folder = Path(backup_dir) if backup_dir else Path(__file__).resolve().parents[1] / 'data' / 'backups'
    folder.mkdir(parents=True, exist_ok=True)
    name = datetime.now(timezone.utc).strftime(f'{table}_before_{reason}_%Y%m%dT%H%M%S%f.json')
    with (folder / name).open('x', encoding='utf-8') as backup:
        json.dump(rows, backup, ensure_ascii=False, indent=2, default=str)


def rename_approved_so_table(backup_dir=None):
    """Run before create_all; migrate either previous name without splitting data."""
    with db.engine.begin() as connection:
        _lock(connection)
        inspector = inspect(connection)
        existing = [name for name in (*LEGACY_TABLES, TABLE) if inspector.has_table(name)]
        if not existing:
            return False
        if existing == [TABLE]:
            return False
        required = {'sales_order', 'customer', 'customer_po', 'items'}
        known = required | set(COLUMNS)
        for name in existing:
            columns = {column['name'] for column in inspector.get_columns(name)}
            if (not required.issubset(columns) or not columns.issubset(known)
                    or inspector.get_pk_constraint(name)['constrained_columns'] != ['sales_order']):
                raise RuntimeError('Unexpected approved-order table schema. Resolve the conflict before startup; no tables were changed.')
        if db.engine.dialect.name == 'postgresql':
            connection.execute(text("SET LOCAL lock_timeout = '10s'"))
            names = ', '.join(f'"{name}"' for name in existing)
            connection.execute(text(f'LOCK TABLE {names} IN ACCESS EXCLUSIVE MODE'))
        counts = {name: connection.execute(text(f'SELECT count(*) FROM "{name}"')).scalar_one()
                  for name in existing}
        populated = [name for name in existing if counts[name] > 0]
        if len(populated) > 1:
            raise RuntimeError('Multiple approved-order tables contain data. Resolve the conflict before startup; no tables were changed.')
        source = populated[0] if populated else next(name for name in (TABLE, 'approved_sales_orders', 'sales_order') if name in existing)
        # An older app may recreate an empty legacy table. Only remove recognized
        # empty leftovers, under a lock, with backup; never merge populated tables.
        for name in existing:
            if name != source:
                _backup(connection, name, 'empty_cleanup', backup_dir)
                connection.execute(text(f'DROP TABLE "{name}"'))
        if source != TABLE:
            _backup(connection, source, 'rename', backup_dir)
            connection.execute(text(f'ALTER TABLE "{source}" RENAME TO "{TABLE}"'))
            return True
    return False


def ensure_approved_so_schema(backup_dir=None):
    renamed = rename_approved_so_table(backup_dir)
    with db.engine.begin() as connection:
        _lock(connection)
        columns = {c['name'] for c in inspect(connection).get_columns(TABLE)}
        missing = {key: value for key, value in COLUMNS.items() if key not in columns}
        if not missing:
            return
        if not renamed:
            _backup(connection, TABLE, 'approval', backup_dir)
        for column, definition in missing.items():
            connection.execute(text(f'ALTER TABLE "{TABLE}" ADD COLUMN "{column}" {definition}'))
        # Existing customer/PO/items, approvals, snapshots, and revisions stay untouched.
