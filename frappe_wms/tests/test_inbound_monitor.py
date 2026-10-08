import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.api.inbound import create_and_submit_goods_receipt, inbound_overview, plan_open_putaway


class TestInboundMonitor(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "WMS-TEST-INM-WH"
        cls.company = frappe.get_all("Company", limit=1, pluck="name")[0]
        cls.supplier = frappe.get_all("Supplier", limit=1, pluck="name")[0]
        cls.uom = "Nos"
        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": cls.company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        for code, role in (("GR", "Receiving"), ("ST", "Storage")):
            if not frappe.db.exists("Storage Type", f"{cls.warehouse}-{code}"):
                frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": code, "storage_type_name": code, "storage_role": role, "capacity_check_method": "None", "active": 1,
                                "allow_mixed_products": 1, "allow_mixed_stock_types": 1, "hu_requirement": "Optional"}).insert(ignore_permissions=True)
        cls.bins = {}
        for code, st in (("RECV", "GR"), ("A1", "ST"), ("A2", "ST")):
            name = cls.bins[code] = f"{cls.warehouse}-{code}"
            if not frappe.db.exists("Storage Bin", name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": name, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-{st}", "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Bin Determination Rule", {"warehouse": cls.warehouse, "activity": "Putaway"}):
            frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": cls.warehouse, "activity": "Putaway", "active": 1, "priority": 1,
                            "destination_storage_type": f"{cls.warehouse}-ST", "fixed_destination_bin": cls.bins["A1"], "strategy": "Least Utilized Bin"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "INM-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "INM-PALLET", "hu_type_name": "INM pallet"}).insert(ignore_permissions=True)
        group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
        cls.items = []
        for n in (1, 2):
            item = f"INM-TEST-ITEM-{n}"
            if not frappe.db.exists("Item", item):
                frappe.get_doc({"doctype": "Item", "item_code": item, "item_name": item, "item_group": group, "stock_uom": cls.uom, "is_stock_item": 1}).insert(ignore_permissions=True)
            if not frappe.db.exists("WMS Product", {"item": item}):
                frappe.get_doc({"doctype": "WMS Product", "item": item, "stock_uom": cls.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
            cls.items.append(item)

    def _delivery(self):
        d = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.bins["RECV"],
            "items": [{"line_number": n + 1, "item": item, "expected_quantity": 10, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"} for n, item in enumerate(self.items)]}).insert(ignore_permissions=True)
        d.submit()
        return d

    def _rows(self, delivery, **per_row):
        return [{"inbound_delivery_item": r.name, "item": r.item, "quantity": 10, "stock_uom": self.uom, "stock_type": "AVAILABLE", "hu_type": "INM-PALLET", **per_row.get(r.item, {})} for r in delivery.items]

    def test_goods_are_packed_into_shared_and_new_handling_units_and_put_away(self):
        d = self._delivery()
        out = create_and_submit_goods_receipt(d.name, self._rows(d, **{self.items[0]: {"handling_unit": "#1"}, self.items[1]: {"handling_unit": "#1"}}))
        hus = {r.handling_unit for r in frappe.get_doc("Goods Receipt", out["goods_receipt"]).items}
        self.assertEqual(len(hus), 1, "both lines packed into the one new handling unit")
        self.assertEqual(len(out["warehouse_tasks"]), 2)
        from frappe_wms.api.monitor import get_delivery_view, task_document
        self.assertEqual(task_document(out["warehouse_tasks"][0]), ["Inbound Delivery", d.name], "a task opens its delivery's screen")
        view = get_delivery_view("Inbound Delivery", d.name)
        self.assertEqual((len(view["tasks"]), len(view["handling_units"]), len(view["receipts"])), (2, 1, 1))
        d2 = self._delivery()
        out = create_and_submit_goods_receipt(d2.name, self._rows(d2))  # blank: a new unit each
        self.assertEqual(len({r.handling_unit for r in frappe.get_doc("Goods Receipt", out["goods_receipt"]).items}), 2)

    def test_direct_placement_puts_the_goods_exactly_where_asked(self):
        d = self._delivery()
        out = create_and_submit_goods_receipt(d.name, self._rows(d, **{self.items[0]: {"destination_bin": self.bins["A2"]}}))
        tasks = {frappe.db.get_value("Warehouse Task", t, "product"): frappe.db.get_value("Warehouse Task", t, "destination_bin") for t in out["warehouse_tasks"]}
        self.assertEqual(tasks[self.items[0]], self.bins["A2"])
        self.assertEqual(tasks[self.items[1]], self.bins["A1"], "the other line follows the putaway rule")
        d2 = self._delivery()
        with self.assertRaisesRegex(frappe.ValidationError, "cannot be used as a destination"):
            frappe.db.set_value("Storage Bin", self.bins["A2"], "putaway_blocked", 1)
            try:
                create_and_submit_goods_receipt(d2.name, self._rows(d2, **{self.items[0]: {"destination_bin": self.bins["A2"]}}))
            finally:
                frappe.db.set_value("Storage Bin", self.bins["A2"], "putaway_blocked", 0)

    def test_tasks_can_be_created_later_for_one_bin(self):
        d = self._delivery()
        out = create_and_submit_goods_receipt(d.name, self._rows(d), create_tasks=0)
        self.assertEqual(out["warehouse_tasks"], [])
        view = inbound_overview(d.name)
        self.assertEqual((len(view["open_requests"]), len(view["handling_units"]), len(view["tasks"])), (2, 2, 0))
        self.assertEqual({i["received"] for i in view["items"]}, {10})
        planned = plan_open_putaway(d.name, self.bins["A2"])
        self.assertEqual({frappe.db.get_value("Warehouse Task", t, "destination_bin") for t in planned["warehouse_tasks"]}, {self.bins["A2"]})
        self.assertEqual(inbound_overview(d.name)["open_requests"], [])
