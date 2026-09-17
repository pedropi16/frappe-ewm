import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.api.monitor import resource_workload, search_queues


class TestResourceGroups(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.company = frappe.get_all("Company", limit=1, pluck="name")[0]
        cls.warehouse = f"WMS-TEST-RESGROUP-{frappe.generate_hash(length=6).upper()}"
        frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse,
            "company": cls.company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)

    def test_resource_group_links_a_queue_to_its_pool_of_physical_resources(self):
        # Mirrors SAP EWM: a Warehouse Queue routes work to a Resource Group, not to one
        # physical device directly - any active Resource in that group can pick it up.
        group = frappe.get_doc({"doctype": "WMS Resource Group", "group_code": f"{self.warehouse}-RF",
            "group_name": "RF Scanners", "warehouse": self.warehouse, "active": 1})
        group.insert(ignore_permissions=True)

        queue = frappe.get_doc({"doctype": "Warehouse Queue", "queue_code": f"{self.warehouse}-Q1", "queue_name": "Q1",
            "warehouse": self.warehouse, "activity": "Pick", "resource_group": group.name, "active": 1})
        queue.insert(ignore_permissions=True)
        self.assertEqual(frappe.db.get_value("Warehouse Queue", queue.name, "resource_group"), group.name)

        resource = frappe.get_doc({"doctype": "WMS Resource", "resource_code": frappe.generate_hash(length=8),
            "warehouse": self.warehouse, "resource_type": "Scanner", "resource_group": group.name,
            "device_id": "RF-GUN-001", "active": 1})
        resource.insert(ignore_permissions=True)
        self.assertEqual(frappe.db.get_value("WMS Resource", resource.name, "resource_group"), group.name)

        rows = search_queues(self.warehouse)
        match = [r for r in rows if r["name"] == queue.name]
        self.assertTrue(match)
        self.assertEqual(match[0]["resource_group"], group.name)

        workload = resource_workload(self.warehouse)
        match = [r for r in workload if r["name"] == resource.name]
        self.assertTrue(match)
        self.assertEqual(match[0]["resource_group"], group.name)

    def test_a_resource_can_be_a_device_type_like_printer(self):
        # "Each Resource is a physical device" - a printer is as much a Resource as a
        # scanner or a forklift, not a special case.
        resource = frappe.get_doc({"doctype": "WMS Resource", "resource_code": frappe.generate_hash(length=8),
            "warehouse": self.warehouse, "resource_type": "Printer", "device_id": "LABEL-PRINTER-01", "active": 1})
        resource.insert(ignore_permissions=True)
        self.assertEqual(resource.resource_type, "Printer")
