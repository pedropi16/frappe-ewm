frappe.ui.form.on("Handling Unit Type", {
    refresh(frm) {
        if (window.frappe_wms) frappe_wms.set_warehouse_filters(frm);
    }
});
