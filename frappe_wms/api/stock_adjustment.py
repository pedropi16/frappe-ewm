import frappe
from frappe_wms.services import posting_change as pc
from frappe_wms.services import stock_adjustment as sa
from frappe_wms.services.concurrency import retry_on_deadlock
from frappe_wms.services.locks import require_free_many
from frappe_wms.utils import parse_json


@frappe.whitelist()
@retry_on_deadlock
def post_stock_adjustment(name):
    return sa.post_stock_adjustment(name)


@frappe.whitelist()
@retry_on_deadlock
def cancel_stock_adjustment(name):
    return sa.cancel_stock_adjustment(name)


@frappe.whitelist()
@retry_on_deadlock
def scrap_stock(lines, reason):
    """Scrap the marked stock lines (a list of {name: WMS Stock Balance, quantity?}); the whole free quantity unless a quantity is given."""
    lines = parse_json(lines, "lines")
    require_free_many([("WMS Stock Balance", l["name"]) for l in lines if l.get("name")])
    return [sa.create_stock_adjustment("Scrapping", reason, l, l.get("quantity")) for l in lines]


@frappe.whitelist()
@retry_on_deadlock
def create_unplanned_stock(warehouse, product, storage_bin, quantity, reason, stock_type="AVAILABLE", batch_no=None, serial_no=None, handling_unit=None, stock_owner=None,
                           entitled_party=None, country_of_origin=None, special_stock_type=None, special_stock_ref=None, valuation_rate=None):
    line = {k: v for k, v in {"warehouse": warehouse, "product": product, "storage_bin": storage_bin, "stock_type": stock_type, "batch_no": batch_no, "serial_no": serial_no,
            "handling_unit": handling_unit, "stock_owner": stock_owner, "entitled_party": entitled_party, "country_of_origin": country_of_origin,
            "special_stock_type": special_stock_type, "special_stock_ref": special_stock_ref, "valuation_rate": valuation_rate}.items() if v}
    return sa.create_stock_adjustment("Unplanned Receipt", reason, line, quantity)


@frappe.whitelist()
@retry_on_deadlock
def change_stock(lines, reason, to_stock_type=None, changes=None):
    """Posting change of the marked stock lines. changes: {to_product, to_batch_no, to_stock_owner, to_entitled_party, to_country_of_origin, to_special_stock_type, to_special_stock_ref}."""
    changes = parse_json(changes, "changes") if changes else {}
    out = []
    lines = parse_json(lines, "lines")
    require_free_many([("WMS Stock Balance", l["name"]) for l in lines if l.get("name")])
    for l in lines:
        name = pc.create_posting_change(l, reason, to_stock_type, changes)
        pc.post_posting_change(name)
        out.append(name)
    return out
