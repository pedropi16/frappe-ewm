import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.services import alerts


class TestAlertDigest(IntegrationTestCase):
    def test_digest_notifies_supervisors_once_per_change(self):
        frappe.set_user("Administrator")
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        wh = f"WMS-TEST-ALR-{frappe.generate_hash(length=5).upper()}"
        frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": wh, "warehouse_name": wh, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        others = frappe.get_all("WMS Warehouse", filters={"active": 1, "name": ["!=", wh]}, pluck="name")
        frappe.db.set_value("WMS Warehouse", {"name": ["in", others or [""]]}, "active", 0)
        settings = frappe.get_single("WMS Settings")
        before = (settings.alert_notifications, settings.alert_recipients)
        frappe.db.set_single_value("WMS Settings", {"alert_notifications": "Desk Notification", "alert_recipients": "Administrator"})
        try:
            original = alerts.warehouse_counts
            counts = {"erp_sync_problems": 2, "negative_quants": 0, "aged_exceptions": 1, "unplanned_requests": 0, "stalled_warehouse_orders": 0, "pending_approval_counts": 0}
            alerts.warehouse_counts = lambda w: dict(counts)
            frappe.cache.delete_value(alerts.CACHE_KEY.format(wh))
            logs = lambda: frappe.db.count("Notification Log", {"for_user": "Administrator", "document_type": "WMS Warehouse", "document_name": wh})  # noqa: E731
            alerts.send_alert_digest()
            self.assertEqual(logs(), 1)
            self.assertIn("ERPNext postings not yet done (2)", frappe.get_all("Notification Log", filters={"document_name": wh}, pluck="subject")[0])
            alerts.send_alert_digest()
            self.assertEqual(logs(), 1, "the same problems are not repeated every hour")
            counts["negative_quants"] = 1
            alerts.send_alert_digest()
            self.assertEqual(logs(), 2)
        finally:
            alerts.warehouse_counts = original
            frappe.db.set_single_value("WMS Settings", {"alert_notifications": before[0] or "Off", "alert_recipients": before[1]})
            frappe.db.set_value("WMS Warehouse", {"name": ["in", others or [""]]}, "active", 1)

    def test_real_counts_run(self):
        frappe.set_user("Administrator")
        wh = frappe.get_all("WMS Warehouse", limit=1, pluck="name")[0]
        self.assertEqual(set(alerts.warehouse_counts(wh)), set(alerts.CHECKS))
