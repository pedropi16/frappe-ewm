import json

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, flt, nowdate

from frappe_wms.api.erp_integration import complete_short, wms_status
from frappe_wms.api.inbound import create_putaway
from frappe_wms.api.outbound import allocate_delivery, create_pick_tasks, post_goods_issue_for_delivery
from frappe_wms.api.scanner import confirm_task
from frappe_wms.tests.bootstrap import TEST_CUSTOMER, TEST_ITEM, TEST_SUPPLIER, company_warehouse, pick_into_new_hu


def update_items(doc, qty):
    from erpnext.controllers.accounts_controller import update_child_qty_rate
    row = doc.items[0]
    trans = [{"docname": row.name, "item_code": row.item_code, "qty": qty, "rate": row.rate, "conversion_factor": 1,
              ("delivery_date" if doc.doctype == "Sales Order" else "schedule_date"): add_days(nowdate(), 3)}]
    update_child_qty_rate(doc.doctype, json.dumps(trans), doc.name)
    return frappe.get_doc(doc.doctype, doc.name)


class TestErpIntegration(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.company = frappe.get_all("Company", limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", TEST_ITEM, "stock_uom")
        if not frappe.db.exists("WMS Product", TEST_ITEM):
            frappe.get_doc({"doctype": "WMS Product", "item": TEST_ITEM, "stock_uom": cls.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "ERPI-PAL"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "ERPI-PAL", "hu_type_name": "ERP Integration Pallet"}).insert(ignore_permissions=True)
        cls.other_wh = company_warehouse(cls.company, "ERPI Not Managed")

    def setUp(self):
        frappe.set_user("Administrator")
        wh = f"ERPI-{frappe.generate_hash(length=5).upper()}"
        self.erp_wh = company_warehouse(self.company, wh)
        frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": wh, "warehouse_name": wh, "company": self.company,
                        "erpnext_warehouse": self.erp_wh, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        for code, role in (("GR", "Receiving"), ("BULK", "Storage"), ("DOOR", "Door")):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": wh, "storage_type_code": code, "storage_type_name": code, "storage_role": role,
                            "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        self.bins = {}
        for key, st in (("RECV", "GR"), ("BULK", "BULK"), ("STAGE", "GR")):
            self.bins[key] = frappe.get_doc({"doctype": "Storage Bin", "bin_code": f"{wh}-{key}", "warehouse": wh, "storage_type": f"{wh}-{st}",
                                             "active": 1, "sequence": 1}).insert(ignore_permissions=True).name
        w = frappe.get_doc("WMS Warehouse", wh)
        w.default_receiving_bin, w.default_shipping_bin, w.default_difference_bin = self.bins["RECV"], self.bins["STAGE"], self.bins["RECV"]
        w.save(ignore_permissions=True)
        frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": wh, "activity": "Putaway", "active": 1, "priority": 1,
                        "destination_storage_type": f"{wh}-BULK", "strategy": "Least Utilized Bin"}).insert(ignore_permissions=True)
        self.wh = wh

    def settings(self, **values):
        frappe.db.set_value("WMS Warehouse", self.wh, values)

    def stock(self, qty):
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "ERPI-PAL", "warehouse": self.wh,
                             "current_bin": self.bins["RECV"], "status": "Open"}).insert(ignore_permissions=True)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.wh,
                              "supplier": TEST_SUPPLIER, "receiving_bin": self.bins["RECV"],
                              "items": [{"line_number": 1, "item": TEST_ITEM, "expected_quantity": qty, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]}).insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.wh, "receiving_bin": self.bins["RECV"],
                             "items": [{"inbound_delivery_item": ind.items[0].name, "item": TEST_ITEM, "quantity": qty, "stock_uom": self.uom,
                                        "handling_unit": hu.name, "stock_type": "AVAILABLE"}]}).insert(ignore_permissions=True)
        gr.submit()
        confirm_task(create_putaway(gr.name)["warehouse_tasks"][0], confirmed_quantity=qty)
        return gr

    def sales_order(self, qty, extra_other_warehouse=False, submit=True):
        items = [{"item_code": TEST_ITEM, "qty": qty, "rate": 10, "warehouse": self.erp_wh, "delivery_date": add_days(nowdate(), 3)}]
        if extra_other_warehouse:
            items.append({"item_code": TEST_ITEM, "qty": 2, "rate": 10, "warehouse": self.other_wh, "delivery_date": add_days(nowdate(), 3)})
        so = frappe.get_doc({"doctype": "Sales Order", "customer": TEST_CUSTOMER, "company": self.company, "transaction_date": nowdate(),
                             "delivery_date": add_days(nowdate(), 3), "items": items}).insert(ignore_permissions=True)
        if submit: so.submit()
        return so

    def purchase_order(self, qty):
        po = frappe.get_doc({"doctype": "Purchase Order", "supplier": TEST_SUPPLIER, "company": self.company, "transaction_date": nowdate(),
                             "schedule_date": add_days(nowdate(), 3),
                             "items": [{"item_code": TEST_ITEM, "qty": qty, "rate": 10, "warehouse": self.erp_wh, "schedule_date": add_days(nowdate(), 3)}]}).insert(ignore_permissions=True)
        po.submit()
        return po

    def deliveries(self, doctype, source):
        return frappe.get_all(doctype, filters={"erp_source_name": source}, fields=["name", "docstatus", "status", "closed_short"], order_by="creation")

    # ------------------------------------------------------------------ Sales Order replication

    def test_sales_order_replicates_only_its_warehouse_lines_and_follows_changes(self):
        self.settings(outbound_replication="Sales Order Submitted", release_replicated_deliveries=1, outbound_follow_up="None")
        so = self.sales_order(5, extra_other_warehouse=True)
        [od] = self.deliveries("Outbound Delivery", so.name)
        od = frappe.get_doc("Outbound Delivery", od.name)
        self.assertEqual(od.docstatus, 1, "released on replication")
        self.assertEqual([(i.item, i.requested_quantity, i.sales_order_item) for i in od.items], [(TEST_ITEM, 5, so.items[0].name)],
                         "the line for the non-WMS warehouse stays in ERPNext")
        self.assertEqual(wms_status("Sales Order", so.name)[0]["name"], od.name)

        so = update_items(so, 3)
        self.assertEqual(frappe.db.get_value("Outbound Delivery Item", od.items[0].name, "requested_quantity"), 3)
        so = update_items(so, 7)
        self.assertEqual(frappe.db.get_value("Outbound Delivery Item", od.items[0].name, "requested_quantity"), 7)
        self.assertEqual(len(self.deliveries("Outbound Delivery", so.name)), 1, "a not-started delivery grows instead of a second one")

        so.reload()
        so.cancel()
        self.assertEqual(frappe.db.get_value("Outbound Delivery", od.name, "docstatus"), 2, "cancelling the order withdraws the delivery")

    def test_changes_are_refused_once_the_warehouse_has_started(self):
        self.stock(10)
        self.settings(outbound_replication="Sales Order Submitted", release_replicated_deliveries=1, outbound_follow_up="Allocate")
        so = self.sales_order(4)
        [od] = self.deliveries("Outbound Delivery", so.name)
        self.assertEqual(frappe.db.get_value("Outbound Delivery", od.name, "allocation_status"), "Fully Allocated")
        with self.assertRaisesRegex(frappe.ValidationError, "already allocated, picked or received"):
            update_items(so, 2)
        so.reload()
        with self.assertRaisesRegex(frappe.ValidationError, "already being executed"):
            so.cancel()
        # growing is still fine: the extra goes to a new delivery
        self.settings(outbound_follow_up="None")
        update_items(so, 6)
        dels = self.deliveries("Outbound Delivery", so.name)
        self.assertEqual(len(dels), 2)
        self.assertEqual(frappe.db.get_value("Outbound Delivery Item", {"parent": dels[1].name}, "requested_quantity"), 2)

    def test_block_policy_refuses_any_change(self):
        self.settings(outbound_replication="Sales Order Submitted", erp_change_policy="Block Changes After Replication")
        so = self.sales_order(5)
        with self.assertRaisesRegex(frappe.ValidationError, "does not accept order changes"):
            update_items(so, 4)

    # ------------------------------------------------------------------ Delivery Note draft = ECC delivery

    def test_draft_delivery_note_is_the_warehouse_delivery_and_goods_issue_posts_it(self):
        from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
        self.stock(10)
        self.settings(outbound_replication="Delivery Note Draft", release_replicated_deliveries=1)
        so = self.sales_order(4)
        self.assertEqual(self.deliveries("Outbound Delivery", so.name), [], "the order itself is not replicated in this mode")
        dn = make_delivery_note(so.name)
        for row in dn.items: row.warehouse = self.erp_wh
        dn.insert(ignore_permissions=True)
        dn.reload()
        self.assertTrue(dn.wms_outbound_delivery)
        od = frappe.get_doc("Outbound Delivery", dn.wms_outbound_delivery)
        self.assertEqual((od.erp_source_doctype, od.erp_source_name, od.items[0].requested_quantity), ("Delivery Note", dn.name, 4))
        with self.assertRaisesRegex(frappe.ValidationError, "submitted automatically"):
            dn.submit()

        allocate_delivery(od.name)
        [task] = create_pick_tasks(od.name)
        _, hu = pick_into_new_hu(task, confirmed_quantity=4)
        # straight to "loaded at a door" - the loading flow itself has its own tests
        frappe.db.set_value("Storage Bin", self.bins["STAGE"], "storage_type", f"{self.wh}-DOOR")
        frappe.db.set_value("Handling Unit", hu, "status", "Loaded")
        gi = frappe.get_doc("Goods Issue", post_goods_issue_for_delivery(od.name)["goods_issue"])

        dn.reload()
        self.assertEqual((dn.docstatus, gi.erpnext_delivery_note), (1, dn.name), "the same Delivery Note is posted, no second one")
        self.assertEqual(flt(dn.items[0].qty), 4)
        self.assertEqual(frappe.db.get_value("Sales Order", so.name, "per_delivered"), 100)
        # the posted Delivery Note is the warehouse's: cancelling it in ERPNext is refused
        with self.assertRaisesRegex(frappe.ValidationError, "posted by the warehouse"):
            dn.cancel()

    def test_draft_delivery_note_changes_follow_and_deletion_withdraws(self):
        from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
        self.settings(outbound_replication="Delivery Note Draft")
        so = self.sales_order(5)
        dn = make_delivery_note(so.name)
        for row in dn.items: row.warehouse = self.erp_wh
        dn.insert(ignore_permissions=True)
        first = frappe.db.get_value("Delivery Note", dn.name, "wms_outbound_delivery")
        dn.reload()
        dn.items[0].qty = 3
        dn.save(ignore_permissions=True)
        second = frappe.db.get_value("Delivery Note", dn.name, "wms_outbound_delivery")
        self.assertNotEqual(first, second)
        self.assertEqual(frappe.db.get_value("Outbound Delivery Item", {"parent": second}, "requested_quantity"), 3)
        self.assertEqual(frappe.db.get_value("Outbound Delivery", first, "docstatus"), 2)
        frappe.delete_doc("Delivery Note", dn.name, ignore_permissions=True)
        self.assertEqual(frappe.db.get_value("Outbound Delivery", second, "docstatus"), 2)

    # ------------------------------------------------------------------ Purchase Receipt draft = ASN

    def test_asn_purchase_receipt_is_posted_by_the_goods_receipt_and_short_completion_closes_the_order(self):
        from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
        self.settings(inbound_replication="Purchase Receipt Draft", close_short_orders=1)
        po = self.purchase_order(10)
        self.assertEqual(self.deliveries("Inbound Delivery", po.name), [])
        pr = make_purchase_receipt(po.name)
        for row in pr.items: row.warehouse = self.erp_wh
        pr.insert(ignore_permissions=True)
        pr.reload()
        ind = frappe.get_doc("Inbound Delivery", pr.wms_inbound_delivery)
        self.assertEqual((ind.docstatus, ind.items[0].expected_quantity, ind.items[0].purchase_order), (1, 10, po.name))

        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "ERPI-PAL", "warehouse": self.wh,
                             "current_bin": self.bins["RECV"], "status": "Open"}).insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.wh, "receiving_bin": self.bins["RECV"],
                             "items": [{"inbound_delivery_item": ind.items[0].name, "item": TEST_ITEM, "quantity": 6, "stock_uom": self.uom,
                                        "handling_unit": hu.name, "stock_type": "AVAILABLE"}]}).insert(ignore_permissions=True)
        gr.submit()
        pr.reload()
        self.assertEqual((pr.docstatus, flt(pr.items[0].qty), gr.erpnext_purchase_receipt), (1, 6, pr.name))
        self.assertEqual(frappe.db.get_value("Purchase Order", po.name, "per_received"), 60)

        complete_short("Inbound Delivery", ind.name, "Supplier shorted 4")
        ind.reload()
        self.assertEqual((ind.status, ind.closed_short, ind.items[0].expected_quantity), ("Completed", 1, 6))
        self.assertEqual(frappe.db.get_value("Purchase Order", po.name, "status"), "Closed", "order remainder closed per warehouse setting")

    def test_outbound_short_completion_and_warehouse_mirror_documents_are_protected(self):
        gr = self.stock(5)
        # the Stock Entry the warehouse posted cannot be cancelled from ERPNext
        se = frappe.get_doc("Stock Entry", gr.erpnext_stock_entry)
        with self.assertRaisesRegex(frappe.ValidationError, "posted by the warehouse"):
            se.cancel()
        self.settings(outbound_replication="Sales Order Submitted", close_short_orders=1, outbound_follow_up="None")
        so = self.sales_order(3)
        [od] = self.deliveries("Outbound Delivery", so.name)
        complete_short("Outbound Delivery", od.name, "Customer cancelled by phone")
        self.assertEqual(frappe.db.get_value("Outbound Delivery", od.name, ["status", "closed_short"]), ("Completed", 1))
        self.assertEqual(frappe.db.get_value("Sales Order", so.name, "status"), "Closed")
