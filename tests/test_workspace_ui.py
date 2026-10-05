import unittest
from html import unescape
from flask import Flask
from app.models import db
from app.routes import bp
from unittest.mock import patch


class WorkspaceUITests(unittest.TestCase):
    def setUp(self):
        self.app = Flask('app')
        self.app.config.update(TESTING=True, SECRET_KEY='test', SQLALCHEMY_DATABASE_URI='sqlite:///:memory:')
        db.init_app(self.app)
        self.app.register_blueprint(bp)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.client = self.app.test_client()
        self.source = dict(sales_order='SO1', customer='Customer', customer_po='HIDDEN-PO',
                           ship_date='2030-03-18', items=[dict(item='Hidden product', quantity=2)])
        self.mock = patch('app.routes._read_approved_orders', return_value={'SO1': self.source})
        self.mock.start()

    def tearDown(self):
        self.mock.stop()
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_home_is_minimal_and_hidden_fields_are_searchable(self):
        html = self.client.get('/').text
        self.assertIn('Order number', html)
        self.assertIn('Open order', html)
        self.assertNotIn('HIDDEN-PO', html)
        self.assertNotIn('Hidden product', html)
        self.assertNotIn('Customer PO', html)
        for query in ('HIDDEN-PO', 'Hidden product'):
            self.assertIn('href="/so/SO1"', self.client.get('/', query_string={'q': query}).text)
        self.assertNotIn('href="/so/SO1"', self.client.get('/?q=not-found').text)

    def test_editor_keeps_allocation_data_and_server_field_names(self):
        html = self.client.get('/so/SO1').text
        for text in ('HIDDEN-PO', '2030-03-18', 'Available for this WO: 2',
                     'name="quantity_1" value="2"', 'name="serials_1"', 'name="notes_1"',
                     'name="item_order"', 'value="preview"', 'data-work-order-entry',
                     'work-order-entry.js', 'Preview work order'):
            self.assertIn(text, html)
        self.assertNotIn('value="push"', html)

    def test_preview_retains_raw_paste_but_export_uses_existing_server_rules(self):
        response = self.client.post('/so/SO1', data=dict(action='preview', item_order='1',
                                   quantity_1='2', serials_1='0001\n\n0002', notes_1='Checked'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('0001\n\n0002</textarea>', unescape(response.text))
        self.assertIn('name="preview_token"', response.text)
        self.assertIn('value="push">Save work order', response.text)
        self.assertIn('id="generated-table"', response.text)
        self.assertIn('data-copy-word="word-header"', response.text)
        self.assertIn('data-copy-word="word-picking"', response.text)

    def test_shared_assets_and_manual_hooks(self):
        response = self.client.get('/wo/new')
        self.assertEqual(response.status_code, 200)
        for text in ('aria-current="page">Sales orders', 'workspace-polish.css',
                     'id="manual-items" data-entry-items', 'id="blank-row"',
                     'name="quantity"', 'serial-feedback', 'id="add-item"'):
            self.assertIn(text, response.text)
        for asset in ('workspace-polish.css', 'workspace-icons.svg', 'work-order-entry.js',
                      'vendor/bootstrap-5.3.3.min.css'):
            with self.client.get('/static/' + asset) as response:
                self.assertEqual(response.status_code, 200)


if __name__ == '__main__':
    unittest.main()
