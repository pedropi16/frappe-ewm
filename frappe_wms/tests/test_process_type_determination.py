import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.services.determination import determine_process_type


class TestProcessTypeDetermination(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "PTDET-TEST-WH"
        cls.other_warehouse = "PTDET-OTHER-WH"
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, limit=1, pluck="name")[0]
        company = frappe.get_all("Company", limit=1, pluck="name")[0]

        for wh in (cls.warehouse, cls.other_warehouse):
            if not frappe.db.exists("WMS Warehouse", wh):
                frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": wh, "warehouse_name": wh, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)

    def tearDown(self):
        for existing in frappe.get_all("Warehouse Process Type Determination Rule", filters={"activity": "Putaway"}, pluck="name"):
            frappe.delete_doc("Warehouse Process Type Determination Rule", existing, force=True, ignore_permissions=True)

    def test_falls_back_to_default_when_no_rule_matches(self):
        result = determine_process_type(self.warehouse, "Putaway", item=self.item, stock_type="AVAILABLE", default="GR_PUTAWAY")
        self.assertEqual(result, "GR_PUTAWAY")

    def test_warehouse_specific_rule_overrides_the_default(self):
        frappe.get_doc({"doctype": "Warehouse Process Type Determination Rule", "priority": 1, "warehouse": self.warehouse,
            "activity": "Putaway", "process_type": "DECON", "active": 1}).insert(ignore_permissions=True)
        result = determine_process_type(self.warehouse, "Putaway", item=self.item, stock_type="AVAILABLE", default="GR_PUTAWAY")
        self.assertEqual(result, "DECON")
        # A different warehouse must be unaffected by a rule scoped to this one.
        other_result = determine_process_type(self.other_warehouse, "Putaway", item=self.item, stock_type="AVAILABLE", default="GR_PUTAWAY")
        self.assertEqual(other_result, "GR_PUTAWAY")

    def test_blank_warehouse_rule_applies_to_every_warehouse(self):
        frappe.get_doc({"doctype": "Warehouse Process Type Determination Rule", "priority": 1,
            "activity": "Putaway", "process_type": "REPLENISH", "active": 1}).insert(ignore_permissions=True)
        for wh in (self.warehouse, self.other_warehouse):
            result = determine_process_type(wh, "Putaway", item=self.item, stock_type="AVAILABLE", default="GR_PUTAWAY")
            self.assertEqual(result, "REPLENISH")

    def test_priority_order_is_respected(self):
        frappe.get_doc({"doctype": "Warehouse Process Type Determination Rule", "priority": 1, "warehouse": self.warehouse,
            "activity": "Putaway", "process_type": "DECON", "active": 1}).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "Warehouse Process Type Determination Rule", "priority": 2, "warehouse": self.warehouse,
            "activity": "Putaway", "process_type": "REPLENISH", "active": 1}).insert(ignore_permissions=True)
        result = determine_process_type(self.warehouse, "Putaway", item=self.item, stock_type="AVAILABLE", default="GR_PUTAWAY")
        self.assertEqual(result, "DECON")

    def test_process_type_determination_indicator_from_wms_product_warehouse_is_matched(self):
        if not frappe.db.exists("WMS Product Warehouse", f"{self.warehouse}-{self.item}"):
            frappe.get_doc({"doctype": "WMS Product Warehouse", "item": self.item, "warehouse": self.warehouse,
                "process_type_determination_indicator": "SPECIAL", "active": 1}).insert(ignore_permissions=True)
        else:
            frappe.db.set_value("WMS Product Warehouse", f"{self.warehouse}-{self.item}", "process_type_determination_indicator", "SPECIAL")
        try:
            frappe.get_doc({"doctype": "Warehouse Process Type Determination Rule", "priority": 1, "warehouse": self.warehouse,
                "activity": "Putaway", "process_type_determination_indicator": "SPECIAL", "process_type": "DECON", "active": 1}).insert(ignore_permissions=True)
            result = determine_process_type(self.warehouse, "Putaway", item=self.item, stock_type="AVAILABLE", default="GR_PUTAWAY")
            self.assertEqual(result, "DECON")
        finally:
            frappe.db.set_value("WMS Product Warehouse", f"{self.warehouse}-{self.item}", "process_type_determination_indicator", None)
