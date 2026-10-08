frappe.ui.form.on("Outbound Delivery", { refresh(frm) {
  frappe_wms.set_warehouse_filters(frm);
  if (frm.is_new() || frm.doc.docstatus >= 2) return;
  frappe_wms_complete_short(frm);
  if (frm.doc.docstatus === 1 && !frm.doc.closed_short && frm.doc.packing_status === "Not Started") {
    frm.add_custom_button(__("Plan Cartons"), () => frappe_wms.call("frappe_wms.api.outbound.plan_cartons", {delivery_name: frm.doc.name}).then(r => {
      frappe.show_alert({message: __("{0} shipping HU(s) planned", [r.message.cartons]), indicator: "green"});
      frm.reload_doc();
    }), __("Actions"));
  }
  if (frm.doc.docstatus === 1 && !frm.doc.closed_short && frappe.user.has_role(["WMS Supervisor", "WMS Administrator", "System Manager"])) {
    frm.add_custom_button(__("Split Delivery"), () => {
      const open = frm.doc.items.filter(r => flt(r.requested_quantity) - Math.max(flt(r.allocated_quantity), flt(r.picked_quantity), flt(r.packed_quantity), flt(r.issued_quantity)) > 0);
      if (!open.length) return frappe.msgprint(__("Nothing is left that has not been allocated, picked or issued."));
      const d = new frappe.ui.Dialog({ title: __("Split {0}", [frm.doc.name]), fields: [
        { fieldname: "lines", fieldtype: "Table", label: __("Quantity to move to the new delivery"), cannot_add_rows: true, in_place_edit: true, data: open.map(r => ({ line: r.name, item: r.item, quantity: 0 })),
          fields: [{ fieldname: "line", fieldtype: "Data", hidden: 1 }, { fieldname: "item", fieldtype: "Data", read_only: 1, in_list_view: 1, label: __("Item") }, { fieldname: "quantity", fieldtype: "Float", in_list_view: 1, label: __("Move") }] },
        { fieldname: "reason", fieldtype: "Small Text", label: __("Reason") }],
        primary_action_label: __("Split"), primary_action(v) {
          const lines = v.lines.filter(l => flt(l.quantity) > 0).map(l => ({ line: l.line, quantity: l.quantity }));
          if (!lines.length) return;
          frappe.call({ method: "frappe_wms.api.erp_integration.split_delivery", args: { delivery_name: frm.doc.name, lines, reason: v.reason }, freeze: true }).then(r => {
            d.hide(); frappe.show_alert({ message: __("Created {0}", [r.message.new]), indicator: "green" }); frm.reload_doc();
          });
        } });
      d.show();
    }, __("Actions"));
  }
  if (frm.doc.allocation_status !== "Fully Allocated") {
    frm.add_custom_button(__("Allocate Stock"), () => frappe_wms.call("frappe_wms.api.outbound.allocate_delivery", {delivery_name: frm.doc.name}).then(() => frm.reload_doc()), __("Actions"));
  } else if (frm.doc.picking_status !== "Picked") {
    frm.add_custom_button(__("Create Pick Tasks"), () => frappe_wms.call("frappe_wms.api.outbound.create_pick_tasks", {delivery_name: frm.doc.name}).then(r => {
      frappe.msgprint(__("Created {0} pick tasks", [r.message.length]));
      frm.reload_doc();
    }), __("Actions"));
  }
} });
