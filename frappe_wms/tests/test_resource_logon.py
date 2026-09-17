import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.api.resource import list_available_resources, log_on, log_off, kick


class TestResourceLogon(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.company = frappe.get_all("Company", limit=1, pluck="name")[0]
        cls.warehouse = f"WMS-TEST-LOGON-{frappe.generate_hash(length=6).upper()}"
        frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse,
            "company": cls.company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        cls.picker_email = "logon-picker@example.com"
        cls.picker2_email = "logon-picker2@example.com"
        cls.supervisor_email = "logon-supervisor@example.com"
        for email, roles in ((cls.picker_email, ["WMS Picker"]), (cls.picker2_email, ["WMS Picker"]), (cls.supervisor_email, ["WMS Supervisor"])):
            if not frappe.db.exists("User", email):
                frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0}).insert(ignore_permissions=True)
                frappe.get_doc("User", email).add_roles(*roles)

    def _new_resource(self, resource_type="Scanner"):
        resource = frappe.get_doc({"doctype": "WMS Resource", "resource_code": frappe.generate_hash(length=8),
            "warehouse": self.warehouse, "resource_type": resource_type, "active": 1})
        resource.insert(ignore_permissions=True)
        return resource.name

    def test_log_on_claims_a_free_resource_and_appears_in_available_list_only_before(self):
        resource = self._new_resource()
        self.assertTrue(any(r["name"] == resource for r in list_available_resources(self.warehouse)))

        frappe.set_user(self.picker_email)
        try:
            result = log_on(resource)
            self.assertEqual(result["resource"], resource)
        finally:
            frappe.set_user("Administrator")

        self.assertEqual(frappe.db.get_value("WMS Resource", resource, "user"), self.picker_email)
        self.assertIsNotNone(frappe.db.get_value("WMS Resource", resource, "logged_in_at"))
        self.assertFalse(any(r["name"] == resource for r in list_available_resources(self.warehouse)))

    def test_log_on_rejects_a_resource_already_claimed_by_someone_else(self):
        resource = self._new_resource()
        frappe.set_user(self.picker_email)
        try:
            log_on(resource)
        finally:
            frappe.set_user("Administrator")

        frappe.set_user(self.picker2_email)
        try:
            with self.assertRaises(frappe.ValidationError):
                log_on(resource)
        finally:
            frappe.set_user("Administrator")

    def test_log_on_to_a_new_resource_transfers_off_the_old_one(self):
        # A user is only ever logged on to one Resource at a time.
        first = self._new_resource()
        second = self._new_resource()
        frappe.set_user(self.picker_email)
        try:
            log_on(first)
            log_on(second)
        finally:
            frappe.set_user("Administrator")

        self.assertIsNone(frappe.db.get_value("WMS Resource", first, "user"))
        self.assertEqual(frappe.db.get_value("WMS Resource", second, "user"), self.picker_email)

    def test_log_off_frees_the_resource(self):
        resource = self._new_resource()
        frappe.set_user(self.picker_email)
        try:
            log_on(resource)
            result = log_off()
            self.assertEqual(result["resource"], resource)
        finally:
            frappe.set_user("Administrator")

        self.assertIsNone(frappe.db.get_value("WMS Resource", resource, "user"))
        self.assertIsNone(frappe.db.get_value("WMS Resource", resource, "logged_in_at"))

    def test_kick_requires_supervisor_and_frees_the_resource_regardless_of_who_is_on_it(self):
        resource = self._new_resource()
        frappe.set_user(self.picker_email)
        try:
            log_on(resource)
        finally:
            frappe.set_user("Administrator")

        frappe.set_user(self.picker2_email)
        try:
            with self.assertRaises(frappe.PermissionError):
                kick(resource)
        finally:
            frappe.set_user("Administrator")
        self.assertEqual(frappe.db.get_value("WMS Resource", resource, "user"), self.picker_email)

        frappe.set_user(self.supervisor_email)
        try:
            result = kick(resource, reason="Shift ended")
            self.assertEqual(result["kicked_user"], self.picker_email)
        finally:
            frappe.set_user("Administrator")
        self.assertIsNone(frappe.db.get_value("WMS Resource", resource, "user"))
