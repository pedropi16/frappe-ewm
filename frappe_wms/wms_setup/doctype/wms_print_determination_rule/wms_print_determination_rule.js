frappe.ui.form.on("WMS Print Determination Rule", {
    refresh(frm) {
        frm.set_query("output_device", () => ({ filters: { resource_type: "Printer", warehouse: frm.doc.warehouse } }));
    },
    warehouse(frm) {
        frm.set_query("output_device", () => ({ filters: { resource_type: "Printer", warehouse: frm.doc.warehouse } }));
    }
});
