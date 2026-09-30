import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.api.inbound import receiving_worklist
from frappe_wms.tests.bootstrap import TEST_ITEM, TEST_SUPPLIER


class TestReceivingWorklist(IntegrationTestCase):
    def test_worklist_lines_barcodes_and_capture_rules(self):
        frappe.set_user("Administrator")
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        wh = f"WMS-TEST-RWL-{frappe.generate_hash(length=5).upper()}"
        frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": wh, "warehouse_name": wh, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "Storage Type", "warehouse": wh, "storage_type_code": "GR", "storage_type_name": "GR", "storage_role": "Receiving",
                        "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        recv = frappe.get_doc({"doctype": "Storage Bin", "bin_code": f"{wh}-RECV", "warehouse": wh, "storage_type": f"{wh}-GR", "active": 1, "sequence": 1}).insert(ignore_permissions=True).name
        uom = frappe.db.get_value("Item", TEST_ITEM, "stock_uom")
        if not frappe.db.exists("WMS Product", TEST_ITEM):
            frappe.get_doc({"doctype": "WMS Product", "item": TEST_ITEM, "stock_uom": uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        barcode = "20" + frappe.generate_hash(length=10)
        item = frappe.get_doc("Item", TEST_ITEM)
        item.append("barcodes", {"barcode": barcode})
        item.save(ignore_permissions=True)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": wh, "supplier": TEST_SUPPLIER, "receiving_bin": recv,
            "items": [{"line_number": 1, "item": TEST_ITEM, "expected_quantity": 5, "stock_uom": uom, "expected_stock_type": "AVAILABLE"},
                      {"line_number": 2, "item": TEST_ITEM, "expected_quantity": 2, "received_quantity": 2, "stock_uom": uom, "expected_stock_type": "AVAILABLE"}]}).insert(ignore_permissions=True)

        wl = receiving_worklist(ind.name)
        self.assertEqual(len(wl["lines"]), 1, "fully received lines are left out")
        line = wl["lines"][0]
        self.assertEqual((line["item"], line["remaining"]), (TEST_ITEM, 5))
        self.assertIn(barcode, line["barcodes"])
        self.assertFalse(line["batch_required"] or line["serial_required"])

        product = frappe.get_doc("WMS Product", TEST_ITEM)
        before = (product.batch_control, product.serial_control)
        try:
            product.db_set({"batch_control": 1, "serial_control": "Required at Receipt"})
            frappe.clear_document_cache("WMS Product", TEST_ITEM)
            line = receiving_worklist(ind.name)["lines"][0]
            self.assertTrue(line["batch_required"] and line["serial_required"])
        finally:
            product.db_set({"batch_control": before[0], "serial_control": before[1]})
