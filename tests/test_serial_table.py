import unittest
from html import unescape
import re
from unittest.mock import patch
from flask import Flask
from app.models import db, SalesOrder
from app.routes import bp
from app.serial_pruner import prune_and_sort_serials


def token(response):
    return unescape(re.search(r'name="preview_token" value="([^"]+)"', response.text).group(1))


class SerialTableTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask('app')
        self.app.config.update(TESTING=True, SECRET_KEY='test', SQLALCHEMY_DATABASE_URI='sqlite:///:memory:')
        db.init_app(self.app)
        self.app.register_blueprint(bp)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.client = self.app.test_client()
        self.sheet = {'SO1': {'sales_order': 'SO1', 'customer': 'Customer', 'customer_po': 'PO1',
                             'items': [{'item': 'Part', 'quantity': 10}]}}
        self.mock = patch('app.routes._read_approved_orders', side_effect=lambda: self.sheet)
        self.mock.start()

    def tearDown(self):
        self.mock.stop()
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def preview_push(self, url, quantity, serials='NA'):
        form = dict(action='preview', item_order='1', quantity_1=str(quantity), serials_1=serials, notes_1='Checked')
        response = self.client.post(url, data=form)
        self.assertEqual(response.status_code, 200, response.text)
        form.update(action='push', preview_token=token(response))
        return form, self.client.post(url, data=form)

    def test_two_word_copy_areas(self):
        self.preview_push('/so/SO1', 5, 'SN001;SN002')
        saved = SalesOrder.query.one()
        html = self.client.get(f'/generated/wo/{saved.id}').text
        self.assertIn('data-copy-word="word-header"', html)
        self.assertIn('data-copy-word="word-picking"', html)
        copied_header = html.split('<div id="word-header"')[1].split('</div>')[0]
        self.assertNotIn('Document Number:', copied_header)
        self.assertNotIn('Date:', copied_header)
        for text in ('Customer PO #', 'NTA Order ID', 'Product Number', 'Check',
                     'End of Part', '1<br>2'):
            self.assertIn(text, html)
        for label in ('Picking:', 'Warehouse:', 'Production Receiving:'):
            self.assertNotIn(label, html)

    def test_numeric_leading_zeros_at_any_length(self):
        self.assertEqual(prune_and_sort_serials('04842010792403'), ['4842010792403'])
        self.assertEqual(prune_and_sort_serials('SN: 04842010792403'), ['4842010792403'])
        self.assertEqual(prune_and_sort_serials('04842010792403;4842010792403'), ['4842010792403'])
        self.assertEqual(prune_and_sort_serials('00123'), ['123'])
        self.assertEqual(prune_and_sort_serials('0484201079240'), ['484201079240'])
        self.assertEqual(prune_and_sort_serials('0;00;000'), ['0'])
        self.assertEqual(prune_and_sort_serials('01;1;001'), ['1'])
        self.assertEqual(prune_and_sort_serials('00012345678901234567890'), ['12345678901234567890'])
        self.assertEqual(prune_and_sort_serials('0ABC;001-23'), ['001-23', '0ABC'])
        self.assertEqual(prune_and_sort_serials('123'), ['123'])
        self.assertEqual(prune_and_sort_serials('S04842010792403'), ['S04842010792403'])

    def test_numeric_duplicate_matches_legacy_saved_leading_zeros(self):
        db.session.add(SalesOrder(sales_order='OTHER', items=[{'item': 'Part', 'quantity': 1, 'serials': ['00123']}]))
        db.session.commit()
        response = self.client.post('/so/SO1', data=dict(action='preview', item_order='1',
                                    quantity_1='1', serials_1='123', notes_1=''))
        self.assertIn('already belongs to', response.text)
        self.assertNotIn('name="preview_token"', response.text)

    def test_leading_s_is_part_of_serial(self):
        self.assertEqual(prune_and_sort_serials('S75CNS0L518419'), ['S75CNS0L518419'])
        self.assertEqual(prune_and_sort_serials('SN: S75CNS0L518419'), ['S75CNS0L518419'])
        self.assertEqual(prune_and_sort_serials('S/N: S75CNS0L518419'), ['S75CNS0L518419'])
        self.assertEqual(prune_and_sort_serials('SNO: S75CNS0L518419'), ['S75CNS0L518419'])

    def test_plain_and_prefixed_serials(self):
        self.assertEqual(prune_and_sort_serials('00123; ABC-456\nsn: 00789; S/N: ZX-12;00123'),
                         ['123', '789', 'ABC-456', 'ZX-12'])
        self.assertEqual(prune_and_sort_serials('ABCST123'), ['ABCST123'])
        self.assertEqual(prune_and_sort_serials('NA; na; N/A; none; null'), ['NA'])

    def test_partial_p1_p2_and_revision(self):
        form, response = self.preview_push('/so/SO1', 5, 'SN001;SN002')
        self.assertEqual(response.status_code, 302)
        p1 = SalesOrder.query.one()
        identity = p1.id, p1.document_number, p1.generated_date
        self.assertEqual(p1.release_number, 1)
        self.assertEqual(p1.items[0]['quantity'], 5)
        self.assertIn('name="quantity_1" value="5"', self.client.get('/so/SO1').text)
        # Retrying the same confirmed preview must not create P2.
        self.assertEqual(self.client.post('/so/SO1', data=form).status_code, 302)
        self.assertEqual(SalesOrder.query.count(), 1)
        _, response = self.preview_push('/so/SO1', 5, 'SN003')
        self.assertEqual(response.status_code, 302)
        p2 = SalesOrder.query.filter_by(release_number=2).one()
        self.assertNotEqual(p2.document_number, p1.document_number)
        self.assertIn('name="quantity_1" value="0"', self.client.get('/so/SO1').text)
        _, response = self.preview_push(f'/wo/{p1.id}/edit', 5, '001;002')
        self.assertEqual(response.status_code, 302)
        db.session.refresh(p1)
        self.assertEqual((p1.id, p1.document_number, p1.generated_date), identity)
        self.assertEqual(p2.items[0]['serials'], ['3'])
        self.assertEqual(SalesOrder.query.count(), 2)
        self.assertEqual(self.client.get(f'/generated/wo/{p2.id}').status_code, 200)

    def test_overallocation_duplicate_serial_and_na(self):
        _, response = self.preview_push('/so/SO1', 5, 'SN001')
        self.assertEqual(response.status_code, 302)
        for qty, serials in [('6', 'NA'), ('5', 'SN001'), ('-1', 'NA'), ('1.5', 'NA')]:
            response = self.client.post('/so/SO1', data=dict(action='preview', item_order='1', quantity_1=qty, serials_1=serials))
            self.assertEqual(response.status_code, 400)
        _, response = self.preview_push('/so/SO1', 5, 'NA')
        self.assertEqual(response.status_code, 302)
        p2 = SalesOrder.query.filter_by(release_number=2).one()
        self.assertRegex(self.client.get(f'/generated/wo/{p2.id}').text, r'<td[^>]*>NA</td>')

    def test_preview_no_write_and_changed_inputs_require_repreview(self):
        form = dict(action='preview', item_order='1', quantity_1='3', serials_1='NA')
        response = self.client.post('/so/SO1', data=form)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SalesOrder.query.count(), 0)
        from datetime import datetime, timezone
        from app.document_numbers import next_document_number
        estimate = next_document_number(datetime.now(timezone.utc))
        self.assertIn('<b>Document Number:</b> ' + estimate, response.text)
        self.assertIn('Estimated, not reserved.', response.text)
        form.update(action='push', preview_token=token(response), quantity_1='4')
        self.assertEqual(self.client.post('/so/SO1', data=form).status_code, 400)
        self.assertEqual(SalesOrder.query.count(), 0)

    def test_push_revalidates_allocations(self):
        form = dict(action='preview', item_order='1', quantity_1='6', serials_1='NA')
        response = self.client.post('/so/SO1', data=form)
        form.update(action='push', preview_token=token(response))
        self.preview_push('/so/SO1', 5)
        self.assertEqual(self.client.post('/so/SO1', data=form).status_code, 400)
        self.assertEqual(SalesOrder.query.count(), 1)

    def test_na_allowed_across_items_and_offline_revision(self):
        self.sheet['SO1']['items'].append({'item': 'Other', 'quantity': 1})
        form = dict(action='preview', item_order='1,2', quantity_1='1', quantity_2='1', serials_1='NA', serials_2='NA')
        response = self.client.post('/so/SO1', data=form)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(re.findall(r'<td[^>]*>NA</td>', response.text.split('<section id="generated-table"')[1])), 2)
        form.update(action='push', preview_token=token(response))
        self.assertEqual(self.client.post('/so/SO1', data=form).status_code, 302)
        saved = SalesOrder.query.one()
        self.sheet = {}
        self.assertEqual(self.client.get('/so/SO1').status_code, 503)
        self.assertEqual(self.client.get(f'/wo/{saved.id}/edit').status_code, 200)
        self.assertEqual(self.client.get('/generated').status_code, 200)


if __name__ == '__main__':
    unittest.main()
