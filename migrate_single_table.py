"""Consolidate this app's legacy tables; leave unrelated database tables alone."""
import json
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import MetaData, Table, create_engine, inspect, select
import os

from app.models import SalesOrder

LEGACY = ("unit_serial", "work_order_note", "work_order_position", "work_order")


def snapshot(connection):
    names = set(inspect(connection).get_table_names())
    metadata = MetaData()
    return {name: [dict(row) for row in connection.execute(
        select(Table(name, metadata, autoload_with=connection))).mappings()]
        for name in LEGACY if name in names}


def consolidate(data):
    positions = {row["work_order_id"]: row["position"] for row in data.get("work_order_position", [])}
    notes = {row["work_order_id"]: row["notes"] for row in data.get("work_order_note", [])}
    serials = {}
    for row in sorted(data.get("unit_serial", []), key=lambda row: row["unit_no"]):
        serials.setdefault(row["work_order_id"], []).append(row["system_sn"])
    orders = {}
    lines = sorted(data.get("work_order", []),
                   key=lambda row: (row["id"] not in positions, positions.get(row["id"], 0), row["id"]))
    for row in lines:
        order = orders.setdefault(row["sales_order"], {
            "sales_order": row["sales_order"], "customer": row["customer"],
            "customer_po": row["customer_po"], "items": [],
        })
        order["items"].append({"id": row["id"], "item": row["item"] or "",
                               "quantity": row["quantity"], "serials": serials.get(row["id"], []),
                               "notes": notes.get(row["id"], "")})
    return orders


def main():
    load_dotenv()
    engine = create_engine(os.environ["DATABASE_DSN"])
    with engine.begin() as connection:
        names = set(inspect(connection).get_table_names())
        # Prevent concurrent writes while reading, checking, and removing the old tables.
        if connection.dialect.name == "postgresql":
            for name in LEGACY:
                if name in names:
                    connection.exec_driver_sql(f'LOCK TABLE "{name}" IN ACCESS EXCLUSIVE MODE')
        data = snapshot(connection)
        source = data
        local_path = Path("instance/wo_generator.db").resolve()
        if not data.get("work_order") and local_path.exists():
            local = create_engine("sqlite:///" + local_path.as_posix())
            with local.connect() as local_connection:
                source = snapshot(local_connection)
            local.dispose()
        backup = Path("data/backups")
        backup.mkdir(parents=True, exist_ok=True)
        backup_path = backup / ("single_table_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".json")
        backup_path.write_text(json.dumps({"postgres_legacy": data, "migration_source": source},
                                         default=str, indent=2), encoding="utf-8")
        orders = consolidate(source)
        SalesOrder.__table__.create(connection, checkfirst=True)
        existing = {row["sales_order"]: dict(row) for row in connection.execute(select(SalesOrder.__table__)).mappings()}
        for number, order in orders.items():
            if number in existing:
                if existing[number] != order:
                    raise RuntimeError(f"Existing SO conflicts with migration: {number}; no tables removed.")
            else:
                connection.execute(SalesOrder.__table__.insert().values(**order))
        saved = {row["sales_order"]: dict(row) for row in connection.execute(select(SalesOrder.__table__)).mappings()}
        assert all(saved[number] == order for number, order in orders.items()), "Migration verification failed"
        metadata = MetaData()
        for name in LEGACY:
            if name in names:
                Table(name, metadata, autoload_with=connection).drop(connection)
        print(f"Preserved {len(orders)} sales orders / {sum(len(o['items']) for o in orders.values())} items.")
        print(f"Backup: {backup_path}")
        print("App table: sales_order. Removed legacy app tables only.")
    engine.dispose()


if __name__ == "__main__":
    main()
