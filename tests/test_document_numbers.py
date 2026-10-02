import unittest
from datetime import datetime
from flask import Flask
from sqlalchemy import text
from app.models import db, SalesOrder
from app.document_numbers import next_document_number
from app import _ensure_schema
from app.schema import migrate_multiple_wos


class DocumentTests(unittest.TestCase):
    def test_migration_and_monthly_numbers(self):
        app = Flask('app')
        app.config.update(TESTING=True, SECRET_KEY='test', SQLALCHEMY_DATABASE_URI='sqlite:///:memory:')
        db.init_app(app)
        with app.app_context():
            with db.engine.begin() as conn:
                conn.execute(text('''CREATE TABLE "WO Details" (sales_order VARCHAR(80) PRIMARY KEY,
                    customer VARCHAR(200), customer_po VARCHAR(120), items JSON, pushed_at TIMESTAMP)'''))
                conn.execute(text('''INSERT INTO "WO Details" (sales_order, items, pushed_at)
                    VALUES ('SO-20261207', :items, '2026-09-29 10:00:00')'''),
                    {'items': '[{"id":1,"item":"Part","quantity":5,"serials":["ABC"],"notes":"Keep"}]'})
            _ensure_schema()
            migrate_multiple_wos()
            _ensure_schema()
            migrate_multiple_wos()
            seed = SalesOrder.query.filter_by(sales_order='SO-20261207').one()
            self.assertTrue(seed.id)
            self.assertEqual(seed.release_number, 1)
            self.assertEqual(seed.items[0]['notes'], 'Keep')
            self.assertEqual(seed.document_number, 'WO-2609-0127')
            self.assertEqual(seed.generated_date, datetime(2026, 9, 29, 10))
            self.assertEqual(next_document_number(datetime(2026, 9, 30)), 'WO-2609-0128')
            self.assertEqual(next_document_number(datetime(2026, 10, 1)), 'WO-2610-0001')
            db.session.add(SalesOrder(sales_order=seed.sales_order, release_number=2, items=[],
                                     document_number='WO-2610-0001'))
            db.session.commit()
            self.assertEqual(SalesOrder.query.count(), 2)
            self.assertEqual(next_document_number(datetime(2026, 10, 1)), 'WO-2610-0002')
            db.session.remove()
            db.drop_all()
