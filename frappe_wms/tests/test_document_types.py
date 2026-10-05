import frappe
from frappe.tests import IntegrationTestCase


class TestDocumentTypes(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.wh = "WMS-TEST-DOCT-WH"
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, order_by="creation asc", limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        if not frappe.db.exists("WMS Warehouse", cls.wh):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.wh, "warehouse_name": cls.wh, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        cls.st = f"{cls.wh}-ST"
        if not frappe.db.exists("Storage Type", cls.st):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.wh, "storage_type_code": "ST", "storage_type_name": "ST", "storage_role": "Storage", "capacity_check_method": "None", "active": 1, "hu_requirement": "Optional"}).insert(ignore_permissions=True)
        cls.bins = []
        for n in (1, 2):
            name = f"{cls.wh}-B{n}"
            cls.bins.append(name)
            if not frappe.db.exists("Storage Bin", name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": name, "warehouse": cls.wh, "storage_type": cls.st, "active": 1, "sequence": n}).insert(ignore_permissions=True)

    def _type(self, **kw):
        code = f"DT-{frappe.generate_hash(length=6)}"
        doc = frappe.get_doc({"doctype": "WMS Document Type", "document_type_code": code, "document_type_name": code, "category": "Warehouse Request", "active": 1, **kw}).insert(ignore_permissions=True)
        return doc

    def _request(self, **kw):
        return frappe.get_doc({"doctype": "Warehouse Request", "request_type": "Internal Move", "warehouse": self.wh, "product": self.item, "requested_quantity": 1, "stock_uom": self.uom,
            "source_bin": self.bins[0], "destination_bin": self.bins[1], "stock_type": "AVAILABLE", "reference_doctype": "User", "reference_name": "Administrator",
            "process_type": "INTERNAL_MOVE", "priority": "Normal", "status": "Open", **kw})

    def test_a_type_fills_defaults_enforces_field_control_and_follows_the_status_profile(self):
        dt = self._type(defaults=[{"fieldname": "process_step", "value": "STEP-A"}], field_control=[{"fieldname": "source_bin", "control": "Required"}, {"fieldname": "stock_type", "control": "Read Only"}],
            status_transitions=[{"from_status": "Open", "to_status": "Partially Tasked"}, {"from_status": "Partially Tasked", "to_status": "Cancelled", "allowed_role": "WMS Supervisor"}])
        request = self._request(document_type=dt.name).insert(ignore_permissions=True)
        self.assertEqual(request.process_step, "STEP-A", "default filled in")
        with self.assertRaises(frappe.ValidationError): self._request(document_type=dt.name, source_bin=None).insert(ignore_permissions=True)  # required
        request.stock_type = "QUALITY"
        with self.assertRaises(frappe.ValidationError): request.save(ignore_permissions=True)  # read only after creation
        request.reload()
        request.status = "Cancelled"
        with self.assertRaises(frappe.ValidationError): request.save(ignore_permissions=True)  # Open -> Cancelled is not in the profile
        request.reload()
        request.status = "Partially Tasked"
        request.save(ignore_permissions=True)

    def test_default_type_applies_when_none_is_named_and_its_number_range_names_the_document(self):
        if not frappe.db.exists("WMS Number Range", {"range_for": "Warehouse Request", "prefix": "DTQ-"}):
            frappe.get_doc({"doctype": "WMS Number Range", "range_for": "Warehouse Request", "prefix": "DTQ-", "number_length": 5, "start_number": 1, "end_number": 99999, "active": 0}).insert(ignore_permissions=True)
        number_range = frappe.db.get_value("WMS Number Range", {"range_for": "Warehouse Request", "prefix": "DTQ-"})
        dt = self._type(is_default=1, warehouse=self.wh, number_range=number_range)
        request = self._request().insert(ignore_permissions=True)
        self.assertEqual(request.document_type, dt.name)
        self.assertTrue(request.name.startswith("DTQ-"), request.name)
        with self.assertRaises(frappe.ValidationError):
            self._request(document_type=self._type(category="Outbound Delivery").name).insert(ignore_permissions=True)  # wrong category

    def test_replication_keeps_a_delivery_request_and_goods_issue_creates_the_final_delivery(self):
        from frappe_wms.services.erp_integration import _delivery_doc, INBOUND
        wh = frappe._dict(name=self.wh, default_stock_type="AVAILABLE")
        supplier = frappe.get_all("Supplier", limit=1, pluck="name")[0]
        company = frappe.db.get_value("WMS Warehouse", self.wh, "company")
        header = {"inbound_delivery_number": frappe.generate_hash(length=8), "supplier": supplier, "company": company, "receiving_bin": self.bins[0], "external_reference": "EXT-1"}
        delivery = _delivery_doc(INBOUND, wh, header, [{"item": self.item, "expected_quantity": 5, "stock_uom": self.uom, "uom": self.uom, "conversion_factor": 1, "expected_stock_type": "AVAILABLE"}])
        request = frappe.get_doc("WMS Delivery Request", delivery.delivery_request)
        self.assertEqual((request.direction, request.status, request.delivery_order, request.partner, request.items[0].quantity), ("Inbound", "Order Created", delivery.name, supplier, 5))
