frappe.ui.form.on("Warehouse Order", {
    refresh(frm) {
        if (window.frappe_wms) frappe_wms.set_warehouse_filters(frm);
        if (frm.is_new()) return;
        if (frm.doc.status === "On Hold") {
            frm.add_custom_button(__("Resume"), () => frappe_wms.call("frappe_wms.api.warehouse_order.resume_warehouse_order", { wo_name: frm.doc.name }).then(() => {
                frappe.show_alert({ message: __("Warehouse Order resumed"), indicator: "green" });
                frm.reload_doc();
            }));
        } else if (!["Completed", "Cancelled"].includes(frm.doc.status)) {
            frm.add_custom_button(__("Put On Hold"), () => frappe.prompt(
                { fieldname: "reason", label: __("Reason"), fieldtype: "Data" },
                (values) => frappe_wms.call("frappe_wms.api.warehouse_order.block_warehouse_order", { wo_name: frm.doc.name, reason: values.reason }).then(() => {
                    frappe.show_alert({ message: __("Warehouse Order put on hold"), indicator: "orange" });
                    frm.reload_doc();
                }),
                __("Put On Hold")
            ));
        }
    }
});
