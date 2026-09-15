import frappe

def execute():
    indexes={
        "WMS Stock Ledger Entry":[["warehouse","product","storage_bin"],["handling_unit","product"],["reference_doctype","reference_name"],["warehouse_task"]],
        "WMS Stock Balance":[["warehouse","product","storage_bin"],["handling_unit","product"],["serial_no"]],
        "Warehouse Task":[["warehouse","status","priority"],["warehouse_request"]],
        "Handling Unit":[["warehouse","current_bin","status"],["parent_hu"]],
    }
    for doctype,groups in indexes.items():
        for fields in groups:
            try: frappe.db.add_index(doctype,fields,index_name="idx_"+"_".join(fields))
            except Exception: pass
