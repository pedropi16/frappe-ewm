import frappe
from frappe.tests import IntegrationTestCase


class TestUserDefaults(IntegrationTestCase):
    def test_wms_defaults_become_user_defaults(self):
        wh = frappe.get_all("WMS Warehouse", pluck="name", limit=1)[0]
        user = frappe.get_doc("User", "Administrator")
        old = {f: user.get(f) for f in ("wms_default_warehouse", "wms_default_monitor_view")}
        self.addCleanup(lambda: (user.reload(), user.update(old), user.save()))
        user.update({"wms_default_warehouse": wh, "wms_default_monitor_view": "Stock Overview"})
        user.save()
        self.assertEqual(frappe.defaults.get_defaults("Administrator").get("WMS Warehouse"), wh)
        self.assertEqual(frappe.defaults.get_user_default("wms_monitor_view", "Administrator"), "Stock Overview")
        user.update({"wms_default_warehouse": None})
        user.save()
        self.assertFalse(frappe.defaults.get_defaults("Administrator").get("WMS Warehouse"))
