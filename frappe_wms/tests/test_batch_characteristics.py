import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.services.batch_characteristics import set_batch_characteristics, get_batch_characteristics


class TestBatchCharacteristics(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.item = "TEST-BATCHCHAR-ITEM"
        if not frappe.db.exists("Item", cls.item):
            item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
            frappe.get_doc({"doctype": "Item", "item_code": cls.item, "item_name": cls.item, "item_group": item_group,
                "stock_uom": "Nos", "is_stock_item": 1, "has_batch_no": 1}).insert(ignore_permissions=True)

    def _make_batch(self, batch_id):
        if not frappe.db.exists("Batch", batch_id):
            frappe.get_doc({"doctype": "Batch", "batch_id": batch_id, "item": self.item}).insert(ignore_permissions=True)
        return batch_id

    def test_set_and_get_round_trip(self):
        batch_id = self._make_batch("BATCHCHAR-1")
        result = set_batch_characteristics(batch_id, {"Grade": "A", "Potency": "98"})
        self.assertEqual(result, {"Grade": "A", "Potency": "98"})
        self.assertEqual(get_batch_characteristics(batch_id), {"Grade": "A", "Potency": "98"})

    def test_setting_again_updates_in_place_rather_than_duplicating(self):
        batch_id = self._make_batch("BATCHCHAR-2")
        set_batch_characteristics(batch_id, {"Grade": "A"})
        set_batch_characteristics(batch_id, {"Grade": "B"})
        self.assertEqual(get_batch_characteristics(batch_id), {"Grade": "B"})
        self.assertEqual(frappe.db.count("WMS Batch Characteristic Value", {"batch_no": batch_id, "characteristic": "Grade"}), 1)

    def test_setting_one_characteristic_leaves_others_on_the_batch_untouched(self):
        batch_id = self._make_batch("BATCHCHAR-3")
        set_batch_characteristics(batch_id, {"Grade": "A", "Color": "Red"})
        set_batch_characteristics(batch_id, {"Grade": "B"})
        self.assertEqual(get_batch_characteristics(batch_id), {"Grade": "B", "Color": "Red"})

    def test_unknown_batch_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            set_batch_characteristics("NO-SUCH-BATCH", {"Grade": "A"})
