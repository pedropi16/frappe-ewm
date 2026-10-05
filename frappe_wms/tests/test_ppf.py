import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.services.ppf import run_scheduled_actions


def mark_done(doc, params):
    frappe.flags.ppf_marker = f"{doc.name}:{params.get('tag')}"
    return "marked"


def boom(doc, params):
    raise ValueError("this action always fails")


class TestPPF(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.wh = "WMS-TEST-PPF-WH"
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, order_by="creation asc", limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        if not frappe.db.exists("WMS Warehouse", cls.wh):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.wh, "warehouse_name": cls.wh, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        cls.st = f"{cls.wh}-ST"
        if not frappe.db.exists("Storage Type", cls.st):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.wh, "storage_type_code": "ST", "storage_type_name": "ST", "storage_role": "Storage", "capacity_check_method": "None", "active": 1, "hu_requirement": "Optional"}).insert(ignore_permissions=True)
        cls.bins = []
        for n in (1, 2):
            name = f"{cls.wh}-B{n}"
            cls.bins.append(name)
            if not frappe.db.exists("Storage Bin", name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": name, "warehouse": cls.wh, "storage_type": cls.st, "active": 1, "sequence": n}).insert(ignore_permissions=True)

    def _profile(self, *actions):
        code = f"PPF-{frappe.generate_hash(length=6)}"
        doc = frappe.get_doc({"doctype": "PPF Action Profile", "profile_code": code, "profile_name": code, "reference_doctype": "Warehouse Request", "active": 1,
            "actions": [{"sequence": i * 10, "active": 1, **a} for i, a in enumerate(actions, 1)]}).insert(ignore_permissions=True)
        self.addCleanup(lambda: doc.db_set("active", 0) or frappe.cache.delete_value("wms_ppf_doctypes"))
        frappe.cache.delete_value("wms_ppf_doctypes")
        return doc

    def _request(self, **kw):
        return frappe.get_doc({"doctype": "Warehouse Request", "request_type": "Internal Move", "warehouse": self.wh, "product": self.item, "requested_quantity": 1, "stock_uom": self.uom,
            "source_bin": self.bins[0], "destination_bin": self.bins[1], "stock_type": "AVAILABLE", "reference_doctype": "User", "reference_name": "Administrator",
            "process_type": "INTERNAL_MOVE", "priority": "Normal", "status": "Open", **kw})

    def _log(self, profile):
        return frappe.get_all("PPF Action Log", filters={"profile": profile.name}, fields=["action_type", "status", "event", "message"], order_by="creation asc")

    def test_after_insert_actions_run_when_their_condition_holds_and_are_logged(self):
        profile = self._profile(
            {"event": "After Insert", "condition": "doc.priority == 'High'", "action_type": "Set Field", "parameters": '{"field": "process_step", "value": "x"}'},
            {"event": "After Insert", "condition": "doc.priority == 'High'", "action_type": "Create ToDo", "parameters": '{"allocated_to": "Administrator", "description": "Look at {{ doc.name }}"}'})
        quiet = self._request(priority="Normal").insert(ignore_permissions=True)
        self.assertEqual(self._log(profile), [], "condition false: nothing runs")
        loud = self._request(priority="High").insert(ignore_permissions=True)
        self.assertEqual([(l.action_type, l.status) for l in self._log(profile)], [("Set Field", "Success"), ("Create ToDo", "Success")])
        self.assertTrue(frappe.db.exists("ToDo", {"reference_type": "Warehouse Request", "reference_name": loud.name}))
        self.assertTrue(quiet.name)

    def test_create_tasks_action_plans_a_request_and_execute_once_stops_a_repeat(self):
        profile = self._profile({"event": "After Insert", "action_type": "Create Tasks"})
        request = self._request().insert(ignore_permissions=True)
        self.assertTrue(frappe.db.exists("Warehouse Task", {"warehouse_request": request.name}))
        self.assertEqual([l.status for l in self._log(profile)], ["Success"])

    def test_a_failing_action_is_logged_and_does_not_undo_the_document(self):
        profile = self._profile({"event": "After Insert", "action_type": "Call Method", "parameters": '{"method": "Boom"}'},
            {"event": "After Insert", "action_type": "Call Method", "parameters": '{"method": "Marker", "tag": "t1"}'})
        from unittest.mock import patch
        real = frappe.get_hooks
        registry = {"wms_ppf_actions": {"Boom": ["frappe_wms.tests.test_ppf.boom"], "Marker": ["frappe_wms.tests.test_ppf.mark_done"]}}
        with patch("frappe.get_hooks", side_effect=lambda name=None, *a, **k: registry.get(name, real(name, *a, **k))):
            request = self._request().insert(ignore_permissions=True)
        self.assertTrue(frappe.db.exists("Warehouse Request", request.name))
        self.assertEqual([(l.status) for l in self._log(profile)], ["Failed", "Success"], "the second action still ran")
        self.assertEqual(frappe.flags.ppf_marker, f"{request.name}:t1")

    def test_status_change_actions_run_only_on_the_matching_transition(self):
        profile = self._profile({"event": "Status Change", "status_field": "status", "status_value": "Cancelled", "action_type": "Create ToDo", "parameters": '{"allocated_to": "Administrator"}'})
        request = self._request().insert(ignore_permissions=True)
        request.priority = "High"; request.save(ignore_permissions=True)
        self.assertEqual(self._log(profile), [])
        request.status = "Cancelled"; request.save(ignore_permissions=True)
        self.assertEqual([l.status for l in self._log(profile)], ["Success"])

    def test_scheduled_actions_run_for_the_documents_their_filters_select_once(self):
        profile = self._profile({"event": "Scheduled", "action_type": "Set Field", "parameters": '{"field": "process_step", "value": "swept", "filters": {"warehouse": "%s", "priority": "Urgent"}}' % self.wh})
        request = self._request(priority="Urgent").insert(ignore_permissions=True)
        run_scheduled_actions(); run_scheduled_actions()
        self.assertEqual([l.status for l in self._log(profile)], ["Success"], "once per document")
        self.assertEqual(frappe.db.get_value("Warehouse Request", request.name, "process_step"), "swept")
