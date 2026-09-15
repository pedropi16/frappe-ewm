frappe.ui.form.on("Warehouse Task", {
 refresh(frm) {
  frappe_wms.set_warehouse_filters(frm);
  if (!frm.is_new() && !["Confirmed","Cancelled","Exception"].includes(frm.doc.status)) {
   frm.add_custom_button(__("Confirm Task"), () => frappe_wms.scan_dialog(__("Confirm Warehouse Task"), [
    {fieldname:"scanned_source",label:__("Scan Source"),fieldtype:"Data",reqd:1,cssClass:"wms-scan-input"},
    {fieldname:"scanned_destination",label:__("Scan Destination"),fieldtype:"Data",reqd:1},
    {fieldname:"confirmed_quantity",label:__("Confirmed Quantity"),fieldtype:"Float",default:frm.doc.planned_quantity,reqd:1},
    {fieldname:"device",label:__("Device"),fieldtype:"Data"}
   ], __("Confirm"), (v,d) => frappe_wms.call("frappe_wms.api.scanner.confirm_task",{task_name:frm.doc.name,...v}).then(()=>{d.hide();frm.reload_doc();})), __("Actions"));
  }
 }
});
