frappe.ui.form.on("Purchase Order", { refresh(frm) {
  if (frm.doc.docstatus !== 1 || flt(frm.doc.per_received) >= 100) return;
  frm.add_custom_button(__("Inbound Delivery"), () => {
    frappe.prompt(
      {fieldname: "warehouse", label: __("WMS Warehouse"), fieldtype: "Link", options: "WMS Warehouse", reqd: 1,
        get_query: () => ({filters: {company: frm.doc.company}})},
      (values) => {
        frappe.call({
          method: "frappe_wms.api.inbound.create_inbound_delivery_from_purchase_order",
          args: {purchase_order_name: frm.doc.name, warehouse: values.warehouse},
          freeze: true,
        }).then((r) => {
          frappe.msgprint(__("Created {0}", [r.message]));
          frappe.set_route("Form", "Inbound Delivery", r.message);
        });
      },
      __("Create Inbound Delivery"),
      __("Create")
    );
  }, __("Create"));
} });
