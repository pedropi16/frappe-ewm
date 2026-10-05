import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from frappe_wms.api.inbound import create_putaway
from frappe_wms.api.outbound import allocate_delivery, create_pick_tasks
from frappe_wms.api.scanner import confirm_task
from frappe_wms.tests.bootstrap import pick_into_new_hu


class TestErpOwner(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "ERPOWN-TEST-WH"
        cls.recv_bin, cls.bulk_bin, cls.stage_bin = (f"{cls.warehouse}-{c}" for c in ("RECV", "BULK", "STAGE"))
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, order_by="creation asc", limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        cls.supplier = frappe.get_all("Supplier", limit=1, pluck="name")[0]
        cls.customer = frappe.get_all("Customer", limit=1, pluck="name")[0]
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        cls.wh = frappe.get_doc("WMS Warehouse", cls.warehouse)
        for code, role in (("GR", "Receiving"), ("BULK", "Storage"), ("DOOR", "Door")):
            if not frappe.db.exists("Storage Type", f"{cls.warehouse}-{code}"):
                frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": code, "storage_type_name": code, "storage_role": role, "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for bin_name, st in ((cls.recv_bin, "GR"), (cls.bulk_bin, "BULK"), (cls.stage_bin, "GR")):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-{st}", "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not cls.wh.default_receiving_bin:
            cls.wh.default_receiving_bin, cls.wh.default_shipping_bin, cls.wh.default_difference_bin = cls.recv_bin, cls.stage_bin, cls.recv_bin
            cls.wh.save(ignore_permissions=True)
        if not frappe.db.exists("Bin Determination Rule", {"warehouse": cls.warehouse, "activity": "Putaway"}):
            frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": cls.warehouse, "activity": "Putaway", "active": 1, "priority": 1, "destination_storage_type": f"{cls.warehouse}-BULK", "strategy": "Least Utilized Bin"}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", {"item": cls.item}):
            frappe.get_doc({"doctype": "WMS Product", "item": cls.item, "stock_uom": cls.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "ERPOWN-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "ERPOWN-PALLET", "hu_type_name": "ERPOWN Pallet"}).insert(ignore_permissions=True)
        for code in ("OWN-A", "PETD-1"):
            if not frappe.db.exists("WMS Stock Owner", code):
                frappe.get_doc({"doctype": "WMS Stock Owner", "owner_code": code, "owner_name": code, "partner_type": "Other"}).insert(ignore_permissions=True)

    def _erpnext_qty(self, **dims):
        return sum(frappe.get_all("Stock Ledger Entry", filters={"warehouse": self.wh.erpnext_warehouse, "item_code": self.item, "is_cancelled": 0, **dims}, pluck="actual_qty"))

    def test_owner_travels_to_erpnext_on_receipt_and_issue_and_into_its_stock_ledger(self):
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "ERPOWN-PALLET", "warehouse": self.warehouse, "current_bin": self.recv_bin, "status": "Open"}).insert(ignore_permissions=True)
        own_before = self._erpnext_qty(wms_stock_owner="OWN-A")
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.recv_bin,
            "stock_owner": "OWN-A", "entitled_party": "PETD-1", "items": [{"line_number": 1, "item": self.item, "expected_quantity": 7, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]}).insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": self.recv_bin,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": self.item, "quantity": 7, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]}).insert(ignore_permissions=True)
        gr.submit()
        se = frappe.get_doc("Stock Entry", gr.erpnext_stock_entry)
        self.assertEqual((se.items[0].to_wms_stock_owner, se.items[0].to_wms_entitled_party), ("OWN-A", "PETD-1"), "the receipt row carries the owner")
        self.assertEqual(self._erpnext_qty(wms_stock_owner="OWN-A") - own_before, 7, "ERPNext's own stock ledger holds it per owner")

        confirm_task(create_putaway(gr.name)["warehouse_tasks"][0], confirmed_quantity=7)
        obd = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "customer": self.customer, "delivery_date": nowdate(),
            "staging_bin": self.stage_bin, "stock_owner": "OWN-A", "entitled_party": "PETD-1", "items": [{"line_number": 1, "item": self.item, "requested_quantity": 3, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"}]}).insert(ignore_permissions=True)
        obd.submit()
        allocate_delivery(obd.name)
        _result, pick_hu = pick_into_new_hu(create_pick_tasks(obd.name)[0], confirmed_quantity=3)
        frappe.db.set_value("Storage Bin", self.stage_bin, "storage_type", f"{self.warehouse}-DOOR")
        frappe.db.set_value("Handling Unit", pick_hu, "status", "Loaded")
        gi = frappe.get_doc({"doctype": "Goods Issue", "outbound_delivery": obd.name, "warehouse": self.warehouse, "staging_bin": self.stage_bin,
            "items": [{"outbound_delivery_item": obd.items[0].name, "item": self.item, "quantity": 3, "stock_uom": self.uom, "handling_unit": pick_hu, "stock_type": "AVAILABLE"}]}).insert(ignore_permissions=True)
        gi.submit()
        issue = frappe.get_doc("Stock Entry", gi.erpnext_stock_entry)
        self.assertEqual((issue.items[0].wms_stock_owner, issue.items[0].wms_entitled_party), ("OWN-A", "PETD-1"), "the issue takes the owner from the stock it booked out")
        self.assertEqual(self._erpnext_qty(wms_stock_owner="OWN-A") - own_before, 4)

    def test_the_owner_on_an_erp_order_goes_onto_the_wms_delivery(self):
        from frappe_wms.services.erp_integration import _header
        po = frappe._dict(doctype="Purchase Order", name="PO-OWNER-TEST", supplier=self.supplier, company="C", wms_stock_owner="OWN-A", wms_entitled_party="PETD-1")
        so = frappe._dict(doctype="Sales Order", name="SO-OWNER-TEST", customer=self.customer, wms_stock_owner="OWN-A")
        wh = frappe._dict(name=self.warehouse, default_receiving_bin=self.recv_bin, default_shipping_bin=self.stage_bin)
        inbound, outbound = _header(po, wh), _header(so, wh)
        self.assertEqual((inbound["stock_owner"], inbound["entitled_party"], outbound["stock_owner"], outbound["entitled_party"]), ("OWN-A", "PETD-1", "OWN-A", None))
