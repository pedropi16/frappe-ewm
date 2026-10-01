// "Warehouse" panel on ERPNext documents that the WMS executes (Sales/Purchase Order, and draft
// Delivery Notes / Purchase Receipts replicated as the warehouse delivery): which WMS deliveries
// carry them and how far each one is - the EWM status ECC shows on its own delivery.
window.frappe_wms_erp_status = function (frm) {
  if (frm.is_new() || frm.doc.__islocal) return;
  frappe.call({ method: "frappe_wms.api.erp_integration.wms_status", args: { doctype: frm.doctype, name: frm.doc.name } }).then((r) => {
    const rows = r.message || [];
    if (!rows.length) return;
    const color = (d) => (d.docstatus === 2 || d.status === "Cancelled" ? "red" : d.closed_short ? "orange"
      : ["Completed", "Goods Issued", "Received"].includes(d.status) ? "green" : "blue");
    rows.forEach((d) => {
      const target = frm.doctype === "Purchase Order" || frm.doctype === "Purchase Receipt" ? "Inbound Delivery" : "Outbound Delivery";
      const detail = target === "Inbound Delivery" ? d.receipt_status : [d.picking_status, d.goods_issue_status].filter(Boolean).join(" · ");
      frm.dashboard.add_indicator(__("WMS {0}: {1}{2}", [d.name, __(d.status), d.closed_short ? " (" + __("short") + ")" : ""]) + (detail ? ` · ${detail}` : ""), color(d));
      frm.add_custom_button(d.name, () => frappe.set_route("Form", target, d.name), __("Warehouse"));
    });
  });
};
frappe.ui.form.on("Delivery Note", { refresh: (frm) => frappe_wms_erp_status(frm) });
frappe.ui.form.on("Purchase Receipt", { refresh: (frm) => frappe_wms_erp_status(frm) });

// SAP "adjust delivery quantity": close a released WMS delivery at what was actually
// received / shipped; the ERPNext order remainder is closed if the warehouse is set up to.
window.frappe_wms_complete_short = function (frm) {
  if (frm.doc.docstatus !== 1 || frm.doc.closed_short || ["Completed", "Cancelled"].includes(frm.doc.status)) return;
  if (!frappe.user.has_role(["WMS Supervisor", "WMS Administrator", "System Manager"])) return;
  frm.add_custom_button(__("Complete Short"), () => {
    frappe.prompt({ fieldname: "reason", fieldtype: "Small Text", label: __("Reason"), reqd: 1 }, (v) => {
      frappe.call({ method: "frappe_wms.api.erp_integration.complete_short", args: { doctype: frm.doctype, delivery_name: frm.doc.name, reason: v.reason }, freeze: true })
        .then(() => { frappe.show_alert({ message: __("{0} completed short", [frm.doc.name]), indicator: "orange" }); frm.reload_doc(); });
    }, __("Complete {0} short", [frm.doc.name]), __("Complete"));
  }, __("Actions"));
};
