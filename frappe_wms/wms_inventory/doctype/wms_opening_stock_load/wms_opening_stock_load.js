frappe.ui.form.on("WMS Opening Stock Load", {
    refresh(frm) {
        if (window.frappe_wms) frappe_wms.set_warehouse_filters(frm);
        if (frm.is_new()) return;
        if (frm.doc.status === "Draft") {
            frm.add_custom_button(__("Post"), () => {
                frappe.confirm(
                    __("Post this opening stock load? This writes directly to the live WMS stock ledger for {0} and mirrors it to ERPNext - it cannot be edited afterwards.", [frm.doc.warehouse]),
                    () => frappe_wms.call("frappe_wms.api.opening_stock.post_opening_stock_load", { name: frm.doc.name }).then(() => {
                        frappe.show_alert({ message: __("Opening stock load posted"), indicator: "green" });
                        frm.reload_doc();
                    })
                );
            }).addClass("btn-primary");
        } else if (frm.doc.status === "Posted") {
            frm.add_custom_button(__("Cancel Load"), () => {
                frappe.confirm(
                    __("Cancel this opening stock load? This reverses every line on the WMS stock ledger and cancels the mirrored ERPNext Stock Reconciliation."),
                    () => frappe_wms.call("frappe_wms.api.opening_stock.cancel_opening_stock_load", { name: frm.doc.name }).then(() => {
                        frappe.show_alert({ message: __("Opening stock load cancelled"), indicator: "orange" });
                        frm.reload_doc();
                    })
                );
            });
            if (frm.doc.erpnext_stock_reconciliations) {
                frm.add_custom_button(__("View ERPNext Stock Reconciliation"), () => {
                    frappe.set_route("Form", "Stock Reconciliation", frm.doc.erpnext_stock_reconciliations.split(",")[0].trim());
                });
            }
        }
    }
});
