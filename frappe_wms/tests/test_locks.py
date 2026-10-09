import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.api.adhoc import create_adhoc_tasks
from frappe_wms.api.outbound import allocate_delivery
from frappe_wms.services import locks
from frappe_wms.tests import test_stock_adjustments as _base


class TestLocks(_base.TestStockAdjustments):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bin2 = f"{cls.wh}-B2"
        if not frappe.db.exists("Storage Bin", cls.bin2):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": cls.bin2, "warehouse": cls.wh, "storage_type": f"{cls.wh}-ST", "active": 1, "sequence": 2}).insert(ignore_permissions=True)

    def setUp(self):
        super().setUp()
        self.users = []
        for email in ("lock-a@example.com", "lock-b@example.com"):
            if not frappe.db.exists("User", email):
                frappe.get_doc({"doctype": "User", "email": email, "first_name": email[:6], "send_welcome_email": 0, "roles": [{"role": "WMS Operator"}, {"role": "WMS Supervisor"}]}).insert(ignore_permissions=True)
            self.users.append(email)
        self.addCleanup(lambda: [locks.release("Handling Unit", "LOCK-HU", force=True), frappe.set_user("Administrator")])

    def test_a_lock_blocks_others_but_not_the_holder_and_expires_with_release(self):
        a, b = self.users
        frappe.set_user(a)
        locks.acquire("Handling Unit", "LOCK-HU")
        locks.acquire("Handling Unit", "LOCK-HU")  # the holder may re-open it
        self.assertTrue(locks.status("Handling Unit", "LOCK-HU")["mine"])
        frappe.set_user(b)
        self.assertFalse(locks.status("Handling Unit", "LOCK-HU")["mine"])  # displaying is fine
        with self.assertRaisesRegex(frappe.ValidationError, "being changed by lock-a"):
            locks.acquire("Handling Unit", "LOCK-HU")
        with self.assertRaisesRegex(frappe.ValidationError, "being changed by"):
            locks.require_free("Handling Unit", "LOCK-HU")
        locks.release("Handling Unit", "LOCK-HU")  # not the holder: no effect
        self.assertEqual(locks.holder("Handling Unit", "LOCK-HU")["user"], a)
        frappe.set_user(a)
        locks.release("Handling Unit", "LOCK-HU")
        frappe.set_user(b)
        locks.acquire("Handling Unit", "LOCK-HU")

    def test_all_or_nothing(self):
        a, b = self.users
        frappe.set_user(b); locks.acquire("Handling Unit", "LOCK-HU")
        frappe.set_user(a)
        with self.assertRaises(frappe.ValidationError):
            locks.acquire_many([("WMS Stock Balance", "LOCK-FREE"), ("Handling Unit", "LOCK-HU")])
        self.assertIsNone(locks.holder("WMS Stock Balance", "LOCK-FREE"))

    def test_locked_objects_cannot_be_changed_through_the_apis(self):
        self._seed(5)
        line = self._line()
        a, b = self.users
        frappe.set_user(a)
        locks.acquire("WMS Stock Balance", line.name)
        frappe.set_user(b)
        with self.assertRaisesRegex(frappe.ValidationError, "being changed by"):
            create_adhoc_tasks([{"name": line.name}], self.bin2)
        with self.assertRaisesRegex(frappe.ValidationError, "being changed by"):
            locks.require_free("WMS Stock Balance", line.name)
        frappe.set_user(a)
        locks.release("WMS Stock Balance", line.name)

    def test_delivery_actions_are_guarded(self):
        a, b = self.users
        frappe.set_user(a); locks.acquire("Outbound Delivery", "LOCK-OBD")
        frappe.set_user(b)
        with self.assertRaisesRegex(frappe.ValidationError, "being changed by"):
            allocate_delivery("LOCK-OBD")
        frappe.set_user(a); locks.release("Outbound Delivery", "LOCK-OBD")

    def test_form_saves_are_refused_but_service_saves_are_not(self):
        self._seed(5)
        doc = frappe.get_doc({"doctype": "WMS Stock Adjustment", "warehouse": self.wh, "adjustment_type": "Scrapping", "product": self.item, "storage_bin": self.bin, "quantity": 1, "reason": "lock test"}).insert(ignore_permissions=True)
        a, b = self.users
        frappe.set_user(a); locks.acquire("WMS Stock Adjustment", doc.name)
        frappe.set_user(b)
        doc.reload(); doc.reason = "changed"
        doc.save(ignore_permissions=True)  # a service-style save is not blocked
        frappe.local.form_dict["cmd"] = "frappe.desk.form.save.savedocs"
        self.addCleanup(lambda: frappe.local.form_dict.pop("cmd", None))
        with self.assertRaisesRegex(frappe.ValidationError, "being changed by"):
            doc.save(ignore_permissions=True)
        frappe.set_user(a); locks.release("WMS Stock Adjustment", doc.name)
