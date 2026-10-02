import unittest
import pandas as pd
from app.importer import _read_frames


class ImporterTests(unittest.TestCase):
    def test_review_metadata_and_original_item_order(self):
        frame = pd.DataFrame([
            {'QB Num': 'SO1', 'Customer': 'Customer', 'Customer PO': 'PO', 'Item': 'Cable', 'Qty': '10',
             'Lead_Time': '10/01/2026', 'Terms': 'Net30', 'Inventory Site': 'WH01S-NTA', 'Configuration': 'Cable config'},
            {'QB Num': 'SO1', 'Customer': 'Customer', 'Customer PO': 'PO', 'Item': 'Nuvo-9000', 'Qty': '5.0',
             'Lead_Time': '10/01/2026', 'Terms': 'Net30', 'Inventory Site': 'WH01S-NTA', 'Configuration': 'System config'}])
        result = _read_frames({'Orders': frame})
        order = result['orders']['SO1']
        self.assertEqual(result['skipped'], 0)
        self.assertEqual(order['ship_date'], '10/01/2026')
        self.assertEqual(order['terms'], 'Net30')
        self.assertEqual([item['item'] for item in order['items']], ['Cable', 'Nuvo-9000'])
        self.assertEqual(order['items'][1]['quantity'], 5)
        self.assertEqual(order['items'][1]['configuration'], 'System config')

    def test_invalid_quantity_is_reported_not_silently_changed_to_one(self):
        for quantity in ('-1', '1.5', 'NaN', 'Infinity', '', 'invalid'):
            frame = pd.DataFrame([{'QB Num': 'SO1', 'Item': 'Part', 'Qty': quantity}])
            result = _read_frames({'Orders': frame})
            self.assertEqual(result['skipped'], 1)
            self.assertTrue(result['messages'])
            self.assertFalse(result['orders'])

    def test_missing_required_header_reports_incomplete_read(self):
        result = _read_frames({'Orders': pd.DataFrame([{'QB Num': 'SO1', 'Item': 'Part'}])})
        self.assertFalse(result['orders'])
        self.assertTrue(result['messages'])
