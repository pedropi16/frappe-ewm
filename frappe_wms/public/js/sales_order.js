frappe.ui.form.on("Sales Order", { refresh(frm) {
  if (frm.doc.docstatus !== 1 || frm.doc.status === "Closed" || flt(frm.doc.per_delivered) >= 100) return;
  frm.add_custom_button(__("Outbound Delivery"), () => {
    frappe.prompt(
      {fieldname: "warehouse", label: __("WMS Warehouse"), fieldtype: "Link", options: "WMS Warehouse", reqd: 1,
        get_query: () => ({filters: {company: frm.doc.company}})},
      (values) => {
        frappe.call({
          method: "frappe_wms.api.outbound.create_outbound_delivery_from_sales_order",
          args: {sales_order_name: frm.doc.name, warehouse: values.warehouse},
          freeze: true,
        }).then((r) => {
          frappe.msgprint(__("Created {0}", [r.message]));
          frappe.set_route("Form", "Outbound Delivery", r.message);
        });
      },
      __("Create Outbound Delivery"),
      __("Create")
    );
  }, __("Create"));
} });
