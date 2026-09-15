frappe.ui.form.on("WMS Quality Inspection", { refresh(frm) {
  if (window.frappe_wms) frappe_wms.set_warehouse_filters(frm);
  if (!frm.is_new() && frm.doc.status === "Draft") {
    frm.add_custom_button(__("Complete Inspection"), () => frappe_wms.call("frappe_wms.api.inventory.complete_inspection", {
      inspection_name: frm.doc.name,
      passed_quantity: frm.doc.passed_quantity,
      failed_quantity: frm.doc.failed_quantity,
    }).then(() => frm.reload_doc()), __("Actions"));
  }
} });
