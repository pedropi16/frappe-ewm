import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from frappe_wms.api.scanner import confirm_task
from frappe_wms.api.inbound import create_fg_receipt_from_work_order
from frappe_wms.services.receipt import create_putaway_requests
from frappe_wms.services.task import create_tasks_for_request


class TestProductionSupply(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "WMS-TEST-PSUP-WH"
        cls.source_bin = "WMS-TEST-PSUP-WH-SRC"
        cls.psup_bin = "WMS-TEST-PSUP-WH-PSUP"
        cls.recv_bin = "WMS-TEST-PSUP-WH-RECV"
        cls.company = frappe.get_all("Company", limit=1, pluck="name")[0]
        cls.uom = "Nos"
        cls.wip_warehouse = "Work In Progress - TC"
        cls.fg_erpnext_warehouse = "Finished Goods - TC"

        if not frappe.db.exists("Item", "WMS-TEST-PSUP-RM"):
            item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
            frappe.get_doc({"doctype": "Item", "item_code": "WMS-TEST-PSUP-RM", "item_name": "PSUP RM", "item_group": item_group, "stock_uom": cls.uom, "is_stock_item": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Item", "WMS-TEST-PSUP-FG"):
            item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
            frappe.get_doc({"doctype": "Item", "item_code": "WMS-TEST-PSUP-FG", "item_name": "PSUP FG", "item_group": item_group, "stock_uom": cls.uom, "is_stock_item": 1}).insert(ignore_permissions=True)
        cls.rm = "WMS-TEST-PSUP-RM"
        cls.fg = "WMS-TEST-PSUP-FG"

        if not frappe.db.exists("BOM", {"item": cls.fg, "docstatus": 1}):
            bom = frappe.get_doc({"doctype": "BOM", "item": cls.fg, "quantity": 1, "company": cls.company, "with_operations": 0,
                "items": [{"item_code": cls.rm, "qty": 2, "uom": cls.uom, "stock_uom": cls.uom}]})
            bom.insert(ignore_permissions=True)
            bom.submit()
            cls.bom_name = bom.name
        else:
            cls.bom_name = frappe.get_all("BOM", filters={"item": cls.fg, "docstatus": 1}, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": cls.company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        cls.wh = frappe.get_doc("WMS Warehouse", cls.warehouse)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-SRC"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "SRC", "storage_type_name": "Source Storage", "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-PSUP"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "PSUP", "storage_type_name": "Production Supply", "storage_role": "Production Supply", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-GR"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "GR", "storage_type_name": "Receiving", "storage_role": "Receiving", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for bin_name, st in ((cls.source_bin, f"{cls.warehouse}-SRC"), (cls.psup_bin, f"{cls.warehouse}-PSUP"), (cls.recv_bin, f"{cls.warehouse}-GR")):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": st, "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not cls.wh.default_receiving_bin:
            cls.wh.default_receiving_bin = cls.recv_bin
            cls.wh.save(ignore_permissions=True)
        for item, preferred_storage_type in ((cls.rm, f"{cls.warehouse}-SRC"), (cls.fg, None)):
            if not frappe.db.exists("WMS Product", {"item": item}):
                frappe.get_doc({"doctype": "WMS Product", "item": item, "stock_uom": cls.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product Warehouse", f"{cls.warehouse}-{cls.rm}"):
            frappe.get_doc({"doctype": "WMS Product Warehouse", "item": cls.rm, "warehouse": cls.warehouse, "preferred_storage_type": f"{cls.warehouse}-SRC", "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Bin Determination Rule", {"warehouse": cls.warehouse, "activity": "Putaway"}):
            frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": cls.warehouse, "activity": "Putaway", "active": 1, "priority": 1,
                "destination_storage_type": f"{cls.warehouse}-SRC", "strategy": "Bin Sequence"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "TEST-PSUP-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "TEST-PSUP-PALLET", "hu_type_name": "Test PSup Pallet"}).insert(ignore_permissions=True)

    def _seed_rm_stock(self, qty):
        # A real Goods Receipt (not a raw ledger post) so ERPNext's own side also has
        # valuated stock for this item/warehouse - the Material Transfer for Manufacture
        # this test exercises later draws from ERPNext's stock ledger, not WMS's.
        supplier = frappe.get_all("Supplier", limit=1, pluck="name")[0]
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "TEST-PSUP-PALLET", "warehouse": self.warehouse, "current_bin": self.recv_bin, "status": "Open"})
        hu.insert(ignore_permissions=True)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": supplier, "receiving_bin": self.recv_bin,
            "items": [{"line_number": 1, "item": self.rm, "expected_quantity": qty, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": self.recv_bin,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": self.rm, "quantity": qty, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]})
        gr.insert(ignore_permissions=True)
        gr.submit()
        requests = create_putaway_requests(gr.name)
        for request_name in requests:
            task_name = create_tasks_for_request(request_name)
            confirm_task(task_name, confirmed_quantity=qty)

    def _submit_work_order(self, qty=10):
        wo = frappe.get_doc({
            "doctype": "Work Order", "production_item": self.fg, "bom_no": self.bom_name, "qty": qty,
            "company": self.company, "planned_start_date": nowdate(),
            "source_warehouse": self.wh.erpnext_warehouse, "wip_warehouse": self.wip_warehouse, "fg_warehouse": self.fg_erpnext_warehouse,
            "skip_transfer": 0,
        })
        wo.insert(ignore_permissions=True)
        wo.submit()
        return wo

    def test_submitting_work_order_stages_material_from_wms_managed_source_warehouse(self):
        self._seed_rm_stock(50)
        wo = self._submit_work_order(qty=10)

        requests = frappe.get_all("Warehouse Request", filters={"reference_doctype": "Work Order", "reference_name": wo.name},
            fields=["name", "product", "requested_quantity", "destination_bin", "source_bin"])
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].product, self.rm)
        self.assertEqual(requests[0].requested_quantity, 20)  # BOM: 2 RM per FG unit x qty 10
        self.assertEqual(requests[0].destination_bin, self.psup_bin)
        self.assertEqual(requests[0].source_bin, self.source_bin)

    def test_confirming_staging_task_posts_material_transfer_for_manufacture(self):
        self._seed_rm_stock(50)
        wo = self._submit_work_order(qty=10)

        request_name = frappe.get_all("Warehouse Request", filters={"reference_doctype": "Work Order", "reference_name": wo.name}, pluck="name")[0]
        task_name = frappe.get_all("Warehouse Task", filters={"warehouse_request": request_name}, pluck="name")[0]
        confirm_task(task_name, confirmed_quantity=20)

        request = frappe.get_doc("Warehouse Request", request_name)
        self.assertEqual(request.status, "Completed")
        self.assertTrue(request.erpnext_stock_entry)

        se = frappe.get_doc("Stock Entry", request.erpnext_stock_entry)
        self.assertEqual(se.stock_entry_type, "Material Transfer for Manufacture")
        self.assertEqual(se.work_order, wo.name)
        self.assertEqual(se.items[0].s_warehouse, self.wh.erpnext_warehouse)
        self.assertEqual(se.items[0].t_warehouse, self.wip_warehouse)

    def test_fg_receipt_from_work_order_posts_manufacture_stock_entry(self):
        wo = self._submit_work_order(qty=5)
        result = create_fg_receipt_from_work_order(wo.name, self.warehouse, 5, frappe.generate_hash(length=10), hu_type="TEST-PSUP-PALLET")
        self.assertTrue(result["goods_receipt"])

        gr = frappe.get_doc("Goods Receipt", result["goods_receipt"])
        self.assertTrue(gr.erpnext_stock_entry)
        se = frappe.get_doc("Stock Entry", gr.erpnext_stock_entry)
        self.assertEqual(se.stock_entry_type, "Material Receipt", "Manufacture requires backflush rows this receipt-only flow doesn't produce")
        self.assertIn(wo.name, se.remarks, "Work Order reference kept in remarks since ERPNext drops work_order on a non-manufacturing purpose")
        self.assertEqual(se.items[0].t_warehouse, self.wh.erpnext_warehouse)

        balance = frappe.get_all("WMS Stock Balance", filters={"product": self.fg, "warehouse": self.warehouse}, fields=["quantity"])
        self.assertEqual(sum(b.quantity for b in balance), 5)
