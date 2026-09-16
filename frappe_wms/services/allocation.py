import frappe
from frappe import _
from frappe.utils import flt
from frappe_wms.services.task import task_names_for_allocations
from frappe_wms.services.warehouse_order import release_next_in_sequence, sync_warehouse_order
from frappe_wms.utils import require_role

def _candidate_balances(row, warehouse):
    filters={"warehouse":warehouse,"product":row.item,"stock_type":row.required_stock_type,"available_quantity":[">",0]}
    if row.required_batch: filters["batch_no"]=row.required_batch
    if row.required_serial_no: filters["serial_no"]=row.required_serial_no
    return frappe.get_all("WMS Stock Balance",filters=filters,fields=["*"],order_by="first_receipt_date asc")

def allocate_delivery(delivery_name):
    require_role("WMS Operator", "WMS Picker", "WMS Supervisor")
    doc=frappe.get_doc("Outbound Delivery",delivery_name); doc.check_permission("write")
    if doc.docstatus != 1: frappe.throw(_("Outbound Delivery must be submitted before it can be allocated"))
    created=[]
    for row in doc.items:
        needed=flt(row.requested_quantity)-flt(row.allocated_quantity)
        for stock in _candidate_balances(row,doc.warehouse):
            if needed<=0: break
            qty=min(needed,flt(stock.available_quantity))
            allocation=frappe.get_doc({"doctype":"Stock Allocation","outbound_delivery":doc.name,"outbound_delivery_item":row.name,"product":row.item,"stock_balance":stock.name,"storage_bin":stock.storage_bin,"handling_unit":stock.handling_unit,"batch_no":stock.batch_no,"serial_no":stock.serial_no,"stock_type":stock.stock_type,"allocated_quantity":qty,"status":"Allocated"})
            allocation.insert(); created.append(allocation.name)
            frappe.db.set_value("WMS Stock Balance",stock.name,{"allocated_quantity":flt(stock.allocated_quantity)+qty,"available_quantity":flt(stock.available_quantity)-qty})
            needed-=qty
        row.db_set("allocated_quantity",flt(row.requested_quantity)-needed)
    doc.db_set("allocation_status","Fully Allocated" if all(flt(x.allocated_quantity)>=flt(x.requested_quantity) for x in doc.items) else "Partially Allocated")
    return created

def cancel_allocations_for_delivery(delivery_name):
    # Unwinds whatever an Outbound Delivery's cancellation can still safely undo: releases each
    # Stock Allocation's reservation back onto its WMS Stock Balance and hard-cancels any
    # still-open (unconfirmed) Pick task referencing it. events.deliveries.before_cancel_outbound_delivery
    # already guarantees nothing here has been physically picked yet.
    allocations = frappe.get_all("Stock Allocation", filters={"outbound_delivery": delivery_name, "status": ["!=", "Cancelled"]},
        fields=["name", "stock_balance", "allocated_quantity"])
    if not allocations: return
    task_names = task_names_for_allocations([a.name for a in allocations])
    warehouse_orders = set()
    for task_name in task_names:
        task = frappe.db.get_value("Warehouse Task", task_name, ["docstatus", "warehouse_order"], as_dict=True)
        if task and task.docstatus == 0:
            frappe.db.set_value("Warehouse Task", task_name, {"status": "Cancelled", "docstatus": 2}, update_modified=True)
            if task.warehouse_order: warehouse_orders.add(task.warehouse_order)
    for wo_name in warehouse_orders:
        release_next_in_sequence(wo_name)
        sync_warehouse_order(wo_name)
    for allocation in allocations:
        if allocation.stock_balance and frappe.db.exists("WMS Stock Balance", allocation.stock_balance):
            balance = frappe.get_doc("WMS Stock Balance", allocation.stock_balance)
            balance.allocated_quantity = max(flt(balance.allocated_quantity) - flt(allocation.allocated_quantity), 0)
            balance.available_quantity = flt(balance.quantity) - balance.allocated_quantity
            balance.flags.ignore_permissions = True
            balance.save()
        frappe.db.set_value("Stock Allocation", allocation.name, "status", "Cancelled")
