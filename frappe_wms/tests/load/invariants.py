"""Consistency checks to run after a load/simulation run (or on any live site, read-only).

    bench --site <site> execute frappe_wms.tests.load.invariants.run --kwargs "{'warehouse': 'MAD1'}"

Each check returns a list of violations; run() prints and returns {check: [violations...]}.
None of these should ever fire, whatever mix of concurrent traffic produced the data:

  ledger_vs_balance      every WMS Stock Balance.quantity equals the sum of its ledger rows
  negative_balances      no negative quantity in a warehouse that forbids negative stock
  allocation_bounds      0 <= allocated_quantity <= quantity on every balance row
  allocation_vs_balance  a balance row's allocated_quantity equals its open Stock Allocations
  wms_vs_erpnext         per item, WMS on-hand equals ERPNext Bin.actual_qty (mirrored warehouse)
  inbound_progress       Inbound Delivery Item.received_quantity equals its submitted GR rows
  over_receipt           no line received beyond its expected quantity
  hu_location            an HU holding stock sits in the bin its stock balances say it's in
  hu_hierarchy           a nested HU is in the same bin as its parent
  task_double_posting    a confirmed task never posted more than its confirmed quantity
  wo_single_owner        a Warehouse Order's tasks are never assigned to a different resource
  outbound_progress      delivery lines never picked/issued beyond requested quantity
"""
import frappe
from frappe.utils import flt

EPS = 0.0001


def ledger_vs_balance(wh):
    rows = frappe.db.sql("""
        select b.name, b.product, b.storage_bin, b.handling_unit, b.quantity, coalesce(l.qty, 0) as ledger_qty
        from `tabWMS Stock Balance` b
        left join (
            select warehouse, product, ifnull(batch_no,'') batch_no, ifnull(serial_no,'') serial_no, ifnull(handling_unit,'') handling_unit,
                   storage_bin, stock_type, sum(quantity) qty
            from `tabWMS Stock Ledger Entry` where warehouse=%(wh)s
            group by 1,2,3,4,5,6,7) l
          on l.warehouse=b.warehouse and l.product=b.product and l.batch_no=ifnull(b.batch_no,'') and l.serial_no=ifnull(b.serial_no,'')
         and l.handling_unit=ifnull(b.handling_unit,'') and l.storage_bin=b.storage_bin and l.stock_type=b.stock_type
        where b.warehouse=%(wh)s and abs(b.quantity - coalesce(l.qty, 0)) > %(eps)s""", {"wh": wh, "eps": EPS}, as_dict=True)
    return [dict(r) for r in rows]


def negative_balances(wh):
    if frappe.db.get_value("WMS Warehouse", wh, "allow_negative_stock"): return []
    return frappe.db.sql("select name, product, storage_bin, handling_unit, quantity from `tabWMS Stock Balance` where warehouse=%s and quantity < -%s", (wh, EPS), as_dict=True)


def allocation_bounds(wh):
    return frappe.db.sql("""select name, product, storage_bin, handling_unit, quantity, allocated_quantity from `tabWMS Stock Balance`
        where warehouse=%s and (allocated_quantity < -%s or allocated_quantity - quantity > %s)""", (wh, EPS, EPS), as_dict=True)


def allocation_vs_balance(wh):
    return frappe.db.sql("""
        select b.name, b.product, b.storage_bin, b.handling_unit, b.allocated_quantity, coalesce(a.open_qty, 0) open_allocations
        from `tabWMS Stock Balance` b
        left join (select stock_balance, sum(greatest(allocated_quantity - ifnull(picked_quantity,0), 0)) open_qty
                   from `tabStock Allocation` where status in ('Allocated', 'Partially Picked', 'Released') group by stock_balance) a on a.stock_balance=b.name
        where b.warehouse=%s and abs(b.allocated_quantity - coalesce(a.open_qty, 0)) > %s""", (wh, EPS), as_dict=True)


def wms_vs_erpnext(wh):
    erp_wh = frappe.db.get_value("WMS Warehouse", wh, "erpnext_warehouse")
    if not erp_wh: return []
    return frappe.db.sql("""
        select coalesce(w.product, e.item_code) item, coalesce(w.qty, 0) wms_qty, coalesce(e.qty, 0) erpnext_qty
        from (select product, sum(quantity) qty from `tabWMS Stock Balance` where warehouse=%(wh)s group by product) w
        left join (select item_code, sum(actual_qty) qty from `tabBin` where warehouse=%(erp)s group by item_code) e on e.item_code=w.product
        where abs(coalesce(w.qty,0) - coalesce(e.qty,0)) > %(eps)s
        union
        select e.item_code, 0, e.qty from (select item_code, sum(actual_qty) qty from `tabBin` where warehouse=%(erp)s group by item_code) e
        where e.qty <> 0 and not exists (select 1 from `tabWMS Stock Balance` b where b.warehouse=%(wh)s and b.product=e.item_code)""",
        {"wh": wh, "erp": erp_wh, "eps": EPS}, as_dict=True)


