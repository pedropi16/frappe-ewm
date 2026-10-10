// SAP EWM "Maintain Outbound Delivery Order" (/SCWM/PRDO) / "Maintain Inbound Delivery" (/SCWM/PRDI): one delivery on its own screen.
//   top    - find another delivery: quick search by number / external reference / partner / source document / product, or the monitor's advanced selection
//   middle - the delivery's header: statuses and general data, with the toolbar of processing actions
//   bottom - tabs: Items, Status, Dates / Times, Locations, Partner, Reference Documents, HU, Transportation Unit
// The Monitor finds deliveries and links here; the processing happens on this screen (public/js/wms_delivery_form.js has its actions and tab contents).
window.wms_delivery_page = (function () {
  const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
  const TABS = [["items", __("Items")], ["status", __("Status")], ["dates", __("Dates / Times")], ["locations", __("Locations")], ["partner", __("Partner")],
    ["references", __("Reference Documents")], ["hu", __("HU")], ["transport", __("Transportation Unit")]];

  function mount(wrapper, doctype) {
    const out = doctype === "Outbound Delivery", side = out ? "Outbound" : "Inbound", route = out ? "wms-outbound-delivery" : "wms-inbound-delivery";
    const page = frappe.ui.make_app_page({ parent: wrapper, title: out ? __("Maintain Outbound Delivery Order") : __("Maintain Inbound Delivery"), single_column: true });
    const slug = frappe.router.slug(doctype);
    const FINDS = [["number", __("Delivery number")], ["external", __("External reference")], ["partner", out ? __("Customer") : __("Supplier")], ["source", __("Source document")], ["product", __("Product")]];
    const $root = $(`<div class="wms-dm">
      <style>
        .wms-dm-card { border:1px solid var(--border-color); border-radius:8px; padding:10px 14px; margin-bottom:10px; background:var(--card-bg,#fff); }
        .wms-dm-find { display:flex; flex-wrap:wrap; gap:6px; align-items:center; }
        .wms-dm-find select, .wms-dm-find input { height:28px; }
        .wms-dm-head { display:grid; grid-template-columns:repeat(3, minmax(260px, 1fr)); gap:4px 28px; }
        .wms-dm-head table { margin:0; font-size:12px; } .wms-dm-head td { padding:2px 6px 2px 0; border:0; } .wms-dm-head td:first-child { color:var(--text-muted); white-space:nowrap; width:42%; }
        .wms-dm-toolbar { display:flex; flex-wrap:wrap; gap:6px; align-items:center; margin-bottom:8px; }
        .wms-dm-title { font-size:15px; font-weight:600; margin-right:8px; }
        .wms-dm-tabs { display:flex; gap:2px; border-bottom:1px solid var(--border-color); margin-bottom:10px; flex-wrap:wrap; }
        .wms-dm-tab { padding:6px 14px; cursor:pointer; border:1px solid transparent; border-bottom:0; border-radius:6px 6px 0 0; font-size:13px; color:var(--text-muted); }
        .wms-dm-tab.active { background:var(--card-bg,#fff); border-color:var(--border-color); color:var(--text-color); font-weight:600; margin-bottom:-1px; }
      </style>
      <div class="wms-dm-card"><div class="wms-dm-find">
        <span class="text-muted">${__("Find")}</span><select class="form-control input-sm wms-dm-by" style="width:170px">${FINDS.map(([k, l]) => `<option value="${k}">${esc(l)}</option>`).join("")}</select>
        <input class="form-control input-sm wms-dm-value" style="width:240px" placeholder="${__("Enter and press Enter")}">
        <button type="button" class="btn btn-primary btn-xs wms-dm-go">${__("Search")}</button>
        <button type="button" class="btn btn-default btn-xs wms-dm-adv-btn">${__("Advanced Search")}</button>
        <span class="text-muted" style="margin-left:10px">${__("Warehouse")}</span><select class="form-control input-sm wms-dm-wh" style="width:160px"></select>
      </div><div class="wms-dm-hits" style="margin-top:8px"></div><div class="wms-dm-adv" style="display:none;margin-top:8px"><div class="wms-dm-adv-sel"></div><div class="wms-dm-adv-res"></div></div></div>
      <div class="wms-dm-list" style="display:none">
        <div class="wms-dm-card">
          <div class="wms-dm-toolbar">
            <button type="button" class="btn btn-default btn-xs wms-dm-open" title="${__("Open the marked delivery")}">${__("Display")}</button>
            <button type="button" class="btn btn-default btn-xs wms-dm-refresh">${__("Refresh")}</button>
            <button type="button" class="btn btn-default btn-xs wms-dm-mass" title="${__("Enter values once and put them into all marked deliveries")}">&#9998; ${__("Mass Change")}</button>
            ${(out ? [["allocate", __("Allocate Stock")], ["pick", __("Create Pick Tasks")], ["cartons", __("Plan Cartons")], ["issue", __("Post Goods Issue")]] : [["putaway", __("Create Putaway Tasks")]])
              .map(([k, l]) => `<button type="button" class="btn btn-default btn-xs wms-dm-act" data-act="${k}">${esc(l)}</button>`).join("")}
          </div>
          <div class="wms-dm-grid"></div>
          <div class="wms-dm-status text-muted" style="margin-top:6px;font-size:12px"></div>
        </div>
      </div>
      <div class="wms-dm-empty text-muted" style="padding:30px;text-align:center">${__("Search for a delivery to display it.")}</div>
      <div class="wms-dm-doc" style="display:none">
        <div class="wms-dm-card">
          <div class="wms-dm-toolbar"><button type="button" class="btn btn-default btn-xs wms-dm-back" style="display:none" title="${__("Back to the list")}">&#9664; ${__("List")}</button><button type="button" class="btn btn-default btn-xs wms-dm-prev" style="display:none">&#9664;</button><button type="button" class="btn btn-default btn-xs wms-dm-next" style="display:none">&#9654;</button><span class="wms-dm-pos text-muted"></span><span class="wms-dm-title"></span><span class="wms-dm-pills"></span><span style="flex:1"></span><span class="wms-dm-buttons" style="display:flex;gap:6px;flex-wrap:wrap"></span></div>
          <div class="wms-dm-head"></div>
        </div>
        <div class="wms-dm-card"><div class="wms-dm-tabs">${TABS.map(([k, l], i) => `<div class="wms-dm-tab ${i ? "" : "active"}" data-tab="${k}">${esc(l)}</div>`).join("")}</div>
          ${TABS.map(([k], i) => `<div class="wms-dm-pane" data-pane="${k}" style="${i ? "display:none" : ""}"></div>`).join("")}</div>
      </div></div>`).appendTo(page.main);
    const $pane = (k) => $root.find(`.wms-dm-pane[data-pane="${k}"]`);
    const state = { warehouses: [], selection: null, rows: [], picked: [] };

    // The surface the screen shares with the Outbound / Inbound Delivery form code: tab containers, headline, buttons.
    const frm = {
      doctype, doc: null,
      fields_dict: Object.fromEntries([["items_html", "items"], ["status_html", "status"], ["dates_html", "dates"], ["locations_html", "locations"], ["partner_html", "partner"], ["references_html", "references"], ["hu_html", "hu"], ["transport_html", "transport"]]
        .map(([f, k]) => [f, { $wrapper: $pane(k) }])),
      dashboard: { set_headline: () => {} },
      is_new: () => false,
      reload_doc: () => load(frm.doc.name),
      add_custom_button(label, fn, group) {
        const $btn = $(`<button type="button" class="btn btn-default btn-xs"></button>`).text(label).on("click", (e) => { e.preventDefault(); fn(); });
        if (!group) { $root.find(".wms-dm-buttons").append($btn); return $btn; }
        let $g = $root.find(`.wms-dm-group[data-group="${group}"]`);
        if (!$g.length) {
          $g = $(`<div class="btn-group wms-dm-group" data-group="${esc(group)}"><button type="button" class="btn btn-default btn-xs dropdown-toggle" data-toggle="dropdown" data-bs-toggle="dropdown">${esc(group)} <span class="caret"></span></button><ul class="dropdown-menu dropdown-menu-right"></ul></div>`).appendTo($root.find(".wms-dm-buttons"));
        }
        const $li = $(`<li><a class="dropdown-item" href="#"></a></li>`).appendTo($g.find("ul"));
        $li.find("a").text(label).on("click", (e) => { e.preventDefault(); fn(); });
        return $li.find("a");
      },
      render_header(v) { headerBlock(v); },
    };

    function kv(rows) { return `<table><tbody>${rows.filter((r) => r[1] !== "" && r[1] != null).map((r) => `<tr><td>${esc(r[0])}</td><td>${r[1]}</td></tr>`).join("")}</tbody></table>`; }
    const lnk = (dt, name) => (name ? `<a href="/app/${frappe.router.slug(dt)}/${encodeURIComponent(name)}">${esc(name)}</a>` : "");
    const dt = (v) => (v ? esc(frappe.datetime.str_to_user(String(v).slice(0, 19))) : "");

    function headerBlock(v) {
      const d = frm.doc, ship = (v.shipments || [])[0] || {}, unit = (v.transport_units || [])[0] || {};
      const user = (u) => (u ? esc(frappe.user.full_name ? frappe.user.full_name(u) : u) : "");
      $root.find(".wms-dm-title").text(`${d.outbound_delivery_number || d.inbound_delivery_number} (${d.name})`);
      const pills = out
        ? [[__("Delivery"), d.status], [__("Allocation"), d.allocation_status], [__("Picking"), d.picking_status], [__("Packing"), d.packing_status], [__("Loading"), d.loading_status], [__("Goods Issue"), d.goods_issue_status]]
        : [[__("Delivery"), d.status], [__("Receipt"), d.receipt_status], [__("Process"), d.process_status], [__("Yard"), d.yard_status]];
      const tone = (x) => (/^(Completed|Goods Issued|Received|Posted|Picked|Fully Allocated|Loaded|Packed|Fully Received)$/.test(x) ? "green" : /Cancelled|Exception/.test(x) ? "red" : /Not |Open|Expected|Draft/.test(x) ? "gray" : "blue");
      $root.find(".wms-dm-pills").html(pills.filter((p) => p[1]).map(([l, x]) => `<span class="indicator-pill ${tone(x)}" style="margin-right:4px"><span class="ellipsis">${esc(l)}: ${esc(__(x))}</span></span>`).join("")
        + (d.closed_short ? `<span class="indicator-pill orange"><span class="ellipsis">${__("completed short")}</span></span>` : "") + (d.docstatus === 2 ? `<span class="indicator-pill red"><span class="ellipsis">${__("Cancelled")}</span></span>` : ""));
      const left = out
        ? [[__("Document"), esc(d.outbound_delivery_number)], [__("Document Type"), lnk("WMS Document Type", d.document_type)], [__("Warehouse"), lnk("WMS Warehouse", d.warehouse)], [__("Customer"), lnk("Customer", d.customer)],
           [__("Ship-to"), esc(d.ship_to_party)], [__("Priority"), esc(d.priority)], [__("Delivery date"), dt(d.delivery_date)], [__("External reference"), esc(d.external_reference)]]
        : [[__("Document"), esc(d.inbound_delivery_number)], [__("Document Type"), lnk("WMS Document Type", d.document_type)], [__("Warehouse"), lnk("WMS Warehouse", d.warehouse)], [__("Supplier"), lnk("Supplier", d.supplier)],
           [__("Posting date"), dt(d.posting_date)], [__("Expected arrival"), dt(d.expected_arrival)], [__("External reference"), esc(d.external_reference)]];
      const middle = out
        ? [[__("Route"), lnk("WMS Route", d.route)], [__("Staging bin"), lnk("Storage Bin", d.staging_bin)], [__("Warehouse door"), lnk("Storage Bin", d.door)], [__("Shipment"), lnk("WMS Shipment", ship.name)],
           [__("Vehicle"), esc(ship.vehicle_registration || unit.vehicle_registration)], [__("Transportation unit"), lnk("WMS Transportation Unit", unit.name)], [__("Preceding document"), (d.erp_source_name ? lnk(d.erp_source_doctype, d.erp_source_name) : "")],
           [__("Delivery request"), lnk("WMS Delivery Request", d.delivery_request)]]
        : [[__("Receiving bin"), lnk("Storage Bin", d.receiving_bin)], [__("Transportation unit"), lnk("WMS Transportation Unit", unit.name)], [__("Vehicle"), esc(unit.vehicle_registration)],
           [__("Preceding document"), (d.erp_source_name ? lnk(d.erp_source_doctype, d.erp_source_name) : "")], [__("Delivery request"), lnk("WMS Delivery Request", d.delivery_request)]];
      const right = [[__("Owner"), lnk("WMS Stock Owner", d.stock_owner)], [__("Party entitled to dispose"), lnk("WMS Stock Owner", d.entitled_party)], [__("Created by"), user(d.owner)], [__("Created on"), dt(d.creation)],
        [__("Last changed by"), user(d.modified_by)], [__("Changed on"), dt(d.modified)], [__("Closed short"), d.closed_short ? esc(d.close_reason || __("Yes")) : ""]];
      $root.find(".wms-dm-head").html(kv(left) + kv(middle) + kv(right));
    }

    async function load(name) {
      const docs = await frappe.call({ method: "frappe.client.get", args: { doctype, name } }).then((r) => r.message);
      frm.doc = docs;
      $root.find(".wms-dm-empty, .wms-dm-list").hide(); $root.find(".wms-dm-doc").show(); $root.find(".wms-dm-hits").empty();
      const at = state.rows.findIndex((r) => r.name === docs.name);
      $root.find(".wms-dm-back").toggle(state.rows.length > 0); $root.find(".wms-dm-prev, .wms-dm-next").toggle(at >= 0 && state.rows.length > 1);
      $root.find(".wms-dm-pos").text(at >= 0 && state.rows.length > 1 ? `${at + 1} / ${state.rows.length}` : "");
      $root.find(".wms-dm-buttons").empty();
      state.warehouse = docs.warehouse; $root.find(".wms-dm-wh").val(docs.warehouse);
      Object.values(frm.fields_dict).forEach((f) => f.$wrapper.empty());
      page.set_title(`${out ? __("Maintain Outbound Delivery Order") : __("Maintain Inbound Delivery")} · ${docs.name}`);
      // processing buttons first, then the screen's own: Delivery Request, Cancel, standard form
      await frappe_wms_delivery.render(frm, side);
      if (docs.docstatus === 1) frm.add_custom_button(__("Cancel Delivery"), () => frappe.confirm(__("Cancel {0}?", [docs.name]), () => frappe.call({ method: "frappe.client.cancel", args: { doctype, name: docs.name }, freeze: true }).then(() => load(docs.name))), __("More"));
      frm.add_custom_button(__("Standard Form"), () => { frappe.flags.wms_standard_form = true; frappe.set_route("Form", doctype, docs.name); }, __("More"));
    }

    async function find() {
      const value = $root.find(".wms-dm-value").val().trim();  // empty = the latest deliveries
      const rows = await frappe.call({ method: "frappe_wms.api.monitor.find_deliveries", args: { doctype, by: $root.find(".wms-dm-by").val(), value, warehouse: $root.find(".wms-dm-wh").val() || undefined } }).then((r) => r.message || []);
      if (rows.length === 1 && value) { frappe.set_route(route, rows[0].name); return; }
      if (!rows.length && $root.find(".wms-dm-wh").val()) { $root.find(".wms-dm-hits").html(`<div class="text-muted">${__("No delivery found in {0}.", [$root.find(".wms-dm-wh").val()])} <a href="#" class="wms-dm-all">${__("Search all warehouses")}</a></div>`);
        $root.find(".wms-dm-all").on("click", (e) => { e.preventDefault(); $root.find(".wms-dm-wh").val(""); find(); }); return; }
      if (!rows.length) { $root.find(".wms-dm-hits").html(`<div class="text-muted">${__("No delivery found.")}</div>`); return; }
      showList(rows);
    }

    // ---- the list of several hits (SAP: the ALV of the found documents); the marked rows are what Display, Mass Change and the actions work on
    const tone = (x) => (/^(Completed|Goods Issued|Received|Posted|Picked|Fully Allocated|Loaded|Packed|Fully Received)$/.test(x) ? "green" : /Cancelled|Exception/.test(x) ? "red" : /Not |Open|Expected|Draft/.test(x) ? "gray" : "blue");
    const pill = (x) => (x ? `<span class="indicator-pill ${tone(x)}"><span class="ellipsis">${esc(__(x))}</span></span>` : "");
    const listStatus = (text, kind) => $root.find(".wms-dm-status").attr("class", `wms-dm-status text-${kind || "muted"}`).text(text);
    function drawList(keep) {
      state.picked = [];
      const open = (r) => `<a href="/app/${route}/${encodeURIComponent(r.name)}">${esc(r.number || r.name)}</a> <span class="text-muted">${esc(r.name)}</span>`;
      const cols = [["number", __("Delivery"), open], ["partner", out ? __("Customer") : __("Supplier")], ["status", __("Status"), (r) => pill(r.status)],
        ...(out ? [["allocation_status", __("Allocation"), (r) => pill(r.allocation_status)], ["picking_status", __("Picking"), (r) => pill(r.picking_status)], ["packing_status", __("Packing"), (r) => pill(r.packing_status)],
          ["loading_status", __("Loading"), (r) => pill(r.loading_status)], ["goods_issue_status", __("Goods Issue"), (r) => pill(r.goods_issue_status)], ["priority", __("Priority")], ["route", __("Route")], ["staging_bin", __("Staging Bin")], ["door", __("Door")]]
          : [["receipt_status", __("Receipt"), (r) => pill(r.receipt_status)], ["process_status", __("Process"), (r) => pill(r.process_status)], ["yard_status", __("Yard"), (r) => pill(r.yard_status)], ["receiving_bin", __("Receiving Bin")]]),
        ["date", out ? __("Delivery Date") : __("Expected Arrival"), (r) => dt(r.date)], ["warehouse", __("Warehouse")], ["external_reference", __("External Reference")],
        ["result", __("Result"), (r) => `<span class="wms-dm-res ${(r.result || "").startsWith("\u2714") ? "text-success" : "text-danger"}" data-k="${esc(r.name)}" title="${esc(r.result || "")}">${esc((r.result || "").slice(0, 90))}</span>`]];
      const grid = new window.wms_grid.DataGrid(state.rows, cols, null, { exportName: out ? "outbound-deliveries" : "inbound-deliveries", onSelect: (sel) => { state.picked = sel; } });
      $root.find(".wms-dm-grid").empty().append(grid.$el);
      if (keep && keep.size) {  // the rows stay marked after an action, so the next one (allocate, then pick) works on the same deliveries
        grid.sels = state.rows.map((r, i) => (keep.has(r.name) ? { r0: i + 1, r1: i + 1, c0: 1, c1: grid._maxC } : null)).filter(Boolean);
        grid._applyHighlight(); grid._renderActionBar();
      }
    }
    function showList(rows) {
      if (rows) state.rows = rows;
      $root.find(".wms-dm-doc, .wms-dm-empty").hide(); $root.find(".wms-dm-list").show();
      drawList(); listStatus(__("Selection resulted in {0} hit(s)", [state.rows.length]));
    }
    const marked = () => (state.picked.length ? state.picked : []);
    const needMarked = () => { if (!marked().length) { listStatus(__("Mark the deliveries first (click their row numbers)."), "warning"); return false; } return true; };
    async function runOn(action, values) {
      const rows = marked();
      const res = await frappe.call({ method: "frappe_wms.api.monitor.process_deliveries", args: { doctype, action, names: JSON.stringify(rows.map((r) => r.name)), values: values ? JSON.stringify(values) : undefined }, freeze: true }).then((r) => r.message);
      const by = Object.fromEntries(rows.map((r) => [r.name, r]));
      res.done.forEach((x) => { by[x.name].result = `\u2714 ${x.text}`; });
      res.errors.forEach((x) => { by[x.name].result = `\u2718 ${x.error}`; });
      const fresh = await frappe.call({ method: "frappe_wms.api.monitor.delivery_rows", args: { doctype, names: JSON.stringify(rows.map((r) => r.name)) } }).then((r) => r.message || []);
      fresh.forEach((f) => Object.assign(by[f.name], f, { result: by[f.name].result }));
      drawList(new Set(rows.map((r) => r.name)));
      window.wms_grid.report(res.done.length ? __("{0} delivery(ies) processed", [res.done.length]) : "", res.errors.map((x) => [by[x.name].number || x.name, x.error]));
      listStatus(res.errors.length ? __("{0} delivery(ies) processed, {1} refused: {2}", [res.done.length, res.errors.length, res.errors[0].error]) : __("{0} delivery(ies) processed", [res.done.length]), res.errors.length ? "danger" : "success");
    }
    function massChange() {
      if (!needMarked()) return;
      const F = out ? [["priority", __("Priority"), "Select", "\nLow\nNormal\nHigh\nUrgent"], ["route", __("Route"), "Link", "WMS Route"], ["staging_bin", __("Staging Bin"), "Link", "Storage Bin"], ["door", __("Door"), "Link", "Storage Bin"], ["delivery_date", __("Delivery Date"), "Datetime"]]
        : [["receiving_bin", __("Receiving Bin"), "Link", "Storage Bin"], ["expected_arrival", __("Expected Arrival"), "Datetime"]];
      const d = new frappe.ui.Dialog({ title: __("Mass Change for {0} marked delivery(ies)", [marked().length]),
        fields: F.map(([fieldname, label, fieldtype, options]) => ({ fieldname, label, fieldtype, options, get_query: options === "Storage Bin" ? () => ({ filters: { warehouse: marked()[0].warehouse } }) : undefined })),
        primary_action_label: __("Apply to marked deliveries"), primary_action: async (values) => { d.hide(); await runOn("change", values); } });
      d.show();
    }
    $root.on("click", ".wms-dm-open", () => { if (marked().length) frappe.set_route(route, marked()[0].name); else listStatus(__("Mark a delivery first (click its row number)."), "warning"); });
    $root.on("click", ".wms-dm-refresh", async () => { if (!state.rows.length) return; const keep = Object.fromEntries(state.rows.map((r) => [r.name, r.result]));
      const rows = await frappe.call({ method: "frappe_wms.api.monitor.delivery_rows", args: { doctype, names: JSON.stringify(state.rows.map((r) => r.name)) } }).then((r) => r.message || []);
      rows.forEach((r) => { r.result = keep[r.name]; }); showList(rows); });
    $root.on("click", ".wms-dm-mass", massChange);
    $root.on("click", ".wms-dm-act", (e) => { if (needMarked()) runOn($(e.currentTarget).data("act")); });
    const step = (n) => { const at = state.rows.findIndex((r) => r.name === frm.doc.name), to = state.rows[at + n]; if (to) frappe.set_route(route, to.name); };
    $root.on("click", ".wms-dm-prev", () => step(-1)); $root.on("click", ".wms-dm-next", () => step(1));
    $root.on("click", ".wms-dm-back", () => { frappe_wms_delivery.release(); showList(); });

    async function advanced() {
      $root.find(".wms-dm-adv").show();
      if (!$root.find(".wms-dm-wh").val()) $root.find(".wms-dm-wh").val(state.warehouses[0] || "");  // the advanced selection works in one warehouse
      if (!state.selection) {
        state.selection = await new wms_selection.SelectionScreen({
          view: out ? "outbound" : "inbound", $mount: $root.find(".wms-dm-adv-sel"), $results: $root.find(".wms-dm-adv-res"), autoOpen: () => false, decorate: { noDetails: true },
          getWarehouse: () => $root.find(".wms-dm-wh").val() || state.warehouses[0],
          makeGrid: (rows) => { frappe.call({ method: "frappe_wms.api.monitor.delivery_rows", args: { doctype, names: JSON.stringify(rows.map((r) => r.name)) } }).then((r) => showList(r.message || [])); return $("<div></div>"); },
        }).init();
      }
      state.selection.openDialog();
    }

    $root.on("click", ".wms-dm-tab", function () {
      $root.find(".wms-dm-tab").removeClass("active"); $(this).addClass("active");
      $root.find(".wms-dm-pane").hide(); $pane($(this).data("tab")).show();
    });
    $root.find(".wms-dm-go").on("click", find);
    $root.find(".wms-dm-value").on("keydown", (e) => { if (e.key === "Enter") find(); });
    $root.find(".wms-dm-adv-btn").on("click", advanced);
    frappe.call({ method: "frappe.client.get_list", args: { doctype: "WMS Warehouse", fields: ["name"], limit_page_length: 100 } }).then((r) => {
      state.warehouses = (r.message || []).map((w) => w.name);
      // quick search covers every warehouse unless one is chosen
      $root.find(".wms-dm-wh").html(`<option value="">${__("All warehouses")}</option>` + state.warehouses.map((w) => `<option>${esc(w)}</option>`).join("")).val(frm.doc ? frm.doc.warehouse : frappe_wms.my_warehouse(state.warehouses));
      $root.find(".wms-dm-wh").on("change", (e) => frappe_wms.remember_warehouse(e.target.value));
    });

    return { show(name) { if (name) return load(decodeURIComponent(name)); if (state.rows.length) { showList(); return; } $root.find(".wms-dm-doc").hide(); $root.find(".wms-dm-empty").show(); find(); } };
  }
  // Loads the shared grid / selection scripts (like the Monitor does), mounts the screen once, and shows the delivery named in the route each time the page is shown.
  const v = () => Math.floor(Date.now() / 600000);
  const load = (src) => new Promise((resolve, reject) => {
    const url = `${src}?v=${v()}`;
    if (document.querySelector(`script[data-wms-src^="${src}"]`)) { resolve(); return; }
    const el = document.createElement("script");
    el.src = url; el.dataset.wmsSrc = url; el.onload = () => resolve(); el.onerror = () => reject(new Error(`could not load ${src}`));
    document.head.appendChild(el);
  });
  function boot(wrapper, doctype) {
    wrapper.__dm_ready = load("/assets/frappe_wms/js/wms_grid.js").then(() => load("/assets/frappe_wms/js/wms_selection.js")).then(() => { wrapper.__dm = mount(wrapper, doctype); })
      .catch((e) => frappe.msgprint({ title: __("Delivery"), indicator: "red", message: esc(e.message) }));
  }
  async function shown(wrapper) {
    await wrapper.__dm_ready;
    const name = frappe.get_route()[1];
    if (wrapper.__dm) wrapper.__dm.show(name);
  }
  return { mount, boot, shown };
})();
