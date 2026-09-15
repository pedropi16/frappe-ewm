import frappe
from frappe import _
from frappe.utils import flt
from frappe_wms.services.task import create_pick_tasks as _create_pick_tasks

def _candidate_balances(row, warehouse):
    filters={"warehouse":warehouse,"product":row.item,"stock_type":row.required_stock_type,"available_quantity":[">",0]}
    if row.required_batch: filters["batch_no"]=row.required_batch
    if row.required_serial_no: filters["serial_no"]=row.required_serial_no
    return frappe.get_all("WMS Stock Balance",filters=filters,fields=["*"],order_by="first_receipt_date asc")

@frappe.whitelist()
def allocate_delivery(delivery_name):
    doc=frappe.get_doc("Outbound Delivery",delivery_name); doc.check_permission("write")
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

@frappe.whitelist()
def create_pick_tasks(delivery_name):
    return _create_pick_tasks(delivery_name)
