frappe.ui.form.on("Storage Type", {
    refresh(frm) {
        if (window.frappe_wms) frappe_wms.set_warehouse_filters(frm);
    }
});
