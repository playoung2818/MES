"""Preview checking uses existing data only, without changing WO save contracts."""
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.datastructures import MultiDict

from app.models import SalesOrder
from app.wo_checks import check_items, load_receiving_parts, load_source_items, normalize_part, preview_checks
import test_serial_table as serial_fixture


def item(name='Part-A', qty=1, serials=None, identity=1):
    return dict(id=identity, item=name, quantity=qty, serials=serials or [], notes='')


def codes(report):
    return [issue['error_type'] for issue in report['issues']]


class CheckerTests(unittest.TestCase):
    def test_normalized_parts_match_and_long_numeric_serial_is_exact(self):
        self.assertEqual(normalize_part(' Part-A B '), 'PARTAB')
        report = check_items('SO1', [item(serials=['00012345678901234567890'])],
                             [{'item': 'part a'}], {'12345678901234567890': 'PART A'})
        self.assertEqual(report['critical_count'], 0)
        self.assertEqual(report['warning_count'], 0)
        self.assertEqual(report['matched_serials'], 1)

    def test_same_row_count_different_parts_is_critical(self):
        report = check_items('SO1', [item(serials=['X'])], [{'item': 'Part-B'}], {'X': 'Part-A'})
        self.assertIn('MISSING_IN_SOURCE', codes(report))
        self.assertIn('EXTRA_IN_SOURCE', codes(report))
        self.assertNotIn('ROW_COUNT_MISMATCH', codes(report))

    def test_row_counts_include_repeated_product_lines(self):
        report = check_items('SO1', [item(serials=['X'])],
                             [{'item': 'Part-A'}, {'item': 'Part-A'}], {'X': 'Part-A'})
        self.assertIn('ROW_COUNT_MISMATCH', codes(report))
        self.assertIn('EXTRA_IN_SOURCE', codes(report))

    def test_intentional_zero_quantity_rows_are_partial_release_warnings(self):
        report = check_items('SO1', [item(serials=['X']), item('Other', 0, identity=2)],
                             [{'item': 'Part-A'}, {'item': 'Other'}], {'X': 'Part-A'})
        self.assertEqual(report['critical_count'], 0)
        self.assertIn('ROW_COUNT_MISMATCH', codes(report))
        self.assertNotIn('NO_SN', codes(report))

    def test_receiving_missing_part_mismatch_and_qty_count(self):
        report = check_items('SO1', [item(qty=3, serials=['X', 'Y'])],
                             [{'item': 'Part-A'}], {'X': 'Part-B'})
        self.assertIn('QTY_MISMATCH', codes(report))
        self.assertIn('PART_MISMATCH', codes(report))
        self.assertIn('SERIAL_NOT_FOUND', codes(report))
        self.assertEqual(report['checked_serials'], 2)

    def test_na_blanks_and_preinstalled_are_not_receiving_serials(self):
        report = check_items('SO1', [item(qty=3, serials=['NA', 'N/A', 'NONE', ''])], [], {})
        self.assertIn('NO_SN', codes(report))
        self.assertIn('SOURCE_NOT_FOUND', codes(report))
        self.assertNotIn('SERIAL_NOT_FOUND', codes(report))
        self.assertEqual(report['checked_serials'], 0)

    def test_mixed_na_requires_review_of_count(self):
        report = check_items('SO1', [item(qty=2, serials=['X', 'NA'])],
                             [{'item': 'Part-A'}], {'X': 'Part-A'})
        self.assertIn('PARTIAL_SN', codes(report))
        self.assertIn('QTY_MISMATCH', codes(report))

    def test_duplicate_raw_serials_are_not_lost_by_preview_deduplication(self):
        report = check_items('SO1', [item(serials=['123'])], [{'item': 'Part-A'}],
                             {'123': 'Part-A'}, {1: 'SN: 00123\n123'})
        self.assertIn('DUPLICATE_SN_IN_WO', codes(report))

    def test_missing_reference_checks_are_not_reported_as_success(self):
        report = check_items('SO1', [item(serials=['X'])], None, None)
        self.assertIn('SOURCE_CHECK_UNAVAILABLE', codes(report))
        self.assertIn('RECEIVING_CHECK_UNAVAILABLE', codes(report))
        self.assertNotIn('MATCH', codes(report))
        self.assertNotIn('SERIAL_NOT_FOUND', codes(report))

    def test_batch_receiving_lookup_uses_latest_date_and_id(self):
        with patch('app.wo_checks.db') as database:
            database.session.execute.return_value.mappings.return_value = [
                {'serial_number': '000123', 'part_number': 'New'},
                {'serial_number': '123', 'part_number': 'Old'},
                {'serial_number': 'abc', 'part_number': 'Letters'}]
            parts = load_receiving_parts(['123', 'ABC', 'NA'])
            self.assertEqual(parts, {'123': 'New', 'ABC': 'Letters'})
            self.assertEqual(database.session.execute.call_count, 1)
            query = str(database.session.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
            self.assertIn('entry_date DESC NULLS LAST', query)
            self.assertIn('id DESC', query)
            self.assertIn('ltrim', query)
            self.assertTrue(query.startswith('SELECT'))

    def test_source_lookup_excludes_drop_ship_and_service_lines(self):
        with patch('app.wo_checks.db') as database:
            database.session.execute.return_value.mappings.return_value.all.return_value = [
                {'Item': 'CPU', 'Inventory Site': 'WH01S-NTA'},
                {'Item': 'System', 'Inventory Site': ' Drop Ship '},
                {'Item': 'Tariff Charge', 'Inventory Site': 'WH01S-NTA'}]
            self.assertEqual(load_source_items('SO1'), [{'item': 'CPU'}])
            self.assertEqual(database.session.execute.call_count, 1)


class PreviewRouteTests(unittest.TestCase):
    setUp = serial_fixture.SerialTableTests.setUp
    tearDown = serial_fixture.SerialTableTests.tearDown

    def references(self):
        return patch.multiple('app.wo_checks',
                              load_source_items=MagicMock(return_value=[{'item': 'Part'}]),
                              load_receiving_parts=MagicMock(return_value={'123': 'Different'}))

    def test_normal_preview_runs_checker_and_does_not_persist(self):
        with self.references(), patch('app.wo_checks.preview_checks', wraps=preview_checks) as checker:
            form = dict(action='preview', item_order='1', quantity_1='1', serials_1='00123', notes_1='')
            page = self.client.post('/so/SO1', data=form)
            self.assertEqual(page.status_code, 200, page.text)
            self.assertIn('WO preview checks', page.text)
            self.assertIn('PART_MISMATCH', page.text)
            self.assertEqual(SalesOrder.query.count(), 0)
            checker.assert_called_once()
            # Advisory findings do not silently change the existing save contract.
            form.update(action='push', preview_token=serial_fixture.token(page))
            response = self.client.post('/so/SO1', data=form)
            self.assertEqual(response.status_code, 302, response.text)
            checker.assert_called_once()
            self.assertEqual(SalesOrder.query.one().items[0]['serials'], ['123'])

    def test_manual_preview_and_revision_run_checker(self):
        with self.references(), patch('app.wo_checks.preview_checks', wraps=preview_checks) as checker:
            form = MultiDict([('action', 'preview'), ('sales_order', 'SO-MANUAL'), ('customer', 'Customer'),
                              ('item', 'Part'), ('quantity', '1'), ('serials', '00123'), ('notes', '')])
            page = self.client.post('/wo/new', data=form)
            self.assertEqual(page.status_code, 200, page.text)
            self.assertIn('PART_MISMATCH', page.text)
            self.assertEqual(SalesOrder.query.count(), 0)
            form['action'] = 'push'
            form['preview_token'] = serial_fixture.token(page)
            self.assertEqual(self.client.post('/wo/new', data=form).status_code, 302)
            saved = SalesOrder.query.one()
            form['action'] = 'preview'
            page = self.client.post(f'/wo/{saved.id}/manual', data=form)
            self.assertEqual(page.status_code, 200, page.text)
            self.assertIn('WO preview checks', page.text)
            self.assertEqual(checker.call_count, 2)

    def test_reference_failure_is_visible_but_preview_and_save_still_work(self):
        with patch('app.wo_checks.load_source_items', side_effect=SQLAlchemyError('offline')), \
             patch('app.wo_checks.load_receiving_parts', side_effect=SQLAlchemyError('offline')):
            form = dict(action='preview', item_order='1', quantity_1='1', serials_1='X', notes_1='')
            page = self.client.post('/so/SO1', data=form)
            self.assertEqual(page.status_code, 200)
            self.assertIn('SOURCE_CHECK_UNAVAILABLE', page.text)
            self.assertIn('RECEIVING_CHECK_UNAVAILABLE', page.text)
            form.update(action='push', preview_token=serial_fixture.token(page))
            self.assertEqual(self.client.post('/so/SO1', data=form).status_code, 302)

    def test_reports_escape_untrusted_reference_part_names(self):
        with patch('app.wo_checks.load_source_items', return_value=[{'item': '<script>bad</script>'}]), \
             patch('app.wo_checks.load_receiving_parts', return_value={'X': '<script>bad</script>'}):
            page = self.client.post('/so/SO1', data=dict(action='preview', item_order='1', quantity_1='1',
                                                      serials_1='X', notes_1=''))
            self.assertIn('&lt;script&gt;bad&lt;/script&gt;', page.text)
            self.assertNotIn('<script>bad</script>', page.text)
