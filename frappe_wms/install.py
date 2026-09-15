import frappe

def _insert(doctype, values):
    name=values.get(frappe.scrub(doctype)+"_code") or values.get("movement_type_code") or values.get("process_type_code") or values.get("stock_type_code") or values.get("exception_code")
    if name and frappe.db.exists(doctype,name): return
    doc=frappe.get_doc({"doctype":doctype,**values}); doc.insert(ignore_permissions=True)

def after_install():
    for code,name,category,alloc,pick,ship in [
        ("AVAILABLE","Available","Available",1,1,1),("QUALITY","Quality Inspection","Quality",0,0,0),("WAREHOUSE_BLOCKED","Warehouse Blocked","Blocked",0,0,0),("PRODUCTION_BLOCKED","Production Blocked","Blocked",0,0,0),("DAMAGED","Damaged","Damaged",0,0,0),("SCRAP","Scrap","Scrap",0,0,0)]:
        _insert("WMS Stock Type",{"stock_type_code":code,"stock_type_name":name,"availability_category":category,"available_for_allocation":alloc,"picking_allowed":pick,"shipping_allowed":ship,"movement_allowed":1,"active":1})
    movement=[("101","Goods Receipt","Receipt","Increase"),("102","Goods Receipt Reversal","Receipt Reversal","Decrease"),("201","Putaway","Putaway","Transfer"),("301","Internal Movement","Internal Movement","Transfer"),("401","Picking","Picking","Transfer"),("402","Pick Reversal","Pick Reversal","Transfer"),("501","Stock Type Change","Stock Type Change","Transfer"),("601","Goods Issue","Goods Issue","Decrease"),("602","Goods Issue Reversal","Goods Issue Reversal","Increase"),("701","Inventory Gain","Inventory Gain","Increase"),("702","Inventory Loss","Inventory Loss","Decrease"),("801","Pack","Pack","Transfer"),("802","Unpack","Unpack","Transfer")]
    for code,name,cat,effect in movement: _insert("WMS Movement Type",{"movement_type_code":code,"movement_type_name":name,"movement_category":cat,"inventory_effect":effect,"active":1})
    process=[("GR_UNLOAD","Goods Receipt Unload","Unload","301"),("GR_PUTAWAY","Goods Receipt Putaway","Putaway","201"),("OB_PICK","Outbound Picking","Pick","401"),("OB_STAGE","Outbound Staging","Stage","301"),("OB_LOAD","Outbound Loading","Load","301"),("INTERNAL_MOVE","Internal Movement","Internal Move","301"),("PACK_REPACK","Packing and Repacking","Pack","801"),("STOCK_TYPE_CHANGE","Stock Type Change","Posting Change","501"),("REPLENISH","Pick Face Replenishment","Putaway","201")]
    for code,name,activity,movement in process: _insert("Warehouse Process Type",{"process_type_code":code,"process_type_name":name,"activity":activity,"source_required":1,"destination_required":1,"stock_required":1,"confirmation_mode":"Handling Unit","movement_type":movement,"active":1})

    from frappe_wms.setup.roles import ensure_roles
    ensure_roles()
