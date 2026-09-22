import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.services.receipt import create_putaway_requests
from frappe_wms.services.quality import complete_inspection


class TestInspectionRule(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "WMS-TEST-INSP-WH"
        cls.recv_bin = "WMS-TEST-INSP-WH-RECV"
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        cls.supplier = frappe.get_all("Supplier", limit=1, pluck="name")[0]
        company = frappe.get_all("Company", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-GR"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "GR", "storage_type_name": "Receiving", "storage_role": "Receiving", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Bin", cls.recv_bin):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": cls.recv_bin, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-GR", "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        wh = frappe.get_doc("WMS Warehouse", cls.warehouse)
        if not wh.default_receiving_bin:
            wh.default_receiving_bin = cls.recv_bin
            wh.save(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "TEST-INSP-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "TEST-INSP-PALLET", "hu_type_name": "Test Insp Pallet"}).insert(ignore_permissions=True)

    def tearDown(self):
        for existing in frappe.get_all("Inspection Rule", filters={"warehouse": self.warehouse}, pluck="name"):
            frappe.delete_doc("Inspection Rule", existing, force=True, ignore_permissions=True)

    def _make_hu(self):
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "TEST-INSP-PALLET", "warehouse": self.warehouse, "current_bin": self.recv_bin, "status": "Open"})
        hu.insert(ignore_permissions=True)
        return hu.name

    def _submit_gr(self, hu, qty=10):
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.recv_bin,
            "items": [{"line_number": 1, "item": self.item, "expected_quantity": qty, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": self.recv_bin,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": self.item, "quantity": qty, "stock_uom": self.uom, "handling_unit": hu, "stock_type": "AVAILABLE"}]})
        gr.insert(ignore_permissions=True)
        gr.submit()
        return gr

    def test_receipt_matching_inspection_rule_lands_in_quality_with_inspection_created(self):
        frappe.get_doc({"doctype": "Inspection Rule", "priority": 1, "warehouse": self.warehouse, "item": self.item, "active": 1}).insert(ignore_permissions=True)

        hu = self._make_hu()
        gr = self._submit_gr(hu)
        gr.reload()
        self.assertEqual(gr.items[0].stock_type, "QUALITY")

        balance = frappe.get_all("WMS Stock Balance", filters={"product": self.item, "handling_unit": hu, "stock_type": "QUALITY"}, fields=["quantity"])
        self.assertEqual(len(balance), 1)
        self.assertEqual(balance[0].quantity, 10)

        inspections = frappe.get_all("WMS Quality Inspection", filters={"goods_receipt": gr.name}, fields=["name", "quantity", "from_stock_type"])
        self.assertEqual(len(inspections), 1)
        self.assertEqual(inspections[0].quantity, 10)
        self.assertEqual(inspections[0].from_stock_type, "QUALITY")

        requests = create_putaway_requests(gr.name)
        request = frappe.get_doc("Warehouse Request", requests[0])
        self.assertEqual(request.stock_type, "QUALITY", "putaway must route the actual QUALITY stock, not the receipt's original stock type")

    def test_receipt_with_no_matching_inspection_rule_is_unaffected(self):
        hu = self._make_hu()
        gr = self._submit_gr(hu)
        gr.reload()
        self.assertEqual(gr.items[0].stock_type, "AVAILABLE")
        self.assertEqual(frappe.get_all("WMS Quality Inspection", filters={"goods_receipt": gr.name}, pluck="name"), [])

    def test_complete_inspection_creates_linked_erpnext_quality_inspection(self):
        frappe.get_doc({"doctype": "Inspection Rule", "priority": 1, "warehouse": self.warehouse, "item": self.item, "active": 1}).insert(ignore_permissions=True)
        hu = self._make_hu()
        gr = self._submit_gr(hu)
        gr.reload()
        self.assertTrue(gr.erpnext_stock_entry, "erpnext sync must have mirrored this receipt for the ERPNext QI reference to resolve")

        inspection = frappe.get_all("WMS Quality Inspection", filters={"goods_receipt": gr.name}, pluck="name")[0]
        result = complete_inspection(inspection, passed_quantity=7, failed_quantity=3)
        self.assertEqual(result["status"], "Completed")

        doc = frappe.get_doc("WMS Quality Inspection", inspection)
        self.assertTrue(doc.erpnext_quality_inspection)
        erpnext_qi = frappe.get_doc("Quality Inspection", doc.erpnext_quality_inspection)
        self.assertEqual(erpnext_qi.reference_type, "Stock Entry")
        self.assertEqual(erpnext_qi.reference_name, gr.erpnext_stock_entry)
        self.assertEqual(erpnext_qi.status, "Rejected", "any failed quantity records as Rejected")
        self.assertEqual(erpnext_qi.item_code, self.item)
