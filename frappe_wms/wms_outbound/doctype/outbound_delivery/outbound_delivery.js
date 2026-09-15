frappe.ui.form.on("Outbound Delivery", { refresh(frm) {
  frappe_wms.set_warehouse_filters(frm);
  if (frm.is_new() || frm.doc.docstatus >= 2) return;
  if (frm.doc.allocation_status !== "Fully Allocated") {
    frm.add_custom_button(__("Allocate Stock"), () => frappe_wms.call("frappe_wms.api.outbound.allocate_delivery", {delivery_name: frm.doc.name}).then(() => frm.reload_doc()), __("Actions"));
  } else if (frm.doc.picking_status !== "Picked") {
    frm.add_custom_button(__("Create Pick Tasks"), () => frappe_wms.call("frappe_wms.api.outbound.create_pick_tasks", {delivery_name: frm.doc.name}).then(r => {
      frappe.msgprint(__("Created {0} pick tasks", [r.message.length]));
      frm.reload_doc();
    }), __("Actions"));
  }
} });
