import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from frappe_wms.api.inbound import create_and_submit_goods_receipt
from frappe_wms.api.outbound import allocate_delivery, create_pick_tasks
from frappe_wms.api.scanner import confirm_task
from frappe_wms.api.consolidation import (
    create_consolidation_group, set_consolidation_target_hu, find_joinable_references,
    add_consolidation_line, remove_consolidation_line, gather_consolidation_group,
    complete_consolidation_group, get_consolidation_group,
)


class TestConsolidation(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "CONSOL-TEST-WH"
        cls.recv_bin = f"{cls.warehouse}-RECV"
        cls.bulk_bin = f"{cls.warehouse}-BULK"
        cls.stage_bin = f"{cls.warehouse}-STAGE"
        cls.psup_bin = f"{cls.warehouse}-PSUP"
        cls.rack_bin = f"{cls.warehouse}-RACK"  # the Consolidation Group's own staging bin - a
        # distinct physical consolidation point, not the PSA itself (matching the real scenario:
        # material already staged at the PSA still has to physically move to get consolidated
        # elsewhere - gathering "in place" at its own existing bin isn't a real move).
        cls.company = frappe.get_all("Company", limit=1, pluck="name")[0]
        cls.supplier = frappe.get_all("Supplier", limit=1, pluck="name")[0]
        cls.customer = frappe.get_all("Customer", limit=1, pluck="name")[0]
        cls.uom = "Nos"
        cls.item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
        cls.wip_warehouse = "Work In Progress - TC"
        cls.fg_erpnext_warehouse = "Finished Goods - TC"

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": cls.company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        cls.wh = frappe.get_doc("WMS Warehouse", cls.warehouse)
        for code, role in (("GR", "Receiving"), ("BULK", "Storage"), ("PSUP", "Production Supply"), ("RACK", "Storage")):
            if not frappe.db.exists("Storage Type", f"{cls.warehouse}-{code}"):
                frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": code, "storage_type_name": code, "storage_role": role, "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for bin_name, st in ((cls.recv_bin, f"{cls.warehouse}-GR"), (cls.bulk_bin, f"{cls.warehouse}-BULK"), (cls.stage_bin, f"{cls.warehouse}-GR"), (cls.psup_bin, f"{cls.warehouse}-PSUP"), (cls.rack_bin, f"{cls.warehouse}-RACK")):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": st, "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not cls.wh.default_receiving_bin:
            cls.wh.default_receiving_bin = cls.recv_bin
            cls.wh.save(ignore_permissions=True)
        if not frappe.db.exists("Bin Determination Rule", {"warehouse": cls.warehouse, "activity": "Putaway"}):
            frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": cls.warehouse, "activity": "Putaway", "active": 1, "priority": 1,
                "destination_storage_type": f"{cls.warehouse}-BULK", "strategy": "Least Utilized Bin"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "CONSOL-TEST-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "CONSOL-TEST-PALLET", "hu_type_name": "Consol Test Pallet"}).insert(ignore_permissions=True)

    def _make_item(self, suffix, preferred_storage_type=None):
        item = f"CONSOL-TEST-ITEM-{suffix}"
        if not frappe.db.exists("Item", item):
            frappe.get_doc({"doctype": "Item", "item_code": item, "item_name": item, "item_group": self.item_group, "stock_uom": self.uom, "is_stock_item": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", {"item": item}):
            frappe.get_doc({"doctype": "WMS Product", "item": item, "stock_uom": self.uom, "warehouse_managed": 1, "active": 1,
                "preferred_storage_type": preferred_storage_type}).insert(ignore_permissions=True)
        return item

    def _receive_and_putaway(self, item, qty):
        hu_number = frappe.generate_hash(length=10)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.recv_bin,
            "items": [{"line_number": 1, "item": item, "expected_quantity": qty, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        result = create_and_submit_goods_receipt(ind.name, [
            {"inbound_delivery_item": ind.items[0].name, "item": item, "quantity": qty, "stock_uom": self.uom, "handling_unit": hu_number, "stock_type": "AVAILABLE", "hu_type": "CONSOL-TEST-PALLET"},
        ])
        confirm_task(result["warehouse_tasks"][0], confirmed_quantity=qty)

    def _make_picked_allocation(self, item, qty):
        # A delivery, allocated and fully picked - the precondition add_consolidation_line
        # enforces for a Stock Allocation reference.
        self._receive_and_putaway(item, qty)
        obd = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "customer": self.customer, "delivery_date": nowdate(), "staging_bin": self.stage_bin,
            "items": [{"line_number": 1, "item": item, "requested_quantity": qty, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"}]})
        obd.insert(ignore_permissions=True)
        obd.submit()
        allocate_delivery(obd.name)
        pick_tasks = create_pick_tasks(obd.name)
        confirm_task(pick_tasks[0], confirmed_quantity=qty)
        allocation = frappe.get_all("Stock Allocation", filters={"outbound_delivery": obd.name}, pluck="name")[0]
        return obd, allocation

    def _make_confirmed_warehouse_request(self, rm_item, fg_item, bom_name, qty=5):
        # Work Order material staging: submitting it raises a Warehouse Request to the PSUP
        # bin (events/work_order.py::on_submit); confirming that request's Putaway task is
        # what makes it eligible to join a Consolidation Group (status == "Completed").
        wo = frappe.get_doc({
            "doctype": "Work Order", "production_item": fg_item, "bom_no": bom_name, "qty": qty,
            "company": self.company, "planned_start_date": nowdate(),
            "source_warehouse": self.wh.erpnext_warehouse, "wip_warehouse": self.wip_warehouse, "fg_warehouse": self.fg_erpnext_warehouse,
            "skip_transfer": 0,
        })
        wo.insert(ignore_permissions=True)
        wo.submit()
        request_name = frappe.get_all("Warehouse Request", filters={"reference_doctype": "Work Order", "reference_name": wo.name}, pluck="name")[0]
        request = frappe.get_doc("Warehouse Request", request_name)
        task_name = frappe.get_all("Warehouse Task", filters={"warehouse_request": request_name}, pluck="name")[0]
        confirm_task(task_name, confirmed_quantity=request.requested_quantity)
        return wo, request_name

    def _make_bom(self, suffix, rm_qty=2):
        rm = self._make_item(f"RM{suffix}", preferred_storage_type=f"{self.warehouse}-BULK")
        fg = self._make_item(f"FG{suffix}")
        if not frappe.db.exists("BOM", {"item": fg, "docstatus": 1}):
            bom = frappe.get_doc({"doctype": "BOM", "item": fg, "quantity": 1, "company": self.company, "with_operations": 0,
                "items": [{"item_code": rm, "qty": rm_qty, "uom": self.uom, "stock_uom": self.uom}]})
            bom.insert(ignore_permissions=True)
            bom.submit()
            bom_name = bom.name
        else:
            bom_name = frappe.get_all("BOM", filters={"item": fg, "docstatus": 1}, pluck="name")[0]
        return rm, fg, bom_name

    def test_gather_then_split_across_a_delivery_and_a_work_order(self):
        item = self._make_item("A", preferred_storage_type=f"{self.warehouse}-BULK")
        obd, allocation = self._make_picked_allocation(item, 6)
        rm, fg, bom_name = self._make_bom("A")
        self._receive_and_putaway(rm, 50)
        _wo, request_name = self._make_confirmed_warehouse_request(rm, fg, bom_name, qty=5)

        group_name = create_consolidation_group(self.warehouse, self.rack_bin)
        add_consolidation_line(group_name, "Stock Allocation", allocation)
        add_consolidation_line(group_name, "Warehouse Request", request_name)
        set_consolidation_target_hu(group_name, frappe.generate_hash(length=10), hu_type="CONSOL-TEST-PALLET")

        group = get_consolidation_group(group_name)
        self.assertEqual(len(group["lines"]), 2)
        self.assertEqual({l["status"] for l in group["lines"]}, {"Ready"})

        created = gather_consolidation_group(group_name)
        self.assertEqual(len(created), 2)
        for task_name in created:
            task = frappe.get_doc("Warehouse Task", task_name)
            self.assertEqual(task.task_type, "Consolidation")
            confirm_task(task_name, confirmed_quantity=task.planned_quantity)

        group = get_consolidation_group(group_name)
        self.assertEqual(group["gather_status"], "Fully Gathered")
        self.assertEqual({l["status"] for l in group["lines"]}, {"Gathered"})

        target_hu = group["target_hu"]
        rm_qty = frappe.db.sql("select coalesce(sum(quantity),0) from `tabWMS Stock Balance` where handling_unit=%s and product=%s", (target_hu, item))[0][0]
        rm2_qty = frappe.db.sql("select coalesce(sum(quantity),0) from `tabWMS Stock Balance` where handling_unit=%s and product=%s", (target_hu, rm))[0][0]
        self.assertEqual(rm_qty, 6)
        self.assertEqual(rm2_qty, 10)  # BOM: 2 RM per FG unit x qty 5

        split_tasks = complete_consolidation_group(group_name)
        self.assertEqual(len(split_tasks), 2)
        for task_name in split_tasks:
            task = frappe.get_doc("Warehouse Task", task_name)
            self.assertEqual(task.task_type, "Deconsolidation")
            confirm_task(task_name, confirmed_quantity=task.planned_quantity)

        group = get_consolidation_group(group_name)
        self.assertEqual(group["status"], "Completed")
        self.assertEqual({l["status"] for l in group["lines"]}, {"Deconsolidated"})

        delivery_stage_qty = frappe.db.sql("select coalesce(sum(quantity),0) from `tabWMS Stock Balance` where storage_bin=%s and product=%s", (self.stage_bin, item))[0][0]
        psup_qty = frappe.db.sql("select coalesce(sum(quantity),0) from `tabWMS Stock Balance` where storage_bin=%s and product=%s", (self.psup_bin, rm))[0][0]
        self.assertEqual(delivery_stage_qty, 6)
        self.assertEqual(psup_qty, 10)

    def test_find_joinable_references_excludes_already_joined_lines(self):
        item = self._make_item("B", preferred_storage_type=f"{self.warehouse}-BULK")
        obd, allocation = self._make_picked_allocation(item, 3)

        candidates = find_joinable_references(obd.name)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["reference_name"], allocation)

        group_name = create_consolidation_group(self.warehouse, self.rack_bin)
        add_consolidation_line(group_name, "Stock Allocation", allocation)

        self.assertEqual(find_joinable_references(obd.name), [])
        with self.assertRaises(frappe.ValidationError):
            add_consolidation_line(group_name, "Stock Allocation", allocation)

    def test_remove_line_blocked_once_gathered(self):
        item = self._make_item("C", preferred_storage_type=f"{self.warehouse}-BULK")
        obd, allocation = self._make_picked_allocation(item, 4)
        group_name = create_consolidation_group(self.warehouse, self.rack_bin)
        line_name = add_consolidation_line(group_name, "Stock Allocation", allocation)
        set_consolidation_target_hu(group_name, frappe.generate_hash(length=10), hu_type="CONSOL-TEST-PALLET")

        gather_tasks = gather_consolidation_group(group_name)
        confirm_task(gather_tasks[0], confirmed_quantity=frappe.db.get_value("Warehouse Task", gather_tasks[0], "planned_quantity"))
        with self.assertRaises(frappe.ValidationError):
            remove_consolidation_line(group_name, line_name)

    def test_cancelling_delivery_before_pick_clears_its_consolidation_line(self):
        item = self._make_item("D", preferred_storage_type=f"{self.warehouse}-BULK")
        self._receive_and_putaway(item, 5)
        obd = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "customer": self.customer, "delivery_date": nowdate(), "staging_bin": self.stage_bin,
            "items": [{"line_number": 1, "item": item, "requested_quantity": 5, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"}]})
        obd.insert(ignore_permissions=True)
        obd.submit()
        allocate_delivery(obd.name)
        allocation = frappe.get_all("Stock Allocation", filters={"outbound_delivery": obd.name}, pluck="name")[0]
        frappe.db.set_value("Stock Allocation", allocation, "status", "Picked")  # simulate picked-ness for the join precondition
        frappe.db.set_value("Stock Allocation", allocation, "picked_quantity", 5)

        group_name = create_consolidation_group(self.warehouse, self.rack_bin)
        line_name = add_consolidation_line(group_name, "Stock Allocation", allocation)
        frappe.db.set_value("Stock Allocation", allocation, "status", "Allocated")  # revert so cancellation is actually allowed

        obd.reload()
        obd.cancel()

        group = get_consolidation_group(group_name)
        line = next(l for l in group["lines"] if l["name"] == line_name)
        self.assertEqual(line["status"], "Cancelled")
