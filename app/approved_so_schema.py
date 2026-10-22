"""Read-only validation of the current shared SO schema.

No approval backfill, legacy table migration, or automatic column additions.
"""
from sqlalchemy import inspect
from .models import db, ApprovedSO

TABLE = 'sales_order_workflow'


def require_current_table_name():
    tables = set(inspect(db.engine).get_table_names())
    if 'approved_work_orders' in tables:
        raise RuntimeError('Run MRP: python -m mrp_system.cli.rename_sales_order_workflow --apply before starting MES. '
                           'The legacy table is never automatically renamed, copied or replaced.')
    legacy = {'sales_order', 'approved_sales_orders'} & tables
    if legacy:
        raise RuntimeError('Legacy SO table names detected: ' + ', '.join(sorted(legacy))
                           + '. Resolve the schema explicitly before startup; no tables are renamed or merged.')


def require_so_workflow_schema():
    require_current_table_name()
    columns = {column['name'] for column in inspect(db.engine).get_columns(TABLE)}
    if 'source_present' not in columns and 'google_present' in columns:
        raise RuntimeError('Run MRP: python -m mrp_system.cli.rename_source_present --apply before starting MES.')
    missing = {column.name for column in ApprovedSO.__table__.columns} - columns
    if missing:
        raise RuntimeError('Incomplete shared SO workflow schema; missing columns: '
                           + ', '.join(sorted(missing))
                           + '. Provision/restore the current schema before starting MES. '
                             'No automatic migration or ownership backfill is performed.')
