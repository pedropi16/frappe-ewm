"""Per-diem storage billing: every day the scheduler records, per warehouse and stock owner, how many
HUs / bins / units were stored (Storage Usage Day). A Billing Rate with activity Storage and basis
Per HU Day / Per Bin Day / Per Unit Day then bills a customer the sum over a period for the owners
that are that customer (WMS Stock Owner > Partner Type Customer)."""
import frappe
from frappe.utils import flt, getdate, nowdate

BASIS_FIELD = {"Per HU Day": "hu_count", "Per Bin Day": "bin_count", "Per Unit Day": "quantity"}


def snapshot_storage_usage(date=None):
    day = str(getdate(date or nowdate()))
    for wh in frappe.get_all("WMS Warehouse", filters={"active": 1}, pluck="name"):
        rows = frappe.db.sql("""select stock_owner, count(distinct nullif(handling_unit, '')) hus, count(distinct storage_bin) bins, sum(quantity) qty
            from `tabWMS Stock Balance` where warehouse=%s and quantity > 0 and ifnull(stock_owner, '') != '' group by stock_owner""", wh, as_dict=True)
        frappe.db.delete("Storage Usage Day", {"warehouse": wh, "usage_date": day})
        for r in rows:
            frappe.get_doc({"doctype": "Storage Usage Day", "warehouse": wh, "stock_owner": r.stock_owner, "usage_date": day,
                            "hu_count": r.hus, "bin_count": r.bins, "quantity": flt(r.qty)}).insert(ignore_permissions=True)


def storage_usage_totals(warehouse, customer, from_date, to_date):
    """{basis: total} over the period for the owners that are this customer."""
    owners = frappe.get_all("WMS Stock Owner", filters={"partner_type": "Customer", "partner": customer}, pluck="name")
    if not owners: return {}
    row = frappe.db.sql("""select sum(hu_count), sum(bin_count), sum(quantity) from `tabStorage Usage Day`
        where warehouse=%s and stock_owner in %s and usage_date between %s and %s""", (warehouse, owners, from_date, to_date))[0]
    return {basis: flt(v) for basis, v in zip(BASIS_FIELD, row)}
