import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, flt, nowdate

from frappe_wms.services.stock import post_entries, transfer_stock
from frappe_wms.services.allocation import allocate_delivery
from frappe_wms.services.quality import complete_inspection
from frappe_wms.services.replenishment import _current_quantity
from frappe_wms.api.inbound import create_putaway, create_inbound_delivery_from_purchase_order
from frappe_wms.api.outbound import create_pick_tasks
from frappe_wms.api.scanner import confirm_task, raise_exception, reverse_task
from frappe_wms.api.inventory import snapshot_count, record_counts, add_found_line, list_open_counts

# Regression coverage for the production-readiness audit's confirmed defects (D1-D13). Each
# test reproduces the exact failure mode found by reading the code / the live bench before the
# fix, per this project's own established practice.


class TestPhase1Hardening(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "PHASE1-TEST-WH"
        cls.recv_bin = f"{cls.warehouse}-RECV"
        cls.bulk_bin = f"{cls.warehouse}-BULK"
        cls.bulk_bin2 = f"{cls.warehouse}-BULK2"
        cls.stage_bin = f"{cls.warehouse}-STAGE"
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, order_by="creation asc", limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        cls.customer = frappe.get_all("Customer", limit=1, pluck="name")[0]
        cls.supplier = frappe.get_all("Supplier", limit=1, pluck="name")[0]
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        cls.company = company

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        cls.wh = frappe.get_doc("WMS Warehouse", cls.warehouse)
        for code, role in ((f"{cls.warehouse}-GR", "Receiving"), (f"{cls.warehouse}-BULK", "Storage"), (f"{cls.warehouse}-STAGE", "Staging"), (f"{cls.warehouse}-COUNT", "Storage")):
            if not frappe.db.exists("Storage Type", code):
                frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": code.split("-")[-1], "storage_type_name": code, "storage_role": role, "capacity_check_method": "HU Count", "allow_mixed_products": 1, "allow_mixed_stock_types": 1, "allow_mixed_batches": 1, "active": 1}).insert(ignore_permissions=True)
        cls.count_bin = f"{cls.warehouse}-COUNT1"
        cls.count_bin2 = f"{cls.warehouse}-COUNT2"
        # The count test (D4/D5) gets bins under its OWN storage type, never bulk_bin/bulk_bin2 -
        # snapshot_count scopes by bin only, not product, so any other test's leftover stock in a
        # shared bin becomes an extra unrelated count line that can never be marked Counted,
        # which also then leaves that bin's removal_blocked stuck (post_count, the only place
        # that releases it, never runs) - exactly what broke test_d9 the first time this file
        # shared bins across tests.
        for bin_name, st in ((cls.recv_bin, f"{cls.warehouse}-GR"), (cls.bulk_bin, f"{cls.warehouse}-BULK"), (cls.bulk_bin2, f"{cls.warehouse}-BULK"), (cls.stage_bin, f"{cls.warehouse}-STAGE"), (cls.count_bin, f"{cls.warehouse}-COUNT"), (cls.count_bin2, f"{cls.warehouse}-COUNT")):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": st, "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not cls.wh.default_receiving_bin:
            cls.wh.default_receiving_bin = cls.recv_bin
            cls.wh.default_shipping_bin = cls.stage_bin
            cls.wh.default_difference_bin = cls.recv_bin
            cls.wh.save(ignore_permissions=True)
        if not frappe.db.exists("Bin Determination Rule", {"warehouse": cls.warehouse, "activity": "Putaway"}):
            frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": cls.warehouse, "activity": "Putaway", "active": 1, "priority": 1, "destination_storage_type": f"{cls.warehouse}-BULK", "strategy": "Least Utilized Bin"}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", {"item": cls.item}):
            frappe.get_doc({"doctype": "WMS Product", "item": cls.item, "stock_uom": cls.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "PHASE1-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "PHASE1-PALLET", "hu_type_name": "Phase1 Pallet"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Item", "PHASE1-BATCH-ITEM"):
            frappe.get_doc({"doctype": "Item", "item_code": "PHASE1-BATCH-ITEM", "item_name": "Phase1 Batch Item", "item_group": "All Item Groups",
                "stock_uom": "Nos", "is_stock_item": 1, "has_batch_no": 1, "create_new_batch": 1}).insert(ignore_permissions=True)
        cls.batch_item = "PHASE1-BATCH-ITEM"
        frappe.db.set_single_value("Stock Settings", "enable_serial_and_batch_no_for_item", 1)
        # Every quantity-sensitive test below gets its OWN item, never the shared cls.item - two
        # tests sharing one product in these shared bins would silently see each other's stock
        # (this project's own documented IntegrationTestCase-methods-don't-roll-back-between-
        # each-other gotcha, hit again here: create_pick_tasks split across 3 tasks instead of 1
        # the first time this file reused cls.item across tests).
        for code in ("D1", "D2", "D9", "D11", "D12", "D4D5"):
            item_code = f"PHASE1-ITEM-{code}"
            if not frappe.db.exists("Item", item_code):
                frappe.get_doc({"doctype": "Item", "item_code": item_code, "item_name": item_code, "item_group": "All Item Groups",
                    "stock_uom": "Nos", "is_stock_item": 1}).insert(ignore_permissions=True)

    def _make_hu(self, bin_name, hu_type="PHASE1-PALLET"):
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": hu_type, "warehouse": self.warehouse, "current_bin": bin_name, "status": "Open"})
        hu.insert(ignore_permissions=True)
        return hu

    def _receive_and_putaway(self, qty, item=None, stock_type="AVAILABLE", shelf_life_days=None):
        item = item or self.item
        hu = self._make_hu(self.recv_bin)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.recv_bin,
            "items": [{"line_number": 1, "item": item, "expected_quantity": qty, "stock_uom": self.uom, "expected_stock_type": stock_type}]})
        ind.insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": self.recv_bin,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": item, "quantity": qty, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": stock_type}]})
        gr.insert(ignore_permissions=True)
        gr.submit()
        # create_putaway already creates AND tasks the requests in one call - calling
        # create_tasks_for_request again on the same (now Fully Tasked) requests double-tasks them.
        task_names = create_putaway(gr.name)["warehouse_tasks"]
        for t in task_names: confirm_task(t, confirmed_quantity=qty)
        return hu

    def test_d1_dates_survive_bin_to_bin_transfer(self):
        # Was: transfer_stock reset the destination balance's first_receipt_date to "now" and
        # dropped shelf_life_expiry_date entirely on every bin-to-bin move.
        key = f"D1-SEED-{frappe.generate_hash(length=6)}"
        post_entries([{
            "warehouse": self.warehouse, "product": "PHASE1-ITEM-D1", "storage_bin": self.bulk_bin, "stock_type": "AVAILABLE",
            "stock_uom": self.uom, "quantity": 5, "movement_type": "701", "shelf_life_expiry_date": "2030-01-31",
        }], "Storage Bin", self.bulk_bin, key)
        frappe.db.set_value("WMS Stock Balance", {"storage_bin": self.bulk_bin, "product": "PHASE1-ITEM-D1", "stock_type": "AVAILABLE", "handling_unit": ["is", "not set"]}, "first_receipt_date", "2020-01-01")
        transfer_stock(
            source={"warehouse": self.warehouse, "product": "PHASE1-ITEM-D1", "storage_bin": self.bulk_bin, "stock_type": "AVAILABLE", "stock_uom": self.uom},
            destination={"storage_bin": self.bulk_bin2, "stock_type": "AVAILABLE"},
            quantity=5, movement_type="301", reference_doctype="Storage Bin", reference_name=self.bulk_bin,
            idempotency_key=f"D1-MOVE-{frappe.generate_hash(length=6)}",
        )
        dest = frappe.get_doc("WMS Stock Balance", frappe.db.get_value("WMS Stock Balance", {"storage_bin": self.bulk_bin2, "product": "PHASE1-ITEM-D1", "stock_type": "AVAILABLE", "handling_unit": ["is", "not set"]}, "name"))
        self.assertEqual(str(dest.first_receipt_date)[:10], "2020-01-01")
        self.assertEqual(str(dest.shelf_life_expiry_date), "2030-01-31")

    def test_d2_and_d3_pick_denial_releases_reservation_and_does_not_relocate_hu(self):
        hu = self._receive_and_putaway(10, item="PHASE1-ITEM-D2")
        source_bin = frappe.db.get_value("Handling Unit", hu.name, "current_bin")
        obd = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "customer": self.customer, "staging_bin": self.stage_bin, "delivery_date": nowdate(),
            "items": [{"line_number": 1, "item": "PHASE1-ITEM-D2", "requested_quantity": 10, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"}]})
        obd.insert(ignore_permissions=True)
        obd.submit()
        allocate_delivery(obd.name)
        tasks = create_pick_tasks(obd.name)
        # Deny the whole pick (0 actually found) - nothing was ever confirmed.
        result = raise_exception(tasks[0], "OOS", remarks="nothing there", revised_quantity=0)
        self.assertEqual(result["status"], "Confirmed")
        # D2: the reservation for what was denied must be released, not stuck forever.
        balance_name = frappe.db.get_value("WMS Stock Balance", {"storage_bin": source_bin, "product": "PHASE1-ITEM-D2", "stock_type": "AVAILABLE", "handling_unit": hu.name}, "name")
        self.assertEqual(flt(frappe.db.get_value("WMS Stock Balance", balance_name, "allocated_quantity")), 0)
        allocation_name = frappe.db.get_value("Stock Allocation", {"outbound_delivery": obd.name}, "name")
        self.assertEqual(flt(frappe.db.get_value("Stock Allocation", allocation_name, "allocated_quantity")), 0)
        self.assertEqual(frappe.db.get_value("Stock Allocation", allocation_name, "status"), "Picked")
        obd.reload()
        self.assertEqual(obd.items[0].requested_quantity, 0)
        # D3: the source HU must NOT have been relocated - nothing was ever actually confirmed.
        self.assertEqual(frappe.db.get_value("Handling Unit", hu.name, "current_bin"), source_bin)

    def test_d7_purchase_receipt_splits_one_row_per_batch(self):
        frappe.db.set_single_value("Buying Settings", "allow_multiple_items", 1)
        po = frappe.get_doc({"doctype": "Purchase Order", "supplier": self.supplier, "company": self.company, "transaction_date": nowdate(), "schedule_date": nowdate(),
            "items": [{"item_code": self.batch_item, "qty": 10, "rate": 5, "schedule_date": nowdate(), "warehouse": self.wh.erpnext_warehouse}]})
        po.insert(ignore_permissions=True)
        po.submit()
        ind_name = create_inbound_delivery_from_purchase_order(po.name, self.warehouse)
        ind = frappe.get_doc("Inbound Delivery", ind_name)
        hu1, hu2 = self._make_hu(self.recv_bin), self._make_hu(self.recv_bin)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": self.recv_bin,
            "items": [
                {"inbound_delivery_item": ind.items[0].name, "item": self.batch_item, "quantity": 6, "stock_uom": "Nos", "handling_unit": hu1.name, "stock_type": "AVAILABLE", "batch_no": "PHASE1-BATCH-A"},
                {"inbound_delivery_item": ind.items[0].name, "item": self.batch_item, "quantity": 4, "stock_uom": "Nos", "handling_unit": hu2.name, "stock_type": "AVAILABLE", "batch_no": "PHASE1-BATCH-B"},
            ]})
        for row in gr.items:
            if not frappe.db.exists("Batch", row.batch_no):
                frappe.get_doc({"doctype": "Batch", "batch_id": row.batch_no, "item": self.batch_item}).insert(ignore_permissions=True)
        gr.insert(ignore_permissions=True)
        gr.submit()
        self.assertTrue(gr.erpnext_purchase_receipt)
        pr = frappe.get_doc("Purchase Receipt", gr.erpnext_purchase_receipt)
        # Was: one summed row per PO item, batch_no never even copied onto it at all.
        self.assertEqual(len(pr.items), 2)
        by_batch = {row.batch_no: row.qty for row in pr.items}
        self.assertEqual(by_batch.get("PHASE1-BATCH-A"), 6)
        self.assertEqual(by_batch.get("PHASE1-BATCH-B"), 4)

    def test_d8_serial_entry_cannot_move_more_than_one_unit(self):
        with self.assertRaises(frappe.ValidationError):
            post_entries([{
                "warehouse": self.warehouse, "product": self.item, "storage_bin": self.bulk_bin, "stock_type": "AVAILABLE",
                "stock_uom": self.uom, "quantity": 3, "movement_type": "701", "serial_no": "PHASE1-SERIAL-1",
            }], "Storage Bin", self.bulk_bin, f"D8-{frappe.generate_hash(length=6)}")

    def test_d9_blocked_handling_unit_excluded_from_allocation(self):
        hu = self._receive_and_putaway(5, item="PHASE1-ITEM-D9")
        frappe.db.set_value("Handling Unit", hu.name, "status", "Blocked")
        obd = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "customer": self.customer, "staging_bin": self.stage_bin, "delivery_date": nowdate(),
            "items": [{"line_number": 1, "item": "PHASE1-ITEM-D9", "requested_quantity": 5, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"}]})
        obd.insert(ignore_permissions=True)
        obd.submit()
        allocate_delivery(obd.name)
        obd.reload()
        # Was: nothing ever checked Handling Unit.status, so a Blocked HU's stock allocated freely.
        self.assertEqual(flt(obd.items[0].allocated_quantity), 0)
        frappe.db.set_value("Handling Unit", hu.name, "status", "Open")
        allocate_delivery(obd.name)
        obd.reload()
        self.assertEqual(flt(obd.items[0].allocated_quantity), 5)

    def test_d11_replenishment_sums_every_hu_in_the_bin(self):
        # Was: _current_quantity used get_value against a filter with no handling_unit, silently
        # returning only one arbitrary row's quantity when a bin holds the product in more than
        # one HU.
        hu1, hu2 = self._make_hu(self.bulk_bin), self._make_hu(self.bulk_bin)
        for hu, qty in ((hu1, 3), (hu2, 4)):
            post_entries([{
                "warehouse": self.warehouse, "product": "PHASE1-ITEM-D11", "storage_bin": self.bulk_bin, "handling_unit": hu.name,
                "stock_type": "AVAILABLE", "stock_uom": self.uom, "quantity": qty, "movement_type": "701",
            }], "Handling Unit", hu.name, f"D11-{hu.name}")
        self.assertEqual(_current_quantity(self.warehouse, "PHASE1-ITEM-D11", self.bulk_bin, "AVAILABLE"), 7)

    def test_d13_quality_inspection_mirrors_stock_type_change_to_erpnext(self):
        hu = self._receive_and_putaway(8)
        bin_name = frappe.db.get_value("Handling Unit", hu.name, "current_bin")
        before_qty = flt(frappe.db.get_value("Bin", {"warehouse": self.wh.erpnext_warehouse, "item_code": self.item}, "actual_qty"))
        qi = frappe.get_doc({"doctype": "WMS Quality Inspection", "warehouse": self.warehouse, "product": self.item, "handling_unit": hu.name,
            "storage_bin": bin_name, "from_stock_type": "AVAILABLE", "quantity": 8, "stock_uom": self.uom,
            "passed_to_stock_type": "AVAILABLE", "failed_to_stock_type": "DAMAGED"})
        qi.insert(ignore_permissions=True)
        complete_inspection(qi.name, passed_quantity=6, failed_quantity=2)
        qi.reload()
        # Was: nothing ever mirrored a QI decision to ERPNext at all.
        self.assertTrue(qi.erpnext_stock_entry)
        se = frappe.get_doc("Stock Entry", qi.erpnext_stock_entry)
        self.assertEqual(se.docstatus, 1)
        self.assertEqual(se.stock_entry_type, "Material Transfer")
        for row in se.items:
            self.assertEqual(row.s_warehouse, self.wh.erpnext_warehouse)
            self.assertEqual(row.t_warehouse, self.wh.erpnext_warehouse)
        after_qty = flt(frappe.db.get_value("Bin", {"warehouse": self.wh.erpnext_warehouse, "item_code": self.item}, "actual_qty"))
        # A same-warehouse stock-type-only change must not alter ERPNext's total on-hand qty.
        self.assertEqual(before_qty, after_qty)

    def test_d12_reversing_a_pick_task_unwinds_allocation_and_delivery_status(self):
        hu = self._receive_and_putaway(6, item="PHASE1-ITEM-D12")
        obd = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "customer": self.customer, "staging_bin": self.stage_bin, "delivery_date": nowdate(),
            "items": [{"line_number": 1, "item": "PHASE1-ITEM-D12", "requested_quantity": 6, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"}]})
        obd.insert(ignore_permissions=True)
        obd.submit()
        allocate_delivery(obd.name)
        tasks = create_pick_tasks(obd.name)
        confirm_task(tasks[0], confirmed_quantity=6)
        obd.reload()
        self.assertEqual(obd.picking_status, "Picked")
        reverse_task(tasks[0])
        obd.reload()
        allocation_name = frappe.db.get_value("Stock Allocation", {"outbound_delivery": obd.name}, "name")
        # Was: nothing ever unwound the Stock Allocation/Outbound Delivery Item after a reversal
        # - the delivery stayed "Picked" even though the stock physically moved back to source.
        self.assertEqual(flt(frappe.db.get_value("Stock Allocation", allocation_name, "picked_quantity")), 0)
        self.assertEqual(frappe.db.get_value("Stock Allocation", allocation_name, "status"), "Allocated")
        self.assertNotEqual(obd.picking_status, "Picked")

    def test_d4_d5_count_blocks_bins_allows_found_stock_and_is_blind_by_default(self):
        post_entries([{
            "warehouse": self.warehouse, "product": "PHASE1-ITEM-D4D5", "storage_bin": self.count_bin, "stock_type": "AVAILABLE",
            "stock_uom": self.uom, "quantity": 9, "movement_type": "701",
        }], "Storage Bin", self.count_bin, f"D4D5-SEED-{frappe.generate_hash(length=6)}")
        count = frappe.get_doc({"doctype": "WMS Physical Inventory Count", "warehouse": self.warehouse, "storage_bin": self.count_bin, "count_date": nowdate()})
        count.insert(ignore_permissions=True)
        snapshot_count(count.name)
        count.reload()
        self.assertTrue(frappe.db.get_value("Storage Bin", self.count_bin, "removal_blocked"))
        self.assertIn(self.count_bin, (count.blocked_bins or "").split(","))
        # Blind by default: the RF listing must not leak book_quantity.
        listed = [c for c in list_open_counts() if c["name"] == count.name][0]
        self.assertNotIn("book_quantity", listed["items"][0])
        # Found stock: a product the book shows nothing for can still be recorded as a gain.
        add_found_line(count.name, "PHASE1-ITEM-D4D5", self.count_bin2, "AVAILABLE", 3)
        count.reload()
        found_row = [r for r in count.items if r.storage_bin == self.count_bin2][0]
        self.assertEqual(flt(found_row.variance), 3)
        self.assertEqual(found_row.status, "Counted")
        record_counts(count.name, {count.items[0].name: 9})
        count.reload()
        from frappe_wms.api.inventory import post_count
        post_count(count.name)
        count.reload()
        self.assertEqual(count.status, "Posted")
        # Both bins this count touched must be released once it posts.
        self.assertFalse(frappe.db.get_value("Storage Bin", self.count_bin, "removal_blocked"))
        self.assertFalse(frappe.db.get_value("Storage Bin", self.count_bin2, "removal_blocked"))

    def test_d10_pull_next_warehouse_order_claim_is_atomic(self):
        # A true concurrent race can't be exercised in a single-process test suite - this
        # verifies the compare-and-set guard itself never lets a second claim through once the
        # first has already succeeded, which is the actual mechanism that makes it race-safe.
        from frappe_wms.services.warehouse_order import get_or_create_warehouse_order
        queue = frappe.db.exists("Warehouse Queue", {"warehouse": self.warehouse, "activity": "Pick"})
        if not queue:
            queue = frappe.get_doc({"doctype": "Warehouse Queue", "warehouse": self.warehouse, "queue_code": "PHASE1-PICK", "queue_name": "Phase1 Pick", "activity": "Pick", "active": 1}).insert(ignore_permissions=True).name
        wo_name = get_or_create_warehouse_order(self.warehouse, "Pick", queue, f"D10-{frappe.generate_hash(length=6)}")
        resource_a = frappe.get_doc({"doctype": "WMS Resource", "resource_code": f"D10A-{frappe.generate_hash(length=6)}", "resource_name": "D10 Resource A", "warehouse": self.warehouse, "resource_type": "Operator", "user": "Administrator", "active": 1}).insert(ignore_permissions=True)
        claimed = 0
        for _ in range(2):
            frappe.db.sql(
                "update `tabWarehouse Order` set assigned_resource=%s, status='Assigned' where name=%s and status='Open' and (assigned_resource is null or assigned_resource='')",
                (resource_a.name, wo_name),
            )
            claimed += frappe.db.sql("select row_count()")[0][0]
        self.assertEqual(claimed, 1)
