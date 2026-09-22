import frappe
from frappe import _
from frappe.utils import flt, now_datetime
from frappe_wms.services.stock import post_entries
from frappe_wms.services.erpnext_sync import sync_physical_inventory_count
from frappe_wms.services.task import my_resource
from frappe_wms.utils import require_role

def list_open_counts(user=None):
    resource = my_resource(user)
    filters = {"status": ["in", ["Draft", "Counting", "Counted"]]}
    if resource: filters["warehouse"] = resource.warehouse
    counts = frappe.get_list("WMS Physical Inventory Count", filters=filters,
        fields=["name", "warehouse", "storage_bin", "storage_type", "product", "status", "count_date"],
        order_by="count_date asc, creation asc", limit=20)
    for count in counts:
        count["items"] = frappe.get_all("WMS Physical Inventory Count Item", filters={"parent": count.name, "status": "Open"},
            fields=["name", "product", "handling_unit", "storage_bin", "stock_type", "stock_uom", "book_quantity", "counted_quantity"])
    return counts

def snapshot_count(count_name):
    frappe.db.sql("select name from `tabWMS Physical Inventory Count` where name=%s for update", count_name)
    doc = frappe.get_doc("WMS Physical Inventory Count", count_name)
    if doc.status != "Draft": frappe.throw(_("Count has already been started"))
    filters = {"warehouse": doc.warehouse, "quantity": [">", 0]}
    if doc.storage_bin: filters["storage_bin"] = doc.storage_bin
    if doc.product: filters["product"] = doc.product
    balances = frappe.get_all("WMS Stock Balance", filters=filters, fields=["*"])
    if doc.storage_type:
        bins_in_type = set(frappe.get_all("Storage Bin", filters={"warehouse": doc.warehouse, "storage_type": doc.storage_type}, pluck="name"))
        balances = [b for b in balances if b.storage_bin in bins_in_type]
    if not balances: frappe.throw(_("No stock found for the given count scope"))
    doc.items = []
    for balance in balances:
        doc.append("items", {
            "product": balance.product, "batch_no": balance.batch_no, "serial_no": balance.serial_no,
            "handling_unit": balance.handling_unit, "storage_bin": balance.storage_bin, "stock_type": balance.stock_type,
            "stock_uom": balance.stock_uom, "book_quantity": balance.quantity, "status": "Open",
        })
    doc.status = "Counting"
    doc.counted_by = frappe.session.user
    doc.counted_at = now_datetime()
    doc.save(ignore_permissions=True)
    return {"count": doc.name, "items": len(doc.items)}

def record_counts(count_name, counted_quantities):
    # counted_quantities: {row_name: counted_quantity}
    frappe.db.sql("select name from `tabWMS Physical Inventory Count` where name=%s for update", count_name)
    doc = frappe.get_doc("WMS Physical Inventory Count", count_name)
    if doc.status not in {"Counting", "Counted"}: frappe.throw(_("Count is not open for recording"))
    for row in doc.items:
        if row.name not in counted_quantities: continue
        row.counted_quantity = flt(counted_quantities[row.name])
        row.variance = row.counted_quantity - flt(row.book_quantity)
        row.status = "Counted"
    # Not "every row is Counted" - a recount only resets the rows sent back for review to
    # Open, while everything else already sits in Posted/Pending Approval from the first
    # pass. Completion means no row is still waiting on its first count.
    doc.status = "Counted" if not any(r.status == "Open" for r in doc.items) else "Counting"
    doc.save(ignore_permissions=True)
    return {"count": doc.name, "status": doc.status}

def _matching_tolerance_group(warehouse, item):
    item_group = frappe.db.get_value("Item", item, "item_group")
    for rule in frappe.get_all("Count Tolerance Group", filters={"active": 1},
            fields=["name", "warehouse", "item_group", "tolerance_percentage", "tolerance_quantity", "requires_recount", "requires_approval"],
            order_by="priority asc"):
        if rule.warehouse and rule.warehouse != warehouse: continue
        if rule.item_group and rule.item_group != item_group: continue
        return rule
    return None

def _within_tolerance(group, variance, book_quantity):
    variance = abs(flt(variance))
    if group.tolerance_quantity and variance <= flt(group.tolerance_quantity): return True
    # A variance against a zero book quantity is never "within tolerance" - finding stock
    # where none was expected is inherently notable, not a rounding artifact.
    if not book_quantity: return False
    return variance / abs(flt(book_quantity)) * 100 <= flt(group.tolerance_percentage)

def _post_row(doc, row, i):
    variance = flt(row.variance)
    if variance != 0:
        movement_type = "701" if variance > 0 else "702"
        entry = {
            "warehouse": doc.warehouse, "product": row.product, "batch_no": row.batch_no, "serial_no": row.serial_no,
            "handling_unit": row.handling_unit, "storage_bin": row.storage_bin, "stock_type": row.stock_type,
            "quantity": variance, "stock_uom": row.stock_uom, "movement_type": movement_type, "reference_line": row.name,
        }
        post_entries([entry], doc.doctype, doc.name, f"PIC:{doc.name}:{i}")
    row.status = "Posted"

