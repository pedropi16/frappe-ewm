from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.services import erp_sync_queue
from frappe_wms.tests.bootstrap import TEST_ITEM, TEST_SUPPLIER, company_warehouse


class TestErpSyncQueue(IntegrationTestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        wh = f"SYNCQ-{frappe.generate_hash(length=5).upper()}"
        frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": wh, "warehouse_name": wh, "company": company,
                        "erpnext_warehouse": company_warehouse(company, wh), "default_stock_type": "AVAILABLE",
                        "erp_sync_mode": "Queued with Retry"}).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "Storage Type", "warehouse": wh, "storage_type_code": "GR", "storage_type_name": "GR", "storage_role": "Receiving",
                        "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        self.recv = frappe.get_doc({"doctype": "Storage Bin", "bin_code": f"{wh}-RECV", "warehouse": wh, "storage_type": f"{wh}-GR",
                                    "active": 1, "sequence": 1}).insert(ignore_permissions=True).name
        if not frappe.db.exists("Handling Unit Type", "SYNCQ-PAL"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "SYNCQ-PAL", "hu_type_name": "Sync Queue Pallet"}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", TEST_ITEM):
            frappe.get_doc({"doctype": "WMS Product", "item": TEST_ITEM, "stock_uom": "Nos", "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        self.wh = wh

    def receipt(self, qty=3):
        uom = frappe.db.get_value("Item", TEST_ITEM, "stock_uom")
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "SYNCQ-PAL", "warehouse": self.wh,
                             "current_bin": self.recv, "status": "Open"}).insert(ignore_permissions=True)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.wh,
                              "supplier": TEST_SUPPLIER, "receiving_bin": self.recv,
                              "items": [{"line_number": 1, "item": TEST_ITEM, "expected_quantity": qty, "stock_uom": uom, "expected_stock_type": "AVAILABLE"}]}).insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.wh, "receiving_bin": self.recv,
                             "items": [{"inbound_delivery_item": ind.items[0].name, "item": TEST_ITEM, "quantity": qty, "stock_uom": uom,
                                        "handling_unit": hu.name, "stock_type": "AVAILABLE"}]}).insert(ignore_permissions=True)
        gr.submit()
        return gr

    def log_for(self, doc, operation):
        return frappe.get_doc("WMS ERP Sync Log", {"reference_name": doc.name, "operation": operation})

    def test_queued_posting_runs_after_the_warehouse_posting(self):
        gr = self.receipt()
        self.assertFalse(frappe.db.get_value("Goods Receipt", gr.name, "erpnext_stock_entry"), "nothing posted to ERPNext inside the transaction")
        self.assertTrue(frappe.db.exists("WMS Stock Ledger Entry", {"reference_name": gr.name}), "the warehouse posting is done")
        log = self.log_for(gr, "goods_receipt")
        self.assertEqual(log.status, "Queued")
        self.assertEqual(erp_sync_queue.run(log.name), "Done")
        se = frappe.db.get_value("Goods Receipt", gr.name, "erpnext_stock_entry")
        self.assertEqual(frappe.db.get_value("Stock Entry", se, "docstatus"), 1)

    def test_failure_is_recorded_and_retried(self):
        gr = self.receipt()
        log = self.log_for(gr, "goods_receipt")
        with patch.dict(erp_sync_queue.OPERATIONS, {"goods_receipt": lambda doc: frappe.throw("Accounting period closed")}):
            self.assertEqual(erp_sync_queue.run(log.name), "Failed")
        log.reload()
        self.assertEqual((log.status, log.attempts), ("Failed", 1))
        self.assertIn("Accounting period closed", log.last_error)
        self.assertTrue(log.next_retry_at)
        self.assertFalse(frappe.db.get_value("Goods Receipt", gr.name, "erpnext_stock_entry"), "the failed attempt left nothing behind")
        self.assertIn(log.name, [p.name for p in erp_sync_queue.open_problems(self.wh)])
        self.assertEqual(erp_sync_queue.retry_now(log.name), "Done")
        self.assertTrue(frappe.db.get_value("Goods Receipt", gr.name, "erpnext_stock_entry"))

    def test_reversal_before_the_posting_ran_cancels_it(self):
        gr = self.receipt()
        frappe.get_doc("Goods Receipt", gr.name).cancel()
        self.assertEqual(self.log_for(gr, "goods_receipt").status, "Cancelled")
        self.assertFalse(frappe.db.exists("WMS ERP Sync Log", {"reference_name": gr.name, "operation": "goods_receipt_reversal"}))
