frappe.ui.form.on("Inbound Delivery", {
    refresh(frm) {
        if (window.frappe_wms) frappe_wms.set_warehouse_filters(frm);
        if (!frm.is_new() && window.frappe_wms_complete_short) frappe_wms_complete_short(frm);
    }
});
