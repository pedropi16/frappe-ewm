import frappe
from frappe import _
from frappe.utils import flt, now_datetime
from frappe_wms.services.stock import post_entries
from frappe_wms.services.task import my_resource
from frappe_wms.utils import require_role

def list_open_counts(user=None):
    require_role("WMS Operator", "WMS Inventory Controller", "WMS Supervisor")
    resource = my_resource(user)
    filters = {"status": ["in", ["Draft", "Counting", "Counted"]]}
    if resource: filters["warehouse"] = resource.warehouse
    counts = frappe.get_list("WMS Physical Inventory Count", filters=filters,
        fields=["name", "warehouse", "storage_bin", "storage_type", "product", "status", "count_date"],
        order_by="count_date asc, creation asc", limit=20)
    # Blind counting (SAP EWM's own default RF behavior): a counter who can see the book
    # quantity before counting just copies it in, which defeats the point of counting at all -
    # reproduced by reading the code: book_quantity was always returned here with nothing
    # gating it. The server still has the real value for variance math either way.
    blind = bool(frappe.get_cached_value("WMS Settings", "WMS Settings", "blind_counting"))
    item_fields = ["name", "product", "handling_unit", "storage_bin", "stock_type", "stock_uom", "counted_quantity"]
    if not blind: item_fields.append("book_quantity")
    for count in counts:
        count["items"] = frappe.get_all("WMS Physical Inventory Count Item", filters={"parent": count.name, "status": "Open"},
            fields=item_fields)
    return counts

def _release_blocked_bins(doc):
    names = [b for b in (doc.blocked_bins or "").split(",") if b]
    if names: frappe.db.set_value("Storage Bin", {"name": ["in", names]}, "removal_blocked", 0)

def snapshot_count(count_name):
    require_role("WMS Operator", "WMS Inventory Controller", "WMS Supervisor")
    doc = frappe.get_doc("WMS Physical Inventory Count", count_name, for_update=True)
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
    # Block every bin this count touches for removal so a pick or move can't slip stock out from
    # under it between snapshot and posting (reproduced by reading the code: nothing here ever
    # touched removal_blocked - a pick mid-count posted its own loss, then the count posted a
    # SECOND loss for the very same units). Only bins not already blocked for some other reason
    # are recorded here, and only those get released again once this count posts or is cancelled.
    bin_names = sorted({b.storage_bin for b in balances if b.storage_bin})
    newly_blocked = [b for b in bin_names if not frappe.db.get_value("Storage Bin", b, "removal_blocked")]
    if newly_blocked:
        frappe.db.set_value("Storage Bin", {"name": ["in", newly_blocked]}, "removal_blocked", 1)
    doc.blocked_bins = ",".join(newly_blocked)
    doc.status = "Counting"
    doc.counted_by = frappe.session.user
    doc.counted_at = now_datetime()
    doc.save(ignore_permissions=True)
    return {"count": doc.name, "items": len(doc.items)}

def add_found_line(count_name, product, storage_bin, stock_type, quantity, batch_no=None, serial_no=None, handling_unit=None, stock_uom=None):
    # A count could only ever confirm or reduce what the book already expected - there was no
    # way to record stock physically found where the book shows nothing at all (a real gain, not
    # noise). A found line has no book quantity to compare against, so its full quantity IS the
    # variance and it's immediately "Counted" - there's nothing left to count against.
    require_role("WMS Operator", "WMS Inventory Controller", "WMS Supervisor")
    doc = frappe.get_doc("WMS Physical Inventory Count", count_name, for_update=True)
    if doc.status not in ("Counting", "Counted"): frappe.throw(_("Count is not open for recording"))
    quantity = flt(quantity)
    if quantity <= 0: frappe.throw(_("Found quantity must be greater than zero"))
    stock_uom = stock_uom or frappe.db.get_value("WMS Product", {"item": product}, "stock_uom") or frappe.db.get_value("Item", product, "stock_uom")
    doc.append("items", {
        "product": product, "batch_no": batch_no, "serial_no": serial_no, "handling_unit": handling_unit,
        "storage_bin": storage_bin, "stock_type": stock_type, "stock_uom": stock_uom,
        "book_quantity": 0, "counted_quantity": quantity, "variance": quantity, "status": "Counted",
    })
    if storage_bin and not frappe.db.get_value("Storage Bin", storage_bin, "removal_blocked"):
        blocked = set(filter(None, (doc.blocked_bins or "").split(",")))
        blocked.add(storage_bin)
        doc.blocked_bins = ",".join(sorted(blocked))
        frappe.db.set_value("Storage Bin", storage_bin, "removal_blocked", 1)
    doc.status = "Counted" if not any(r.status == "Open" for r in doc.items) else doc.status
    doc.save(ignore_permissions=True)
    return {"count": doc.name, "row": doc.items[-1].name}

