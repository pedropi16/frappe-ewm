frappe.ui.form.on("Inbound Delivery", {
    refresh(frm) {
        if (window.frappe_wms) frappe_wms.set_warehouse_filters(frm);
    }
});
