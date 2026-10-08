frappe.ui.form.on("WMS Stock Adjustment", {
    refresh(frm) {
        if (window.frappe_wms) frappe_wms.set_warehouse_filters(frm);
        if (frm.is_new()) return;
        if (frm.doc.status === "Draft") frm.add_custom_button(__("Post"), () => frappe_wms.call("frappe_wms.api.stock_adjustment.post_stock_adjustment", { name: frm.doc.name }).then(() => frm.reload_doc())).addClass("btn-primary");
        if (frm.doc.status === "Posted") frm.add_custom_button(__("Cancel Posting"), () => frappe_wms.call("frappe_wms.api.stock_adjustment.cancel_stock_adjustment", { name: frm.doc.name }).then(() => frm.reload_doc()));
    }
});
