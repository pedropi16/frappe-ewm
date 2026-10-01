import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.api.selection import execute_selection
from frappe_wms.tests.bootstrap import TEST_CUSTOMER, TEST_ITEM, TEST_SUPPLIER


class TestWarehouseScoping(IntegrationTestCase):
    """A User Permission on WMS Warehouse limits a user to that warehouse everywhere."""

    def test_user_permission_scopes_documents_lists_and_monitor(self):
        frappe.set_user("Administrator")
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        whs = []
        for _ in range(2):
            wh = f"SCOPE-{frappe.generate_hash(length=5).upper()}"
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": wh, "warehouse_name": wh, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
            frappe.get_doc({"doctype": "Storage Type", "warehouse": wh, "storage_type_code": "GR", "storage_type_name": "GR", "storage_role": "Receiving",
                            "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
            b = frappe.get_doc({"doctype": "Storage Bin", "bin_code": f"{wh}-B", "warehouse": wh, "storage_type": f"{wh}-GR", "active": 1, "sequence": 1}).insert(ignore_permissions=True).name
            frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": wh, "supplier": TEST_SUPPLIER,
                            "receiving_bin": b, "items": [{"line_number": 1, "item": TEST_ITEM, "expected_quantity": 1, "stock_uom": "Nos", "expected_stock_type": "AVAILABLE"}]}).insert(ignore_permissions=True)
            frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": wh, "customer": TEST_CUSTOMER,
                            "delivery_date": frappe.utils.nowdate(), "items": [{"line_number": 1, "item": TEST_ITEM, "requested_quantity": 1, "stock_uom": "Nos", "required_stock_type": "AVAILABLE"}]}).insert(ignore_permissions=True)
            if not frappe.db.exists("Handling Unit Type", "SCOPE-PAL"):
                frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "SCOPE-PAL", "hu_type_name": "Scope Pallet"}).insert(ignore_permissions=True)
            frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "SCOPE-PAL", "warehouse": wh, "current_bin": b}).insert(ignore_permissions=True)
            whs.append(wh)
        mine, other = whs
        user = f"scope-{frappe.generate_hash(length=6)}@example.test"
        u = frappe.get_doc({"doctype": "User", "email": user, "first_name": "Scope", "send_welcome_email": 0}).insert(ignore_permissions=True)
        u.add_roles("WMS Supervisor")
        frappe.get_doc({"doctype": "User Permission", "user": user, "allow": "WMS Warehouse", "for_value": mine, "apply_to_all_doctypes": 1}).insert(ignore_permissions=True)

        frappe.set_user(user)
        try:
            for doctype in ("Inbound Delivery", "Outbound Delivery", "Handling Unit", "Storage Bin"):
                seen = set(frappe.get_list(doctype, filters={"warehouse": ["in", whs]}, pluck="warehouse"))
                self.assertEqual(seen, {mine}, doctype)
            other_delivery = frappe.db.get_value("Outbound Delivery", {"warehouse": other})
            self.assertFalse(frappe.has_permission("Outbound Delivery", "read", other_delivery))
            self.assertEqual(len(execute_selection("inbound", mine)["rows"]), 1)
            with self.assertRaises(frappe.PermissionError):
                execute_selection("inbound", other)
        finally:
            frappe.set_user("Administrator")
