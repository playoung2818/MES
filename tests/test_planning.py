import unittest
from datetime import date, timedelta
from unittest.mock import patch
from flask import Flask
from sqlalchemy import text
from app.models import db, SalesOrder, ProductionOverride, PickedQtyOverride, ApprovedSO
from app.routes import bp
from app.production_planning import load_orders, build_plan, labor_map


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask('app')
        self.app.config.update(TESTING=True, SECRET_KEY='test', SQLALCHEMY_DATABASE_URI='sqlite:///:memory:')
        db.init_app(self.app)
        self.app.register_blueprint(bp)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.client = self.app.test_client()
        self.day = date.today()
        while self.day.weekday() >= 5:
            self.day += timedelta(days=1)
        with db.engine.begin() as connection:
            connection.execute(text('''CREATE TABLE open_sales_orders (
                "QB Num" TEXT, "Item" TEXT, "Qty(-)" REAL, "Ship Date" TEXT,
                "Name" TEXT, "Inventory Site" TEXT, "Terms" TEXT)'''))
            connection.execute(text('''INSERT INTO open_sales_orders VALUES
                ('SO1','Nuvo-9000',2,:day,'Customer','WH01S-NTA','Net30'),
                ('SO1','POC-700',4,:day,'Customer','WH01S-NTA','Net30'),
                ('SO1','Cable',20,:day,'Customer','WH01S-NTA','Net30'),
                ('SO2','NRU-230',10,:placeholder,'Customer','WH01X-NTA',''),
                ('SO3','PCIe-PoE454at',1,'','Customer','WH01S-NTA',''),
                ('DROP','Nuvo-9000',100,:day,'Customer','Drop Ship','')'''),
                               {'day': self.day.isoformat(), 'placeholder': f'{self.day.year}-12-31'})
        from datetime import datetime
        with db.engine.connect() as connection:
            records = connection.execute(text('SELECT * FROM open_sales_orders')).mappings().all()
        orders = {}
        for record in records:
            row = orders.setdefault(record['QB Num'], ApprovedSO(sales_order=record['QB Num'],
                     customer=record['Name'], items=[], is_active=True, approved_at=datetime.now(),
                     synced_at=datetime.now(), google_present=True,
                     metadata_fields={'ship_date': record['Ship Date'], 'terms': record['Terms'],
                                      'inventory_site': record['Inventory Site']}))
            row.items = row.items + [{'item': record['Item'], 'quantity': int(record['Qty(-)']),
                                      'inventory_site': record['Inventory Site']}]
        db.session.add_all(orders.values())
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def headers(self):
        self.client.get('/production_planning')
        with self.client.session_transaction() as session:
            return {'X-CSRFToken': session['planning_csrf']}

    def test_shared_data_capacity_and_read_only_page(self):
        frame = load_orders()
        self.assertNotIn('DROP', set(frame['QB Num']))
        plan = build_plan(frame)
        week = next(week for week in plan['capacity_weeks'] if week['so_count'])
        self.assertEqual(week['used_hours_str'], '4')
        self.assertEqual({row['qb_num'] for row in plan['unassigned_lt_orders']}, {'SO2', 'SO3'})
        response = self.client.get('/production_planning')
        self.assertEqual(response.status_code, 200)
        self.assertIn('workspace-planning-link', response.text)
        self.assertIn('Save Schedule', response.text)
        self.assertIn('Weekly Labor Capacity', response.text)
        self.assertEqual(SalesOrder.query.count(), 0)
        self.assertEqual(ProductionOverride.query.count(), 0)
        self.assertEqual(PickedQtyOverride.query.count(), 0)

    def test_order_colors_follow_picked_status(self):
        with db.engine.begin() as connection:
            connection.execute(text('CREATE TABLE wo_structured ("QB Num" TEXT, "Picked" TEXT)'))
            connection.execute(text("INSERT INTO wo_structured VALUES ('SO1','NA'), ('SO2','Picked'), ('SO3','NA')"))
        db.session.add(SalesOrder(sales_order='SO1', items=[{'item': 'Nuvo-9000', 'quantity': 1}]))
        db.session.commit()
        html = self.client.get('/production_planning').text
        self.assertRegex(html, r'class="order-line order-picked lt-same-day" data-wo="SO1"')
        self.assertRegex(html, r'class="unassigned-lt-row order-line order-na" data-wo="SO2"')
        self.assertRegex(html, r'class="unassigned-lt-row order-line order-na" data-wo="SO3"')
        # Red takes precedence when production is on/after the ship date.
        ProductionOverride.query.delete()
        db.session.add(ProductionOverride(wo_number='SO1', production_date=self.day))
        db.session.commit()
        source = db.session.get(ApprovedSO, 'SO1')
        source.metadata_fields = dict(source.metadata_fields, ship_date=(self.day + timedelta(days=7)).isoformat())
        db.session.commit()
        html = self.client.get('/production_planning').text
        self.assertRegex(html, r'class="order-line order-picked" data-wo="SO1"')

    def test_schedule_finished_goods_and_return(self):
        headers = self.headers()
        def save(area, day=''):
            return self.client.post('/api/production_schedule', headers=headers,
                                    json={'assignments': [{'wo_number': 'SO2', 'target_area': area,
                                                           'production_date': day}]})
        self.assertEqual(save('schedule', self.day.isoformat()).status_code, 200)
        self.assertEqual(db.session.get(ProductionOverride, 'SO2').production_date, self.day)
        self.assertEqual(save('finished_goods').status_code, 200)
        plan = build_plan(load_orders())
        self.assertEqual([row['qb_num'] for row in plan['passed_lt_orders']], ['SO2'])
        self.assertEqual(save('schedule', self.day.isoformat()).status_code, 200)
        db.session.expire_all()
        self.assertFalse(db.session.get(ProductionOverride, 'SO2').is_finished_goods)
        self.assertEqual(SalesOrder.query.count(), 0)

    def test_invalid_batch_saves_nothing(self):
        response = self.client.post('/api/production_schedule', headers=self.headers(), json={'assignments': [
            {'wo_number': 'SO1', 'production_date': self.day.isoformat()},
            {'wo_number': 'DOES-NOT-EXIST', 'target_area': 'finished_goods'}]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ProductionOverride.query.count(), 0)

    def test_database_failure_rolls_back_whole_batch(self):
        from app.planning_routes import _upsert
        from sqlalchemy.exc import SQLAlchemyError
        headers = self.headers()
        count = 0
        def fail_second(model, values):
            nonlocal count
            count += 1
            if count == 2:
                raise SQLAlchemyError('simulated database failure')
            return _upsert(model, values)
        with patch('app.planning_routes._upsert', side_effect=fail_second), patch.object(self.app.logger, 'exception'):
            response = self.client.post('/api/production_schedule', headers=headers, json={'assignments': [
                {'wo_number': 'SO1', 'production_date': self.day.isoformat()},
                {'wo_number': 'SO2', 'target_area': 'finished_goods'}]})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(ProductionOverride.query.count(), 0)

    def test_csrf_and_invalid_dates(self):
        self.assertEqual(self.client.post('/api/production_schedule', json={}).status_code, 403)
        headers = self.headers()
        weekend = self.day
        while weekend.weekday() != 5:
            weekend += timedelta(days=1)
        for value in (weekend.isoformat(), 'invalid', (date.today()-timedelta(days=1)).isoformat()):
            response = self.client.post('/api/production_schedule', headers=headers,
                                       json={'assignments': [{'wo_number': 'SO1', 'production_date': value}]})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(ProductionOverride.query.count(), 0)

    def row_for_so1(self):
        plan = build_plan(load_orders())
        return next(row for group in plan['date_groups'] for row in group['orders'] if row['qb_num'] == 'SO1')

    def test_saved_partial_quantities_revisions_and_remaining_labor(self):
        from datetime import datetime
        # A legacy override must not override the WO Details quantities.
        db.session.add(PickedQtyOverride(wo_number='SO1', picked_qty=6, updated_at=datetime.now()))
        db.session.commit()
        self.assertEqual(self.row_for_so1()['wo_status'], 'NA')
        self.assertEqual(self.row_for_so1()['picked_qty'], 0)
        first = SalesOrder(sales_order='SO1', release_number=1,
                           items=[{'item': 'Nuvo-9000', 'quantity': 1}, {'item': 'Cable', 'quantity': 20}])
        db.session.add(first)
        db.session.commit()
        row = self.row_for_so1()
        self.assertEqual((row['wo_status'], row['picked_qty'], row['remaining_units'], row['labor_hours']),
                         ('Picked', 1, 5, 3))
        second = SalesOrder(sales_order='SO1', release_number=2,
                            items=[{'item': 'POC-700', 'quantity': 2}])
        db.session.add(second)
        db.session.commit()
        row = self.row_for_so1()
        self.assertEqual((row['picked_qty'], row['remaining_units'], row['labor_hours']), (3, 3, 2))
        first.items = [{'item': 'nuvo-9000', 'quantity': 2}]
        db.session.commit()
        row = self.row_for_so1()
        self.assertEqual((row['picked_qty'], row['remaining_units'], row['labor_hours']), (4, 2, 1))
        db.session.delete(second)
        db.session.commit()
        self.assertEqual(self.row_for_so1()['picked_qty'], 2)

    def test_manual_picked_edit_is_disabled(self):
        response = self.client.post('/api/wo_picked_qty', headers=self.headers(),
                                   json={'wo_number': 'SO1', 'picked_qty': 3})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(PickedQtyOverride.query.count(), 0)
        html = self.client.get('/production_planning').text
        self.assertNotIn('class="picked-qty-input"', html)
        self.assertIn('Calculated from saved WO Details quantities', html)

    def test_saved_quantities_are_clamped_per_product(self):
        db.session.add(SalesOrder(sales_order='SO1', items=[{'item': 'Nuvo-9000', 'quantity': 20}]))
        db.session.commit()
        row = self.row_for_so1()
        self.assertEqual((row['picked_qty'], row['remaining_units'], row['labor_hours']), (2, 4, 2))

    def test_duplicate_product_lines_do_not_double_count_saved_qty(self):
        from app.production_planning import pick_totals
        import pandas as pd
        frame = pd.DataFrame([{'QB Num': 'DUP', 'Item': 'Nuvo-9000', 'Qty': 1},
                              {'QB Num': 'DUP', 'Item': 'Nuvo-9000', 'Qty': 1}])
        totals = pick_totals(labor_map(frame)['DUP'], {'nuvo-9000': 1})
        self.assertEqual((totals['picked_qty'], totals['remaining_units'], totals['labor_hours']), (1, 1, 1))

    def test_fully_picked_unknown_model_has_zero_remaining_labor(self):
        db.session.add(SalesOrder(sales_order='SO3', items=[{'item': 'PCIe-PoE454at', 'quantity': 1}]))
        db.session.commit()
        plan = build_plan(load_orders())
        row = next(row for row in plan['unassigned_lt_orders'] if row['qb_num'] == 'SO3')
        self.assertEqual((row['wo_status'], row['picked_qty'], row['remaining_units'], row['labor_hours']),
                         ('Picked', 1, 0, 0))

    def test_inficon_uses_normal_product_family_rules(self):
        import pandas as pd
        frame = pd.DataFrame([{'QB Num': 'INF', 'Customer': 'Inficon', 'Item': 'Cable', 'Qty': 12},
                              {'QB Num': 'INF', 'Customer': 'Inficon', 'Item': 'Nuvo-9000', 'Qty': 2}])
        info = labor_map(frame)['INF']
        self.assertEqual(info['item'], 'Nuvo-9000')
        self.assertEqual(info['qty'], 2)
        self.assertEqual(info['base_labor_hours'], 2)
        cable_only = labor_map(frame.iloc[:1])['INF']
        self.assertEqual(cable_only['unit_rows'], [])
        self.assertIsNone(cable_only['base_labor_hours'])
        plan = build_plan(load_orders())
        unknown = next(row for row in plan['unassigned_lt_orders'] if row['qb_num'] == 'SO3')
        self.assertIsNone(unknown['labor_hours'])

    def test_missing_source_has_helpful_error(self):
        from app.production_planning import PlanningDataError
        with patch('app.planning_routes.load_orders', side_effect=PlanningDataError('MRP data unavailable')):
            response = self.client.get('/production_planning')
        self.assertEqual(response.status_code, 503)
        self.assertIn('MRP data unavailable', response.text)
