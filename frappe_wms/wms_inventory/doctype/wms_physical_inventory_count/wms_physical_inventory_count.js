frappe.ui.form.on("WMS Physical Inventory Count", { refresh(frm) {
  if (window.frappe_wms) frappe_wms.set_warehouse_filters(frm);
  if (frm.is_new()) return;

  if (frm.doc.status === "Draft") {
    frm.add_custom_button(__("Snapshot Stock"), () => frappe_wms.call("frappe_wms.api.inventory.snapshot_count", {count_name: frm.doc.name}).then(() => frm.reload_doc()), __("Actions"));
  }

  if (["Counting", "Counted"].includes(frm.doc.status)) {
    frm.add_custom_button(__("Save Counted Quantities"), () => {
      const counted_quantities = {};
      (frm.doc.items || []).forEach((row) => { counted_quantities[row.name] = row.counted_quantity || 0; });
      frappe_wms.call("frappe_wms.api.inventory.record_counts", {count_name: frm.doc.name, counted_quantities: JSON.stringify(counted_quantities)}).then(() => frm.reload_doc());
    }, __("Actions"));
  }

  if (frm.doc.status === "Counted") {
    frm.add_custom_button(__("Post Variances"), () => frappe_wms.call("frappe_wms.api.inventory.post_count", {count_name: frm.doc.name}).then(() => frm.reload_doc()), __("Actions"));
  }
} });
