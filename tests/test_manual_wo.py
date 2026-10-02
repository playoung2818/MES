import unittest
from unittest.mock import patch
from flask import Flask
from werkzeug.datastructures import MultiDict
from app.models import db, SalesOrder
from app.routes import bp
from test_serial_table import token


class ManualTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask('app')
        self.app.config.update(TESTING=True, SECRET_KEY='test', SQLALCHEMY_DATABASE_URI='sqlite:///:memory:')
        db.init_app(self.app)
        self.app.register_blueprint(bp)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.client = self.app.test_client()
        self.mock = patch('app.routes._read_google_orders', side_effect=AssertionError('Manual route must not read Google Sheets'))
        self.reader = self.mock.start()
        self.form = MultiDict([
            ('sales_order', 'SO-MANUAL'), ('customer', 'Customer'), ('customer_po', 'PO123'),
            ('item', 'System'), ('quantity', '5'), ('serials', 'SN001;SN002'), ('notes', 'First lot'),
            ('item', 'Cable'), ('quantity', '5'), ('serials', 'NA'), ('notes', ''), ('action', 'preview')])

    def tearDown(self):
        self.mock.stop()
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def push(self, url='/wo/new'):
        self.form['action'] = 'preview'
        response = self.client.post(url, data=self.form)
        self.assertEqual(response.status_code, 200, response.text)
        self.form['preview_token'] = token(response)
        self.form['action'] = 'push'
        response = self.client.post(url, data=self.form)
        self.assertEqual(response.status_code, 302, response.text)
        return response

    def test_blank_preview_push_revision_and_next_release(self):
        self.assertEqual(self.client.get('/wo/new').status_code, 200)
        response = self.client.post('/wo/new', data=self.form)
        self.assertEqual(response.status_code, 200)
        self.assertIn('PO123', response.text.split('<section id="generated-table"')[1])
        self.assertEqual(SalesOrder.query.count(), 0)
        self.assertIn('<b>Document Number:</b> WO-', response.text)
        self.assertIn('Estimated, not reserved.', response.text)
        self.push()
        saved = SalesOrder.query.one()
        identity = saved.document_number, saved.generated_date
        self.assertTrue(saved.is_manual)
        self.assertEqual(saved.items[0]['serials'], ['1', '2'])
        self.assertEqual(self.client.post('/wo/new', data=self.form).status_code, 302)
        self.assertEqual(SalesOrder.query.count(), 1)
        self.assertIn('/manual', self.client.get(f'/wo/{saved.id}/edit').location)
        self.form.setlist('quantity', ['3', '4'])
        self.push(f'/wo/{saved.id}/manual')
        db.session.refresh(saved)
        self.assertEqual((saved.document_number, saved.generated_date), identity)
        self.assertEqual(saved.items[0]['quantity'], 3)
        self.form.setlist('serials', ['NA', 'NA'])
        self.push()
        self.assertEqual(SalesOrder.query.count(), 2)
        second = SalesOrder.query.filter_by(release_number=2).one()
        self.assertNotEqual(saved.document_number, second.document_number)
        self.reader.assert_not_called()

    def test_changed_preview_invalid_qty_and_duplicate(self):
        response = self.client.post('/wo/new', data=self.form)
        self.form['action'] = 'push'
        self.form['preview_token'] = token(response)
        self.form['customer'] = 'Changed'
        self.assertEqual(self.client.post('/wo/new', data=self.form).status_code, 400)
        self.assertEqual(SalesOrder.query.count(), 0)
        self.form['action'] = 'preview'
        self.form.setlist('quantity', ['-1', '5'])
        response = self.client.post('/wo/new', data=self.form)
        self.assertEqual(response.status_code, 400)
        self.assertIn('value="-1"', response.text)
        self.form.setlist('quantity', ['5', '5'])
        self.push()
        self.form['action'] = 'preview'
        self.assertEqual(self.client.post('/wo/new', data=self.form).status_code, 400)
        self.assertEqual(SalesOrder.query.count(), 1)
