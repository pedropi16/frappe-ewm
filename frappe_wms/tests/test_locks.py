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

    def test_delivery_worklist_goes_delivery_by_delivery(self):
        from frappe_wms.services.delivery_worklist import process
        names = []
        for _i in range(2):
            d = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.wh, "customer": frappe.get_all("Customer", limit=1, pluck="name")[0], "delivery_date": frappe.utils.nowdate(),
                                "items": [{"line_number": 1, "item": self.item, "requested_quantity": 1, "stock_uom": frappe.db.get_value("Item", self.item, "stock_uom"), "required_stock_type": "AVAILABLE"}]}).insert(ignore_permissions=True)
            d.submit(); names.append(d.name)
        a, b = self.users
        frappe.set_user(a); locks.acquire("Outbound Delivery", names[0])
        frappe.set_user(b)
        out = process("Outbound Delivery", "change", names, {"priority": "High", "route": ""})
        self.assertEqual([x["name"] for x in out["done"]], [names[1]])
        self.assertIn("being changed by", out["errors"][0]["error"])
        self.assertEqual(frappe.db.get_value("Outbound Delivery", names[1], "priority"), "High")
        self.assertNotEqual(frappe.db.get_value("Outbound Delivery", names[0], "priority"), "High")
        frappe.set_user(a); locks.release("Outbound Delivery", names[0])

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

    def test_task_confirmation_and_packing_moves_respect_locks(self):
        from frappe_wms.services.handling_unit import create_handling_unit
        from frappe_wms.services.packing_center import move_nodes
        from frappe_wms.services.task import confirm_task
        hu_type = frappe.get_all("Handling Unit Type", limit=1, pluck="name")[0]
        hu = create_handling_unit(f"LOCK-{frappe.generate_hash(length=6)}", hu_type, self.bin, warehouse=self.wh)
        hu = hu.name if hasattr(hu, "name") else hu
        self._seed(4, handling_unit=hu)
        tasks = create_adhoc_tasks([{"handling_unit": hu}], self.bin2)
        a, b = self.users
        frappe.set_user(a); locks.acquire("Handling Unit", hu)
        frappe.set_user(b)
        with self.assertRaisesRegex(frappe.ValidationError, "being changed by"):
            confirm_task(tasks[0], verify=False)  # the RF / desk confirmation stops at the lock
        res = move_nodes(self.wh, [{"kind": "hu", "name": hu}], "bin", self.bin2, "lock-test")
        self.assertEqual(res["moved"], 0)
        self.assertIn("being changed by", res["errors"][0]["error"])
        frappe.set_user(a); locks.release("Handling Unit", hu)
        frappe.set_user(b)
        confirm_task(tasks[0], verify=False)
