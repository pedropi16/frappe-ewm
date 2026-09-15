frappe.ui.form.on("Packing Order", { refresh(frm) {
  if (window.frappe_wms) frappe_wms.set_warehouse_filters(frm);
  if (!frm.is_new() && ["Draft", "Open", "In Process"].includes(frm.doc.status)) {
    frm.add_custom_button(__("Confirm Packing"), () => frappe_wms.call("frappe_wms.api.scanner.complete_packing_order", {packing_order_name: frm.doc.name}).then(() => frm.reload_doc()), __("Actions"));
  }
} });
