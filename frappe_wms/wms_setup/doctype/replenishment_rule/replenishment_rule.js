frappe.ui.form.on("Replenishment Rule", {
    refresh(frm) {
        if (window.frappe_wms) frappe_wms.set_warehouse_filters(frm);
    }
});
