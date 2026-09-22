frappe.ui.form.on("Work Order", { refresh(frm) {
  if (frm.doc.docstatus !== 1 || flt(frm.doc.produced_qty) >= flt(frm.doc.qty)) return;
  frm.add_custom_button(__("FG Receipt (WMS)"), () => {
    frappe.prompt(
      [
        {fieldname: "warehouse", label: __("WMS Warehouse"), fieldtype: "Link", options: "WMS Warehouse", reqd: 1,
          get_query: () => ({filters: {company: frm.doc.company}})},
        {fieldname: "quantity", label: __("Quantity"), fieldtype: "Float", reqd: 1,
          default: flt(frm.doc.qty) - flt(frm.doc.produced_qty)},
        {fieldname: "stock_type", label: __("Stock Type"), fieldtype: "Link", options: "WMS Stock Type", default: "AVAILABLE", reqd: 1},
        {fieldname: "handling_unit", label: __("Handling Unit"), fieldtype: "Link", options: "Handling Unit",
          description: __("Leave blank to create a new one from the Handling Unit Type below.")},
        {fieldname: "hu_type", label: __("Handling Unit Type"), fieldtype: "Link", options: "Handling Unit Type"},
        {fieldname: "batch_no", label: __("Batch No"), fieldtype: "Data"},
        {fieldname: "serial_no", label: __("Serial No"), fieldtype: "Data"},
      ],
      (values) => {
        frappe.call({
          method: "frappe_wms.api.inbound.create_fg_receipt_from_work_order",
          args: {
            work_order_name: frm.doc.name, warehouse: values.warehouse, quantity: values.quantity,
            handling_unit: values.handling_unit, hu_type: values.hu_type,
            batch_no: values.batch_no, serial_no: values.serial_no, stock_type: values.stock_type,
          },
          freeze: true,
        }).then((r) => {
          frappe.msgprint(__("Goods Receipt {0} posted", [r.message.goods_receipt || r.message]));
          frm.reload_doc();
        });
      },
      __("Finished Goods Receipt"),
      __("Receive")
    );
  }, __("Create"));
} });
