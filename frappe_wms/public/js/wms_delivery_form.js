// The delivery maintenance screens (SAP EWM "Maintain Outbound Delivery Order" / "Maintain Inbound Delivery"): the Outbound / Inbound Delivery form with
// its header, status indicators, the Items tab and the Status / Dates / Locations / Partner / References / HU / Transportation Unit tabs, and the actions that
// drive the delivery (allocate, create tasks, receive & pack, shipment, goods issue...). The Monitor only searches and displays; it links here.
window.frappe_wms_delivery = (function () {
  const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
  const slug = (dt) => frappe.router.slug(dt);
  const lnk = (dt, name) => (name ? `<a href="/app/${slug(dt)}/${encodeURIComponent(name)}">${esc(name)}</a>` : "");
  const fmt = (v) => (v ? esc(frappe.datetime.str_to_user(String(v).slice(0, 19))) : "");
  const num = (v) => (v == null ? "" : esc(flt(v)));
  const table = (cols, rows) => (rows && rows.length
    ? `<div style="overflow-x:auto"><table class="table table-bordered table-sm"><thead><tr>${cols.map((c) => `<th>${esc(c[1])}</th>`).join("")}</tr></thead><tbody>${
      rows.map((r) => `<tr>${cols.map((c) => `<td>${c[2] ? c[2](r) : esc(r[c[0]])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`
    : `<div class="text-muted" style="margin-bottom:8px;">${__("None")}</div>`);
  const section = (title, body) => `<h6 style="margin:14px 0 6px;">${esc(title)}</h6>${body}`;
  const kv = (rows) => `<table class="table table-sm" style="max-width:640px"><tbody>${rows.filter((r) => r[1] !== "" && r[1] != null).map((r) => `<tr><td class="text-muted" style="width:220px">${esc(r[0])}</td><td>${r[1]}</td></tr>`).join("")}</tbody></table>`;
  const call = (method, args) => frappe.call({ method: `frappe_wms.api.${method}`, args, freeze: true }).then((r) => r.message);
  const setHtml = (frm, field, html) => { if (frm.fields_dict[field]) frm.fields_dict[field].$wrapper.html(html); };

  const COLORS = { Completed: "green", "Goods Issued": "green", Received: "green", Posted: "green", Picked: "green", "Fully Allocated": "green", Loaded: "green", Packed: "green", "Fully Received": "green",
    Cancelled: "red", Exception: "red" };
  const indicators = (frm, pairs) => {
    const d = frm.doc;
    const pills = pairs.filter((p) => p[1]).map(([label, value]) => `<span class="indicator-pill ${COLORS[value] || (/Not|Open|Expected|Draft/.test(value) ? "gray" : "blue")}" style="margin-right:6px;"><span class="ellipsis">${esc(label)}: ${esc(__(value))}</span></span>`).join("");
    frm.dashboard.set_headline(`<b>${esc(d.outbound_delivery_number || d.inbound_delivery_number || d.name)}</b> · ${esc(d.warehouse || "")} · ${esc(d.customer || d.supplier || "")}${d.closed_short ? " · " + __("completed short") : ""}<div style="margin-top:6px;">${pills}</div>`);
  };

  function renderCommon(frm, v, side) {
    const d = frm.doc, dt = v.dates || {};
    setHtml(frm, "dates_html", kv([[__("Created"), fmt(dt.created)], [__("Last changed"), fmt(dt.changed)], [side === "Outbound" ? __("Delivery date") : __("Expected arrival"), fmt(dt.delivery_date || dt.expected_arrival)],
      [__("Posting date"), fmt(dt.posting_date)], [side === "Outbound" ? __("First confirmed task") : __("First receipt"), fmt(dt.first_task || dt.first_receipt)], [__("Goods issue posted"), fmt(dt.goods_issue)]]));
    const ref = v.references || {};
    setHtml(frm, "references_html", kv([[__("Delivery Request"), lnk("WMS Delivery Request", ref.delivery_request)], [__("ERP document"), ref.erp_source && ref.erp_source[1] ? lnk(ref.erp_source[0], ref.erp_source[1]) : ""],
      [__("Sales Orders"), (ref.sales_orders || []).map((n) => lnk("Sales Order", n)).join(", ")], [__("Purchase Orders"), (ref.purchase_orders || []).map((n) => lnk("Purchase Order", n)).join(", ")],
      [__("External reference"), esc(ref.external_reference)], [__("Customer instruction"), esc(ref.customer_instruction)]]));
    setHtml(frm, "partner_html", kv([[side === "Outbound" ? __("Customer") : __("Supplier"), lnk(side === "Outbound" ? "Customer" : "Supplier", d.customer || d.supplier)], [__("Ship-to"), esc(d.ship_to_party)],
      [__("Owner"), lnk("WMS Stock Owner", d.stock_owner)], [__("Party entitled to dispose"), lnk("WMS Stock Owner", d.entitled_party)],
      [__("Carrier"), (v.shipments || []).map((s) => esc(s.carrier || "")).filter(Boolean).join(", ") || (v.transport_units || []).map((u) => esc(u.carrier || "")).filter(Boolean).join(", ")]]));
    const hus = (v.handling_units || []);
    setHtml(frm, "hu_html", table([["name", __("Handling Unit"), (r) => lnk("Handling Unit", r.name || r.handling_unit)], ["hu_type", __("Type")], ["current_bin", __("Bin"), (r) => lnk("Storage Bin", r.current_bin)], ["status", __("Status")],
      ["contents", __("Contents"), (r) => esc((r.lines || []).map((l) => `${flt(l.quantity)} × ${l.product || l.item}${l.batch_no ? " " + l.batch_no : ""}${l.serial_no ? " " + l.serial_no : ""}`).join(", "))]], hus));
    setHtml(frm, "transport_html",
      section(__("Shipments"), table([["name", __("Shipment"), (r) => lnk("WMS Shipment", r.name)], ["status", __("Status")], ["route", __("Route")], ["carrier", __("Carrier")], ["vehicle_registration", __("Vehicle")], ["door", __("Door")], ["planned_departure", __("Planned departure"), (r) => fmt(r.planned_departure)]], v.shipments || [])) +
      section(__("Transportation Units"), table([["name", __("Unit"), (r) => lnk("WMS Transportation Unit", r.name)], ["unit_type", __("Type")], ["status", __("Status")], ["activity_status", __("Activity")], ["vehicle_registration", __("Vehicle")], ["door", __("Door")], ["seal_number", __("Seal")]], v.transport_units || [])) +
      section(__("Dock Appointments"), table([["name", __("Appointment"), (r) => lnk("WMS Dock Appointment", r.name)], ["status", __("Status")], ["planned_start", __("Planned"), (r) => fmt(r.planned_start)], ["door", __("Door")], ["vehicle_registration", __("Vehicle")]], v.appointments || [])));
  }

  const taskCols = [["name", __("Task"), (r) => lnk("Warehouse Task", r.name)], ["task_type", __("Type")], ["product", __("Product")], ["planned_quantity", __("Planned"), (r) => num(r.planned_quantity)], ["confirmed_quantity", __("Confirmed"), (r) => num(r.confirmed_quantity)],
    ["source_bin", __("From"), (r) => lnk("Storage Bin", r.source_bin)], ["destination_bin", __("To"), (r) => lnk("Storage Bin", r.destination_bin)], ["status", __("Status")]];

  function renderOutbound(frm, v) {
    setHtml(frm, "status_html",
      section(__("Warehouse Tasks"), table(taskCols, v.tasks)) +
      section(__("Stock Allocations"), table([["product", __("Product")], ["storage_bin", __("Bin"), (r) => lnk("Storage Bin", r.storage_bin)], ["handling_unit", __("HU"), (r) => lnk("Handling Unit", r.handling_unit)], ["batch_no", __("Batch")], ["serial_no", __("Serial")],
        ["allocated_quantity", __("Allocated"), (r) => num(r.allocated_quantity)], ["picked_quantity", __("Picked"), (r) => num(r.picked_quantity)], ["status", __("Status")]], v.allocations)) +
      (v.requests.length ? section(__("Cross-docking"), table([["name", __("Request"), (r) => lnk("Warehouse Request", r.name)], ["product", __("Product")], ["requested_quantity", __("Quantity"), (r) => num(r.requested_quantity)], ["status", __("Status")]], v.requests)) : "") +
      section(__("Packing Orders"), table([["name", __("Packing Order"), (r) => lnk("Packing Order", r.name)], ["status", __("Status")], ["work_center_bin", __("Work center bin")]], v.packing_orders)) +
      section(__("Goods Issues"), table([["name", __("Goods Issue"), (r) => lnk("Goods Issue", r.name)], ["status", __("Status")], ["posting_datetime", __("Posted"), (r) => fmt(r.posting_datetime)], ["shipment", __("Shipment"), (r) => lnk("WMS Shipment", r.shipment)]], v.goods_issues)));
    const d = frm.doc;
    setHtml(frm, "locations_html", kv([[__("Staging bin"), lnk("Storage Bin", d.staging_bin)], [__("Door"), lnk("Storage Bin", d.door)], [__("Route"), lnk("WMS Route", d.route)]]) +
      section(__("Stock is taken from"), table([["storage_bin", __("Bin"), (r) => lnk("Storage Bin", r.storage_bin)], ["product", __("Product")], ["allocated_quantity", __("Quantity"), (r) => num(r.allocated_quantity)]], v.allocations)));
  }

  function renderInbound(frm, v) {
    setHtml(frm, "status_html",
      section(__("Goods Receipts"), table([["name", __("Goods Receipt"), (r) => lnk("Goods Receipt", r.name)], ["docstatus", __("Docstatus")], ["creation", __("Created"), (r) => fmt(r.creation)]], v.receipts)) +
      section(__("Putaway Requests"), table([["name", __("Request"), (r) => lnk("Warehouse Request", r.name)], ["request_type", __("Type")], ["product", __("Product")], ["requested_quantity", __("Quantity"), (r) => num(r.requested_quantity)],
        ["created_quantity", __("Tasked"), (r) => num(r.created_quantity)], ["source_hu", __("HU"), (r) => lnk("Handling Unit", r.source_hu)], ["destination_bin", __("To bin"), (r) => lnk("Storage Bin", r.destination_bin)], ["status", __("Status")]], v.requests)) +
      section(__("Warehouse Tasks"), table(taskCols, v.tasks)));
    const d = frm.doc;
    setHtml(frm, "locations_html", kv([[__("Receiving bin"), lnk("Storage Bin", d.receiving_bin)]]) +
      section(__("Received into"), table([["handling_unit", __("Handling Unit"), (r) => lnk("Handling Unit", r.handling_unit)], ["current_bin", __("Bin"), (r) => lnk("Storage Bin", r.current_bin)]], v.handling_units)));
  }

  // ---------- inbound: receive & pack, create tasks
  async function receiveDialog(frm) {
    const wl = await frappe.call("frappe_wms.api.inbound.receiving_worklist", { inbound_delivery: frm.doc.name }).then((r) => r.message);
    const types = `<option value=""></option>` + (wl.hu_types || []).map((t) => `<option ${t.name === wl.default_hu_type ? "selected" : ""}>${esc(t.name)}</option>`).join("");
    const row = (l) => `<tr data-line="${esc(l.inbound_delivery_item)}" data-item="${esc(l.item)}" data-uom="${esc(l.stock_uom)}">
      <td>${esc(l.line_number)} ${esc(l.item)}<br><span class="text-muted">${esc(l.item_name || "")}</span></td><td class="text-right">${flt(l.remaining)}</td>
      <td><input type="number" step="any" class="form-control input-xs r-qty" style="width:80px" value="${flt(l.remaining)}"></td>
      <td><input class="form-control input-xs r-hu" style="width:110px" placeholder="${__("new")}"></td><td><select class="form-control input-xs r-hut" style="width:110px">${types}</select></td>
      <td><input class="form-control input-xs r-batch" style="width:100px" ${l.batch_required ? "" : "disabled"} placeholder="${l.batch_required ? __("required") : ""}"></td>
      <td><input class="form-control input-xs r-serial" style="width:100px" ${l.serial_required ? "" : "disabled"} placeholder="${l.serial_required ? __("required") : ""}"></td>
      <td><input class="form-control input-xs r-bin" style="width:130px" placeholder="${__("by putaway rules")}"></td>
      <td><button type="button" class="btn btn-xs btn-default r-split" title="${__("Split the line over another handling unit")}">+</button></td></tr>`;
    const d = new frappe.ui.Dialog({ title: __("Receive & Pack {0}", [frm.doc.name]), size: "extra-large", fields: [
      { fieldname: "lines", fieldtype: "HTML" },
      { fieldname: "create_tasks", fieldtype: "Check", label: __("Create putaway tasks now"), default: 1, description: __("Untick to receive now and create the tasks later.") }],
      primary_action_label: __("Receive"), primary_action: async (v) => {
        const items = [];
        d.$wrapper.find("tr[data-line]").each(function () {
          const $r = $(this), quantity = flt($r.find(".r-qty").val());
          if (!(quantity > 0)) return;
          items.push({ inbound_delivery_item: $r.data("line"), item: $r.data("item"), stock_uom: $r.data("uom"), quantity, handling_unit: $r.find(".r-hu").val().trim(), hu_type: $r.find(".r-hut").val() || undefined,
            batch_no: $r.find(".r-batch").val().trim() || undefined, serial_no: $r.find(".r-serial").val().trim() || undefined, destination_bin: $r.find(".r-bin").val().trim() || undefined, stock_type: "AVAILABLE" });
        });
        if (!items.length) { frappe.show_alert({ message: __("Enter a quantity to receive."), indicator: "orange" }); return; }
        const out = await frappe.call("frappe_wms.api.inbound.create_and_submit_goods_receipt", { inbound_delivery: frm.doc.name, items: JSON.stringify(items), create_tasks: v.create_tasks ? 1 : 0 }).then((r) => r.message);
        d.hide();
        frappe.show_alert({ message: __("Goods Receipt {0} posted, {1} task(s) created", [out.goods_receipt, out.warehouse_tasks.length]), indicator: "green" });
        frm.reload_doc();
      } });
    d.fields_dict.lines.$wrapper.html(`<div style="overflow-x:auto;"><table class="table table-bordered table-sm"><thead><tr><th>${__("Line")}</th><th>${__("Open")}</th><th>${__("Quantity")}</th>
      <th>${__("Handling Unit")}<br><span class="text-muted" style="font-weight:normal">${__("blank = new, #1 = shared")}</span></th><th>${__("HU Type")}</th><th>${__("Batch")}</th><th>${__("Serial No")}</th>
      <th>${__("Direct Placement Bin")}</th><th></th></tr></thead><tbody>${wl.lines.map(row).join("")}</tbody></table></div>`);
    d.fields_dict.lines.$wrapper.on("click", ".r-split", (e) => {
      const $r = $(e.currentTarget).closest("tr"), $c = $r.clone();
      $c.find("input").not(".r-qty").val(""); $c.find(".r-qty").val(0); $c.find(".r-split").remove(); $r.after($c);
    });
    d.show();
  }

  function inboundTasksDialog(frm, count) {
    const d = new frappe.ui.Dialog({ title: __("Create Tasks for {0}", [frm.doc.name]), fields: [
      { fieldname: "info", fieldtype: "HTML", options: `<p>${__("{0} putaway request(s) are waiting for tasks.", [count])}</p>` },
      { fieldname: "destination_bin", fieldtype: "Link", options: "Storage Bin", label: __("Direct Placement Bin"), description: __("Blank: the putaway rules choose the bins."), get_query: () => ({ filters: { warehouse: frm.doc.warehouse } }) }],
      primary_action_label: __("Create Tasks"), primary_action: async (v) => {
        const out = await call("inbound.plan_open_putaway", { inbound_delivery: frm.doc.name, destination_bin: v.destination_bin });
        d.hide();
        frappe.show_alert({ message: __("{0} task(s) created", [out.warehouse_tasks.length]), indicator: out.unplanned_requests.length ? "orange" : "green" });
        frm.reload_doc();
      } });
    d.show();
  }

  // ---------- outbound
  function shipmentDialog(frm) {
    const d = new frappe.ui.Dialog({ title: __("Create Shipment for {0}", [frm.doc.name]), fields: [
      { fieldname: "route", fieldtype: "Link", options: "WMS Route", label: __("Route"), default: frm.doc.route }, { fieldname: "carrier", fieldtype: "Link", options: "Supplier", label: __("Carrier") },
      { fieldname: "vehicle_registration", fieldtype: "Data", label: __("Vehicle Registration") }, { fieldname: "driver_name", fieldtype: "Data", label: __("Driver") }],
      primary_action_label: __("Create"), primary_action: async (v) => {
        const name = await call("shipping.create_shipment", { warehouse: frm.doc.warehouse, outbound_deliveries: JSON.stringify([frm.doc.name]), ...v });
        d.hide(); frappe.show_alert({ message: __("Shipment {0} created", [name]), indicator: "green" }); frm.reload_doc();
      } });
    d.show();
  }

  function splitDialog(frm) {
    const open = frm.doc.items.filter((r) => flt(r.requested_quantity) - Math.max(flt(r.allocated_quantity), flt(r.picked_quantity), flt(r.packed_quantity), flt(r.issued_quantity)) > 0);
    if (!open.length) return frappe.msgprint(__("Nothing is left that has not been allocated, picked or issued."));
    const d = new frappe.ui.Dialog({ title: __("Split {0}", [frm.doc.name]), fields: [
      { fieldname: "lines", fieldtype: "Table", label: __("Quantity to move to the new delivery"), cannot_add_rows: true, in_place_edit: true, data: open.map((r) => ({ line: r.name, item: r.item, quantity: 0 })),
        fields: [{ fieldname: "line", fieldtype: "Data", hidden: 1 }, { fieldname: "item", fieldtype: "Data", read_only: 1, in_list_view: 1, label: __("Item") }, { fieldname: "quantity", fieldtype: "Float", in_list_view: 1, label: __("Move") }] },
      { fieldname: "reason", fieldtype: "Small Text", label: __("Reason") }],
      primary_action_label: __("Split"), primary_action(v) {
        const lines = v.lines.filter((l) => flt(l.quantity) > 0).map((l) => ({ line: l.line, quantity: l.quantity }));
        if (!lines.length) return;
        frappe.call({ method: "frappe_wms.api.erp_integration.split_delivery", args: { delivery_name: frm.doc.name, lines, reason: v.reason }, freeze: true }).then((r) => {
          d.hide(); frappe.show_alert({ message: __("Created {0}", [r.message.new]), indicator: "green" }); frm.reload_doc();
        });
      } });
    d.show();
  }

  function buttons(frm, side, v) {
    const d = frm.doc, released = d.docstatus === 1 && !d.closed_short && !["Completed", "Cancelled"].includes(d.status);
    if (d.delivery_request) frm.add_custom_button(__("Delivery Request"), () => frappe.set_route("Form", "WMS Delivery Request", d.delivery_request));
    if (!released) return;
    const A = __("Actions");
    if (side === "Inbound") {
      const open = (v.items || []).reduce((a, i) => a + i.open, 0);
      if (open > 0) frm.add_custom_button(__("Receive & Pack…"), () => receiveDialog(frm)).addClass("btn-primary");
      if ((v.open_requests || []).length) frm.add_custom_button(__("Create Tasks…"), () => inboundTasksDialog(frm, v.open_requests.length), A);
    } else {
      if (d.allocation_status !== "Fully Allocated") frm.add_custom_button(__("Allocate Stock"), () => call("outbound.allocate_delivery", { delivery_name: d.name }).then(() => frm.reload_doc())).addClass("btn-primary");
      else if (d.picking_status !== "Picked") frm.add_custom_button(__("Create Pick Tasks"), () => call("outbound.create_pick_tasks", { delivery_name: d.name }).then((r) => { frappe.show_alert({ message: __("{0} pick task(s) created", [r.length]), indicator: "green" }); frm.reload_doc(); })).addClass("btn-primary");
      if (d.packing_status === "Not Started") frm.add_custom_button(__("Plan Cartons"), () => call("outbound.plan_cartons", { delivery_name: d.name }).then((r) => { frappe.show_alert({ message: __("{0} shipping HU(s) planned", [r.cartons]), indicator: "green" }); frm.reload_doc(); }), A);
      if (d.picking_status === "Picked" && d.loading_status !== "Loaded" && d.goods_issue_status !== "Posted" && !(v.shipments || []).length) frm.add_custom_button(__("Create Shipment…"), () => shipmentDialog(frm), A);
      if (d.picking_status === "Picked" && d.loading_status === "Loaded" && d.goods_issue_status !== "Posted") frm.add_custom_button(__("Post Goods Issue"), () => call("outbound.post_goods_issue_for_delivery", { delivery_name: d.name }).then(() => { frappe.show_alert({ message: __("Goods Issue posted"), indicator: "green" }); frm.reload_doc(); })).addClass("btn-success");
    }
    if (frappe.user.has_role(["WMS Supervisor", "WMS Administrator", "System Manager"])) {
      if (side === "Outbound") frm.add_custom_button(__("Split Delivery"), () => splitDialog(frm), A);
      if (window.frappe_wms_complete_short) frappe_wms_complete_short(frm);
    }
  }

  // The items of the delivery as an ALV grid with each line's own statuses (the Items tab).
  function renderItems(frm, side, $host) {
    const out = side === "Outbound";
    const cols = out
      ? [["line_number", __("Line")], ["item", __("Product"), (r) => lnk("Item", r.item)], ["requested_quantity", __("Quantity")], ["allocated_quantity", __("Allocated")], ["picked_quantity", __("Picked")], ["packed_quantity", __("Packed")],
         ["issued_quantity", __("Issued")], ["stock_uom", __("UoM")], ["required_stock_type", __("Stock Type")], ["required_batch", __("Batch")], ["status", __("Status")]]
      : [["line_number", __("Line")], ["item", __("Product"), (r) => lnk("Item", r.item)], ["item_name", __("Description")], ["expected_quantity", __("Expected")], ["received_quantity", __("Received")], ["putaway_quantity", __("Put away")],
         ["stock_uom", __("UoM")], ["expected_stock_type", __("Stock Type")], ["status", __("Status")]];
    $host.empty().append(window.wms_grid.DataGrid
      ? new window.wms_grid.DataGrid(frm.doc.items || [], cols, out ? "Outbound Delivery Item" : "Inbound Delivery Item", { noGroup: true, totals: true, exportName: frm.doc.name,
        numeric: out ? ["requested_quantity", "allocated_quantity", "picked_quantity", "packed_quantity", "issued_quantity"] : ["expected_quantity", "received_quantity", "putaway_quantity"] }).$el
      : table(cols, frm.doc.items));
  }

  // Fetches the delivery's tabs and draws everything the screen shows besides the header: indicators, items, tabs and the buttons.
  async function render(frm, side) {
    const v = await frappe.call({ method: "frappe_wms.api.monitor.get_delivery_view", args: { doctype: frm.doctype, name: frm.doc.name } }).then((r) => r.message);
    const d = frm.doc;
    indicators(frm, side === "Outbound"
      ? [[__("Delivery"), d.status], [__("Allocation"), d.allocation_status], [__("Picking"), d.picking_status], [__("Packing"), d.packing_status], [__("Loading"), d.loading_status], [__("Goods Issue"), d.goods_issue_status]]
      : [[__("Delivery"), d.status], [__("Receipt"), d.receipt_status], [__("Process"), d.process_status], [__("Yard"), d.yard_status]]);
    if (frm.fields_dict.items_html) renderItems(frm, side, frm.fields_dict.items_html.$wrapper);
    side === "Outbound" ? renderOutbound(frm, v) : renderInbound(frm, v);
    renderCommon(frm, v, side);
    if (frm.render_header) frm.render_header(v);
    buttons(frm, side, v);
    return v;
  }

  // The standard form is the data view; the delivery's screen is its own page (SAP: the monitor row opens the maintain-delivery transaction).
  function refresh(frm, side) {
    if (window.frappe_wms) frappe_wms.set_warehouse_filters(frm);
    if (frm.is_new() || frappe.flags.wms_standard_form) { frappe.flags.wms_standard_form = false; return; }
    frappe.set_route(side === "Outbound" ? "wms-outbound-delivery" : "wms-inbound-delivery", frm.doc.name);
  }
  return { refresh, render };
})();
