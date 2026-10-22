"""Isolated UI fixture: in-memory SQLite, no production config or external sync.
Run from the repo root: PYTHONPATH=. python tests/ui_fixture.py
"""
from copy import deepcopy
from datetime import date, datetime, timedelta
import os
from flask import Flask
from app.models import db, ApprovedSO, SalesOrder, ProductionOverride
from app.routes import bp
from app.wo_service import totals
from uuid import uuid4


def create_fixture():
    app = Flask('app')
    app.config.update(TESTING=True, SECRET_KEY='isolated-ui-fixture',
                      SQLALCHEMY_DATABASE_URI='sqlite:///:memory:')
    db.init_app(app)
    app.register_blueprint(bp)

    @app.after_request
    def mark_fixture(response):
        response.headers['X-MES-UI-Fixture'] = 'isolated'
        return response

    with app.app_context():
        db.create_all()
        today = date.today()
        future = today + timedelta(days=2)
        while future.weekday() >= 5:
            future += timedelta(days=1)
        sources = {}
        for index, (customer, product, qty) in enumerate([
            ('Northstar Robotics', 'Nuvo-9160GC', 2),
            ('Atlas Automation', 'NRU-220S', 8),
            ('Meridian Systems', 'POC-715', 4),
            ('Fieldline Technologies', 'SEMIL-2000GC', 25),
            ('Summit Industrial', 'F-860', 6),
        ], 1):
            number = f'SO-20261{207 + index:03}'
            sources[number] = dict(sales_order=number, customer=customer, customer_po=f'PO-4810{index}',
                                   ship_date=future.isoformat(), terms='Net30', inventory_site='WH01S-NTA',
                                   items=[dict(item=product, quantity=qty, configuration='Standard configuration'),
                                          dict(item='Cable kit', quantity=1, configuration='Include accessories')])
        # Seed current MRP handoff output directly; MES has no approval/sync helper.
        for number, source in sources.items():
            for item in source['items']:
                item['id'] = str(uuid4())
            db.session.add(ApprovedSO(sales_order=number, customer=source['customer'],
                customer_po=source['customer_po'], items=deepcopy(source['items']),
                metadata_fields={key: source[key] for key in ('ship_date', 'terms', 'inventory_site')},
                latest_snapshot=deepcopy(source), approved_snapshot=deepcopy(source),
                approved_order_signature=totals(source['items'], normalized=True),
                source_present=True, workflow_status='released_to_mes', approved_at=datetime.now(),
                synced_at=datetime.now(), source_revision=1, revision=1))
        db.session.flush()
        # A genuine ordered-quantity amendment returns ownership to MRP.
        row = db.session.get(ApprovedSO, 'SO-20261209')
        changed = deepcopy(row.latest_snapshot)
        changed['items'][0]['quantity'] += 1
        row.previous_snapshot = deepcopy(row.latest_snapshot)
        row.latest_snapshot = changed
        row.workflow_status = 'needs_material_review'
        row.returned_from_mes = True
        row.review_incident_id = uuid4()
        row.source_revision += 1
        db.session.add(SalesOrder(id='fixture-saved', sales_order='SO-20261209', release_number=1,
                                 customer='Atlas Automation', customer_po='PO-48102',
                                 document_number='WO-FIXTURE', generated_date=datetime.now(),
                                 pushed_at=datetime.now(),
                                 items=[dict(id=1, item='NRU-220S', quantity=1, serials=['FIXTURE-SN'], notes='Checked')]))
        db.session.add(ProductionOverride(wo_number='SO-20261212', is_finished_goods=True))
        db.session.commit()
    return app


if __name__ == '__main__':
    create_fixture().run(host='127.0.0.1', port=int(os.environ.get('MES_UI_TEST_PORT', 5057)), use_reloader=False)