def inbound_progress(wh):
    return frappe.db.sql("""
        select i.parent, i.name, i.item, i.expected_quantity, i.received_quantity, coalesce(g.qty, 0) receipted
        from `tabInbound Delivery Item` i join `tabInbound Delivery` d on d.name=i.parent
        left join (select gi.inbound_delivery_item, sum(gi.quantity) qty from `tabGoods Receipt Item` gi
                   join `tabGoods Receipt` gr on gr.name=gi.parent where gr.docstatus=1 group by gi.inbound_delivery_item) g on g.inbound_delivery_item=i.name
        where d.warehouse=%s and abs(coalesce(i.received_quantity,0) - coalesce(g.qty,0)) > %s""", (wh, EPS), as_dict=True)


def over_receipt(wh):
    return frappe.db.sql("""
        select i.parent, i.name, i.item, i.expected_quantity, g.qty receipted
        from `tabInbound Delivery Item` i join `tabInbound Delivery` d on d.name=i.parent
        join (select gi.inbound_delivery_item, sum(gi.quantity) qty from `tabGoods Receipt Item` gi
              join `tabGoods Receipt` gr on gr.name=gi.parent where gr.docstatus=1 group by gi.inbound_delivery_item) g on g.inbound_delivery_item=i.name
        where d.warehouse=%s and g.qty - i.expected_quantity > %s""", (wh, EPS), as_dict=True)


def hu_location(wh):
    return frappe.db.sql("""
        select h.name, h.current_bin, group_concat(distinct b.storage_bin) stock_bins
        from `tabHandling Unit` h join `tabWMS Stock Balance` b on b.handling_unit=h.name and b.quantity > %s
        where b.warehouse=%s group by h.name, h.current_bin
        having count(distinct b.storage_bin) > 1 or max(b.storage_bin) <> ifnull(h.current_bin, '')""", (EPS, wh), as_dict=True)


def hu_hierarchy(wh):
    return frappe.db.sql("""select c.name, c.current_bin, c.parent_hu, p.current_bin parent_bin from `tabHandling Unit` c
        join `tabHandling Unit` p on p.name=c.parent_hu where c.warehouse=%s and ifnull(c.current_bin,'') <> ifnull(p.current_bin,'')""", wh, as_dict=True)


def task_double_posting(wh):
    # ledger rows posted by a task (positive side) vs the task's confirmed quantity
    return frappe.db.sql("""
        select t.name, t.task_type, t.confirmed_quantity, l.qty posted
        from `tabWarehouse Task` t
        join (select reference_name, sum(quantity) qty from `tabWMS Stock Ledger Entry`
              where reference_doctype='Warehouse Task' and quantity > 0 and ifnull(reversal_of,'')='' and storage_bin not in
                (select name from `tabStorage Bin` where storage_type in (select name from `tabStorage Type` where storage_role='Difference'))
              group by reference_name) l on l.reference_name=t.name
        where t.warehouse=%s and l.qty - t.confirmed_quantity > %s""", (wh, EPS), as_dict=True)


def wo_single_owner(wh):
    return frappe.db.sql("""
        select o.name, o.assigned_resource, group_concat(distinct t.assigned_resource) task_resources
        from `tabWarehouse Order` o join `tabWarehouse Task` t on t.warehouse_order=o.name
        where o.warehouse=%s and ifnull(o.assigned_resource,'') <> '' and ifnull(t.assigned_resource,'') <> ''
        group by o.name, o.assigned_resource having count(distinct t.assigned_resource) > 1 or max(t.assigned_resource) <> o.assigned_resource""", wh, as_dict=True)


def outbound_progress(wh):
    return frappe.db.sql("""
        select i.parent, i.name, i.item, i.requested_quantity, i.allocated_quantity, i.picked_quantity, i.issued_quantity
        from `tabOutbound Delivery Item` i join `tabOutbound Delivery` d on d.name=i.parent
        where d.warehouse=%s and (i.picked_quantity - i.requested_quantity > %s or i.issued_quantity - i.requested_quantity > %s
              or i.issued_quantity - i.picked_quantity > %s)""", (wh, EPS, EPS, EPS), as_dict=True)


CHECKS = [ledger_vs_balance, negative_balances, allocation_bounds, allocation_vs_balance, wms_vs_erpnext, inbound_progress,
          over_receipt, hu_location, hu_hierarchy, task_double_posting, wo_single_owner, outbound_progress]


def run(warehouse="MAD1", limit=15):
    out = {}
    for check in CHECKS:
        try:
            rows = check(warehouse)
        except Exception as e:  # a broken check must not hide the others
            rows = [{"check_error": repr(e)}]
        out[check.__name__] = [dict(r) for r in rows]
        print(f"{check.__name__:<24} {len(rows):>5} violation(s)")
        for r in rows[:limit]:
            print("    ", {k: (float(v) if hasattr(v, 'is_integer') else v) for k, v in dict(r).items()})
    return {k: len(v) for k, v in out.items()}
