import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate, add_to_date, get_time, now_datetime

from frappe_wms.services.wave import generate_waves_from_templates, auto_release_due_waves
from frappe_wms.api.inbound import create_putaway
from frappe_wms.api.scanner import confirm_task


class TestWaveTemplates(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "WAVETPL-TEST-WH"
        cls.recv_bin = f"{cls.warehouse}-RECV"
        cls.bulk_bin = f"{cls.warehouse}-BULK"
        cls.stage_bin = f"{cls.warehouse}-STAGE"
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        cls.customer = frappe.get_all("Customer", limit=1, pluck="name")[0]
        cls.supplier = frappe.get_all("Supplier", limit=1, pluck="name")[0]
        company = frappe.get_all("Company", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-GR"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "GR", "storage_type_name": "GR", "storage_role": "Receiving", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-BULK"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "BULK", "storage_type_name": "BULK", "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for bin_name, st in ((cls.recv_bin, f"{cls.warehouse}-GR"), (cls.bulk_bin, f"{cls.warehouse}-BULK"), (cls.stage_bin, f"{cls.warehouse}-GR")):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": st, "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Bin Determination Rule", {"warehouse": cls.warehouse, "activity": "Putaway"}):
            frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": cls.warehouse, "activity": "Putaway", "active": 1, "priority": 1, "destination_storage_type": f"{cls.warehouse}-BULK", "strategy": "Least Utilized Bin"}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", {"item": cls.item}):
            frappe.get_doc({"doctype": "WMS Product", "item": cls.item, "stock_uom": cls.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "WAVETPL-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "WAVETPL-PALLET", "hu_type_name": "WaveTpl Pallet"}).insert(ignore_permissions=True)

    def _receive_and_putaway(self, qty):
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "WAVETPL-PALLET", "warehouse": self.warehouse, "current_bin": self.recv_bin, "status": "Open"})
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

    def tearDown(self):
        for existing in frappe.get_all("Wave Template", filters={"warehouse": self.warehouse}, pluck="name"):
            frappe.delete_doc("Wave Template", existing, force=True, ignore_permissions=True)
        for wave in frappe.get_all("WMS Wave", filters={"warehouse": self.warehouse}, pluck="name"):
            frappe.delete_doc("WMS Wave", wave, force=True, ignore_permissions=True)
        for delivery in frappe.get_all("Outbound Delivery", filters={"warehouse": self.warehouse}, pluck="name"):
            doc = frappe.get_doc("Outbound Delivery", delivery)
            if doc.docstatus == 1: doc.cancel()
            frappe.delete_doc("Outbound Delivery", delivery, force=True, ignore_permissions=True)

    def _make_delivery(self, qty=1):
        obd = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse,
            "customer": self.customer, "delivery_date": nowdate(), "staging_bin": self.stage_bin,
            "items": [{"line_number": 1, "item": self.item, "requested_quantity": qty, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"}]})
        obd.insert(ignore_permissions=True)
        obd.submit()
        return obd

    def _make_template(self, cutoff_time, auto_release=1):
        code = frappe.generate_hash(length=8)
        return frappe.get_doc({"doctype": "Wave Template", "template_code": code, "template_name": code, "warehouse": self.warehouse,
            "priority": "Normal", "picking_strategy": "Single Order", "cutoff_time": cutoff_time, "auto_release": auto_release, "active": 1}).insert(ignore_permissions=True)

    def test_template_sweeps_matching_draft_deliveries_into_a_new_wave(self):
        delivery = self._make_delivery()
        template = self._make_template("23:59:59")

        created = generate_waves_from_templates()
        self.assertEqual(len(created), 1)
        wave = frappe.get_doc("WMS Wave", created[0])
        self.assertEqual(wave.wave_template, template.name)
        self.assertEqual([r.outbound_delivery for r in wave.deliveries], [delivery.name])

    def test_delivery_already_on_an_open_wave_is_not_swept_again(self):
        self._make_delivery()
        self._make_template("23:59:59")
        first_run = generate_waves_from_templates()
        self.assertEqual(len(first_run), 1)
        second_run = generate_waves_from_templates()
        self.assertEqual(len(second_run), 0, "the delivery is already on an open wave from the first run")

    def test_auto_release_fires_after_cutoff_and_not_before(self):
        self._receive_and_putaway(10)
        self._make_delivery()
        past_cutoff = get_time(add_to_date(now_datetime(), minutes=-5))
        self._make_template(past_cutoff, auto_release=1)
        due = generate_waves_from_templates()
        self.assertEqual(len(due), 1)

        released = auto_release_due_waves()
        self.assertEqual(len(released), 1)
        wave = frappe.get_doc("WMS Wave", frappe.get_all("WMS Wave", filters={"warehouse": self.warehouse}, pluck="name")[0])
        self.assertEqual(wave.status, "Released")

    def test_auto_release_skips_template_before_its_cutoff(self):
        self._make_delivery()
        future_cutoff = get_time(add_to_date(now_datetime(), minutes=5))
        self._make_template(future_cutoff, auto_release=1)
        generate_waves_from_templates()

        released = auto_release_due_waves()
        self.assertEqual(len(released), 0)

    def test_auto_release_disabled_never_releases_regardless_of_cutoff(self):
        self._make_delivery()
        past_cutoff = get_time(add_to_date(now_datetime(), minutes=-5))
        self._make_template(past_cutoff, auto_release=0)
        generate_waves_from_templates()

        released = auto_release_due_waves()
        self.assertEqual(len(released), 0)
