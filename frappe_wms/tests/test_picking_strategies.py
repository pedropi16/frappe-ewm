import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate, flt

from frappe_wms.api.inbound import create_putaway
from frappe_wms.api.outbound import allocate_delivery, create_pick_tasks
from frappe_wms.api.scanner import confirm_task

# Phase 4: Warehouse Process Type.picking_strategy (Single-Step / Two-Step / Pick-Pack-Pass),
# configurable per process type. Single-Step is exactly today's pre-existing behavior (pick
# straight to the delivery's own staging bin) and is covered by every other pick-task test in
# this suite - these tests are scoped to the two NEW strategies only.


class TestPickingStrategies(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "PICKSTRAT-TEST-WH"
        cls.recv_bin = f"{cls.warehouse}-RECV"
        cls.bulk_bin = f"{cls.warehouse}-BULK"
        cls.stage_bin = f"{cls.warehouse}-STAGE"
        cls.shared_pick_staging_bin = f"{cls.warehouse}-PICKSTAGE"
        cls.uom = "Nos"
        cls.supplier = frappe.get_all("Supplier", limit=1, pluck="name")[0]
        cls.customer = frappe.get_all("Customer", limit=1, pluck="name")[0]
        company = frappe.get_all("Company", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        cls.wh = frappe.get_doc("WMS Warehouse", cls.warehouse)
        for code, role in ((f"{cls.warehouse}-GR", "Receiving"), (f"{cls.warehouse}-BULK", "Storage"), (f"{cls.warehouse}-STAGE", "Staging")):
            if not frappe.db.exists("Storage Type", code):
                frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": code.split("-")[-1], "storage_type_name": code, "storage_role": role, "capacity_check_method": "HU Count", "allow_mixed_products": 1, "allow_mixed_stock_types": 1, "active": 1}).insert(ignore_permissions=True)
        for bin_name, st in ((cls.recv_bin, f"{cls.warehouse}-GR"), (cls.bulk_bin, f"{cls.warehouse}-BULK"), (cls.stage_bin, f"{cls.warehouse}-STAGE"), (cls.shared_pick_staging_bin, f"{cls.warehouse}-STAGE")):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": st, "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not cls.wh.default_receiving_bin:
            cls.wh.default_receiving_bin = cls.recv_bin
            cls.wh.default_shipping_bin = cls.stage_bin
            cls.wh.default_difference_bin = cls.recv_bin
            cls.wh.default_picking_staging_bin = cls.shared_pick_staging_bin
            cls.wh.save(ignore_permissions=True)
        if not frappe.db.exists("Bin Determination Rule", {"warehouse": cls.warehouse, "activity": "Putaway"}):
            frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": cls.warehouse, "activity": "Putaway", "active": 1, "priority": 1, "destination_storage_type": f"{cls.warehouse}-BULK", "strategy": "Least Utilized Bin"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "PICKSTRAT-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "PICKSTRAT-PALLET", "hu_type_name": "Pickstrat Pallet"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "PICKSTRAT-SHIPBOX"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "PICKSTRAT-SHIPBOX", "hu_type_name": "Pickstrat Ship Box"}).insert(ignore_permissions=True)

    def _make_item_and_process_type(self, item_code, process_type_code, picking_strategy, pick_pack_pass_hu_type=None):
        if not frappe.db.exists("Item", item_code):
            item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
            frappe.get_doc({"doctype": "Item", "item_code": item_code, "item_name": item_code, "item_group": item_group, "stock_uom": self.uom, "is_stock_item": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", {"item": item_code}):
            frappe.get_doc({"doctype": "WMS Product", "item": item_code, "stock_uom": self.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Warehouse Process Type", process_type_code):
            frappe.get_doc({"doctype": "Warehouse Process Type", "process_type_code": process_type_code, "process_type_name": process_type_code,
                "activity": "Pick", "source_required": 1, "destination_required": 1, "stock_required": 1,
                "confirmation_mode": "Handling Unit", "movement_type": "401", "picking_strategy": picking_strategy,
                "pick_pack_pass_hu_type": pick_pack_pass_hu_type, "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Warehouse Process Type Determination Rule", {"item": item_code, "activity": "Pick"}):
            frappe.get_doc({"doctype": "Warehouse Process Type Determination Rule", "priority": 1, "activity": "Pick",
                "item": item_code, "process_type": process_type_code, "active": 1}).insert(ignore_permissions=True)
        return item_code

    def _receive_and_putaway(self, item_code, qty):
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "PICKSTRAT-PALLET", "warehouse": self.warehouse, "current_bin": self.recv_bin, "status": "Open"})
        hu.insert(ignore_permissions=True)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.recv_bin,
            "items": [{"line_number": 1, "item": item_code, "expected_quantity": qty, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": self.recv_bin,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": item_code, "quantity": qty, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]})
        gr.insert(ignore_permissions=True)
        gr.submit()
        putaway = create_putaway(gr.name)
        confirm_task(putaway["warehouse_tasks"][0], confirmed_quantity=qty)
        return hu

    def _make_and_allocate_delivery(self, item_code, qty):
        obd = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "customer": self.customer, "delivery_date": nowdate(), "staging_bin": self.stage_bin,
            "items": [{"line_number": 1, "item": item_code, "requested_quantity": qty, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"}]})
        obd.insert(ignore_permissions=True)
        obd.submit()
        allocate_delivery(obd.name)
        return obd

    def test_two_step_pick_lands_in_the_shared_staging_bin_not_the_delivery(self):
        item_code = self._make_item_and_process_type("TEST-PICKSTRAT-TWOSTEP", "PICKSTRAT-PT-TWOSTEP", "Two-Step")
        self._receive_and_putaway(item_code, 10)
        obd = self._make_and_allocate_delivery(item_code, 10)

        task_names = create_pick_tasks(obd.name)
        self.assertEqual(len(task_names), 1)
        task = frappe.get_doc("Warehouse Task", task_names[0])
        self.assertEqual(task.destination_bin, self.shared_pick_staging_bin)
        self.assertNotEqual(task.destination_bin, self.stage_bin)
        self.assertTrue(task.requires_sort_after_pick)
        self.assertTrue(task.unpack_at_destination)

    def test_two_step_pick_confirmation_auto_creates_a_sort_task_to_the_delivery(self):
        item_code = self._make_item_and_process_type("TEST-PICKSTRAT-TWOSTEP-2", "PICKSTRAT-PT-TWOSTEP-2", "Two-Step")
        self._receive_and_putaway(item_code, 8)
        obd = self._make_and_allocate_delivery(item_code, 8)
        task_names = create_pick_tasks(obd.name)

        result = confirm_task(task_names[0], confirmed_quantity=8)
        self.assertIn("sort_task", result)

        sort_task = frappe.get_doc("Warehouse Task", result["sort_task"])
        self.assertEqual(sort_task.task_type, "Sort")
        self.assertEqual(sort_task.source_bin, self.shared_pick_staging_bin)
        self.assertEqual(sort_task.destination_bin, self.stage_bin)
        self.assertEqual(sort_task.planned_quantity, 8)

        # Stock is sitting in the shared staging bin until the Sort task is actually confirmed.
        shared_qty = flt(frappe.db.get_value("WMS Stock Balance", {"storage_bin": self.shared_pick_staging_bin, "product": item_code}, "quantity"))
        self.assertEqual(shared_qty, 8)

        confirm_task(sort_task.name, confirmed_quantity=8)
        final_qty = flt(frappe.db.get_value("WMS Stock Balance", {"storage_bin": self.stage_bin, "product": item_code}, "quantity"))
        self.assertEqual(final_qty, 8)
        shared_qty_after = flt(frappe.db.get_value("WMS Stock Balance", {"storage_bin": self.shared_pick_staging_bin, "product": item_code}, "quantity"))
        self.assertEqual(shared_qty_after, 0)

    def test_two_step_without_a_shared_staging_bin_configured_throws(self):
        nostage_wh = "PICKSTRAT-NOSTAGE-WH"
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        if not frappe.db.exists("WMS Warehouse", nostage_wh):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": nostage_wh, "warehouse_name": nostage_wh, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        for code, role in ((f"{nostage_wh}-GR", "Receiving"), (f"{nostage_wh}-STAGE", "Staging")):
            if not frappe.db.exists("Storage Type", code):
                frappe.get_doc({"doctype": "Storage Type", "warehouse": nostage_wh, "storage_type_code": code.split("-")[-1], "storage_type_name": code, "storage_role": role, "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        recv_bin = f"{nostage_wh}-RECV"
        stage_bin = f"{nostage_wh}-STAGE-BIN"
        for bin_name, st in ((recv_bin, f"{nostage_wh}-GR"), (stage_bin, f"{nostage_wh}-STAGE")):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": nostage_wh, "storage_type": st, "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        item_code = "TEST-PICKSTRAT-NOSTAGE"
        if not frappe.db.exists("Item", item_code):
            item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
            frappe.get_doc({"doctype": "Item", "item_code": item_code, "item_name": item_code, "item_group": item_group, "stock_uom": self.uom, "is_stock_item": 1}).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "WMS Product", "item": item_code, "stock_uom": self.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        pt_code = "PICKSTRAT-PT-NOSTAGE"
        frappe.get_doc({"doctype": "Warehouse Process Type", "process_type_code": pt_code, "process_type_name": pt_code,
            "activity": "Pick", "source_required": 1, "destination_required": 1, "stock_required": 1,
            "confirmation_mode": "Handling Unit", "movement_type": "401", "picking_strategy": "Two-Step", "active": 1}).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "Warehouse Process Type Determination Rule", "priority": 1, "activity": "Pick",
            "item": item_code, "process_type": pt_code, "active": 1}).insert(ignore_permissions=True)

        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "PICKSTRAT-PALLET", "warehouse": nostage_wh, "current_bin": recv_bin, "status": "Open"})
        hu.insert(ignore_permissions=True)
        from frappe_wms.services.stock import post_entries
        post_entries([{"warehouse": nostage_wh, "product": item_code, "storage_bin": recv_bin, "handling_unit": hu.name,
            "stock_type": "AVAILABLE", "stock_uom": self.uom, "quantity": 5, "movement_type": "701"}],
            "Storage Bin", recv_bin, f"pickstrat-nostage-seed:{frappe.generate_hash(length=8)}")

        obd = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": nostage_wh, "customer": self.customer, "delivery_date": nowdate(), "staging_bin": stage_bin,
            "items": [{"line_number": 1, "item": item_code, "requested_quantity": 5, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"}]})
        obd.insert(ignore_permissions=True)
        obd.submit()
        allocate_delivery(obd.name)

        with self.assertRaises(frappe.ValidationError):
            create_pick_tasks(obd.name)

    def test_pick_pack_pass_uses_one_shared_hu_across_every_line_of_the_delivery(self):
        item_a = self._make_item_and_process_type("TEST-PICKSTRAT-PPP-A", "PICKSTRAT-PT-PPP", "Pick-Pack-Pass", pick_pack_pass_hu_type="PICKSTRAT-SHIPBOX")
        item_b = self._make_item_and_process_type("TEST-PICKSTRAT-PPP-B", "PICKSTRAT-PT-PPP", "Pick-Pack-Pass", pick_pack_pass_hu_type="PICKSTRAT-SHIPBOX")
        self._receive_and_putaway(item_a, 5)
        self._receive_and_putaway(item_b, 3)

        obd = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "customer": self.customer, "delivery_date": nowdate(), "staging_bin": self.stage_bin,
            "items": [
                {"line_number": 1, "item": item_a, "requested_quantity": 5, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"},
                {"line_number": 2, "item": item_b, "requested_quantity": 3, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"},
            ]})
        obd.insert(ignore_permissions=True)
        obd.submit()
        allocate_delivery(obd.name)

        task_names = create_pick_tasks(obd.name)
        self.assertEqual(len(task_names), 2)
        tasks = [frappe.get_doc("Warehouse Task", n) for n in task_names]
        self.assertTrue(all(t.destination_hu for t in tasks))
        self.assertEqual(tasks[0].destination_hu, tasks[1].destination_hu)
        self.assertFalse(any(t.requires_sort_after_pick for t in tasks))

        obd.reload()
        self.assertEqual(obd.pick_pack_pass_hu, tasks[0].destination_hu)
        self.assertEqual(frappe.db.get_value("Handling Unit", obd.pick_pack_pass_hu, "hu_type"), "PICKSTRAT-SHIPBOX")

    def test_pick_pack_pass_without_an_hu_type_configured_throws(self):
        item_code = self._make_item_and_process_type("TEST-PICKSTRAT-PPP-NOHU", "PICKSTRAT-PT-PPP-NOHU", "Pick-Pack-Pass")
        self._receive_and_putaway(item_code, 4)
        obd = self._make_and_allocate_delivery(item_code, 4)

        with self.assertRaises(frappe.ValidationError):
            create_pick_tasks(obd.name)