def _recompute_pic_status(doc):
    if all(r.status == "Posted" for r in doc.items): return "Posted"
    if any(r.status in ("Pending Recount", "Pending Approval") for r in doc.items): return "Under Review"
    return doc.status

def post_count(count_name):
    require_role("WMS Inventory Controller", "WMS Supervisor")
    frappe.db.sql("select name from `tabWMS Physical Inventory Count` where name=%s for update", count_name)
    doc = frappe.get_doc("WMS Physical Inventory Count", count_name)
    if doc.status != "Counted": frappe.throw(_("All lines must be counted before posting"))
    for i, row in enumerate(doc.items, 1):
        if row.status in ("Posted", "Pending Approval"): continue
        variance = flt(row.variance)
        if variance == 0:
            row.status = "Posted"
            continue
        group = _matching_tolerance_group(doc.warehouse, row.product)
        if not group or _within_tolerance(group, variance, row.book_quantity):
            _post_row(doc, row, i)
            continue
        row.tolerance_group = group.name
        if group.requires_recount and not row.recount_count:
            row.status = "Pending Recount"
        elif group.requires_approval:
            row.status = "Pending Approval"
        else:
            _post_row(doc, row, i)
    # sync_physical_inventory_count aggregates variance across every row in doc.items, not
    # just the ones this call touched - it's a one-shot design, so it must only ever fire
    # once the count is fully resolved (no row still Pending Recount/Approval), or a later
    # recount/approval pass would double-post the rows this pass already covered.
    doc.status = _recompute_pic_status(doc)
    if doc.status == "Posted":
        gain_entry, loss_entry = sync_physical_inventory_count(doc)
        if gain_entry: doc.erpnext_gain_stock_entry = gain_entry
        if loss_entry: doc.erpnext_loss_stock_entry = loss_entry
        doc.posted_by = frappe.session.user
        doc.posted_at = now_datetime()
    doc.save(ignore_permissions=True)
    return {"count": doc.name, "status": doc.status}

def request_recount(count_name):
    require_role("WMS Inventory Controller", "WMS Supervisor")
    frappe.db.sql("select name from `tabWMS Physical Inventory Count` where name=%s for update", count_name)
    doc = frappe.get_doc("WMS Physical Inventory Count", count_name)
    for row in doc.items:
        if row.status != "Pending Recount": continue
        row.recount_count = (row.recount_count or 0) + 1
        row.counted_quantity = None
        row.variance = None
        row.status = "Open"
    doc.status = "Counting" if any(r.status == "Open" for r in doc.items) else doc.status
    doc.save(ignore_permissions=True)
    return {"count": doc.name, "status": doc.status}

def approve_variance(count_name, remarks=None):
    require_role("WMS Supervisor")
    frappe.db.sql("select name from `tabWMS Physical Inventory Count` where name=%s for update", count_name)
    doc = frappe.get_doc("WMS Physical Inventory Count", count_name)
    if doc.status != "Under Review": frappe.throw(_("Count is not awaiting approval"))
    for i, row in enumerate(doc.items, 1):
        if row.status != "Pending Approval": continue
        _post_row(doc, row, i)
    doc.approved_by = frappe.session.user
    doc.approved_at = now_datetime()
    doc.review_remarks = remarks
    doc.status = _recompute_pic_status(doc)
    if doc.status == "Posted":
        gain_entry, loss_entry = sync_physical_inventory_count(doc)
        if gain_entry: doc.erpnext_gain_stock_entry = gain_entry
        if loss_entry: doc.erpnext_loss_stock_entry = loss_entry
        doc.posted_by = frappe.session.user
        doc.posted_at = now_datetime()
    doc.save(ignore_permissions=True)
    return {"count": doc.name, "status": doc.status}

def analyze_differences(warehouse, from_date=None, to_date=None, product=None):
    # Per-product variance history from posted count lines - a row that ever passed through
    # Pending Recount/Pending Approval (recount_count > 0 or a tolerance_group is set) is
    # flagged as an over-tolerance event, distinguishing a real supervisor-reviewed
    # discrepancy from routine within-tolerance noise.
    return frappe.db.sql("""
        select i.product,
            sum(case when i.variance > 0 then i.variance else 0 end) as total_gain,
            sum(case when i.variance < 0 then i.variance else 0 end) as total_loss,
            sum(i.variance) as net_variance,
            sum(case when i.recount_count > 0 or i.tolerance_group is not null then 1 else 0 end) as over_tolerance_events,
            count(*) as line_count
        from `tabWMS Physical Inventory Count Item` i
        join `tabWMS Physical Inventory Count` p on p.name = i.parent
        where p.warehouse = %(warehouse)s and i.status = 'Posted'
            and (%(product)s is null or i.product = %(product)s)
            and (%(from_date)s is null or p.count_date >= %(from_date)s)
            and (%(to_date)s is null or p.count_date <= %(to_date)s)
        group by i.product
        order by abs(sum(i.variance)) desc
    """, {"warehouse": warehouse, "product": product, "from_date": from_date, "to_date": to_date}, as_dict=True)
