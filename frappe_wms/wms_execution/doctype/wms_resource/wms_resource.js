frappe.ui.form.on("WMS Resource", {
    refresh(frm) {
        if (window.frappe_wms) frappe_wms.set_warehouse_filters(frm);
        if (!frm.is_new() && frm.doc.user && frappe.user.has_role("WMS Supervisor")) {
            frm.add_custom_button(__("Kick"), () => frappe.prompt(
                { fieldname: "reason", label: __("Reason"), fieldtype: "Small Text" },
                (values) => frappe_wms.call("frappe_wms.api.resource.kick", { resource_code: frm.doc.name, reason: values.reason }).then(() => {
                    frappe.show_alert({ message: __("Logged off {0}", [frm.doc.user]), indicator: "green" });
                    frm.reload_doc();
                }),
                __("Kick user off this Resource")
            ));
        }
    }
});
