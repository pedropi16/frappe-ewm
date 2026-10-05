import frappe
from frappe import _


def execute(filters=None):
    filters = frappe._dict(filters or {})
    if not filters.warehouse: frappe.throw(_("Choose a warehouse"))
    values = {"warehouse": filters.warehouse, "from_date": filters.from_date or "1900-01-01", "to_date": filters.to_date or "2999-12-31"}
    rows = frappe.db.sql("""select picking_status, goods_issue_status, count(*) as deliveries from `tabOutbound Delivery`
        where warehouse=%(warehouse)s and docstatus=1 and delivery_date between %(from_date)s and %(to_date)s group by picking_status, goods_issue_status order by picking_status, goods_issue_status""", values, as_dict=True)
    columns = [{"fieldname": "picking_status", "label": _("Picking"), "fieldtype": "Data", "width": 160}, {"fieldname": "goods_issue_status", "label": _("Goods Issue"), "fieldtype": "Data", "width": 160},
        {"fieldname": "deliveries", "label": _("Deliveries"), "fieldtype": "Int", "width": 110}]
    return columns, rows, None, None
