"""Shared SO ownership guards and execution reconciliation; SQLite only."""
import unittest
from datetime import date
from app.models import db, ApprovedSO, SalesOrder
from app.approved_so import approved_orders
from app.wo_service import new_order
from test_planning import PlanningTests
from test_serial_table import token


class WorkflowTests(unittest.TestCase):
    setUp = PlanningTests.setUp
    tearDown = PlanningTests.tearDown
    headers = PlanningTests.headers

    def wo(self, qty=2, shipped=False):
        wo=SalesOrder(sales_order='SO1',is_shipped=shipped,items=[{'id':1,'item':'Nuvo-9000','quantity':qty,'serials':[],'notes':''}])
        db.session.add(wo);db.session.commit()
        return wo

    def form(self, qty):
        return dict(action='preview',item_order='1',quantity_1=str(qty),serials_1='NA',notes_1='')

    def single_product(self, qty=7, shipped=6):
        approval=db.session.get(ApprovedSO,'SO1')
        approval.items=[{'id':'source','item':'Nuvo-9000','quantity':qty}]
        approval.approved_order_signature={'nuvo-9000':qty}
        approval.latest_snapshot={'ship_date':'2026-10-07','items':[{'id':'source','item':'Nuvo-9000','quantity':qty,'ordered_quantity':qty,'shipped_quantity':shipped,'remaining_quantity':qty-shipped}]}
        approval.approved_snapshot=approval.latest_snapshot
        db.session.commit()
        return approval

    def test_returned_so_absent_from_mes_active_pages_but_wo_stays_flagged(self):
        saved=self.wo()
        approval=db.session.get(ApprovedSO,'SO1')
        approval.workflow_status='needs_material_review';approval.returned_from_mes=True
        db.session.commit()
        self.assertNotIn('SO1',approved_orders())
        self.assertNotIn('Open order SO1',self.client.get('/').text)
        self.assertNotIn('data-wo="SO1"',self.client.get('/production_planning').text)
        self.assertIn('Returned from MES',self.client.get('/generated').text)
        self.assertIn('read-only',self.client.get('/generated/wo/'+saved.id).text)
        self.assertEqual(self.client.post('/wo/'+saved.id+'/edit',data=self.form(3)).status_code,400)
        self.assertEqual(db.session.get(SalesOrder,saved.id).items[0]['quantity'],2)
        self.assertEqual(self.client.get('/so/SO1').status_code,503)

    def test_tariff_charge_is_excluded_from_published_execution_items(self):
        approval=self.single_product(7,0)
        approval.items=approval.items+[{'id':'tariff','item':' Tariff Charge ','quantity':1}]
        db.session.commit()
        self.assertEqual([i['item'] for i in approved_orders()['SO1']['items']],['Nuvo-9000'])

    def test_date_only_snapshot_difference_does_not_block_release(self):
        approval=self.single_product(7,0)
        approval.latest_snapshot=dict(approval.latest_snapshot,ship_date='2030-01-01')
        db.session.commit()
        self.assertEqual(self.client.get('/so/SO1').status_code,200)
        self.assertEqual(approval.workflow_status,'released_to_mes')

    def test_external_shipments_do_not_generate_duplicate_work(self):
        self.single_product(7,6)
        saved=self.wo(1)
        source=approved_orders()['SO1']
        self.assertEqual(source['items'][0]['quantity'],1)
        self.assertFalse(source['reconciliation_required'])
        self.assertEqual(new_order(source,[saved]).items[0]['quantity'],0)

    def test_partial_shipping_overlap_is_not_an_order_reduction(self):
        self.single_product(7,6)
        saved=self.wo(7,False)
        source=approved_orders()['SO1']
        self.assertEqual(source['items'][0]['quantity'],7)
        self.assertEqual(source['reconciliation_details']['allocations'],[])
        self.assertEqual(source['reconciliation_details']['shipments'],[])
        self.assertFalse(source['reconciliation_required'])
        self.assertEqual(source['workflow_status'],'released_to_mes')
        self.assertEqual(new_order(source,[saved]).items[0]['quantity'],0)
        self.assertEqual(source['fulfillment'],[dict(item='nuvo-9000',ordered=7,allocated=7,shipped=6,remaining=1)])
        self.assertEqual(self.client.get('/so/SO1').status_code,200)
        page=self.client.get('/generated/wo/'+saved.id)
        self.assertNotIn('WO reconciliation required',page.text)
        self.assertIn('Remaining to ship',page.text)
        self.assertEqual(db.session.get(SalesOrder,saved.id).items[0]['quantity'],7)
        self.assertFalse(saved.is_shipped)
        self.assertEqual(self.client.post('/wo/'+saved.id+'/edit',data=self.form(7)).status_code,200)

    def test_partial_invoicing_does_not_allow_duplicate_release(self):
        self.single_product(7,6)
        saved=self.wo(2)
        source=approved_orders()['SO1']
        self.assertFalse(source['reconciliation_required'])
        self.assertEqual(source['items'][0]['quantity'],2)
        self.assertEqual(new_order(source,[saved]).items[0]['quantity'],0)
        self.assertEqual(self.client.post('/so/SO1',data=self.form(1)).status_code,400)
        self.assertEqual(SalesOrder.query.count(),1)

    def test_fully_invoiced_order_retains_wo_history_without_new_work(self):
        self.single_product(7,7)
        saved=self.wo(7)
        source=approved_orders()['SO1']
        self.assertFalse(source['reconciliation_required'])
        self.assertEqual(source['fulfillment'][0]['remaining'],0)
        self.assertEqual(source['items'][0]['quantity'],7)
        self.assertEqual(new_order(source,[saved]).items[0]['quantity'],0)

    def test_invoicing_does_not_hide_real_overallocation(self):
        self.single_product(7,6)
        self.wo(8)
        source=approved_orders()['SO1']
        self.assertTrue(source['reconciliation_required'])
        self.assertEqual(source['reconciliation_details']['allocations'],
                         [dict(item='nuvo-9000',ordered=7,allocated=8)])

    def test_invoicing_more_than_ordered_requires_reconciliation(self):
        self.single_product(7,8)
        self.wo(7)
        source=approved_orders()['SO1']
        self.assertTrue(source['reconciliation_required'])
        self.assertEqual(source['reconciliation_details']['shipments'][0]['source_shipped'],8)

    def test_partial_invoicing_across_multiple_open_releases_is_ambiguous(self):
        self.single_product(7,6)
        self.wo(3)
        db.session.add(SalesOrder(sales_order='SO1',release_number=2,
            items=[{'id':1,'item':'Nuvo-9000','quantity':4,'serials':[]}]))
        db.session.commit()
        source=approved_orders()['SO1']
        self.assertTrue(source['reconciliation_required'])
        self.assertIn('Assign invoiced shipments',source['reconciliation_details']['shipments'][0]['reason'])

    def test_mes_shipments_are_not_subtracted_twice(self):
        self.single_product(7,6)
        self.wo(6,True)
        # Avoid duplicate release numbers; production requires unique per SO.
        wo=SalesOrder(sales_order='SO1',release_number=2,items=[{'id':1,'item':'Nuvo-9000','quantity':1,'serials':[]}])
        db.session.add(wo);db.session.commit()
        source=approved_orders()['SO1']
        self.assertEqual(source['items'][0]['quantity'],7)
        self.assertFalse(source['reconciliation_required'])
        self.assertEqual(new_order(source,SalesOrder.mes_query().filter_by(sales_order='SO1').all()).items[0]['quantity'],0)

    def test_reconciliation_blocks_new_release_but_explicit_reduction_resolves_it(self):
        approval=self.single_product(8,0)
        saved=self.wo(10)
        source=approved_orders()['SO1']
        self.assertTrue(source['reconciliation_required'])
        self.assertEqual(self.client.get('/so/SO1').status_code,409)
        response=self.client.get('/wo/'+saved.id+'/edit')
        self.assertIn('allocated 10',response.text)
        form=self.form(8)
        preview=self.client.post('/wo/'+saved.id+'/edit',data=form)
        self.assertEqual(preview.status_code,200,preview.text)
        form.update(action='push',preview_token=token(preview))
        response=self.client.post('/wo/'+saved.id+'/edit',data=form)
        self.assertEqual(response.status_code,302,response.text)
        self.assertEqual(db.session.get(SalesOrder,saved.id).items[0]['quantity'],8)
        self.assertFalse(approved_orders()['SO1']['reconciliation_required'])
        self.assertEqual(approval.workflow_status,'released_to_mes')

    def test_owner_change_after_preview_blocks_saved_wo_push(self):
        self.single_product(7,0)
        saved=self.wo(2)
        form=self.form(3)
        preview=self.client.post('/wo/'+saved.id+'/edit',data=form)
        form.update(action='push',preview_token=token(preview))
        approval=db.session.get(ApprovedSO,'SO1');approval.workflow_status='needs_material_review';approval.returned_from_mes=True
        db.session.commit()
        self.assertEqual(self.client.post('/wo/'+saved.id+'/edit',data=form).status_code,400)
        self.assertEqual(db.session.get(SalesOrder,saved.id).items[0]['quantity'],2)

    def test_explicit_mes_disposition_resolves_finished_goods_review_not_allocations(self):
        approval=self.single_product(8,0)
        approval.reconciliation_details={'finished_goods':True};approval.reconciliation_required=True
        db.session.commit()
        self.assertTrue(approved_orders()['SO1']['reconciliation_required'])
        response=self.client.post('/api/production_schedule',headers=self.headers(),json={
            'assignments':[{'wo_number':'SO1','target_area':'finished_goods'}]})
        self.assertEqual(response.status_code,200,response.text)
        self.assertFalse(approved_orders()['SO1']['reconciliation_required'])

    def test_manual_editor_allows_quick_order_without_mrp_release(self):
        approval=db.session.get(ApprovedSO,'SO1');approval.workflow_status='needs_material_review';approval.returned_from_mes=True
        db.session.commit()
        form={'action':'preview','sales_order':'SO1','customer':'Customer','customer_po':'',
              'item':['Nuvo-9000'],'quantity':['1'],'serials':['NA'],'notes':['']}
        response=self.client.post('/wo/new',data=form)
        self.assertEqual(response.status_code,200,response.text)
        form.update(action='push',preview_token=token(response))
        response=self.client.post('/wo/new',data=form)
        self.assertEqual(response.status_code,302,response.text)
        saved=SalesOrder.query.one()
        self.assertTrue(saved.is_manual)
        self.assertEqual(saved.sales_order,'SO1')
        self.assertEqual(approval.workflow_status,'needs_material_review')
