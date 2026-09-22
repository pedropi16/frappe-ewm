import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, now_datetime

from frappe_wms.services.allocation import _candidate_balances
from frappe_wms.services.stock import post_entries, _balance_name


class TestRemovalRules(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "REMOVALRULE-TEST-WH"
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        cls.uom = "Nos"

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-ST"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "ST", "storage_type_name": "ST", "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        cls.bins = [f"{cls.warehouse}-BIN{i}" for i in range(3)]
        for bin_name in cls.bins:
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-ST", "active": 1, "sequence": 1}).insert(ignore_permissions=True)

    def _make_item(self, item_code):
        if not frappe.db.exists("Item", item_code):
            item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
            frappe.get_doc({"doctype": "Item", "item_code": item_code, "item_name": item_code, "item_group": item_group, "stock_uom": self.uom, "is_stock_item": 1}).insert(ignore_permissions=True)
        return item_code

    def _row(self, item):
        return frappe._dict(item=item, required_stock_type="AVAILABLE", required_batch=None, required_serial_no=None)

    def _seed(self, item, bin_name, qty, *, first_receipt_date=None, shelf_life_expiry_date=None):
        entry = {"warehouse": self.warehouse, "product": item, "storage_bin": bin_name,
            "stock_type": "AVAILABLE", "stock_uom": self.uom, "quantity": qty, "movement_type": "701"}
        if shelf_life_expiry_date: entry["shelf_life_expiry_date"] = shelf_life_expiry_date
        post_entries([entry], "Storage Bin", bin_name, f"test-removal-rule:{frappe.generate_hash(length=8)}")
        if first_receipt_date:
            name = _balance_name({"warehouse": self.warehouse, "product": item, "batch_no": None, "serial_no": None,
                "handling_unit": None, "storage_bin": bin_name, "stock_type": "AVAILABLE"})
            frappe.db.set_value("WMS Stock Balance", name, "first_receipt_date", first_receipt_date)

    def _make_rule(self, item, strategy, **kwargs):
        for existing in frappe.get_all("Removal Rule", filters={"warehouse": self.warehouse, "item": item}, pluck="name"):
            frappe.delete_doc("Removal Rule", existing, force=True, ignore_permissions=True)
        rule = frappe.get_doc({"doctype": "Removal Rule", "warehouse": self.warehouse, "item": item, "priority": 1, "strategy": strategy, "active": 1, **kwargs})
        rule.insert(ignore_permissions=True)
        return rule

    def test_fifo_orders_by_receipt_date_ignoring_expiry(self):
        item = self._make_item("TEST-RR-FIFO")
        self._seed(item, self.bins[0], 5, first_receipt_date=add_days(now_datetime(), -1), shelf_life_expiry_date=add_days(now_datetime(), 5))
        self._seed(item, self.bins[1], 5, first_receipt_date=add_days(now_datetime(), -5), shelf_life_expiry_date=add_days(now_datetime(), 1))
        self._make_rule(item, "FIFO")
        result = _candidate_balances(self._row(item), self.warehouse)
        self.assertEqual(result[0].storage_bin, self.bins[1], "FIFO must pick the earliest receipt date first, regardless of expiry")

    def test_lifo_orders_by_receipt_date_descending(self):
        item = self._make_item("TEST-RR-LIFO")
        self._seed(item, self.bins[0], 5, first_receipt_date=add_days(now_datetime(), -1))
        self._seed(item, self.bins[1], 5, first_receipt_date=add_days(now_datetime(), -5))
        self._make_rule(item, "LIFO")
        result = _candidate_balances(self._row(item), self.warehouse)
        self.assertEqual(result[0].storage_bin, self.bins[0], "LIFO must pick the most recent receipt first")

    def test_fefo_orders_by_soonest_expiry_ignoring_receipt_date(self):
        item = self._make_item("TEST-RR-FEFO")
        self._seed(item, self.bins[0], 5, first_receipt_date=add_days(now_datetime(), -5), shelf_life_expiry_date=add_days(now_datetime(), 30))
        self._seed(item, self.bins[1], 5, first_receipt_date=add_days(now_datetime(), -1), shelf_life_expiry_date=add_days(now_datetime(), 2))
        self._make_rule(item, "FEFO")
        result = _candidate_balances(self._row(item), self.warehouse)
        self.assertEqual(result[0].storage_bin, self.bins[1], "FEFO must pick the soonest-expiring balance first, regardless of receipt date")

    def test_stringent_fifo_rejects_sort_fields(self):
        item = self._make_item("TEST-RR-STRINGENT")
        with self.assertRaises(frappe.ValidationError):
            self._make_rule(item, "Stringent FIFO", sort_fields=[{"field": "quantity", "direction": "Asc"}])

    def test_partial_quantity_first_orders_by_smallest_quantity(self):
        item = self._make_item("TEST-RR-PARTIAL")
        self._seed(item, self.bins[0], 8)
        self._seed(item, self.bins[1], 2)
        self._make_rule(item, "Partial Quantity First")
        result = _candidate_balances(self._row(item), self.warehouse)
        self.assertEqual(result[0].storage_bin, self.bins[1])

    def test_by_quantity_orders_by_largest_quantity(self):
        item = self._make_item("TEST-RR-BYQTY")
        self._seed(item, self.bins[0], 2)
        self._seed(item, self.bins[1], 8)
        self._make_rule(item, "By Quantity")
        result = _candidate_balances(self._row(item), self.warehouse)
        self.assertEqual(result[0].storage_bin, self.bins[1])

    def test_fixed_bin_restricts_to_configured_bin_only(self):
        item = self._make_item("TEST-RR-FIXEDBIN")
        self._seed(item, self.bins[0], 5)
        self._seed(item, self.bins[1], 5)
        self._make_rule(item, "Fixed Bin", fixed_bin=self.bins[1])
        result = _candidate_balances(self._row(item), self.warehouse)
        self.assertEqual([b.storage_bin for b in result], [self.bins[1]])

    def test_fixed_bin_requires_fixed_bin_field(self):
        item = self._make_item("TEST-RR-FIXEDBIN-INVALID")
        with self.assertRaises(frappe.ValidationError):
            self._make_rule(item, "Fixed Bin")

    def test_custom_sort_fields_override_named_strategy(self):
        item = self._make_item("TEST-RR-CUSTOM")
        self._seed(item, self.bins[0], 2)
        self._seed(item, self.bins[1], 8)
        # FIFO would normally ignore quantity entirely - sort_fields overrides it to sort by
        # quantity descending instead, proving the override actually takes effect.
        self._make_rule(item, "FIFO", sort_fields=[{"field": "quantity", "direction": "Desc"}])
        result = _candidate_balances(self._row(item), self.warehouse)
        self.assertEqual(result[0].storage_bin, self.bins[1])

    def test_no_matching_rule_preserves_fefo_then_fifo_default(self):
        item = self._make_item("TEST-RR-DEFAULT")
        self.assertFalse(frappe.get_all("Removal Rule", filters={"warehouse": self.warehouse, "item": item}))
        self._seed(item, self.bins[0], 5, first_receipt_date=add_days(now_datetime(), -5), shelf_life_expiry_date=add_days(now_datetime(), 30))
        self._seed(item, self.bins[1], 5, first_receipt_date=add_days(now_datetime(), -1), shelf_life_expiry_date=add_days(now_datetime(), 2))
        result = _candidate_balances(self._row(item), self.warehouse)
        self.assertEqual(result[0].storage_bin, self.bins[1], "with no configured Removal Rule, FEFO-then-FIFO must remain the default")
