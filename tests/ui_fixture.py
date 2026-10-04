"""Isolated UI fixture: in-memory SQLite, no production config or external sync.
Run from the repo root: PYTHONPATH=. python tests/ui_fixture.py
"""
from copy import deepcopy
from datetime import date, datetime, timedelta
import os
from flask import Flask
from app.models import db, SalesOrder, ProductionOverride
from app.routes import bp
from app.approved_so import sync_snapshots, approve


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
        sync_snapshots(dict(orders=sources, skipped=0, messages=[]))
        from app.models import ApprovedSO
        for row in ApprovedSO.query.all():
            approve(row, [item['id'] for item in row.latest_snapshot['items']], row.source_revision, row.revision)
        # Include a pending review and a saved partial release in the real workflows.
        changed = deepcopy(sources)
        changed['SO-20261209']['customer_po'] = 'PO-UPDATED'
        sync_snapshots(dict(orders=changed, skipped=0, messages=[]))
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
