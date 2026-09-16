frappe.ui.form.on("WMS Shipment", {
    refresh(frm) {
        if (window.frappe_wms) frappe_wms.set_warehouse_filters(frm);
        if (frm.is_new()) return;
        if (frm.doc.status === "Loaded") {
            frm.add_custom_button(__("Depart"), () => frappe.confirm(
                __("Mark {0} as departed?", [frm.doc.name]),
                () => frappe_wms.call("frappe_wms.api.shipping.depart_shipment", { shipment_name: frm.doc.name }).then(() => frm.reload_doc())
            ));
        }
        if (frm.doc.status === "Departed") {
            frm.add_custom_button(__("Complete"), () => frappe.confirm(
                __("Mark {0} as completed (e.g. proof of delivery received)?", [frm.doc.name]),
                () => frappe_wms.call("frappe_wms.api.shipping.complete_shipment", { shipment_name: frm.doc.name }).then(() => frm.reload_doc())
            ));
        }
    }
});
