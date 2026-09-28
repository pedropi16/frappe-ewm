frappe.ui.form.on("WMS Posting Change", {
    refresh(frm) {
        if (window.frappe_wms) frappe_wms.set_warehouse_filters(frm);
        if (frm.is_new() || frm.doc.status !== "Draft") return;
        frm.add_custom_button(__("Post"), () => {
            frappe_wms.call("frappe_wms.api.posting_change.post_posting_change", { name: frm.doc.name }).then(() => {
                frappe.show_alert({ message: __("Posting change posted"), indicator: "green" });
                frm.reload_doc();
            });
        }).addClass("btn-primary");
    }
});
