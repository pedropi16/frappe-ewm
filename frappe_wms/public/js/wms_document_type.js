// WMS Document Type: shape the form of a document from its type's Field Control rows (Hidden / Read Only / Required).
(function () {
  const apply = (frm) => {
    frappe.call({ method: "frappe_wms.services.document_types.field_controls", args: { doctype: frm.doctype, document_type: frm.doc.document_type, warehouse: frm.doc.warehouse } }).then((r) => {
      (r.message || []).forEach((c) => {
        if (!frm.fields_dict[c.fieldname]) return;
        if (c.control === "Hidden") frm.toggle_display(c.fieldname, false);
        else if (c.control === "Required") frm.set_df_property(c.fieldname, "reqd", 1);
        else if (c.control === "Read Only" && !frm.is_new()) frm.set_df_property(c.fieldname, "read_only", 1);
      });
    });
  };
  ["Inbound Delivery", "Outbound Delivery", "Warehouse Request", "WMS Delivery Request", "Final Outbound Delivery"].forEach((doctype) => {
    frappe.ui.form.on(doctype, { refresh: apply, document_type: apply });
  });
})();
