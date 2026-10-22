"""Current MES eligibility and handoff guards; no independent SO approval API."""
import unittest
from copy import deepcopy
from datetime import date, datetime
from unittest.mock import patch
from uuid import uuid4

from flask import Flask
from sqlalchemy import inspect

from app.models import db, ApprovedSO, SalesOrder, ProductionOverride
from app.routes import bp
from app.approved_so import approved_orders
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
                       'items': [{'id': str(uuid4()), 'item': 'Cable', 'quantity': 10, 'configuration': 'Cable config'},
                                 {'id': str(uuid4()), 'item': 'Nuvo-9000', 'quantity': 5}]}

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def pending_fixture(self):
        row = ApprovedSO(sales_order='SO1', items=[], latest_snapshot=deepcopy(self.source),
                         source_present=True, source_revision=1, revision=0,
                         workflow_status='needs_material_review', review_incident_id=uuid4(),
                         synced_at=datetime.now())
        db.session.add(row)
        db.session.commit()
        return row

    def handoff_fixture(self, row=None, reverse=False):
        """Seed the result of MRP handoff directly, not a MES approval function."""
        from app.wo_service import totals
        row = row or self.pending_fixture()
        row.customer = self.source['customer']
        row.customer_po = self.source['customer_po']
        row.items = deepcopy(row.latest_snapshot['items'])
        if reverse:
            row.items.reverse()
        row.metadata_fields = {key: self.source[key] for key in ('ship_date', 'terms', 'inventory_site')}
        row.approved_snapshot = deepcopy(row.latest_snapshot)
        row.approved_order_signature = totals(row.items, normalized=True)
        row.approved_at = datetime.now()
        row.workflow_status = 'released_to_mes'
        row.revision += 1
        db.session.commit()
        return row

    def form(self):
        return dict(action='preview', item_order='1,2', quantity_1='5', quantity_2='10',
                    serials_1='NA', serials_2='NA', notes_1='', notes_2='Cable config')

    def test_mrp_owned_orders_are_not_eligible_even_with_an_old_approval_timestamp(self):
        row = self.pending_fixture()
        row.approved_at = datetime.now()
        db.session.commit()
        for status in ('needs_material_review', 'needs_production_planning', 'closed'):
            row.workflow_status = status
            db.session.commit()
            self.assertNotIn('SO1', approved_orders())
            self.assertEqual(self.client.get('/so/SO1').status_code, 503)

    def test_current_handoff_controls_generation_and_keeps_mrp_item_order(self):
        row = self.pending_fixture()
        with patch('app.routes.read_google_sheet', side_effect=AssertionError('GET must not read Google')):
            self.assertEqual(self.client.get('/').status_code, 200)
            self.assertEqual(self.client.get('/so/SO1').status_code, 503)
            self.handoff_fixture(row, reverse=True)
            self.assertEqual(self.client.get('/so/SO1').status_code, 200)
        self.assertEqual([item['item'] for item in approved_orders()['SO1']['items']], ['Nuvo-9000', 'Cable'])
        self.assertEqual(list(load_orders()['Item']), ['Nuvo-9000', 'Cable'])
        self.assertEqual(labor_map(load_orders())['SO1']['qty'], 5)
        self.assertEqual(SalesOrder.query.count(), 0)

    def test_handoff_requires_approval_timestamp(self):
        row = self.pending_fixture()
        row.workflow_status = 'released_to_mes'
        db.session.commit()
        self.assertNotIn('SO1', approved_orders())

    def test_returned_revision_blocks_new_work_without_rewriting_published_content(self):
        row = self.handoff_fixture()
        original = deepcopy(row.items)
        row.previous_snapshot = deepcopy(row.latest_snapshot)
        incoming = deepcopy(row.latest_snapshot)
        incoming['items'][1]['quantity'] = 6
        row.latest_snapshot = incoming
        row.workflow_status = 'needs_material_review'
        row.returned_from_mes = True
        db.session.commit()
        self.assertNotIn('SO1', approved_orders())
        self.assertEqual(self.client.get('/so/SO1').status_code, 503)
        self.assertEqual(row.items, original)

    def test_missing_source_hides_active_order_but_preserves_owner_and_saved_wo(self):
        row = self.handoff_fixture()
        saved = SalesOrder(sales_order='SO1', is_manual=True,
                           items=[{'id': 1, 'item': 'System', 'quantity': 1, 'serials': []}])
        db.session.add(saved)
        db.session.commit()
        original = deepcopy(row.items), deepcopy(saved.items), row.revision
        row.previous_snapshot = deepcopy(row.latest_snapshot)
        row.latest_snapshot = None
        row.source_present = False
        db.session.commit()
        self.assertNotIn('SO1', approved_orders())
        self.assertNotIn('Open order SO1', self.client.get('/').text)
        self.assertNotIn('data-wo="SO1"', self.client.get('/production_planning').text)
        self.assertEqual(self.client.get('/so/SO1').status_code, 503)
        self.assertEqual(self.client.get('/generated/wo/' + saved.id).status_code, 200)
        self.assertEqual(row.workflow_status, 'released_to_mes')
        self.assertEqual((row.items, saved.items, row.revision), original)
        row.source_present = True
        row.latest_snapshot = deepcopy(self.source)
        db.session.commit()
        self.assertIn('SO1', approved_orders())

    def test_date_only_change_does_not_remove_mes_ownership(self):
        row = self.handoff_fixture()
        incoming = deepcopy(row.latest_snapshot)
        incoming['ship_date'] = '2030-12-31'
        row.latest_snapshot = incoming
        db.session.commit()
        self.assertIn('SO1', approved_orders())
        self.assertEqual(row.workflow_status, 'released_to_mes')

    def test_preview_stale_after_mrp_publication_cannot_push(self):
        row = self.handoff_fixture(reverse=True)
        form = self.form()
        response = self.client.post('/so/SO1', data=form)
        self.assertEqual(response.status_code, 200)
        form.update(action='push', preview_token=token(response))
        row.customer_po = 'PO2'
        row.revision += 1
        db.session.commit()
        response = self.client.post('/so/SO1', data=form)
        self.assertEqual(response.status_code, 400)
        self.assertIn('Approved SO changed after preview', response.text)
        self.assertEqual(SalesOrder.query.count(), 0)
        form['action'] = 'preview'
        response = self.client.post('/so/SO1', data=form)
        form.update(action='push', preview_token=token(response))
        self.assertEqual(self.client.post('/so/SO1', data=form).status_code, 302)
        self.assertEqual([item['item'] for item in SalesOrder.query.one().items], ['Nuvo-9000', 'Cable'])

    def test_case_insensitive_allocations_still_limit_new_picking(self):
        row = self.handoff_fixture(reverse=True)
        db.session.add(SalesOrder(sales_order='SO1', items=[{'id': 1, 'item': 'NUVO-9000', 'quantity': 2, 'serials': []}]))
        db.session.commit()
        self.assertIn('name="quantity_1" value="3"', self.client.get('/so/SO1').text)
        response = self.client.post('/so/SO1', data={**self.form(), 'quantity_1': '4'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('exceeds remaining quantity 3', response.text)
        self.assertEqual(row.workflow_status, 'released_to_mes')

    def test_disabled_legacy_posts_cannot_approve_sync_or_change_schedule(self):
        row = self.pending_fixture()
        db.session.add(ProductionOverride(wo_number='SO1', is_finished_goods=True))
        db.session.commit()
        before = deepcopy(row.latest_snapshot), row.revision, row.workflow_status
        with patch('app.routes.read_google_sheet', side_effect=AssertionError('MES must not import SOs')):
            for path in ('/sync-google', '/production_planning/orders/SO1'):
                self.assertEqual(self.client.post(path, data={'action': 'approve'}).status_code, 410)
        self.assertEqual((row.latest_snapshot, row.revision, row.workflow_status), before)
        self.assertIsNone(row.approved_at)
        self.assertTrue(db.session.get(ProductionOverride, 'SO1').is_finished_goods)
        self.assertEqual(SalesOrder.query.count(), 0)
        self.assertTrue(self.client.get('/production_planning/orders/SO1').location.endswith('/sales_orders/SO1'))

    def test_current_schema_does_not_require_legacy_activation_column(self):
        from app.approved_so_schema import require_so_workflow_schema
        require_so_workflow_schema()
        self.assertNotIn('is_active', {column['name'] for column in inspect(db.engine).get_columns('sales_order_workflow')})
        self.handoff_fixture()
        self.assertIn('SO1', approved_orders())

    def test_schema_validation_reports_missing_fields_without_migrating(self):
        from app.approved_so_schema import require_so_workflow_schema
        with patch('app.approved_so_schema.inspect') as schema:
            schema.return_value.get_columns.return_value = [{'name': 'sales_order'}]
            with self.assertRaisesRegex(RuntimeError, 'Incomplete shared SO workflow schema'):
                require_so_workflow_schema()
        self.assertEqual(ApprovedSO.query.count(), 0)

    def test_old_workflow_table_blocks_startup_without_creating_replacement(self):
        from app.approved_so_schema import require_current_table_name
        with patch('app.approved_so_schema.inspect') as schema:
            schema.return_value.get_table_names.return_value = ['approved_work_orders']
            with self.assertRaisesRegex(RuntimeError, 'rename_sales_order_workflow'):
                require_current_table_name()
            schema.return_value.get_columns.assert_not_called()

    def test_legacy_table_names_block_startup_without_migrating(self):
        from app.approved_so_schema import require_current_table_name
        with patch('app.approved_so_schema.inspect') as schema:
            schema.return_value.get_table_names.return_value = ['sales_order']
            with self.assertRaisesRegex(RuntimeError, 'Legacy SO table names'):
                require_current_table_name()
            schema.return_value.get_columns.assert_not_called()

    def test_old_source_presence_name_still_requires_explicit_rename(self):
        from app.approved_so_schema import require_so_workflow_schema
        with patch('app.approved_so_schema.inspect') as schema:
            schema.return_value.get_columns.return_value = [{'name': 'google_present'}]
            with self.assertRaisesRegex(RuntimeError, 'rename_source_present'):
                require_so_workflow_schema()