def cancel_count(count_name):
    require_role("WMS Inventory Controller", "WMS Supervisor")
    doc = frappe.get_doc("WMS Physical Inventory Count", count_name, for_update=True)
    if doc.status in ("Posted", "Cancelled"): frappe.throw(_("A posted or already-cancelled count cannot be cancelled"))
    _release_blocked_bins(doc)
    doc.status = "Cancelled"
    doc.save(ignore_permissions=True)
    return {"count": doc.name, "status": doc.status}

def record_counts(count_name, counted_quantities):
    # counted_quantities: {row_name: counted_quantity}
    require_role("WMS Operator", "WMS Inventory Controller", "WMS Supervisor")
    doc = frappe.get_doc("WMS Physical Inventory Count", count_name, for_update=True)
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

def _mirror_posted_rows(doc, rows):
    # Mirror to ERPNext exactly the lines _post_row just posted to the WMS ledger, as they post -
    # not once at the very end. A count can post in several passes (within-tolerance lines
    # immediately, the rest after a recount or a supervisor's approval), and waiting for the last
    # pass meant lines already in the WMS ledger never reached ERPNext while the count sat Under
    # Review, or at all if it was then cancelled (reproduced in a simulated shift: a 1-unit loss
    # posted in WMS, the count left pending recount, ERPNext still showing the old quantity).
    # Each line posts once (its status moves to Posted), so mirroring per pass can't double up.
    if not rows: return
    from frappe_wms.services.erp_sync_queue import dispatch
    dispatch("count_rows", doc, rows=[r.name for r in rows])
    # The operation writes the entries onto the count row in the database; keep the in-memory
    # doc in step so a later doc.save() in this request doesn't blank them again.
    doc.erpnext_gain_stock_entry, doc.erpnext_loss_stock_entry = frappe.db.get_value(
        doc.doctype, doc.name, ["erpnext_gain_stock_entry", "erpnext_loss_stock_entry"])

def post_count(count_name):
    require_role("WMS Inventory Controller", "WMS Supervisor")
    doc = frappe.get_doc("WMS Physical Inventory Count", count_name, for_update=True)
    if doc.status != "Counted": frappe.throw(_("All lines must be counted before posting"))
    posted_now = []
    for i, row in enumerate(doc.items, 1):
        if row.status in ("Posted", "Pending Approval"): continue
        variance = flt(row.variance)
        if variance == 0:
            row.status = "Posted"
            continue
        group = _matching_tolerance_group(doc.warehouse, row.product)
        if not group or _within_tolerance(group, variance, row.book_quantity):
            _post_row(doc, row, i); posted_now.append(row)
            continue
        row.tolerance_group = group.name
        if group.requires_recount and not row.recount_count:
            row.status = "Pending Recount"
        elif group.requires_approval:
            row.status = "Pending Approval"
        else:
            _post_row(doc, row, i); posted_now.append(row)
    _mirror_posted_rows(doc, posted_now)
    doc.status = _recompute_pic_status(doc)
    if doc.status == "Posted":
        doc.posted_by = frappe.session.user
        doc.posted_at = now_datetime()
        _release_blocked_bins(doc)
    doc.save(ignore_permissions=True)
    return {"count": doc.name, "status": doc.status}

def request_recount(count_name):
    require_role("WMS Inventory Controller", "WMS Supervisor")
    doc = frappe.get_doc("WMS Physical Inventory Count", count_name, for_update=True)
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
    doc = frappe.get_doc("WMS Physical Inventory Count", count_name, for_update=True)
    if doc.status != "Under Review": frappe.throw(_("Count is not awaiting approval"))
    posted_now = []
    for i, row in enumerate(doc.items, 1):
        if row.status != "Pending Approval": continue
        _post_row(doc, row, i); posted_now.append(row)
    _mirror_posted_rows(doc, posted_now)
    doc.approved_by = frappe.session.user
    doc.approved_at = now_datetime()
    doc.review_remarks = remarks
    doc.status = _recompute_pic_status(doc)
    if doc.status == "Posted":
        doc.posted_by = frappe.session.user
        doc.posted_at = now_datetime()
        _release_blocked_bins(doc)
    doc.save(ignore_permissions=True)
    return {"count": doc.name, "status": doc.status}

def analyze_differences(warehouse, from_date=None, to_date=None, product=None):
    require_role("WMS Operator", "WMS Inventory Controller", "WMS Supervisor")
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
