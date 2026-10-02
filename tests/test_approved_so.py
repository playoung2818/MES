import json
import tempfile
import unittest
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from flask import Flask
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError
from app.models import db, ApprovedSO, SalesOrder, ProductionOverride
from app.routes import bp
from app.approved_so import differences, review_items, sync_snapshots, approve, approved_orders
from app.production_planning import load_orders, labor_map
from test_serial_table import token


class ApprovedSOTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask('app')
        self.app.config.update(TESTING=True, SECRET_KEY='test', SQLALCHEMY_DATABASE_URI='sqlite:///:memory:')
        db.init_app(self.app)
        self.app.register_blueprint(bp)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.client = self.app.test_client()
        self.source = {'sales_order': 'SO1', 'customer': 'Inficon', 'customer_po': 'PO1',
                       'ship_date': date.today().isoformat(), 'terms': 'Net30', 'inventory_site': 'WH01S-NTA',
                       'items': [{'item': 'Cable', 'quantity': 10, 'configuration': 'Cable config'},
                                 {'item': 'Nuvo-9000', 'quantity': 5}]}

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def result(self, source=None):
        return {'orders': {'SO1': deepcopy(source or self.source)}, 'skipped': 0, 'messages': []}

    def csrf(self):
        self.client.get('/production_planning/orders')
        with self.client.session_transaction() as session:
            return session['planning_csrf']

    def sync(self, source=None):
        sync_snapshots(self.result(source))
        return db.session.get(ApprovedSO, 'SO1')

    def publish(self, row=None, reverse=False):
        row = row or db.session.get(ApprovedSO, 'SO1')
        ids = [item['id'] for item in row.latest_snapshot['items']]
        approve(row, ids[::-1] if reverse else ids, row.source_revision, row.revision)
        return row

    def test_sync_stages_only_and_generation_never_reads_google(self):
        row = self.sync()
        self.assertIsNone(row.approved_at)
        self.assertEqual(approved_orders(), {})
        self.assertEqual(SalesOrder.query.count(), 0)
        with patch('app.routes.read_google_sheet', side_effect=AssertionError('GET must not read Google')):
            self.assertEqual(self.client.get('/').status_code, 200)
            self.assertEqual(self.client.get('/so/SO1').status_code, 503)
            self.assertEqual(self.client.get('/production_planning').status_code, 200)
            self.publish(row, reverse=True)
            response = self.client.get('/so/SO1')
            self.assertEqual(response.status_code, 200)
        self.assertEqual([item['item'] for item in approved_orders()['SO1']['items']], ['Nuvo-9000', 'Cable'])
        self.assertEqual(list(load_orders()['Item']), ['Nuvo-9000', 'Cable'])
        self.assertEqual(labor_map(load_orders())['SO1']['qty'], 5)
        self.assertEqual(SalesOrder.query.count(), 0)

    def test_each_sync_diff_and_unapproved_changes_keep_final_order(self):
        row = self.publish(self.sync(), reverse=True)
        ids = [item['id'] for item in row.items]
        source = deepcopy(self.source)
        source['items'][1]['quantity'] = 6
        source['customer_po'] = 'PO2'
        self.sync(source)
        self.assertEqual(row.customer_po, 'PO1')
        self.assertEqual(row.items[0]['quantity'], 5)
        self.assertTrue(any('quantity' in change for change in differences(row.previous_snapshot, row.latest_snapshot)))
        self.assertEqual([item['id'] for item in review_items(row)], ids)
        self.sync(source)
        self.assertEqual(differences(row.previous_snapshot, row.latest_snapshot), [])
        self.assertTrue(differences(row.approved_snapshot, row.latest_snapshot))
        approve(row, ids, row.source_revision, row.revision)
        self.assertEqual(row.customer_po, 'PO2')
        self.assertEqual([item['id'] for item in row.items], ids)
        self.assertEqual(row.items[0]['quantity'], 6)

    def test_configuration_change_keeps_unambiguous_identity_and_order(self):
        row = self.publish(self.sync(), reverse=True)
        ids = [item['id'] for item in row.items]
        changed = deepcopy(self.source)
        changed['items'][1]['configuration'] = 'Updated system config'
        self.sync(changed)
        self.assertEqual([item['id'] for item in review_items(row)], ids)
        self.assertTrue(any('configuration' in change for change in differences(row.previous_snapshot, row.latest_snapshot)))
        approve(row, ids, row.source_revision, row.revision)
        response = self.client.get('/so/SO1')
        self.assertIn('Updated system config', response.text)

    def test_order_only_diff_does_not_overwrite_manual_order(self):
        row = self.publish(self.sync(), reverse=True)
        source = deepcopy(self.source)
        source['items'].reverse()
        self.sync(source)
        changes = differences(row.previous_snapshot, row.latest_snapshot)
        self.assertEqual(changes, ['Google item order changed (separate from item content)'])
        self.assertEqual([item['item'] for item in row.items], ['Nuvo-9000', 'Cable'])

    def test_added_item_is_appended_in_review_and_removed_item_disappears(self):
        row = self.publish(self.sync(), reverse=True)
        source = deepcopy(self.source)
        source['items'].insert(0, {'item': 'New item', 'quantity': 1})
        self.sync(source)
        self.assertEqual([item['item'] for item in review_items(row)], ['Nuvo-9000', 'Cable', 'New item'])
        source['items'] = [item for item in source['items'] if item['item'] != 'Cable']
        self.sync(source)
        self.assertEqual([item['item'] for item in review_items(row)], ['Nuvo-9000', 'New item'])
        self.assertTrue(any('Removed item' in change for change in differences(row.approved_snapshot, row.latest_snapshot)))

    def test_empty_or_malformed_sync_preserves_everything(self):
        row = self.publish(self.sync())
        before = deepcopy(row.latest_snapshot), deepcopy(row.items), row.source_revision
        for result in ({'orders': {}}, dict(self.result(), skipped=1), dict(self.result(), messages=['missing columns'])):
            with self.assertRaises(ValueError):
                sync_snapshots(result)
            db.session.rollback()
        self.assertEqual((row.latest_snapshot, row.items, row.source_revision), before)
        self.assertTrue(row.is_active)

    def test_removal_automatically_excludes_and_reappearance_requires_approval(self):
        row = self.publish(self.sync(), reverse=True)
        ids = [item['id'] for item in row.items]
        approved_before = deepcopy(row.approved_snapshot), deepcopy(row.items), row.approved_at
        revision = row.revision
        form = dict(action='preview', item_order='1,2', quantity_1='5', quantity_2='10',
                    serials_1='NA', serials_2='NA', notes_1='', notes_2='Cable config')
        preview = self.client.post('/so/SO1', data=form)
        form.update(action='push', preview_token=token(preview))
        saved = SalesOrder(sales_order='SO1', customer='Inficon', customer_po='PO1',
                           items=[{'id': 1, 'item': 'Nuvo-9000', 'quantity': 2, 'serials': ['NA'], 'notes': ''}])
        db.session.add(saved)
        db.session.commit()
        saved_before = deepcopy(saved.items), saved.pushed_at, saved.document_number
        other = deepcopy(self.source)
        other['sales_order'] = 'SO2'
        sync_snapshots({'orders': {'SO2': other}, 'skipped': 0, 'messages': []})
        self.assertNotIn('SO1', approved_orders())
        self.assertNotIn('SO1', list(load_orders()['QB Num']))
        self.assertFalse(row.google_present)
        self.assertFalse(row.is_active)
        self.assertEqual(row.revision, revision + 1)
        self.assertEqual((row.approved_snapshot, row.items, row.approved_at), approved_before)
        self.assertEqual(self.client.get('/so/SO1').status_code, 503)
        self.assertEqual(self.client.post('/so/SO1', data=form).status_code, 503)
        self.assertEqual(SalesOrder.query.count(), 1)
        self.assertEqual((saved.items, saved.pushed_at, saved.document_number), saved_before)
        self.assertEqual(self.client.get('/wo/' + saved.id + '/edit').status_code, 200)
        self.assertNotIn('<td class="fw-semibold">SO1</td>', self.client.get('/production_planning/orders').text)
        self.assertIn('Assign Production Date (1)', self.client.get('/production_planning').text)
        self.assertIn('No approval required — excluded', self.client.get('/production_planning/orders?status=all').text)
        detail = self.client.get('/production_planning/orders/SO1').text
        self.assertIn('No review or retirement approval is required', detail)
        self.assertNotIn('value="retire"', detail)
        self.assertIsNotNone(db.session.get(ApprovedSO, 'SO1'))
        self.sync()
        self.assertEqual([item['id'] for item in review_items(row)], ids)
        self.assertFalse(row.is_active)
        self.assertNotIn('SO1', approved_orders())
        self.assertIn('<td class="fw-semibold">SO1</td>', self.client.get('/production_planning/orders?status=unapproved').text)
        self.publish(row, reverse=True)
        self.assertIn('SO1', approved_orders())

    def test_already_removed_rows_are_excluded_without_another_sync(self):
        row = self.publish(self.sync())
        row.google_present = False  # Simulate a removal under the previous policy.
        row.latest_snapshot = None
        db.session.commit()
        self.assertTrue(row.is_active)
        self.assertNotIn('SO1', approved_orders())
        self.assertEqual(self.client.get('/so/SO1').status_code, 503)
        self.sync()  # Reappears: old active flag must not cause automatic activation.
        self.assertFalse(row.is_active)
        self.assertNotIn('SO1', approved_orders())

    def test_conflicting_approval_and_allocation_reduction_are_blocked(self):
        row = self.publish(self.sync())
        old_source, old_revision = row.source_revision, row.revision
        changed = deepcopy(self.source)
        changed['items'][1]['quantity'] = 6
        self.sync(changed)
        with self.assertRaisesRegex(ValueError, 'changed in another session'):
            approve(row, [item['id'] for item in row.latest_snapshot['items']], old_source, old_revision)
        db.session.rollback()
        db.session.add(SalesOrder(sales_order='SO1', items=[{'item': 'nuvo-9000', 'quantity': 7}]))
        db.session.commit()
        with self.assertRaisesRegex(ValueError, 'saved WOs allocate'):
            self.publish(row)
        db.session.rollback()
        self.assertEqual(row.items[1]['quantity'], 5)

    def test_preview_stale_after_so_approval_and_final_push(self):
        row = self.publish(self.sync(), reverse=True)
        form = dict(action='preview', item_order='1,2', quantity_1='5', quantity_2='10',
                    serials_1='NA', serials_2='NA', notes_1='', notes_2='Cable config')
        response = self.client.post('/so/SO1', data=form)
        self.assertEqual(response.status_code, 200)
        form.update(action='push', preview_token=token(response))
        changed = deepcopy(self.source)
        changed['customer_po'] = 'PO2'
        self.sync(changed)
        approve(row, [item['id'] for item in review_items(row)], row.source_revision, row.revision)
        response = self.client.post('/so/SO1', data=form)
        self.assertEqual(response.status_code, 400)
        self.assertIn('Approved SO changed after preview', response.text)
        self.assertEqual(SalesOrder.query.count(), 0)
        form['action'] = 'preview'
        response = self.client.post('/so/SO1', data=form)
        form.update(action='push', preview_token=token(response))
        self.assertEqual(self.client.post('/so/SO1', data=form).status_code, 302)
        self.assertEqual([item['item'] for item in SalesOrder.query.one().items], ['Nuvo-9000', 'Cable'])

    def test_review_forms_csrf_and_sync_endpoint(self):
        self.assertEqual(self.client.post('/sync-google').status_code, 403)
        csrf = self.csrf()
        with patch('app.routes.read_google_sheet', return_value=self.result()):
            response = self.client.post('/sync-google', data={'csrf_token': csrf})
        self.assertEqual(response.status_code, 302)
        row = db.session.get(ApprovedSO, 'SO1')
        response = self.client.get('/production_planning/orders/SO1')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Push Approved SO', response.text)
        self.assertEqual(self.client.post('/production_planning/orders/SO1').status_code, 403)
        response = self.client.post('/production_planning/orders/SO1', data={
            'csrf_token': csrf, 'source_revision': row.source_revision, 'revision': row.revision,
            'item_order': ','.join(item['id'] for item in row.latest_snapshot['items']), 'action': 'approve'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('SO1', approved_orders())

    def test_case_only_product_change_keeps_partial_allocation(self):
        row = self.publish(self.sync(), reverse=True)
        db.session.add(SalesOrder(sales_order='SO1', items=[{'id': 1, 'item': 'Nuvo-9000', 'quantity': 2, 'serials': []}]))
        db.session.commit()
        changed = deepcopy(self.source)
        changed['items'][1]['item'] = 'NUVO-9000'
        self.sync(changed)
        approve(row, [item['id'] for item in review_items(row)], row.source_revision, row.revision)
        response = self.client.get('/so/SO1')
        self.assertEqual(response.status_code, 200)
        self.assertIn('name="quantity_1" value="3"', response.text)
        response = self.client.post('/so/SO1', data=dict(action='preview', item_order='1,2',
                                   quantity_1='4', quantity_2='10', serials_1='NA', serials_2='NA'))
        self.assertEqual(response.status_code, 400)
        self.assertIn('exceeds remaining quantity 3', response.text)

    def test_duplicate_lines_flagged_and_stale_review_form_rejected(self):
        source = deepcopy(self.source)
        source['items'].append(deepcopy(source['items'][0]))
        row = self.sync(source)
        csrf = self.csrf()
        response = self.client.get('/production_planning/orders/SO1')
        self.assertIn('Repeated product/configuration/site lines', response.text)
        old_source_revision = row.source_revision
        ids = ','.join(item['id'] for item in row.latest_snapshot['items'])
        source['customer_po'] = 'Changed'
        self.sync(source)
        response = self.client.post('/production_planning/orders/SO1', data={
            'csrf_token': csrf, 'source_revision': old_source_revision, 'revision': row.revision,
            'item_order': ids, 'action': 'approve'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('changed in another session', response.text)
        self.assertIsNone(row.approved_at)

    def review_form(self, row, production_date=''):
        return {'csrf_token': self.csrf(), 'source_revision': row.source_revision,
                'revision': row.revision, 'action': 'approve', 'production_date': production_date,
                'item_order': ','.join(item['id'] for item in review_items(row))}

    def next_weekday(self):
        from datetime import timedelta
        target = date.today()
        while target.weekday() >= 5:
            target += timedelta(days=1)
        return target

    def test_review_shows_lt_and_saves_production_date_with_approval(self):
        row = self.sync()
        response = self.client.get('/production_planning/orders/SO1')
        self.assertIn('L/T (Google Sheet)', response.text)
        self.assertIn(self.source['ship_date'], response.text)
        self.assertIn('name="production_date"', response.text)
        self.assertIn('<th>L/T ↑</th>', self.client.get('/production_planning/orders').text)
        target = self.next_weekday()
        response = self.client.post('/production_planning/orders/SO1', data=self.review_form(row, target.isoformat()))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(row.is_active)
        schedule = db.session.get(ProductionOverride, 'SO1')
        self.assertEqual(schedule.production_date, target)
        self.assertFalse(schedule.is_finished_goods)
        self.assertEqual(SalesOrder.query.count(), 0)
        from app.production_planning import build_plan
        plan = build_plan(load_orders())
        planned = next(order for group in plan['date_groups'] for order in group['orders'] if order['qb_num'] == 'SO1')
        self.assertEqual(planned['production_date'], target.isoformat())

    def test_review_invalid_production_date_does_not_publish_so(self):
        from datetime import timedelta
        row = self.sync()
        weekend = self.next_weekday()
        while weekend.weekday() != 5:
            weekend += timedelta(days=1)
        for raw in ('invalid', weekend.isoformat(), (date.today()-timedelta(days=1)).isoformat()):
            response = self.client.post('/production_planning/orders/SO1', data=self.review_form(row, raw))
            self.assertEqual(response.status_code, 400)
            self.assertIsNone(row.approved_at)
            self.assertEqual(ProductionOverride.query.count(), 0)

    def test_blank_review_date_preserves_finished_goods_schedule(self):
        row = self.sync()
        target = self.next_weekday()
        db.session.add(ProductionOverride(wo_number='SO1', production_date=target, is_finished_goods=True))
        db.session.commit()
        response = self.client.get('/production_planning/orders/SO1')
        self.assertIn('Finished goods', response.text)
        response = self.client.post('/production_planning/orders/SO1', data=self.review_form(row))
        self.assertEqual(response.status_code, 302)
        schedule = db.session.get(ProductionOverride, 'SO1')
        self.assertEqual(schedule.production_date, target)
        self.assertTrue(schedule.is_finished_goods)

    def test_review_assignment_returns_so_from_finished_goods(self):
        row = self.sync()
        db.session.add(ProductionOverride(wo_number='SO1', is_finished_goods=True,
                       finished_goods_updated_at=datetime.now(), finished_goods_updated_by='previous'))
        db.session.commit()
        response = self.client.post('/production_planning/orders/SO1',
                                    data=self.review_form(row, self.next_weekday().isoformat()))
        self.assertEqual(response.status_code, 302)
        schedule = db.session.get(ProductionOverride, 'SO1')
        self.assertFalse(schedule.is_finished_goods)
        self.assertIsNone(schedule.finished_goods_updated_at)
        self.assertIsNone(schedule.finished_goods_updated_by)

    def test_review_date_and_so_publication_roll_back_together(self):
        from sqlalchemy.exc import SQLAlchemyError
        from app.approved_so import save_production_date
        row = self.sync()
        form = self.review_form(row, self.next_weekday().isoformat())
        def save_then_fail(*args, **kwargs):
            save_production_date(*args, **kwargs)
            raise SQLAlchemyError('simulated schedule failure')
        with patch('app.approved_so.save_production_date', side_effect=save_then_fail), patch.object(self.app.logger, 'exception'):
            response = self.client.post('/production_planning/orders/SO1', data=form)
        self.assertEqual(response.status_code, 503)
        self.assertIsNone(row.approved_at)
        self.assertFalse(row.is_active)
        self.assertEqual(row.revision, 0)
        self.assertEqual(ProductionOverride.query.count(), 0)

    def test_review_reads_material_ready_date_without_modifying_source_table(self):
        from app.material_readiness import load_material_readiness
        row = self.sync()
        response = self.client.get('/production_planning/orders/SO1')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Readiness data unavailable', response.text)
        self.assertFalse(inspect(db.engine).has_table('so_material_readiness'))
        with db.engine.begin() as connection:
            connection.execute(text('CREATE TABLE so_material_readiness ("QB Num" TEXT PRIMARY KEY, earliest_material_ready_date DATE)'))
            connection.execute(text('INSERT INTO so_material_readiness VALUES (:so, :ready)'),
                               [{'so': 'SO1', 'ready': '2030-03-18'}, {'so': 'SO2', 'ready': None}])
        before = deepcopy(row.latest_snapshot), row.source_revision, row.revision
        response = self.client.get('/production_planning/orders/SO1')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Earliest Material Ready Date', response.text)
        self.assertIn('2030-03-18', response.text)
        self.assertIn('Read-only · Source: so_material_readiness', response.text)
        self.assertEqual((row.latest_snapshot, row.source_revision, row.revision), before)
        self.assertEqual(load_material_readiness('SO2')['message'], 'Not determined')
        self.assertEqual(load_material_readiness('MISSING')['message'], 'No readiness record')
        self.assertEqual(load_material_readiness("SO1' OR 1=1 --")['message'], 'No readiness record')
        with db.engine.connect() as connection:
            self.assertEqual(connection.execute(text('SELECT count(*) FROM so_material_readiness')).scalar(), 2)
            self.assertEqual(connection.execute(text('SELECT earliest_material_ready_date FROM so_material_readiness WHERE "QB Num" = :so'),
                                                {'so': 'SO1'}).scalar(), '2030-03-18')
        with patch('app.material_readiness.inspect', side_effect=SQLAlchemyError('read failure')):
            self.assertEqual(self.client.get('/production_planning/orders/SO1').status_code, 200)
        with db.engine.begin() as connection:
            connection.execute(text('DROP TABLE so_material_readiness'))

    def test_review_summary_lt_sort_and_status_filters(self):
        now = datetime.now()
        def source(number, lt):
            return dict(sales_order=number, customer='Customer', customer_po='', ship_date=lt, items=[])
        approved = source('APP', '10/02/2030')
        changed = source('CHANGE', '9/30/2030')
        db.session.add_all([
            ApprovedSO(sales_order='APP', items=[], latest_snapshot=approved, approved_snapshot=deepcopy(approved),
                       approved_at=now, synced_at=now, google_present=True, is_active=True),
            ApprovedSO(sales_order='NEW', items=[], latest_snapshot=source('NEW', '10/01/2030'),
                       synced_at=now, google_present=True),
            ApprovedSO(sales_order='CHANGE', items=[], latest_snapshot=changed,
                       approved_snapshot=source('CHANGE', '9/29/2030'), approved_at=now,
                       synced_at=now, google_present=True, is_active=True),
            ApprovedSO(sales_order='LEGACY', items=[], metadata_fields={'ship_date': 'invalid'}),
            ApprovedSO(sales_order='REMOVE', items=[], metadata_fields={'ship_date': '9/28/2030'},
                       approved_snapshot=source('REMOVE', '9/28/2030'), approved_at=now,
                       synced_at=now, google_present=False, is_active=True),
            ApprovedSO(sales_order='RETIRE', items=[], approved_at=now, synced_at=now,
                       google_present=False, is_active=False),
        ])
        db.session.commit()
        import re
        def so_ids(response):
            return re.findall(r'<td class="fw-semibold">([^<]+)</td>', response.text)
        response = self.client.get('/production_planning/orders?status=all')
        self.assertEqual(so_ids(response), ['REMOVE', 'CHANGE', 'NEW', 'APP', 'LEGACY', 'RETIRE'])
        self.assertEqual(so_ids(self.client.get('/production_planning/orders')), ['CHANGE', 'NEW', 'LEGACY'])
        for status, number in [('approved', 'APP'), ('changed', 'CHANGE'), ('unapproved', 'NEW'),
                               ('legacy', 'LEGACY'), ('removed', 'REMOVE'), ('retired', 'RETIRE')]:
            response = self.client.get('/production_planning/orders?status=' + status)
            self.assertEqual(so_ids(response), [number])
            self.assertIn('name="status"', response.text)
        self.assertEqual(so_ids(self.client.get('/production_planning/orders?all=1')),
                         ['REMOVE', 'CHANGE', 'NEW', 'APP', 'LEGACY', 'RETIRE'])
        self.assertEqual(so_ids(self.client.get('/production_planning/orders?status=approved&q=APP')), ['APP'])
        self.assertEqual(so_ids(self.client.get('/production_planning/orders?status=changed&q=APP')), [])
        self.assertEqual(self.client.get('/production_planning/orders?status=invalid').status_code, 400)

    def test_table_rename_preserves_approved_records_and_is_idempotent(self):
        from app.approved_so_schema import rename_approved_so_table
        self.publish(self.sync(), reverse=True)
        with db.engine.begin() as connection:
            before = [dict(row) for row in connection.execute(text('SELECT * FROM approved_work_orders')).mappings()]
            connection.execute(text('ALTER TABLE approved_work_orders RENAME TO sales_order'))
        db.session.expire_all()
        with tempfile.TemporaryDirectory() as folder:
            self.assertTrue(rename_approved_so_table(folder))
            self.assertFalse(rename_approved_so_table(folder))
            backups = list(Path(folder).glob('sales_order_before_rename_*.json'))
            self.assertEqual(len(backups), 1)
        with db.engine.connect() as connection:
            after = [dict(row) for row in connection.execute(text('SELECT * FROM approved_work_orders')).mappings()]
        self.assertEqual(before, after)
        self.assertFalse(inspect(db.engine).has_table('sales_order'))
        self.assertIn('SO1', approved_orders())

    def test_intermediate_approved_sales_orders_name_migrates(self):
        from app.approved_so_schema import rename_approved_so_table
        self.publish(self.sync(), reverse=True)
        with db.engine.begin() as connection:
            before = [dict(row) for row in connection.execute(text('SELECT * FROM approved_work_orders')).mappings()]
            connection.execute(text('ALTER TABLE approved_work_orders RENAME TO approved_sales_orders'))
        db.session.expire_all()
        with tempfile.TemporaryDirectory() as folder:
            self.assertTrue(rename_approved_so_table(folder))
            self.assertEqual(len(list(Path(folder).glob('approved_sales_orders_before_rename_*.json'))), 1)
        self.assertFalse(inspect(db.engine).has_table('approved_sales_orders'))
        with db.engine.connect() as connection:
            after = [dict(row) for row in connection.execute(text('SELECT * FROM approved_work_orders')).mappings()]
        self.assertEqual(before, after)
        self.assertIn('SO1', approved_orders())

    def test_table_rename_refuses_two_existing_names(self):
        from app.approved_so_schema import rename_approved_so_table
        with db.engine.begin() as connection:
            connection.execute(text('CREATE TABLE sales_order (sales_order TEXT PRIMARY KEY)'))
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(RuntimeError, 'Unexpected approved-order table schema'):
                rename_approved_so_table(folder)
            self.assertEqual(list(Path(folder).iterdir()), [])
        self.assertTrue(inspect(db.engine).has_table('sales_order'))
        self.assertTrue(inspect(db.engine).has_table('approved_work_orders'))
        with db.engine.begin() as connection:
            connection.execute(text('DROP TABLE sales_order'))

    def test_rename_cleans_only_recognized_empty_legacy_leftover(self):
        from sqlalchemy import MetaData
        from app.approved_so_schema import rename_approved_so_table
        self.publish(self.sync())
        ApprovedSO.__table__.to_metadata(MetaData(), name='sales_order').create(db.engine)
        with tempfile.TemporaryDirectory() as folder:
            self.assertFalse(rename_approved_so_table(folder))
            self.assertEqual(len(list(Path(folder).glob('sales_order_before_empty_cleanup_*.json'))), 1)
        self.assertFalse(inspect(db.engine).has_table('sales_order'))
        self.assertIn('SO1', approved_orders())

    def test_rename_never_merges_two_populated_tables(self):
        from sqlalchemy import MetaData
        from app.approved_so_schema import rename_approved_so_table
        self.publish(self.sync())
        ApprovedSO.__table__.to_metadata(MetaData(), name='sales_order').create(db.engine)
        with db.engine.begin() as connection:
            connection.execute(text("INSERT INTO sales_order (sales_order,items,metadata_fields) VALUES ('OTHER','[]','{}')"))
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(RuntimeError, 'Multiple approved-order tables contain data'):
                rename_approved_so_table(folder)
            self.assertEqual(list(Path(folder).iterdir()), [])
        with db.engine.begin() as connection:
            self.assertEqual(connection.execute(text('SELECT count(*) FROM sales_order')).scalar(), 1)
            self.assertEqual(connection.execute(text('SELECT count(*) FROM approved_work_orders')).scalar(), 1)
            connection.execute(text('DROP TABLE sales_order'))

    def test_legacy_schema_backup_and_no_implicit_approval(self):
        from app.approved_so_schema import ensure_approved_so_schema
        db.session.remove()
        db.drop_all()
        with db.engine.begin() as connection:
            connection.execute(text('CREATE TABLE sales_order (sales_order TEXT PRIMARY KEY, customer TEXT, customer_po TEXT, items JSON)'))
            connection.execute(text('INSERT INTO sales_order VALUES (:so,:customer,:po,:items)'),
                               {'so': 'LEGACY', 'customer': 'Customer', 'po': 'PO', 'items': json.dumps([{'item': 'Part', 'quantity': 2}])})
        with tempfile.TemporaryDirectory() as folder:
            ensure_approved_so_schema(folder)
            backups = list(Path(folder).glob('*.json'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(json.loads(backups[0].read_text(encoding='utf-8'))[0]['sales_order'], 'LEGACY')
            ensure_approved_so_schema(folder)
            self.assertEqual(len(list(Path(folder).glob('*.json'))), 1)
        row = db.session.get(ApprovedSO, 'LEGACY')
        self.assertEqual(row.items, [{'item': 'Part', 'quantity': 2}])
        self.assertIsNone(row.approved_at)
        self.assertFalse(row.is_active)
        self.assertIn('latest_snapshot', {column['name'] for column in inspect(db.engine).get_columns('approved_work_orders')})
