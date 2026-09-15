frappe.ui.form.on("WMS Stock Ledger Entry", {
    refresh(frm) {
        if (window.frappe_wms) frappe_wms.set_warehouse_filters(frm);
    }
});
