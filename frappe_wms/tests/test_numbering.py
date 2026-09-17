import frappe
from frappe.tests import IntegrationTestCase


class TestNumbering(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.company = frappe.get_all("Company", limit=1, pluck="name")[0]
        cls.warehouse = f"WMS-TEST-NUMBERING-{frappe.generate_hash(length=6).upper()}"
        frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse,
            "company": cls.company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "BULK", "storage_type_name": "BULK",
            "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        cls.bulk_bin = f"{cls.warehouse}-BULK"
        frappe.get_doc({"doctype": "Storage Bin", "bin_code": cls.bulk_bin, "warehouse": cls.warehouse,
            "storage_type": f"{cls.warehouse}-BULK", "active": 1, "sequence": 1}).insert(ignore_permissions=True)

    def test_warehouse_order_uses_the_default_series_when_no_range_is_configured(self):
        wo = frappe.get_doc({"doctype": "Warehouse Order", "warehouse": self.warehouse, "activity": "Pick", "priority": "Normal", "status": "Open"})
        wo.insert(ignore_permissions=True)
        self.assertTrue(wo.name.startswith("WO-"))

    def test_warehouse_order_uses_a_configured_range_scoped_to_its_warehouse(self):
        number_range = frappe.get_doc({"doctype": "WMS Number Range", "range_for": "Warehouse Order", "warehouse": self.warehouse,
            "prefix": "WOTN-", "number_length": 4, "start_number": 1, "end_number": 99, "active": 1}).insert(ignore_permissions=True)

        wo = frappe.get_doc({"doctype": "Warehouse Order", "warehouse": self.warehouse, "activity": "Pick", "priority": "Normal", "status": "Open"})
        wo.insert(ignore_permissions=True)
        self.assertEqual(wo.name, "WOTN-0001")

        wo2 = frappe.get_doc({"doctype": "Warehouse Order", "warehouse": self.warehouse, "activity": "Pick", "priority": "Normal", "status": "Open"})
        wo2.insert(ignore_permissions=True)
        self.assertEqual(wo2.name, "WOTN-0002")

        # A different warehouse never configured for this range still falls back to the
        # ordinary series - the range is opt-in per warehouse, not global just because one
        # warehouse turned it on.
        other_warehouse = f"{self.warehouse}-OTHER"
        frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": other_warehouse, "warehouse_name": other_warehouse,
            "company": self.company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        wo3 = frappe.get_doc({"doctype": "Warehouse Order", "warehouse": other_warehouse, "activity": "Pick", "priority": "Normal", "status": "Open"})
        wo3.insert(ignore_permissions=True)
        self.assertTrue(wo3.name.startswith("WO-"))

        frappe.db.set_value("WMS Number Range", number_range.name, "active", 0)

    def test_packing_order_range_is_scoped_via_the_outbound_delivery_warehouse(self):
        # Packing Order has no warehouse field of its own - the range lookup must derive it from
        # the delivery being packed (see numbering.py's _warehouse_of).
        customer = frappe.get_all("Customer", limit=1, pluck="name")[0]
        item = frappe.get_all("Item", filters={"is_stock_item": 1}, limit=1, pluck="name")[0]
        uom = frappe.db.get_value("Item", item, "stock_uom")
        obd = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8),
            "warehouse": self.warehouse, "customer": customer, "delivery_date": frappe.utils.nowdate(),
            "items": [{"line_number": 1, "item": item, "requested_quantity": 1, "stock_uom": uom, "required_stock_type": "AVAILABLE"}]})
        obd.insert(ignore_permissions=True)

        number_range = frappe.get_doc({"doctype": "WMS Number Range", "range_for": "Packing Order", "warehouse": self.warehouse,
            "prefix": "PACKTN-", "number_length": 4, "start_number": 1, "end_number": 99, "active": 1}).insert(ignore_permissions=True)

        order = frappe.get_doc({"doctype": "Packing Order", "outbound_delivery": obd.name, "work_center_bin": self.bulk_bin})
        order.insert(ignore_permissions=True)
        self.assertEqual(order.name, "PACKTN-0001")

        frappe.db.set_value("WMS Number Range", number_range.name, "active", 0)
