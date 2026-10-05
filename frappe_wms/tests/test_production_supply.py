import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from frappe_wms.api.scanner import confirm_task
from frappe_wms.api.inbound import create_fg_receipt_from_work_order
from frappe_wms.services.receipt import create_putaway_requests
from frappe_wms.services.task import create_tasks_for_request
from frappe_wms.tests.bootstrap import company_warehouse, pick_into_new_hu


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
        cls.wip_warehouse = company_warehouse(cls.company, "Work In Progress")
        cls.fg_erpnext_warehouse = company_warehouse(cls.company, "Finished Goods")

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
        pick_into_new_hu(task_name, confirmed_quantity=20)  # 20 of the 50 on the source pallet

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

    def test_pmr_staging_single_and_cross_order_and_backflushed_consumption(self):
        from frappe_wms.services import production_supply as ps
        supply = f"{self.warehouse}-PSA-SUP"
        if not frappe.db.exists("Storage Bin", supply):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": supply, "warehouse": self.warehouse, "storage_type": f"{self.warehouse}-PSUP", "active": 1, "sequence": 2}).insert(ignore_permissions=True)
        psa = frappe.get_doc({"doctype": "Production Supply Area", "warehouse": self.warehouse, "psa_code": frappe.generate_hash(length=5), "psa_name": "Test PSA", "supply_bin": supply}).insert(ignore_permissions=True)
        self.addCleanup(lambda: psa.db_set("active", 0))  # an active PSA turns Work Orders of this warehouse into PMRs
        self._seed_rm_stock(50)

        wo = self._submit_work_order(qty=10)  # 2 RM per FG
        self.assertFalse(frappe.get_all("Warehouse Request", filters={"reference_doctype": "Work Order", "reference_name": wo.name}), "the PMR drives staging, not the direct pull")
        pmr = frappe.get_doc("Production Material Request", {"work_order": wo.name})
        item = pmr.items[0]
        self.assertEqual((item.product, item.psa, item.required_quantity, pmr.status), (self.rm, psa.name, 20, "Open"))

        proposal = max([r for r in ps.staging_overview(psa.name) if r.pmr_item == item.name][0]["proposals"], key=lambda p: p.available_quantity)  # earlier tests leave open tasks on other lines
        self.assertEqual(proposal.storage_bin, self.source_bin)
        source = {"source_bin": proposal.storage_bin, "source_hu": proposal.handling_unit, "batch_no": proposal.batch_no}

        single = ps.stage_items(psa.name, "Single Order", [{**source, "pmr_item": item.name, "quantity": 12}])
        pick_into_new_hu(single[0]["task"], confirmed_quantity=12)
        item.reload()
        self.assertEqual((item.tasked_quantity, item.staged_quantity), (12, 12))
        self.assertEqual(frappe.db.get_value("Production Material Request", pmr.name, "status"), "Partially Staged")

        cross = ps.stage_items(psa.name, "Cross Order", [{**source, "product": self.rm, "pmr_items": [item.name], "quantity": 8}])
        pick_into_new_hu(cross[0]["task"], confirmed_quantity=8)
        item.reload()
        self.assertEqual((item.tasked_quantity, item.staged_quantity), (20, 12), "cross-order stock is pooled, not reserved")
        self.assertEqual(frappe.db.get_value("Production Material Request", pmr.name, "status"), "Staged")
        self.assertEqual(ps.pool_available(psa.name, self.rm), 8)
        with self.assertRaises(frappe.ValidationError):
            ps.stage_items(psa.name, "Single Order", [{**source, "pmr_item": item.name, "quantity": 1}])

        real_se = frappe.get_all("Stock Entry", limit=1, pluck="name")[0]  # the ledger entry links to a real document
        entry = lambda qty, n: frappe._dict(purpose="Manufacture", work_order=wo.name, name=real_se, items=[frappe._dict(  # noqa: E731
            s_warehouse=self.wh.erpnext_warehouse, item_code=self.rm, qty=qty, transfer_qty=qty, name=f"row{n}", is_finished_item=0, is_scrap_item=0)])
        ps.consume_from_stock_entry(entry(15, 1))  # 12 reserved + 3 from the pool
        item.reload()
        self.assertEqual(item.consumed_quantity, 15)
        self.assertEqual(ps._psa_stock(psa.name, self.rm), 5)
        with self.assertRaises(frappe.ValidationError):
            ps.consume_from_stock_entry(entry(6, 2))
        ps.consume_from_stock_entry(entry(5, 3))
        self.assertEqual(frappe.db.get_value("Production Material Request", pmr.name, "status"), "Consumed")

    def test_automatic_psa_stages_a_new_pmr_on_its_own(self):
        from frappe_wms.services import production_supply as ps
        supply = f"{self.warehouse}-PSA-SUP"
        if not frappe.db.exists("Storage Bin", supply):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": supply, "warehouse": self.warehouse, "storage_type": f"{self.warehouse}-PSUP", "active": 1, "sequence": 2}).insert(ignore_permissions=True)
        psa = frappe.get_doc({"doctype": "Production Supply Area", "warehouse": self.warehouse, "psa_code": frappe.generate_hash(length=5), "psa_name": "Auto PSA",
            "supply_bin": supply, "staging_mode": "Automatic"}).insert(ignore_permissions=True)
        self.addCleanup(lambda: psa.db_set("active", 0))
        self._seed_rm_stock(50)

        wo = self._submit_work_order(qty=10)
        item = frappe.get_doc("Production Material Request", {"work_order": wo.name}).items[0]
        self.assertEqual(item.tasked_quantity, 20)
        tasks = frappe.get_all("Warehouse Task", filters={"warehouse_request": ["in", frappe.get_all("Warehouse Request", filters={"reference_doctype": "Production Material Request"}, pluck="name")], "product": self.rm}, fields=["planned_quantity", "source_bin"])
        self.assertEqual((sum(t.planned_quantity for t in tasks), tasks[0].source_bin), (20, self.source_bin))
        self.assertEqual(ps.auto_stage(psa.name), [], "nothing left open: running again creates nothing")

    def test_real_manufacture_entry_is_consumed_out_of_the_psa_with_the_stock_guard_on(self):
        from erpnext.manufacturing.doctype.work_order.work_order import make_stock_entry
        from frappe_wms.services import production_supply as ps
        supply = f"{self.warehouse}-PSA-SUP"
        if not frappe.db.exists("Storage Bin", supply):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": supply, "warehouse": self.warehouse, "storage_type": f"{self.warehouse}-PSUP", "active": 1, "sequence": 2}).insert(ignore_permissions=True)
        psa = frappe.get_doc({"doctype": "Production Supply Area", "warehouse": self.warehouse, "psa_code": frappe.generate_hash(length=5), "psa_name": "Real PSA", "supply_bin": supply, "staging_mode": "Automatic"}).insert(ignore_permissions=True)
        self.addCleanup(lambda: psa.db_set("active", 0))
        frappe.db.set_single_value("WMS Settings", "enforce_wms_only_stock_movements", 1)
        self._seed_rm_stock(50)

        wo = frappe.get_doc({"doctype": "Work Order", "production_item": self.fg, "bom_no": self.bom_name, "qty": 5, "company": self.company, "planned_start_date": nowdate(),
            "source_warehouse": self.wh.erpnext_warehouse, "wip_warehouse": self.wip_warehouse, "fg_warehouse": self.fg_erpnext_warehouse, "skip_transfer": 1, "from_wip_warehouse": 0})
        wo.insert(ignore_permissions=True)
        wo.submit()
        pmr = frappe.get_doc("Production Material Request", {"work_order": wo.name})
        self.assertEqual(pmr.items[0].tasked_quantity, 10, "automatic PSA staged the 2 x 5 RM")
        for task in frappe.get_all("Warehouse Task", filters={"warehouse_request": ["in", frappe.get_all("Warehouse Request", filters={"reference_doctype": "Production Material Request", "reference_name": pmr.name}, pluck="name")]}, pluck="name"):
            pick_into_new_hu(task, confirmed_quantity=frappe.db.get_value("Warehouse Task", task, "planned_quantity"))
        self.assertEqual(ps._psa_stock(psa.name, self.rm), 10)

        se = frappe.get_doc(make_stock_entry(wo.name, "Manufacture", 5))
        se.fg_completed_qty = 5
        from unittest.mock import patch
        from frappe_wms.events.erpnext_stock_guard import validate as guard
        with patch("frappe_wms.services.production_supply.stock_entry_is_pmr_consumption", return_value=False):
            with self.assertRaises(frappe.ValidationError): guard(se)  # without the exemption the guard refuses the WMS-managed source warehouse
        guard(se)
        se.insert(ignore_permissions=True)
        se.submit()  # the guard must let the consumption through, and the hook books it out of the PSA
        self.assertEqual(ps._psa_stock(psa.name, self.rm), 0)
        item = frappe.get_doc("Production Material Request", pmr.name).items[0]
        self.assertEqual((item.consumed_quantity, frappe.db.get_value("Production Material Request", pmr.name, "status")), (10, "Consumed"))

        se.cancel()  # cancelled in ERPNext: the PSA gets the material back
        self.assertEqual(ps._psa_stock(psa.name, self.rm), 10)
        item = frappe.get_doc("Production Material Request", pmr.name).items[0]
        self.assertEqual((item.consumed_quantity, frappe.db.get_value("Production Material Request", pmr.name, "status")), (0, "Staged"))

    def test_short_closing_a_staging_task_makes_the_quantity_open_again(self):
        from frappe_wms.services import production_supply as ps
        from frappe_wms.services.task import raise_exception
        supply = f"{self.warehouse}-PSA-SUP"
        if not frappe.db.exists("Storage Bin", supply):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": supply, "warehouse": self.warehouse, "storage_type": f"{self.warehouse}-PSUP", "active": 1, "sequence": 2}).insert(ignore_permissions=True)
        psa = frappe.get_doc({"doctype": "Production Supply Area", "warehouse": self.warehouse, "psa_code": frappe.generate_hash(length=5), "psa_name": "Short PSA", "supply_bin": supply}).insert(ignore_permissions=True)
        self.addCleanup(lambda: psa.db_set("active", 0))
        self._seed_rm_stock(50)
        wo = self._submit_work_order(qty=5)
        item = frappe.get_doc("Production Material Request", {"work_order": wo.name}).items[0]
        proposal = max(ps.staging_overview(psa.name)[0]["proposals"], key=lambda p: p.available_quantity)
        out = ps.stage_items(psa.name, "Single Order", [{"source_bin": proposal.storage_bin, "source_hu": proposal.handling_unit, "batch_no": proposal.batch_no, "pmr_item": item.name, "quantity": 10}])
        code = frappe.get_all("WMS Exception Code", filters={"allows_quantity_change": 1, "active": 1}, pluck="name", limit=1)
        if not code: self.skipTest("no quantity-changing exception code configured")
        raise_exception(out[0]["task"], code[0], remarks="short", revised_quantity=4)
        item.reload()
        self.assertEqual((item.tasked_quantity, item.required_quantity - item.tasked_quantity), (4, 6), "the 6 not found can be staged again")
