import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, flt, now_datetime, nowdate

from frappe_wms.services import archiving
from frappe_wms.services.stock import post_entries, rebuild_balances
from frappe_wms.tests.bootstrap import TEST_ITEM


class TestLedgerArchiving(IntegrationTestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        self.wh = f"WMS-TEST-ARC-{frappe.generate_hash(length=5).upper()}"
        frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": self.wh, "warehouse_name": self.wh, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "Storage Type", "warehouse": self.wh, "storage_type_code": "ST", "storage_type_name": "ST", "storage_role": "Storage",
                        "capacity_check_method": "None", "active": 1}).insert(ignore_permissions=True)
        self.bins = []
        for n in (1, 2):
            self.bins.append(frappe.get_doc({"doctype": "Storage Bin", "bin_code": f"{self.wh}-{n}", "warehouse": self.wh, "storage_type": f"{self.wh}-ST",
                                             "active": 1, "sequence": n}).insert(ignore_permissions=True).name)
        self.uom = frappe.db.get_value("Item", TEST_ITEM, "stock_uom")
        if not frappe.db.exists("WMS Product", TEST_ITEM):
            frappe.get_doc({"doctype": "WMS Product", "item": TEST_ITEM, "stock_uom": self.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)

    def _post(self, bin_name, qty, days_ago, mt="701"):
        names = post_entries([{"warehouse": self.wh, "product": TEST_ITEM, "storage_bin": bin_name, "stock_type": "AVAILABLE",
                               "stock_uom": self.uom, "quantity": qty, "movement_type": mt}], "WMS Warehouse", self.wh, f"arc-test:{frappe.generate_hash(length=8)}")
        frappe.db.set_value("WMS Stock Ledger Entry", names[0], "posting_datetime", add_days(now_datetime(), -days_ago))

    def _ledger(self):
        return frappe.get_all("WMS Stock Ledger Entry", filters={"warehouse": self.wh}, fields=["storage_bin", "quantity", "movement_type", "posting_datetime"])

    def _balances(self):
        return {b.storage_bin: flt(b.quantity) for b in frappe.get_all("WMS Stock Balance", filters={"warehouse": self.wh}, fields=["storage_bin", "quantity"]) if flt(b.quantity)}

    def test_archive_keeps_balances_and_fifo_and_writes_the_file(self):
        self._post(self.bins[0], 10, 800)
        self._post(self.bins[0], -4, 700, "702")
        self._post(self.bins[1], 3, 750)
        self._post(self.bins[1], -3, 740, "702")   # emptied position: no carry-forward
        self._post(self.bins[0], 2, 10)             # recent: stays
        rebuild_balances(self.wh)                   # balances as the (backdated) ledger has them
        before = self._balances()
        first_receipt = frappe.db.get_value("WMS Stock Balance", {"warehouse": self.wh, "storage_bin": self.bins[0]}, "first_receipt_date")

        run = frappe.get_doc({"doctype": "WMS Ledger Archive Run", "cutoff_date": add_days(nowdate(), -400), "warehouse": self.wh, "status": "Queued"}).insert()
        self.assertEqual(archiving.execute_run(run.name), "Completed")
        run.reload()
        self.assertEqual((run.entries_archived, run.positions_carried_forward), (4, 1))
        self.assertTrue(run.archive_file)

        ledger = self._ledger()
        self.assertEqual(sorted((r.movement_type, flt(r.quantity)) for r in ledger), [("701", 2.0), ("999", 6.0)])
        carry = next(r for r in ledger if r.movement_type == "999")
        self.assertEqual(carry.posting_datetime, first_receipt, "the carry-forward keeps the position's first receipt date")
        self.assertEqual(self._balances(), before)
        rebuild_balances(self.wh)
        self.assertEqual(self._balances(), before, "a rebuild from the ledger gives the same balances")

        import gzip
        content = frappe.get_doc("File", {"file_url": run.archive_file}).get_content()
        self.assertEqual(len(gzip.decompress(content).decode().strip().splitlines()), 4)

    def test_reversal_of_archived_document_is_refused_and_retention_validated(self):
        run = frappe.get_doc({"doctype": "WMS Ledger Archive Run", "cutoff_date": add_days(nowdate(), -400), "warehouse": self.wh, "status": "Queued"}).insert()
        archiving.execute_run(run.name)
        old_doc = frappe._dict(doctype="Goods Receipt", name="GR-OLD", warehouse=self.wh, posting_datetime=add_days(now_datetime(), -500))
        with self.assertRaises(frappe.ValidationError):
            archiving.ensure_reversible(old_doc)
        archiving.ensure_reversible(frappe._dict(doctype="Goods Receipt", name="GR-NEW", warehouse=self.wh, posting_datetime=now_datetime()))
        with self.assertRaises(frappe.ValidationError):
            archiving.start_run(add_days(nowdate(), -30), self.wh)
        settings = frappe.get_single("WMS Settings")
        settings.ledger_retention_months = 6
        with self.assertRaises(frappe.ValidationError):
            settings.save()
