import unittest
from frappe_wms.services.stock import _balance_name

class TestStockService(unittest.TestCase):
    def test_balance_name_is_stable(self):
        values={"warehouse":"NL01","product":"ITEM-1","storage_bin":"BIN-1","stock_type":"AVAILABLE"}
        self.assertEqual(_balance_name(values),_balance_name(dict(values)))
