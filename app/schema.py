"""Additive upgrades and backed-up migration from one SO row to multiple WOs."""
import json
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import inspect, text, MetaData
from .models import db, SalesOrder


def migrate_multiple_wos():
    with db.engine.begin() as conn:
        inspector = inspect(conn)
        if 'id' in {c['name'] for c in inspector.get_columns('WO Details')}:
            return
        if conn.dialect.name == 'postgresql':
            conn.execute(text('LOCK TABLE "WO Details" IN ACCESS EXCLUSIVE MODE'))
        rows = [dict(r) for r in conn.execute(text('SELECT * FROM "WO Details"')).mappings()]
        backup = Path('data/backups')
        backup.mkdir(parents=True, exist_ok=True)
        path = backup / ('multiple_wos_' + datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f') + '.json')
        path.write_text(json.dumps(rows, default=str, indent=2), encoding='utf-8')
        if conn.dialect.name == 'postgresql':
            conn.execute(text('ALTER TABLE "WO Details" ADD COLUMN id VARCHAR(36)'))
            conn.execute(text('ALTER TABLE "WO Details" ADD COLUMN release_number INTEGER NOT NULL DEFAULT 1'))
            for row in rows:
                conn.execute(text('UPDATE "WO Details" SET id=:id WHERE sales_order=:so'),
                             {'id': str(uuid4()), 'so': row['sales_order']})
            pk = inspector.get_pk_constraint('WO Details')['name']
            quoted = conn.dialect.identifier_preparer.quote(pk)
            conn.execute(text(f'ALTER TABLE "WO Details" DROP CONSTRAINT {quoted}'))
            conn.execute(text('ALTER TABLE "WO Details" ADD PRIMARY KEY (id)'))
            conn.execute(text('ALTER TABLE "WO Details" ADD CONSTRAINT uq_wo_so_release UNIQUE (sales_order, release_number)'))
            conn.execute(text('CREATE INDEX IF NOT EXISTS ix_wo_sales_order ON "WO Details" (sales_order)'))
        elif conn.dialect.name == 'sqlite':
            # SQLite cannot replace a primary key with ALTER TABLE.
            new = SalesOrder.__table__.to_metadata(MetaData(), name='wo_details_upgrade')
            for index in list(new.indexes):
                new.indexes.remove(index)
            new.create(conn)
            for row in rows:
                conn.execute(text('''INSERT INTO wo_details_upgrade
                    (id, sales_order, release_number, customer, customer_po, items, pushed_at, "Generated Date", "Document #")
                    VALUES (:id, :so, 1, :customer, :po, :items, :pushed, :generated, :document)'''),
                    dict(id=str(uuid4()), so=row['sales_order'], customer=row['customer'], po=row['customer_po'],
                         items=row['items'], pushed=row['pushed_at'], generated=row['Generated Date'], document=row['Document #']))
            conn.execute(text('DROP TABLE "WO Details"'))
            conn.execute(text('ALTER TABLE wo_details_upgrade RENAME TO "WO Details"'))
            conn.execute(text('CREATE INDEX ix_wo_sales_order ON "WO Details" (sales_order)'))
        else:
            raise RuntimeError('Multiple-WO migration supports PostgreSQL and SQLite only.')
        count = conn.execute(text('SELECT COUNT(*) FROM "WO Details"')).scalar_one()
        if count != len(rows):
            raise RuntimeError('WO migration row count verification failed')
