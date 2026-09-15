frappe.ui.form.on("WMS Warehouse", {
    refresh(frm) {
        if (window.frappe_wms) frappe_wms.set_warehouse_filters(frm);
    },
    warehouse_name(frm) {
        // Live preview: derive the code from the name unless the user set one explicitly.
        if (!frm.doc.warehouse_code && frm.doc.warehouse_name) {
            frm.set_value("warehouse_code", derive_code(frm.doc.warehouse_name));
        }
    },
});

function derive_code(name) {
    return (name || "").trim().replace(/[^A-Za-z0-9]+/g, "-").replace(/^-+|-+$/g, "").toUpperCase();
}
