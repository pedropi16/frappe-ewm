frappe.provide("frappe_wms");

frappe_wms.call = function(method, args = {}, freeze_message = __("Processing warehouse transaction...")) {
    return frappe.call({ method, args, freeze: true, freeze_message });
};

frappe_wms.set_warehouse_filters = function(frm) {
    ["receiving_bin", "staging_bin", "door", "source_bin", "destination_bin", "current_bin"].forEach((field) => {
        if (frm.fields_dict[field]) {
            frm.set_query(field, () => ({ filters: { warehouse: frm.doc.warehouse } }));
        }
    });
};

frappe_wms.scan_dialog = function(title, fields, primary_label, action) {
    const dialog = new frappe.ui.Dialog({ title, fields, primary_action_label: primary_label,
        primary_action(values) { action(values, dialog); }
    });
    dialog.show();
    const first = fields.find((field) => field.fieldtype === "Data");
    if (first) setTimeout(() => dialog.get_field(first.fieldname).set_focus(), 150);
    return dialog;
};

$(document).on("toolbar_setup", function() {
    if (!frappe.user.has_role("WMS Operator")) return;
});
