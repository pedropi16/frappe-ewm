from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.services.determination import determine_destination_bin, get_putaway_strategies


def last_bin_first(bins, context):
    return sorted(bins, key=lambda b: -(b.sequence or 0))


def keep_alpha(balances, context):
    return sorted(balances, key=lambda b: b.storage_bin)


def group_by_marker(task):
    return "XTRA"


def force_queue(task, queue):
    return queue


class TestExtensionHooks(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.wh = "WMS-TEST-HOOK-WH"
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, order_by="creation asc", limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        if not frappe.db.exists("WMS Warehouse", cls.wh):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.wh, "warehouse_name": cls.wh, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        cls.st = f"{cls.wh}-ST"
        if not frappe.db.exists("Storage Type", cls.st):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.wh, "storage_type_code": "ST", "storage_type_name": "ST", "storage_role": "Storage", "capacity_check_method": "None", "active": 1, "hu_requirement": "Optional"}).insert(ignore_permissions=True)
        cls.bins = []
        for seq in (1, 2, 3):
            name = f"{cls.wh}-B{seq}"
            cls.bins.append(name)
            if not frappe.db.exists("Storage Bin", name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": name, "warehouse": cls.wh, "storage_type": cls.st, "active": 1, "sequence": seq}).insert(ignore_permissions=True)

    def _hooks(self, **registered):
        real = frappe.get_hooks
        return patch("frappe.get_hooks", side_effect=lambda name=None, *a, **k: registered.get(name, real(name, *a, **k)))

    def test_a_registered_putaway_strategy_chooses_the_bin_for_a_rule_with_strategy_custom(self):
        path = "frappe_wms.tests.test_extension_hooks.last_bin_first"
        with self._hooks(wms_putaway_strategies={"Last first": [path]}):
            self.assertIn("Last first", get_putaway_strategies())
            rule = frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": self.wh, "activity": "Putaway", "active": 1, "priority": 1, "destination_storage_type": self.st, "strategy": "Custom", "custom_strategy": "Last first"}).insert(ignore_permissions=True)
            self.addCleanup(lambda: rule.db_set("active", 0))
            self.assertEqual(determine_destination_bin({"warehouse": self.wh, "activity": "Putaway", "item": self.item, "stock_type": "AVAILABLE", "destination_hu": "X"}), self.bins[-1])
        with self.assertRaises(frappe.ValidationError):  # not registered any more
            frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": self.wh, "activity": "Putaway", "active": 1, "priority": 2, "destination_storage_type": self.st, "strategy": "Custom", "custom_strategy": "Last first"}).insert(ignore_permissions=True)

    def test_a_registered_removal_strategy_is_usable_from_a_removal_rule(self):
        from frappe_wms.services.removal_rules import apply_strategy
        with self._hooks(wms_removal_strategies={"Alpha": ["frappe_wms.tests.test_extension_hooks.keep_alpha"]}):
            out = apply_strategy("Custom", [frappe._dict(storage_bin="B"), frappe._dict(storage_bin="A")], custom_strategy="Alpha")
            self.assertEqual([b.storage_bin for b in out], ["A", "B"])

    def test_wocr_group_key_and_queue_override_hooks_are_applied_when_a_task_is_attached(self):
        from frappe_wms.services.warehouse_order import attach_task
        with self._hooks(wms_wocr_group_key=["frappe_wms.tests.test_extension_hooks.group_by_marker"], wms_queue_override=["frappe_wms.tests.test_extension_hooks.force_queue"]):
            task = frappe.get_doc({"doctype": "Warehouse Task", "task_type": "Internal Move", "warehouse": self.wh, "product": self.item, "planned_quantity": 1, "stock_uom": self.uom, "source_bin": self.bins[0],
                "destination_bin": self.bins[1], "stock_type_from": "AVAILABLE", "stock_type_to": "AVAILABLE", "movement_type": "301", "priority": "Normal", "status": "Open"})
            attach_task(task, "KEY1")
            self.assertTrue(frappe.db.get_value("Warehouse Order", task.warehouse_order, "batch_key").endswith("~XTRA"))
