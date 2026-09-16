frappe.ui.form.on("Handling Unit", {
    refresh(frm) {
        frappe_wms.set_warehouse_filters(frm);
        if (!frm.is_new()) {
            frm.add_custom_button(__("HU Overview"), () => frappe_wms.call("frappe_wms.api.scanner.hu_overview", { hu_number: frm.doc.name }, __("Loading HU...")).then(r => frappe.msgprint({ title: __("HU Overview"), message: `<pre>${frappe.utils.escape_html(JSON.stringify(r.message, null, 2))}</pre>`, wide: true })));
            if (frm.doc.stock_status === "Empty") {
                frm.add_custom_button(__("Recycle"), () => frappe.confirm(
                    __("Free this HU's number for reuse and delete the record?"),
                    () => frappe_wms.call("frappe_wms.api.handling_unit.recycle_handling_unit", { hu_name: frm.doc.name }).then(() => {
                        frappe.show_alert({ message: __("Handling Unit recycled"), indicator: "green" });
                        frappe.set_route("List", "Handling Unit");
                    })
                ));
            }
        }
        frm.trigger("hu_type");
    },
    packaging_material(frm) {
        if (!frm.doc.packaging_material) return;
        frappe.db.get_value("Packaging Material", frm.doc.packaging_material, ["hu_type", "tare_weight"]).then(({ message }) => {
            if (message.hu_type && !frm.doc.hu_type) frm.set_value("hu_type", message.hu_type);
            if (message.tare_weight && !frm.doc.tare_weight) frm.set_value("tare_weight", message.tare_weight);
        });
    },
    hu_type(frm) {
        if (!frm.doc.hu_type) { frm.toggle_reqd("hu_number", true); return; }
        frappe.db.get_value("Handling Unit Type", frm.doc.hu_type, "numbering_mode").then(({ message }) => {
            const internal = message.numbering_mode === "Internal";
            frm.toggle_reqd("hu_number", !internal);
            frm.toggle_display("hu_number", !internal || !frm.is_new());
            frm.set_df_property("hu_number", "description", internal ? __("Left blank - the system will assign the next number from this HU Type's Number Range.") : "");
        });
    },
});
