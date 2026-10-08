import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, flt, nowdate

from frappe_wms.api.outbound import plan_cartons
from frappe_wms.tests.bootstrap import TEST_CUSTOMER, TEST_ITEM


class TestCartonization(IntegrationTestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        self.uom = frappe.db.get_value("Item", TEST_ITEM, "stock_uom")
        if not frappe.db.exists("WMS Product", TEST_ITEM):
            frappe.get_doc({"doctype": "WMS Product", "item": TEST_ITEM, "stock_uom": self.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        frappe.db.set_value("WMS Product", TEST_ITEM, {"gross_weight_per_unit": 2, "volume_per_unit": 1})
        self.wh = f"CARTON-{frappe.generate_hash(length=5).upper()}"
        frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": self.wh, "warehouse_name": self.wh, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        for code, wt, vol in (("CT-S", 10, 6), ("CT-L", 40, 20)):
            frappe.db.delete("Handling Unit Type", {"name": code})
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": code, "hu_type_name": code, "category": "Box", "maximum_weight": wt,
                            "maximum_volume": vol, "cartonizable": 1, "active": 1}).insert(ignore_permissions=True)
        frappe.db.set_value("Handling Unit Type", {"cartonizable": 1, "name": ["not in", ("CT-S", "CT-L")]}, "cartonizable", 0)

    def delivery(self, qty):
        d = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.wh,
                            "customer": TEST_CUSTOMER, "delivery_date": add_days(nowdate(), 1),
                            "items": [{"line_number": 1, "item": TEST_ITEM, "requested_quantity": qty, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"}]}).insert(ignore_permissions=True)
        d.submit()
        return d.name

    def test_smallest_box_that_holds_it_else_fewest_large_ones(self):
        # 3 units = 6 kg / 3 vol -> one small box; 25 units = 50 kg / 25 vol -> one large (40/20) holds 20 units, + 5 units in a small box
        self.assertEqual(plan_cartons(self.delivery(3)), {"cartons": 1, "types": ["CT-S"]})
        name = self.delivery(25)
        self.assertEqual(plan_cartons(name)["cartons"], 2)
        rows = frappe.get_all("Planned Shipping HU", {"parent": name}, ["hu_type", "quantity"], order_by="carton_no")
        self.assertEqual([(r.hu_type, flt(r.quantity)) for r in rows], [("CT-L", 20), ("CT-S", 5)])
        self.assertEqual(plan_cartons(name)["cartons"], 2, "planning again replaces the plan")
        self.assertEqual(frappe.db.count("Planned Shipping HU", {"parent": name}), 2)

    def test_product_without_weight_or_volume_is_refused(self):
        frappe.db.set_value("WMS Product", TEST_ITEM, {"gross_weight_per_unit": 0, "volume_per_unit": 0})
        with self.assertRaisesRegex(frappe.ValidationError, "neither weight nor volume"):
            plan_cartons(self.delivery(1))
