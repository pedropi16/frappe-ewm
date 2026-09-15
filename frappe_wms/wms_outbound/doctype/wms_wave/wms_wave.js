frappe.ui.form.on("WMS Wave", { refresh(frm) {
  frappe_wms.set_warehouse_filters(frm);
  if (!frm.is_new() && frm.doc.status === "Draft") {
    frm.add_custom_button(__("Release Wave"), () => frappe_wms.call("frappe_wms.api.outbound.release_wave", {wave_name: frm.doc.name}).then(r => {
      frappe.msgprint(__("Created {0} pick tasks", [r.message.length]));
      frm.reload_doc();
    }), __("Actions"));
  }
} });
