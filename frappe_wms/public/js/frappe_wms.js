frappe.provide("frappe_wms");

// The warehouse a user works in: their WMS Warehouse default, else the one they last picked on any WMS page, else the first.
frappe_wms.my_warehouse = function (names) {
  let last = null;
  try { last = localStorage.getItem("wms_last_warehouse"); } catch (e) { /* storage blocked: not remembered */ }
  const mine = frappe.defaults.get_user_default("WMS Warehouse");
  return [mine, last].find((w) => w && names.includes(w)) || names[0] || "";
};
frappe_wms.remember_warehouse = function (name) {
  try { if (name) localStorage.setItem("wms_last_warehouse", name); } catch (e) { /* storage blocked: not remembered */ }
};

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

// Confirmation in the foreground (SAP): the task is confirmed in a dialog where the user gives what the system cannot guess - the quantity, which serial numbers or batch
// leave the stock, a destination HU (blank: a new pick HU is created when only part of the stock moves). A task that needs nothing of that is confirmed in the background.
frappe_wms.confirm_foreground = async function (task, onDone) {
    const t = await frappe.db.get_value("Warehouse Task", task, ["planned_quantity", "confirmed_quantity", "product", "source_bin", "source_hu", "destination_bin", "destination_hu", "stock_uom"]).then((r) => r.message);
    const open = flt(t.planned_quantity) - flt(t.confirmed_quantity);
    const need = await frappe.call({ method: "frappe_wms.api.scanner.confirmation_details", args: { task_name: task, quantity: open } }).then((r) => r.message || {});
    const key = `TC-${task}-${frappe.utils.get_random(10)}`;  // one per dialog: a resend of the same confirmation cannot post twice
    const fields = [
        { fieldname: "scanned_source", label: __("Scan Source ({0})", [t.source_hu || t.source_bin || "-"]), fieldtype: "Data", reqd: 1, cssClass: "wms-scan-input" },
        { fieldname: "scanned_destination", label: __("Scan Destination ({0})", [t.destination_hu || t.destination_bin || "-"]), fieldtype: "Data", reqd: 1 },
        { fieldname: "confirmed_quantity", label: __("Confirmed Quantity"), fieldtype: "Float", default: open, reqd: 1, description: `${__("Planned")}: ${flt(t.planned_quantity)} ${t.stock_uom || ""}` }];
    if (need.serial) fields.push({ fieldname: "serial_text", label: __("Serial Numbers (one per line)"), fieldtype: "Small Text", reqd: 1,
        description: __("Choose {0} of: {1}", [need.serial.count, need.serial.choices.slice(0, 40).join(", ") + (need.serial.choices.length > 40 ? " ..." : "")]) });
    if (need.batch) fields.push({ fieldname: "batch_no", label: __("Batch"), fieldtype: "Select", options: ["", ...need.batch.choices].join("\n"), reqd: 1 });
    fields.push({ fieldname: "destination_hu", label: __("Destination Handling Unit"), fieldtype: "Link", options: "Handling Unit", description: __("Blank: a new HU is created when only part of the stock moves.") });
    const d = new frappe.ui.Dialog({ title: __("Confirm {0} (foreground)", [task]), fields, primary_action_label: __("Confirm"), primary_action: async (v) => {
        const serials = (v.serial_text || "").split(/[\n,;]+/).map((x) => x.trim()).filter(Boolean);
        await frappe.call({ method: "frappe_wms.api.scanner.confirm_task", args: { task_name: task, scanned_source: v.scanned_source, scanned_destination: v.scanned_destination, idempotency_key: key, confirmed_quantity: v.confirmed_quantity, destination_hu: v.destination_hu || undefined, batch_no: v.batch_no || undefined,
            serial_numbers: serials.length ? JSON.stringify(serials) : undefined }, freeze: true });
        d.hide(); frappe.show_alert({ message: __("Task {0} confirmed", [task]), indicator: "green" }); if (onDone) onDone();
    } });
    d.show();
    return d;
};

$(document).on("toolbar_setup", function() {
    if (!frappe.user.has_role("WMS Operator")) return;
});

// Change mode (SAP): opening one of these documents locks it for everybody else, who can still display it. The lock is renewed
// every minute and given back when the user leaves the form (the server drops it after 5 minutes anyway).
frappe_wms.locked_forms = ["WMS Stock Adjustment", "WMS Posting Change", "WMS Physical Inventory Count", "WMS Wave", "WMS Quality Inspection", "WMS Shipment", "VAS Order"];
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
