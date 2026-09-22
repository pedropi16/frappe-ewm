import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.services.receipt import create_putaway_requests
from frappe_wms.services.task import create_tasks_for_request
from frappe_wms.api.scanner import confirm_task


class TestStorageProcess(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "WMS-TEST-SP-WH"
        cls.recv_bin = "WMS-TEST-SP-WH-RECV"
        cls.qc_bin = "WMS-TEST-SP-WH-QC"
        cls.bulk_bin = "WMS-TEST-SP-WH-BULK"
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        cls.supplier = frappe.get_all("Supplier", limit=1, pluck="name")[0]
        company = frappe.get_all("Company", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-GR"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "GR", "storage_type_name": "Receiving", "storage_role": "Receiving", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-QC"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "QC", "storage_type_name": "Quality Staging", "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-BULK"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "BULK", "storage_type_name": "Bulk", "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for bin_name, storage_type in ((cls.recv_bin, f"{cls.warehouse}-GR"), (cls.qc_bin, f"{cls.warehouse}-QC"), (cls.bulk_bin, f"{cls.warehouse}-BULK")):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": storage_type, "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        wh = frappe.get_doc("WMS Warehouse", cls.warehouse)
        if not wh.default_receiving_bin:
            wh.default_receiving_bin = cls.recv_bin
            wh.save(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "TEST-SP-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "TEST-SP-PALLET", "hu_type_name": "Test SP Pallet"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Bin Determination Rule", {"warehouse": cls.warehouse, "activity": "Putaway"}):
            frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": cls.warehouse, "activity": "Putaway", "active": 1, "priority": 1,
                "destination_storage_type": f"{cls.warehouse}-QC", "strategy": "Bin Sequence"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Bin Determination Rule", {"warehouse": cls.warehouse, "activity": "Internal Move"}):
            frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": cls.warehouse, "activity": "Internal Move", "active": 1, "priority": 1,
                "destination_storage_type": f"{cls.warehouse}-BULK", "strategy": "Bin Sequence"}).insert(ignore_permissions=True)
        for code, activity, movement in (("TEST-SP-STEP1", "Putaway", "201"), ("TEST-SP-STEP2", "Internal Move", "301")):
            if not frappe.db.exists("Warehouse Process Type", code):
                frappe.get_doc({"doctype": "Warehouse Process Type", "process_type_code": code, "process_type_name": code, "activity": activity,
                    "source_required": 1, "destination_required": 1, "stock_required": 1, "confirmation_mode": "Handling Unit",
                    "movement_type": movement, "active": 1}).insert(ignore_permissions=True)

    def tearDown(self):
        for existing in frappe.get_all("Process Determination Rule", filters={"warehouse": self.warehouse}, pluck="name"):
            frappe.delete_doc("Process Determination Rule", existing, force=True, ignore_permissions=True)

    def _make_hu(self):
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "TEST-SP-PALLET", "warehouse": self.warehouse, "current_bin": self.recv_bin, "status": "Open"})
        hu.insert(ignore_permissions=True)
        return hu.name

    def _submit_gr(self, hu, qty=20):
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.recv_bin,
            "items": [{"line_number": 1, "item": self.item, "expected_quantity": qty, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": self.recv_bin,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": self.item, "quantity": qty, "stock_uom": self.uom, "handling_unit": hu, "stock_type": "AVAILABLE"}]})
        gr.insert(ignore_permissions=True)
        gr.submit()
        return gr

    def test_two_step_process_chains_automatically_on_confirmation(self):
        frappe.get_doc({"doctype": "Storage Process", "process_code": "TEST-SP-CHAIN", "process_name": "Test Chain", "process_category": "Inbound",
            "warehouse": self.warehouse, "active": 1, "steps": [
                {"sequence": 1, "step_code": "STEP1", "process_type": "TEST-SP-STEP1", "create_task_automatically": 1, "next_step_on_confirmation": 1},
                {"sequence": 2, "step_code": "STEP2", "process_type": "TEST-SP-STEP2", "create_task_automatically": 1, "next_step_on_confirmation": 1},
            ]}).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "Process Determination Rule", "priority": 1, "warehouse": self.warehouse, "document_type": "Inbound Delivery",
            "storage_process": "TEST-SP-CHAIN", "active": 1}).insert(ignore_permissions=True)

        hu = self._make_hu()
        gr = self._submit_gr(hu)
        request_names = create_putaway_requests(gr.name)
        self.assertEqual(len(request_names), 1)
        request = frappe.get_doc("Warehouse Request", request_names[0])
        self.assertEqual(request.storage_process, "TEST-SP-CHAIN")
        self.assertEqual(request.process_step, "STEP1")
        self.assertEqual(request.process_type, "TEST-SP-STEP1")

        task1_name = create_tasks_for_request(request.name)
        task1 = frappe.get_doc("Warehouse Task", task1_name)
        self.assertEqual(task1.task_type, "Putaway")
        self.assertEqual(task1.destination_bin, self.qc_bin)

        confirm_task(task1_name, confirmed_quantity=20)

        request.reload()
        self.assertEqual(request.process_step, "STEP2")
        chained = frappe.get_all("Warehouse Task", filters={"warehouse_request": request.name, "predecessor_task": task1_name}, fields=["name", "task_type", "source_bin", "destination_bin", "planned_quantity", "status"])
        self.assertEqual(len(chained), 1)
        task2 = chained[0]
        self.assertEqual(task2.task_type, "Internal Move")
        self.assertEqual(task2.source_bin, self.qc_bin)
        self.assertEqual(task2.destination_bin, self.bulk_bin)
        self.assertEqual(task2.planned_quantity, 20)
        self.assertNotEqual(task2.status, "On Hold", "predecessor already confirmed by the time this step was created")

        confirm_task(task2.name, confirmed_quantity=20)
        request.reload()
        self.assertEqual(request.status, "Completed")

        bulk_balance = frappe.get_all("WMS Stock Balance", filters={"product": self.item, "storage_bin": self.bulk_bin, "handling_unit": hu}, fields=["quantity"])
        self.assertEqual(len(bulk_balance), 1)
        self.assertEqual(bulk_balance[0].quantity, 20)

    def test_step_without_auto_create_stops_the_chain(self):
        frappe.get_doc({"doctype": "Storage Process", "process_code": "TEST-SP-MANUAL", "process_name": "Test Manual Stop", "process_category": "Inbound",
            "warehouse": self.warehouse, "active": 1, "steps": [
                {"sequence": 1, "step_code": "STEP1", "process_type": "TEST-SP-STEP1", "create_task_automatically": 1, "next_step_on_confirmation": 0},
                {"sequence": 2, "step_code": "STEP2", "process_type": "TEST-SP-STEP2", "create_task_automatically": 1, "next_step_on_confirmation": 1},
            ]}).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "Process Determination Rule", "priority": 1, "warehouse": self.warehouse, "document_type": "Inbound Delivery",
            "storage_process": "TEST-SP-MANUAL", "active": 1}).insert(ignore_permissions=True)

        hu = self._make_hu()
        gr = self._submit_gr(hu)
        request_names = create_putaway_requests(gr.name)
        request = frappe.get_doc("Warehouse Request", request_names[0])
        task1_name = create_tasks_for_request(request.name)
        confirm_task(task1_name, confirmed_quantity=20)

        request.reload()
        self.assertEqual(request.process_step, "STEP1", "next_step_on_confirmation=0 should stop the chain")
        chained = frappe.get_all("Warehouse Task", filters={"predecessor_task": task1_name})
        self.assertEqual(len(chained), 0)

    def test_receipt_with_no_matching_process_determination_rule_is_unaffected(self):
        hu = self._make_hu()
        gr = self._submit_gr(hu)
        request_names = create_putaway_requests(gr.name)
        request = frappe.get_doc("Warehouse Request", request_names[0])
        self.assertFalse(request.storage_process)
        self.assertEqual(request.process_type, "GR_PUTAWAY")
