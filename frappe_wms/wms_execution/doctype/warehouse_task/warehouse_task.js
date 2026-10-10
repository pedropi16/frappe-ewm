frappe.ui.form.on("Warehouse Task", {
 refresh(frm) {
  frappe_wms.set_warehouse_filters(frm);
  if (!frm.is_new()) frappe.call({ method: "frappe_wms.api.monitor.task_document", args: { task_name: frm.doc.name } }).then((r) => {
   if (r.message) frm.add_custom_button(__("{0} {1}", [__(r.message[0]), r.message[1]]), () => frappe.set_route("Form", r.message[0], r.message[1]));
  });
  if (!frm.is_new() && !["Confirmed","Cancelled","Exception"].includes(frm.doc.status)) {
   frm.add_custom_button(__("Confirm Task"), () => frappe_wms.confirm_foreground(frm.doc.name, () => frm.reload_doc()), __("Actions"));
  }
  if (!frm.is_new() && frm.doc.status === "Confirmed" && !frm.doc.reversal_of) {
   frm.add_custom_button(__("Reverse Task"), () => frappe.prompt(
    {fieldname:"reason",label:__("Reason"),fieldtype:"Small Text"},
    (v) => frappe_wms.call("frappe_wms.api.scanner.reverse_task",{task_name:frm.doc.name,reason:v.reason}).then(()=>frm.reload_doc()),
    __("Reverse Task")
   ), __("Actions"));
  }
 }
});
