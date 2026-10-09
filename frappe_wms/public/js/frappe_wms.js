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

// Change mode (SAP): opening one of these documents locks it for everybody else, who can still display it. The lock is renewed
// every minute and given back when the user leaves the form (the server drops it after 5 minutes anyway).
frappe_wms.locked_forms = ["WMS Stock Adjustment", "WMS Posting Change", "WMS Physical Inventory Count", "WMS Wave", "Warehouse Order", "WMS Quality Inspection", "WMS Shipment", "VAS Order"];
frappe_wms.form_lock = null;
frappe_wms.drop_form_lock = function () {
    const l = frappe_wms.form_lock;
    if (!l) return;
    clearInterval(l.timer);
    frappe.call({ method: "frappe_wms.api.locks.release_lock", args: { object_type: l.doctype, object_name: l.name }, silent: true });
    frappe_wms.form_lock = null;
};
$(document).on("form-refresh", function (e, frm) {
    if (!frappe_wms.locked_forms.includes(frm.doctype) || frm.is_new()) return;
    const l = frappe_wms.form_lock;
    if (l && l.doctype === frm.doctype && l.name === frm.doc.name) return;
    frappe_wms.drop_form_lock();
    frappe.call({ method: "frappe_wms.api.locks.lock_status", args: { object_type: frm.doctype, object_name: frm.doc.name }, silent: true }).then((r) => {
        const held = r.message;
        if (held && !held.mine) {
            frm.disable_form();
            frm.dashboard.set_headline(__("Display only: being changed by {0}", [held.user]), "orange");
            return;
        }
        frappe.call({ method: "frappe_wms.api.locks.acquire_lock", args: { object_type: frm.doctype, object_name: frm.doc.name, purpose: "form" }, silent: true }).then(() => {
            const args = { objects: JSON.stringify([[frm.doctype, frm.doc.name]]) };
            frappe_wms.form_lock = { doctype: frm.doctype, name: frm.doc.name, timer: setInterval(() => frappe.call({ method: "frappe_wms.api.locks.renew_locks", args, silent: true }), 60000) };
        });
    });
});
frappe.router.on("change", function () {
    const l = frappe_wms.form_lock, route = frappe.get_route();
    if (l && !(route[0] === "Form" && route[1] === l.doctype && route[2] === l.name)) frappe_wms.drop_form_lock();
});
$(window).on("beforeunload", function () {
    const l = frappe_wms.form_lock;
    if (l) navigator.sendBeacon(`/api/method/frappe_wms.api.locks.release_lock?object_type=${encodeURIComponent(l.doctype)}&object_name=${encodeURIComponent(l.name)}&csrf_token=${encodeURIComponent(frappe.csrf_token)}`);
});
