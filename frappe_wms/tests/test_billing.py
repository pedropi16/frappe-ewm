import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from frappe_wms.services.receipt import create_putaway_requests
from frappe_wms.services.task import create_tasks_for_request
from frappe_wms.api.scanner import confirm_task
from frappe_wms.services.billing import generate_billing_for_period, create_billing_sales_invoice


class TestBilling(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "BILL-TEST-WH"
        cls.recv_bin = f"{cls.warehouse}-RECV"
        cls.bulk_bin = f"{cls.warehouse}-BULK"
        cls.stage_bin = f"{cls.warehouse}-STAGE"
        cls.item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
        cls.uom = "Nos"
        cls.supplier = frappe.get_all("Supplier", limit=1, pluck="name")[0]
        cls.customer = frappe.get_all("Customer", limit=1, pluck="name")[0]
        cls.company = frappe.get_all("Company", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": cls.company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-GR"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "GR", "storage_type_name": "GR", "storage_role": "Receiving", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-BULK"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "BULK", "storage_type_name": "BULK", "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for bin_name, st in ((cls.recv_bin, f"{cls.warehouse}-GR"), (cls.bulk_bin, f"{cls.warehouse}-BULK"), (cls.stage_bin, f"{cls.warehouse}-GR")):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": st, "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Bin Determination Rule", {"warehouse": cls.warehouse, "activity": "Putaway"}):
            frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": cls.warehouse, "activity": "Putaway", "active": 1, "priority": 1, "destination_storage_type": f"{cls.warehouse}-BULK", "strategy": "Least Utilized Bin"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "BILL-TEST-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "BILL-TEST-PALLET", "hu_type_name": "Billing Test Pallet"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Item", "BILL-TEST-SERVICE"):
            frappe.get_doc({"doctype": "Item", "item_code": "BILL-TEST-SERVICE", "item_name": "BILL-TEST-SERVICE", "item_group": cls.item_group, "stock_uom": cls.uom, "is_stock_item": 0}).insert(ignore_permissions=True)

    def tearDown(self):
        for existing in frappe.get_all("Billing Rate", filters={"warehouse": self.warehouse}, pluck="name"):
            frappe.delete_doc("Billing Rate", existing, force=True, ignore_permissions=True)

    def _make_item(self, suffix):
        item = f"BILL-TEST-ITEM-{suffix}"
        if not frappe.db.exists("Item", item):
            frappe.get_doc({"doctype": "Item", "item_code": item, "item_name": item, "item_group": self.item_group, "stock_uom": self.uom, "is_stock_item": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", {"item": item}):
            frappe.get_doc({"doctype": "WMS Product", "item": item, "stock_uom": self.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        return item

    def _make_hu(self):
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "BILL-TEST-PALLET", "warehouse": self.warehouse, "current_bin": self.recv_bin, "status": "Open"})
        hu.insert(ignore_permissions=True)
        return hu

    def _submit_gr(self, hu, item, qty):
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.recv_bin,
            "items": [{"line_number": 1, "item": item, "expected_quantity": qty, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": self.recv_bin,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": item, "quantity": qty, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]})
        gr.insert(ignore_permissions=True)
        gr.submit()
        return gr

    def _make_delivery(self, item, qty):
        obd = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "customer": self.customer, "delivery_date": nowdate(), "staging_bin": self.stage_bin,
            "items": [{"line_number": 1, "item": item, "requested_quantity": qty, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"}]})
        obd.insert(ignore_permissions=True)
        obd.submit()
        return obd

    def _confirm_cross_dock_task(self, item, qty):
        # Reuses the real Cross Dock pipeline (P4 sub-item 4) rather than fabricating a
        # Warehouse Task by hand - the only realistic way a task ends up traceable to an
        # Outbound Delivery/customer is through this actual production flow.
        obd = self._make_delivery(item, qty)
        hu = self._make_hu()
        gr = self._submit_gr(hu, item, qty)
        request_names = create_putaway_requests(gr.name)
        request = next(r for r in [frappe.get_doc("Warehouse Request", n) for n in request_names] if r.request_type == "Cross Dock")
        task_name = create_tasks_for_request(request.name)
        confirm_task(task_name, confirmed_quantity=qty)
        return obd, task_name

    def test_confirmed_activity_aggregates_into_charge_by_rate_basis(self):
        item = self._make_item("A")
        frappe.get_doc({"doctype": "Billing Rate", "priority": 1, "warehouse": self.warehouse, "activity": "Cross Dock",
            "uom_basis": "Per Unit", "rate": 2.5, "billing_item": "BILL-TEST-SERVICE", "active": 1}).insert(ignore_permissions=True)
        self._confirm_cross_dock_task(item, 6)

        lines = generate_billing_for_period(self.warehouse, self.customer, nowdate(), nowdate())
        matching = [l for l in lines if l["activity"] == "Cross Dock"]
        self.assertEqual(len(matching), 1)
        self.assertGreaterEqual(matching[0]["task_count"], 1)
        self.assertGreaterEqual(matching[0]["quantity"], 6)
        self.assertEqual(matching[0]["uom_basis"], "Per Unit")
        self.assertAlmostEqual(matching[0]["charge"], matching[0]["quantity"] * 2.5)

    def test_activity_with_no_matching_rate_is_excluded(self):
        item = self._make_item("B")
        self._confirm_cross_dock_task(item, 4)
        lines = generate_billing_for_period(self.warehouse, self.customer, nowdate(), nowdate())
        self.assertEqual([l for l in lines if l["activity"] == "Cross Dock"], [])

    def test_task_with_no_traceable_outbound_delivery_is_excluded(self):
        # The Putaway remainder of a partial cross-dock match has no Outbound Delivery in its
        # reference chain (its Warehouse Request points back to the Goods Receipt instead).
        frappe.get_doc({"doctype": "Billing Rate", "priority": 1, "warehouse": self.warehouse, "activity": "Putaway",
            "uom_basis": "Per Task", "rate": 10, "billing_item": "BILL-TEST-SERVICE", "active": 1}).insert(ignore_permissions=True)
        item = self._make_item("C")
        self._make_delivery(item, 3)
        hu = self._make_hu()
        gr = self._submit_gr(hu, item, 10)
        request_names = create_putaway_requests(gr.name)
        putaway_request = next(r for r in [frappe.get_doc("Warehouse Request", n) for n in request_names] if r.request_type == "Putaway")
        task_name = create_tasks_for_request(putaway_request.name)
        confirm_task(task_name, confirmed_quantity=7)

        lines = generate_billing_for_period(self.warehouse, self.customer, nowdate(), nowdate())
        self.assertEqual([l for l in lines if l["activity"] == "Putaway"], [])

    def test_create_billing_sales_invoice_produces_draft_invoice_with_expected_lines(self):
        item = self._make_item("D")
        frappe.get_doc({"doctype": "Billing Rate", "priority": 1, "warehouse": self.warehouse, "activity": "Cross Dock",
            "uom_basis": "Per Task", "rate": 15, "billing_item": "BILL-TEST-SERVICE", "active": 1}).insert(ignore_permissions=True)
        self._confirm_cross_dock_task(item, 8)

        invoice_name = create_billing_sales_invoice(self.warehouse, self.customer, nowdate(), nowdate())
        invoice = frappe.get_doc("Sales Invoice", invoice_name)
        self.assertEqual(invoice.docstatus, 0)
        self.assertEqual(invoice.customer, self.customer)
        self.assertEqual(len(invoice.items), 1)
        self.assertEqual(invoice.items[0].item_code, "BILL-TEST-SERVICE")
        self.assertEqual(invoice.items[0].rate, 15)
