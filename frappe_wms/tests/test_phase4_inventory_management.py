import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.api.inbound import create_putaway
from frappe_wms.api.scanner import confirm_task
from frappe_wms.api.inventory import snapshot_count, record_counts, post_count, complete_inspection, check_replenishment_needs
from frappe_wms.services.stock import transfer_stock


class TestPhase4InventoryManagement(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "PHASE4-TEST-WH"
        cls.recv_bin = f"{cls.warehouse}-RECV"
        cls.bulk_bin = f"{cls.warehouse}-BULK"
        cls.pick_bin = f"{cls.warehouse}-PICK"
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        cls.supplier = frappe.get_all("Supplier", limit=1, pluck="name")[0]
        company = frappe.get_all("Company", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        cls.wh = frappe.get_doc("WMS Warehouse", cls.warehouse)
        for code, role in ((f"{cls.warehouse}-GR", "Receiving"), (f"{cls.warehouse}-BULK", "Storage"), (f"{cls.warehouse}-PICK", "Picking")):
            if not frappe.db.exists("Storage Type", code):
                frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": code.split("-")[-1], "storage_type_name": code, "storage_role": role, "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for bin_name, st in ((cls.recv_bin, f"{cls.warehouse}-GR"), (cls.bulk_bin, f"{cls.warehouse}-BULK"), (cls.pick_bin, f"{cls.warehouse}-PICK")):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": st, "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not cls.wh.default_receiving_bin:
            cls.wh.default_receiving_bin = cls.recv_bin
            cls.wh.default_shipping_bin = cls.pick_bin
            cls.wh.default_difference_bin = cls.recv_bin
            cls.wh.save(ignore_permissions=True)
        if not frappe.db.exists("Bin Determination Rule", {"warehouse": cls.warehouse, "activity": "Putaway"}):
            frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": cls.warehouse, "activity": "Putaway", "active": 1, "priority": 1, "destination_storage_type": f"{cls.warehouse}-BULK", "strategy": "Least Utilized Bin"}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", {"item": cls.item}):
            frappe.get_doc({"doctype": "WMS Product", "item": cls.item, "stock_uom": cls.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "PHASE4-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "PHASE4-PALLET", "hu_type_name": "Phase4 Pallet"}).insert(ignore_permissions=True)

    def _receive_and_putaway(self, qty):
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "PHASE4-PALLET", "warehouse": self.warehouse, "current_bin": self.recv_bin, "status": "Open"})
        hu.insert(ignore_permissions=True)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.recv_bin,
            "items": [{"line_number": 1, "item": self.item, "expected_quantity": qty, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": self.recv_bin,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": self.item, "quantity": qty, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]})
        gr.insert(ignore_permissions=True)
        gr.submit()
        putaway = create_putaway(gr.name)
        confirm_task(putaway["warehouse_tasks"][0], confirmed_quantity=qty)
        return hu

    def test_physical_inventory_count_posts_loss_variance(self):
        hu = self._receive_and_putaway(20)
        count = frappe.get_doc({"doctype": "WMS Physical Inventory Count", "warehouse": self.warehouse, "storage_bin": self.bulk_bin, "count_date": frappe.utils.nowdate()})
        count.insert(ignore_permissions=True)

        snap = snapshot_count(count.name)
        self.assertEqual(snap["items"], 1)
        count.reload()
        self.assertEqual(count.status, "Counting")
        self.assertEqual(count.items[0].book_quantity, 20)

        record_counts(count.name, {count.items[0].name: 17})
        count.reload()
        self.assertEqual(count.status, "Counted")
        self.assertEqual(count.items[0].variance, -3)

        post_count(count.name)
        count.reload()
        self.assertEqual(count.status, "Posted")

        balance = frappe.get_all("WMS Stock Balance", filters={"storage_bin": self.bulk_bin, "handling_unit": hu.name}, fields=["quantity"])[0]
        self.assertEqual(balance.quantity, 17)

        entries = frappe.get_all("WMS Stock Ledger Entry", filters={"reference_doctype": "WMS Physical Inventory Count", "reference_name": count.name}, fields=["movement_type", "quantity"])
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].movement_type, "702")
        self.assertEqual(entries[0].quantity, -3)

    def test_physical_inventory_count_requires_all_lines_counted_before_posting(self):
        self._receive_and_putaway(4)
        count = frappe.get_doc({"doctype": "WMS Physical Inventory Count", "warehouse": self.warehouse, "storage_bin": self.bulk_bin, "count_date": frappe.utils.nowdate()})
        count.insert(ignore_permissions=True)
        snapshot_count(count.name)
        with self.assertRaises(frappe.ValidationError):
            post_count(count.name)

    def test_quality_inspection_splits_stock_between_pass_and_fail(self):
        hu = self._receive_and_putaway(20)
        transfer_stock(
            source={"warehouse": self.warehouse, "product": self.item, "handling_unit": hu.name, "storage_bin": self.bulk_bin, "stock_type": "AVAILABLE", "stock_uom": self.uom},
            destination={"handling_unit": hu.name, "storage_bin": self.bulk_bin, "stock_type": "QUALITY"},
            quantity=8, movement_type="501", reference_doctype="Handling Unit", reference_name=hu.name, idempotency_key=f"test-qi-setup:{hu.name}",
        )

        qi = frappe.get_doc({"doctype": "WMS Quality Inspection", "warehouse": self.warehouse, "product": self.item, "handling_unit": hu.name, "storage_bin": self.bulk_bin,
            "from_stock_type": "QUALITY", "quantity": 8, "stock_uom": self.uom, "passed_to_stock_type": "AVAILABLE", "failed_to_stock_type": "DAMAGED"})
        qi.insert(ignore_permissions=True)

        result = complete_inspection(qi.name, passed_quantity=5, failed_quantity=3)
        self.assertEqual(result["status"], "Completed")

        qi.reload()
        self.assertEqual(qi.status, "Completed")
        self.assertTrue(qi.completed_at)

        quality_qty = frappe.get_all("WMS Stock Balance", filters={"handling_unit": hu.name, "storage_bin": self.bulk_bin, "stock_type": "QUALITY"}, fields=["quantity"])[0].quantity
        damaged_qty = frappe.get_all("WMS Stock Balance", filters={"handling_unit": hu.name, "storage_bin": self.bulk_bin, "stock_type": "DAMAGED"}, fields=["quantity"])[0].quantity
        self.assertEqual(quality_qty, 0)
        self.assertEqual(damaged_qty, 3)

    def test_quality_inspection_rejects_mismatched_pass_fail_split(self):
        hu = self._receive_and_putaway(6)
        transfer_stock(
            source={"warehouse": self.warehouse, "product": self.item, "handling_unit": hu.name, "storage_bin": self.bulk_bin, "stock_type": "AVAILABLE", "stock_uom": self.uom},
            destination={"handling_unit": hu.name, "storage_bin": self.bulk_bin, "stock_type": "QUALITY"},
            quantity=6, movement_type="501", reference_doctype="Handling Unit", reference_name=hu.name, idempotency_key=f"test-qi-setup2:{hu.name}",
        )
        qi = frappe.get_doc({"doctype": "WMS Quality Inspection", "warehouse": self.warehouse, "product": self.item, "handling_unit": hu.name, "storage_bin": self.bulk_bin,
            "from_stock_type": "QUALITY", "quantity": 6, "stock_uom": self.uom})
        qi.insert(ignore_permissions=True)
        with self.assertRaises(frappe.ValidationError):
            complete_inspection(qi.name, passed_quantity=4, failed_quantity=1)  # only sums to 5, not 6

    def test_replenishment_rule_creates_and_fills_pick_task(self):
        self._receive_and_putaway(25)
        rule = frappe.get_doc({"doctype": "Replenishment Rule", "warehouse": self.warehouse, "product": self.item, "storage_bin": self.pick_bin, "stock_type": "AVAILABLE",
            "minimum_quantity": 5, "target_quantity": 12, "source_storage_type": f"{self.warehouse}-BULK", "priority": "Normal", "active": 1})
        rule.insert(ignore_permissions=True)

        tasks = check_replenishment_needs()
        self.assertEqual(len(tasks), 1)
        task = frappe.get_doc("Warehouse Task", tasks[0])
        self.assertEqual(task.task_type, "Putaway")
        self.assertEqual(task.destination_bin, self.pick_bin)
        self.assertEqual(task.planned_quantity, 12)

        confirm_task(task.name, confirmed_quantity=12)
        pick_qty = frappe.get_all("WMS Stock Balance", filters={"storage_bin": self.pick_bin, "product": self.item}, fields=["quantity"])[0].quantity
        self.assertEqual(pick_qty, 12)

        # Above minimum now, so re-running must not create a duplicate.
        self.assertEqual(check_replenishment_needs(), [])

    def test_replenishment_check_does_not_duplicate_pending_request(self):
        self._receive_and_putaway(25)
        rule = frappe.get_doc({"doctype": "Replenishment Rule", "warehouse": self.warehouse, "product": self.item, "storage_bin": self.pick_bin, "stock_type": "AVAILABLE",
            "minimum_quantity": 5, "target_quantity": 10, "source_storage_type": f"{self.warehouse}-BULK", "priority": "Normal", "active": 1})
        rule.insert(ignore_permissions=True)
        first = check_replenishment_needs()
        self.assertEqual(len(first), 1)
        second = check_replenishment_needs()
        self.assertEqual(second, [], "an open request for the rule should block a second one from being raised")
