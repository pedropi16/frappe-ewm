frappe.pages["wms-monitor"].on_page_load = function (wrapper) {
  const page = frappe.ui.make_app_page({
    parent: wrapper,
    title: __("WMS Monitor"),
    single_column: true,
  });
  new WMSMonitor(page);
};

// SAP EWM-style Warehouse Management Monitor: one warehouse selector, a node tree of
// independent views down the left (Overview / Inbound / Outbound / Stock Overview /
// Warehouse Tasks / Handling Units / Stock Movements / Resources & Queues) instead of one
// long page you scroll through. Each view loads its own data only when selected.
const VIEWS = [
  { key: "overview", label: __("Overview") },
  { key: "inbound", label: __("Inbound Monitor") },
  { key: "outbound", label: __("Outbound Monitor") },
  { key: "stock", label: __("Stock Overview") },
  { key: "tasks", label: __("Warehouse Tasks") },
  { key: "hu", label: __("Handling Units") },
  { key: "movements", label: __("Stock Movements") },
  { key: "resources", label: __("Resources & Queues") },
  { key: "differences", label: __("Difference Analyzer") },
  { key: "kpis", label: __("KPIs") },
  { key: "slotting", label: __("Slotting") },
  { key: "bin_assignment", label: __("Bin Assignment") },
  { key: "kitting", label: __("Kitting") },
  { key: "billing", label: __("Billing") },
  { key: "alerts", label: __("Alerts") },
];

let _grid_styles_injected = false;
function ensure_grid_styles() {
  if (_grid_styles_injected) return;
  _grid_styles_injected = true;
  $("<style>", { text: `
    .wms-grid-toolbar { display:flex; align-items:center; gap:8px; margin-bottom:6px; }
    .wms-grid-toolbar .wms-grid-hint { font-size:12px; }
    .wms-grid-scroll { overflow:auto; max-height:65vh; border:1px solid var(--border-color); }
    .wms-grid-table { margin-bottom:0; user-select:none; }
    .wms-grid-table th, .wms-grid-table td { white-space:nowrap; }
    .wms-grid-corner, .wms-grid-colhead, .wms-grid-rowhead { cursor:pointer; background:var(--control-bg,#f5f5f5); }
    .wms-grid-colhead:hover, .wms-grid-rowhead:hover { background:var(--bg-color,#e9ecef); }
    .wms-grid-cell { cursor:cell; }
    .wms-grid-selected { background:rgba(59,130,246,.18) !important; }
    .wms-grid-anchor { outline:1px solid rgba(59,130,246,.7); outline-offset:-1px; }
  ` }).appendTo("head");
}

// Excel-like grid: click a cell to select it, shift+click or drag to extend a rectangular
// range, click a column/row header (or the corner) to select a whole column/row/everything.
// Ctrl/Cmd+C or the Copy button copies the selection as tab-separated text - pastes straight
// into a spreadsheet. Row/reference links still navigate normally; only the surrounding cell
// area starts a drag-select. A quick-filter box narrows visible rows client-side (substring
// match across every column), independent of whatever server-side filters a view also has.
class DataGrid {
  constructor(rows, columns, doctype) {
    this.rows = rows || [];
    this.columns = columns || [];
    this.doctype = doctype;
    this.filterText = "";
    this.sel = null; // {r0,r1,c0,c1} - r0 includes the header row (0); c0 excludes the gutter (starts at 1)
    this.anchor = null;
    this.$el = $(`<div class="wms-grid"></div>`);
    ensure_grid_styles();
    this._build();
  }

  _visibleRows() {
    if (!this.filterText) return this.rows;
    const needle = this.filterText.toLowerCase();
    return this.rows.filter((row) => this.columns.some(([f]) => String(row[f] ?? "").toLowerCase().includes(needle)));
  }

  _build() {
    const $toolbar = $(`
      <div class="wms-grid-toolbar">
        <input type="text" class="form-control input-sm wms-grid-filter" style="width:220px;" placeholder="${__("Filter visible rows...")}">
        <button type="button" class="btn btn-default btn-xs wms-grid-copy">${__("Copy")}</button>
        <span class="text-muted wms-grid-hint"></span>
      </div>
    `);
    this.$scroll = $(`<div class="wms-grid-scroll"><table class="table table-bordered table-sm wms-grid-table"></table></div>`);
    this.$el.empty().append($toolbar, this.$scroll);
    this.$table = this.$scroll.find("table");
    this.$el.attr("tabindex", 0).css("outline", "none");
    $toolbar.find(".wms-grid-filter").on("input", (e) => { this.filterText = e.target.value; this.sel = null; this._render(); });
    $toolbar.find(".wms-grid-copy").on("click", () => this._copy());
    this._bindSelection();
    this.$el.on("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && (e.key === "c" || e.key === "C")) { e.preventDefault(); this._copy(); }
    });
    this._render();
  }

  _render() {
    const rows = this._visibleRows();
    this._visRows = rows;
    const maxR = rows.length, maxC = this.columns.length;
    const head = [`<th class="wms-grid-corner" data-r="0" data-c="0"></th>`].concat(
      this.columns.map(([, label], ci) => `<th class="wms-grid-colhead" data-r="0" data-c="${ci + 1}">${frappe.utils.escape_html(label)}</th>`)
    ).join("");
    const body = rows.map((row, ri) => {
      const cells = [`<th class="wms-grid-rowhead" data-r="${ri + 1}" data-c="0">${ri + 1}</th>`].concat(
        this.columns.map(([field, , renderFn], ci) => {
          const raw = row[field];
          let inner;
          if (renderFn) {
            inner = renderFn(row, ri);
          } else if ((field === "name" || field === "reference_name") && raw) {
            const target_doctype = field === "reference_name" ? row.reference_doctype : this.doctype;
            inner = target_doctype
              ? `<a href="/app/${frappe.router.slug(target_doctype)}/${encodeURIComponent(raw)}">${frappe.utils.escape_html(String(raw))}</a>`
              : frappe.utils.escape_html(String(raw));
          } else {
            inner = raw === null || raw === undefined ? "" : frappe.utils.escape_html(String(raw));
          }
          return `<td class="wms-grid-cell" data-r="${ri + 1}" data-c="${ci + 1}">${inner}</td>`;
        })
      ).join("");
      return `<tr>${cells}</tr>`;
    }).join("");
    this.$table.html(`<thead><tr>${head}</tr></thead><tbody>${body}</tbody>`);
    this._maxR = maxR; this._maxC = maxC;
    this.$el.find(".wms-grid-hint").text(rows.length === this.rows.length ? __("{0} row(s)", [rows.length]) : __("{0} of {1} row(s)", [rows.length, this.rows.length]));
    this._applyHighlight();
  }

  _cellsInRange(r0, r1, c0, c1) {
    const lo_r = Math.min(r0, r1), hi_r = Math.max(r0, r1);
    const lo_c = Math.max(1, Math.min(c0, c1)), hi_c = Math.max(c0, c1); // never select the gutter (col 0)
    const out = [];
    for (let r = Math.max(0, lo_r); r <= hi_r; r++) for (let c = lo_c; c <= hi_c; c++) out.push([r, c]);
    return out;
  }

  _applyHighlight() {
    this.$table.find(".wms-grid-selected, .wms-grid-anchor").removeClass("wms-grid-selected wms-grid-anchor");
    if (!this.sel) return;
    for (const [r, c] of this._cellsInRange(this.sel.r0, this.sel.r1, this.sel.c0, this.sel.c1)) {
      this.$table.find(`[data-r="${r}"][data-c="${c}"]`).addClass("wms-grid-selected");
    }
    if (this.anchor) this.$table.find(`[data-r="${this.anchor.r}"][data-c="${this.anchor.c}"]`).addClass("wms-grid-anchor");
  }

  _bindSelection() {
    let dragging = false;
    this.$table.on("mousedown", "th, td", (e) => {
      if ($(e.target).is("a, button, input, select, textarea, label")) return; // let interactive controls work normally, don't hijack them into a selection
      const $cell = $(e.currentTarget);
      const r = Number($cell.data("r")), c = Number($cell.data("c"));
      this.$el.trigger("focus");
      if (c === 0 && r === 0) { this.sel = { r0: 0, r1: this._maxR, c0: 1, c1: this._maxC }; this.anchor = { r: 0, c: 1 }; }
      else if (c === 0) { this.sel = { r0: r, r1: r, c0: 1, c1: this._maxC }; this.anchor = { r, c: 1 }; }
      else if (r === 0) { this.sel = { r0: 0, r1: this._maxR, c0: c, c1: c }; this.anchor = { r: 0, c }; }
      else if (e.shiftKey && this.anchor) { this.sel = { r0: this.anchor.r, r1: r, c0: this.anchor.c, c1: c }; }
      else { this.sel = { r0: r, r1: r, c0: c, c1: c }; this.anchor = { r, c }; dragging = true; }
      this._applyHighlight();
      e.preventDefault();
    });
    this.$table.on("mouseenter", "td.wms-grid-cell", (e) => {
      if (!dragging || !this.anchor) return;
      const $cell = $(e.currentTarget);
      this.sel = { r0: this.anchor.r, r1: Number($cell.data("r")), c0: this.anchor.c, c1: Number($cell.data("c")) };
      this._applyHighlight();
    });
    $(document).on("mouseup", () => { dragging = false; });
  }

  _copy() {
    if (!this.sel) { frappe.show_alert({ message: __("Select a cell, row, or column first"), indicator: "orange" }); return; }
    const cells = this._cellsInRange(this.sel.r0, this.sel.r1, this.sel.c0, this.sel.c1);
    const byRow = {};
    for (const [r, c] of cells) (byRow[r] || (byRow[r] = [])).push(c);
    const lines = Object.keys(byRow).map(Number).sort((a, b) => a - b).map((r) => {
      const cs = byRow[r].sort((a, b) => a - b);
      return cs.map((c) => {
        if (r === 0) return this.columns[c - 1][1];
        const row = this._visRows[r - 1];
        const value = row[this.columns[c - 1][0]];
        return value === null || value === undefined ? "" : String(value);
      }).join("\t");
    });
    const text = lines.join("\n");
    const done = () => frappe.show_alert({ message: __("Copied {0} cell(s)", [cells.length]), indicator: "green" });
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(() => this._copy_fallback(text, done));
    } else {
      this._copy_fallback(text, done);
    }
  }

  _copy_fallback(text, done) {
    const $ta = $("<textarea>").val(text).css({ position: "fixed", opacity: 0 }).appendTo("body");
    $ta[0].select();
    try { document.execCommand("copy"); done(); }
    catch (e) { frappe.show_alert({ message: __("Could not copy - your browser blocked it"), indicator: "red" }); }
    $ta.remove();
  }
}

// Builds a <select class="...cls..."> with a blank "any" option - used for filter fields backed
// by a fixed Select fieldtype, so a filter can't silently return nothing from a typo or a
// mismatched case (e.g. typing "picked" instead of "Picked").
function select_html(cls, options, placeholder) {
  const opts = [`<option value="">${frappe.utils.escape_html(placeholder || __("Any"))}</option>`]
    .concat(options.map((o) => `<option value="${frappe.utils.escape_html(o)}">${frappe.utils.escape_html(o)}</option>`));
  return `<select class="form-control input-sm ${cls}" style="width:150px;">${opts.join("")}</select>`;
}

const TASK_TYPES = ["Unload", "Putaway", "Pick", "Internal Move", "Deconsolidation", "Consolidation", "Stage", "Load", "Posting Change", "Inventory Count", "Cross Dock"];
const TASK_STATUSES = ["Open", "On Hold", "Available", "Assigned", "In Process", "Partially Confirmed", "Confirmed", "Cancelled", "Exception"];
const PRIORITIES = ["Low", "Normal", "High", "Urgent"];
const INBOUND_STATUSES = ["Draft", "Expected", "Arrived", "Receiving", "Partially Received", "Received", "Putaway In Process", "Completed", "Cancelled"];
const OUTBOUND_STATUSES = ["Draft", "Open", "Allocated", "Picking", "Picked", "Packing", "Packed", "Staging", "Staged", "Loading", "Loaded", "Goods Issued", "Completed", "Cancelled"];
const HU_STATUSES = ["Created", "Open", "Closed", "In Process", "Staged", "Loaded", "Shipped", "Empty", "Blocked", "Cancelled"];
const HU_STOCK_STATUSES = ["Empty", "Partial", "Full"];
const WAVE_STATUSES = ["Draft", "Released", "Picking", "Picked", "Completed", "Cancelled"];

class WMSMonitor {
  constructor(page) {
    this.page = page;
    this.warehouse = null;
    this.view = "overview";

    this.$body = $(`
      <div class="wms-monitor">
        <div class="wms-monitor-filters form-inline" style="margin-bottom:16px;"></div>
        <div class="wms-monitor-shell" style="display:flex; gap:20px; align-items:flex-start;">
          <div class="wms-monitor-nav" style="flex:0 0 190px;">
            <div class="list-group wms-mon-nav-list"></div>
          </div>
          <div class="wms-monitor-content" style="flex:1; min-width:0;">
            ${VIEWS.map((v) => `
              <div class="wms-mon-view" data-view="${v.key}" style="display:none;">
                <h4>${frappe.utils.escape_html(v.label)}</h4>
                <div class="wms-mon-view-body" data-view-body="${v.key}"></div>
              </div>
            `).join("")}
          </div>
        </div>
      </div>
    `).appendTo(this.page.main);

    this.render_nav();
    this.render_warehouse_filter();
  }

  render_nav() {
    const $nav = this.$body.find(".wms-mon-nav-list");
    $nav.html(VIEWS.map((v) => `
      <a href="#" class="wms-mon-nav-item" data-view="${v.key}">${frappe.utils.escape_html(v.label)}</a>
    `).join(""));
    $nav.find(".wms-mon-nav-item").on("click", (e) => {
      e.preventDefault();
      this.show_view($(e.currentTarget).data("view"));
    });
    this.highlight_nav();
  }

  highlight_nav() {
    this.$body.find(".wms-mon-nav-item").removeClass("active").each((_, el) => {
      if ($(el).data("view") === this.view) $(el).addClass("active");
    });
  }

  show_view(view) {
    this.view = view;
    this.highlight_nav();
    this.$body.find(".wms-mon-view").hide();
    this.$body.find(`.wms-mon-view[data-view="${view}"]`).show();
    if (this.warehouse) this.load_view(view);
  }

  async render_warehouse_filter() {
    const warehouses = await frappe.db.get_list("WMS Warehouse", { fields: ["name"], limit: 200 });
    const $filters = this.$body.find(".wms-monitor-filters");
    const options = warehouses.map((w) => `<option value="${frappe.utils.escape_html(w.name)}">${frappe.utils.escape_html(w.name)}</option>`).join("");
    $filters.html(`
      <div>
        <label>${__("Warehouse")}</label><br>
        <select class="form-control wms-mon-warehouse"><option value="">${__("Select a warehouse")}</option>${options}</select>
      </div>
    `);
    $filters.find(".wms-mon-warehouse").on("change", (e) => {
      this.warehouse = e.target.value || null;
      if (this.warehouse) this.load_view(this.view);
    });
    this.show_view(this.view);
    if (warehouses.length === 1) {
      $filters.find(".wms-mon-warehouse").val(warehouses[0].name).trigger("change");
    }
  }

  load_view(view) {
    const loaders = {
      overview: () => this.load_overview(),
      inbound: () => this.load_inbound(),
      outbound: () => this.load_outbound(),
      stock: () => this.load_stock_overview(),
      tasks: () => this.load_tasks(),
      hu: () => this.load_handling_units(),
      movements: () => this.load_movements(),
      resources: () => this.load_resources(),
      differences: () => this.load_differences(),
      kpis: () => this.load_kpis(),
      slotting: () => this.load_slotting(),
      bin_assignment: () => this.load_bin_assignment(),
      kitting: () => this.load_kitting(),
      billing: () => this.load_billing(),
      alerts: () => this.load_alerts(),
    };
    (loaders[view] || (() => {}))();
  }

  body_for(view) { return this.$body.find(`.wms-mon-view-body[data-view-body="${view}"]`); }

  render_cards($container, cards) {
    $container.empty();
    cards.forEach((card) => {
      const $card = $(`
        <div class="wms-mon-card" style="border:1px solid var(--border-color);border-radius:8px;padding:10px 16px;min-width:140px;cursor:pointer;display:inline-block;margin:0 12px 12px 0;">
          <div style="font-size:22px;font-weight:700;">${card.value}</div>
          <div class="text-muted" style="font-size:12px;">${frappe.utils.escape_html(card.label)}</div>
        </div>
      `).appendTo($container);
      if (card.route) $card.on("click", () => frappe.set_route(...card.route));
    });
  }

  // ---------- Overview ----------
  async load_overview() {
    const summary = await frappe.call("frappe_wms.api.monitor.get_summary", { warehouse: this.warehouse }).then((r) => r.message);
    const $wrap = this.body_for("overview");
    $wrap.html(`<div class="wms-mon-summary"></div>`);
    const cards = [
      { label: __("Exceptions"), value: summary.exceptions, route: ["List", "Warehouse Task", { warehouse: this.warehouse, status: "Exception" }] },
      { label: __("Pending Replenishment"), value: summary.pending_replenishment, route: ["List", "Warehouse Request", { warehouse: this.warehouse, request_type: "Replenish" }] },
      { label: __("Inbound In Progress"), value: summary.inbound_in_progress, route: ["List", "Inbound Delivery", { warehouse: this.warehouse }] },
      { label: __("Outbound In Progress"), value: summary.outbound_in_progress, route: ["List", "Outbound Delivery", { warehouse: this.warehouse }] },
      { label: __("Open Counts"), value: summary.open_counts, route: ["List", "WMS Physical Inventory Count", { warehouse: this.warehouse }] },
      { label: __("Open Inspections"), value: summary.open_inspections, route: ["List", "WMS Quality Inspection", { warehouse: this.warehouse }] },
      { label: __("Open Waves"), value: summary.open_waves, route: ["List", "WMS Wave", { warehouse: this.warehouse }] },
      { label: __("Active Resources"), value: summary.active_resources, route: ["List", "WMS Resource", { warehouse: this.warehouse }] },
    ];
    (summary.open_tasks_by_type || []).forEach((row) => {
      cards.unshift({ label: __("Open {0}", [row.task_type]), value: row.count, route: ["List", "Warehouse Task", { warehouse: this.warehouse, task_type: row.task_type }] });
    });
    this.render_cards($wrap.find(".wms-mon-summary"), cards);
  }

  // ---------- Inbound Monitor ----------
  async load_inbound() {
    const $wrap = this.body_for("inbound");
    if (!$wrap.find(".wms-mon-ind-filters").length) {
      $wrap.html(`
        <div class="wms-mon-ind-filters form-inline" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;">
          ${select_html("wms-mon-ind-status", INBOUND_STATUSES, __("Status"))}
          <input class="form-control input-sm wms-mon-ind-supplier" placeholder="${__("Supplier")}" style="width:160px;">
          ${select_html("wms-mon-ind-receipt-status", ["Not Received", "Partially Received", "Fully Received"], __("Receipt Status"))}
          ${select_html("wms-mon-ind-process-status", ["Not Started", "In Process", "Completed", "Exception"], __("Process Status"))}
          <button class="btn btn-primary btn-sm wms-mon-ind-search">${__("Search")}</button>
        </div>
        <div class="wms-mon-ind-table"></div>
      `);
      $wrap.find(".wms-mon-ind-status, .wms-mon-ind-receipt-status, .wms-mon-ind-process-status").on("change", () => this.search_inbound_deliveries());
      $wrap.find(".wms-mon-ind-search").on("click", () => this.search_inbound_deliveries());
    }
    this.search_inbound_deliveries();
  }

  async search_inbound_deliveries() {
    if (!this.warehouse) return;
    const $wrap = this.body_for("inbound");
    const args = {
      warehouse: this.warehouse,
      status: $wrap.find(".wms-mon-ind-status").val() || undefined,
      supplier: $wrap.find(".wms-mon-ind-supplier").val() || undefined,
      receipt_status: $wrap.find(".wms-mon-ind-receipt-status").val() || undefined,
      process_status: $wrap.find(".wms-mon-ind-process-status").val() || undefined,
    };
    const rows = await frappe.call("frappe_wms.api.monitor.search_inbound_deliveries", args).then((r) => r.message || []);
    const $table = $wrap.find(".wms-mon-ind-table");
    if (!rows.length) { $table.html(`<div class="text-muted">${__("No inbound deliveries found")}</div>`); return; }
    $table.empty().append(this.render_table(rows, [
      ["name", __("Delivery")], ["inbound_delivery_number", __("Number")], ["warehouse", __("Warehouse")],
      ["company", __("Company")], ["supplier", __("Supplier")], ["receiving_bin", __("Receiving Bin")],
      ["expected_arrival", __("Expected Arrival")], ["posting_date", __("Posting Date")],
      ["receipt_status", __("Receipt Status")], ["process_status", __("Process Status")], ["status", __("Status")],
      ["external_reference", __("External Ref")], ["modified", __("Last Modified")],
    ], "Inbound Delivery"));
  }

  // ---------- Outbound Monitor ----------
  async load_outbound() {
    const $wrap = this.body_for("outbound");
    if (!$wrap.find(".wms-mon-obd-filters").length) {
      $wrap.html(`
        <div class="wms-mon-obd-filters form-inline" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;">
          ${select_html("wms-mon-obd-status", OUTBOUND_STATUSES, __("Status"))}
          <input class="form-control input-sm wms-mon-obd-customer" placeholder="${__("Customer")}" style="width:160px;">
          ${select_html("wms-mon-obd-priority", PRIORITIES, __("Priority"))}
          ${select_html("wms-mon-obd-allocation", ["Not Allocated", "Partially Allocated", "Fully Allocated"], __("Allocation"))}
          <button class="btn btn-primary btn-sm wms-mon-obd-search">${__("Search")}</button>
        </div>
        <div class="wms-mon-obd-table" style="margin-bottom:12px;"></div>
        <div class="wms-mon-obd-detail" style="margin-bottom:24px;"></div>
        <h5>${__("Waves")}</h5>
        <div class="wms-mon-wave-filters form-inline" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;">
          ${select_html("wms-mon-wave-status", WAVE_STATUSES, __("Status"))}
          <button class="btn btn-primary btn-sm wms-mon-wave-search">${__("Search")}</button>
        </div>
        <div class="wms-mon-wave-table"></div>
      `);
      $wrap.find(".wms-mon-obd-status, .wms-mon-obd-priority, .wms-mon-obd-allocation").on("change", () => this.search_outbound_deliveries());
      $wrap.find(".wms-mon-obd-search").on("click", () => this.search_outbound_deliveries());
      $wrap.find(".wms-mon-wave-status").on("change", () => this.search_waves());
      $wrap.find(".wms-mon-wave-search").on("click", () => this.search_waves());
    }
    this.search_outbound_deliveries();
    this.search_waves();
  }

  async search_outbound_deliveries() {
    if (!this.warehouse) return;
    const $wrap = this.body_for("outbound");
    const args = {
      warehouse: this.warehouse,
      status: $wrap.find(".wms-mon-obd-status").val() || undefined,
      customer: $wrap.find(".wms-mon-obd-customer").val() || undefined,
      priority: $wrap.find(".wms-mon-obd-priority").val() || undefined,
      allocation_status: $wrap.find(".wms-mon-obd-allocation").val() || undefined,
    };
    const rows = await frappe.call("frappe_wms.api.monitor.search_outbound_deliveries", args).then((r) => r.message || []);
    const $table = $wrap.find(".wms-mon-obd-table");
    if (!rows.length) { $table.html(`<div class="text-muted">${__("No outbound deliveries found")}</div>`); return; }
    $table.empty().append(this.render_table(rows, [
      ["name", __("Delivery")],
      ["view", __(""), (row) => `<button type="button" class="btn btn-xs btn-default wms-mon-obd-view" data-delivery="${frappe.utils.escape_html(row.name)}">${__("View")}</button>`],
      ["outbound_delivery_number", __("Number")], ["warehouse", __("Warehouse")], ["customer", __("Customer")],
      ["route", __("Route")], ["delivery_date", __("Delivery Date")], ["priority", __("Priority")],
      ["staging_bin", __("Staging Bin")], ["door", __("Door")], ["allocation_status", __("Allocation")],
      ["picking_status", __("Picking")], ["packing_status", __("Packing")], ["loading_status", __("Loading")],
      ["goods_issue_status", __("Goods Issue")], ["status", __("Status")], ["external_reference", __("External Ref")],
      ["modified", __("Last Modified")],
    ], "Outbound Delivery"));
    $table.find(".wms-mon-obd-view").on("click", (e) => this.load_delivery_detail(e.currentTarget.dataset.delivery));
  }

  async load_delivery_detail(delivery_name) {
    const $detail = this.body_for("outbound").find(".wms-mon-obd-detail");
    $detail.html(`<div class="text-muted">${__("Loading...")}</div>`);
    const status = await frappe.call("frappe_wms.api.monitor.get_delivery_execution_status", { delivery_name }).then((r) => r.message);
    const d = status.delivery;
    const docstatusLabel = { 0: __("Draft"), 1: __("Submitted"), 2: __("Cancelled") }[d.docstatus];

    const $wrap = $(`<div class="wms-mon-detail-card" style="padding:14px;"></div>`);
    $wrap.append(`
      <h5>${frappe.utils.escape_html(d.name)} <small class="text-muted">(${docstatusLabel})</small></h5>
      <div style="display:flex; flex-wrap:wrap; gap:16px; margin-bottom:10px;">
        <div><b>${__("Allocation")}:</b> ${frappe.utils.escape_html(d.allocation_status || "-")}</div>
        <div><b>${__("Picking")}:</b> ${frappe.utils.escape_html(d.picking_status || "-")}</div>
        <div><b>${__("Packing")}:</b> ${frappe.utils.escape_html(d.packing_status || "-")}</div>
        <div><b>${__("Loading")}:</b> ${frappe.utils.escape_html(d.loading_status || "-")}</div>
        <div><b>${__("Goods Issue")}:</b> ${frappe.utils.escape_html(d.goods_issue_status || "-")}</div>
      </div>
    `);

    const $actions = $(`<div style="margin-bottom:10px;"></div>`);
    if (d.docstatus !== 1) {
      $actions.append(`<span class="text-muted">${__("Submit the document before it can be allocated or picked.")}</span>`);
    } else {
      if (d.allocation_status !== "Fully Allocated") {
        const btn = $(`<button class="btn btn-xs btn-primary">${__("Allocate Stock")}</button>`);
        btn.on("click", () => frappe.call("frappe_wms.api.outbound.allocate_delivery", { delivery_name }).then(() => this.load_delivery_detail(delivery_name)));
        $actions.append(btn);
      } else if (d.picking_status !== "Picked") {
        const btn = $(`<button class="btn btn-xs btn-primary">${__("Create Pick Tasks")}</button>`);
        btn.on("click", () => frappe.call("frappe_wms.api.outbound.create_pick_tasks", { delivery_name }).then(() => this.load_delivery_detail(delivery_name)));
        $actions.append(btn);
      }
      if (d.picking_status === "Picked" && d.goods_issue_status !== "Posted") {
        if (d.loading_status === "Loaded") {
          const btn = $(`<button class="btn btn-xs btn-success" style="margin-left:6px;">${__("Post Goods Issue")}</button>`);
          btn.on("click", () => frappe.call("frappe_wms.api.outbound.post_goods_issue_for_delivery", { delivery_name })
            .then(() => { frappe.show_alert({ message: __("Goods Issue posted"), indicator: "green" }); this.load_delivery_detail(delivery_name); this.search_outbound_deliveries(); })
            .catch(() => {}));
          $actions.append(btn);
        } else {
          $actions.append(`<span class="text-muted" style="margin-left:6px;">${__("Load the Handling Unit onto a Shipment before Goods Issue can be posted.")}</span>`);
        }
      }
    }
    $wrap.append($actions);

    $wrap.append(`<h6>${__("Stock Allocations")}</h6>`);
    if (!status.allocations.length) {
      $wrap.append(`<div class="text-muted">${__("None yet")}</div>`);
    } else {
      $wrap.append(this.render_table(status.allocations, [
        ["name", __("Allocation")], ["product", __("Product")], ["storage_bin", __("Bin")], ["handling_unit", __("HU")],
        ["batch_no", __("Batch")], ["serial_no", __("Serial")], ["allocated_quantity", __("Allocated")],
        ["picked_quantity", __("Picked")], ["status", __("Status")],
      ], "Stock Allocation"));
    }

    $wrap.append(`<h6 style="margin-top:10px;">${__("Pick Tasks")}</h6>`);
    if (!status.tasks.length) {
      $wrap.append(`<div class="text-muted">${__("None yet")}</div>`);
    } else {
      $wrap.append(this.render_table(status.tasks, [
        ["name", __("Task")], ["task_type", __("Type")], ["status", __("Status")], ["product", __("Product")],
        ["planned_quantity", __("Planned")], ["confirmed_quantity", __("Confirmed")], ["stock_uom", __("UOM")],
        ["batch_no", __("Batch")], ["serial_no", __("Serial")],
        ["source_bin", __("Source")], ["destination_bin", __("Destination")],
        ["source_hu", __("Source HU")], ["destination_hu", __("Destination HU")],
        ["priority", __("Priority")], ["assigned_resource", __("Resource")], ["sequence", __("Sequence")],
      ], "Warehouse Task"));
    }
    if (status.warehouse_orders.length) {
      $wrap.append(`<div><b>${__("Warehouse Orders")}:</b> ${status.warehouse_orders.map((wo) =>
        `<a href="/app/warehouse-order/${encodeURIComponent(wo)}">${frappe.utils.escape_html(wo)}</a>`).join(", ")}</div>`);
    }

    $wrap.append(`<h6 style="margin-top:10px;">${__("Packing Orders")}</h6>`);
    if (!status.packing_orders.length) {
      $wrap.append(`<div class="text-muted">${__("None")}</div>`);
    } else {
      $wrap.append(this.render_table(status.packing_orders, [
        ["name", __("Packing Order")], ["status", __("Status")], ["work_center_bin", __("Work Center Bin")],
      ], "Packing Order"));
    }

    $wrap.append(`<h6 style="margin-top:10px;">${__("Goods Issues")}</h6>`);
    if (!status.goods_issues.length) {
      $wrap.append(`<div class="text-muted">${__("None yet")}</div>`);
    } else {
      $wrap.append(this.render_table(status.goods_issues, [
        ["name", __("Goods Issue")], ["status", __("Status")], ["posting_datetime", __("Posted")], ["reversed", __("Reversed")],
      ], "Goods Issue"));
    }

    $detail.empty().append($wrap);
  }

  async search_waves() {
    if (!this.warehouse) return;
    const $wrap = this.body_for("outbound");
    const args = { warehouse: this.warehouse, status: $wrap.find(".wms-mon-wave-status").val() || undefined };
    const rows = await frappe.call("frappe_wms.api.monitor.search_waves", args).then((r) => r.message || []);
    const $table = $wrap.find(".wms-mon-wave-table");
    if (!rows.length) { $table.html(`<div class="text-muted">${__("No waves found")}</div>`); return; }
    $table.empty().append(this.render_table(rows, [
      ["name", __("Wave")], ["route", __("Route")], ["ship_date", __("Ship Date")], ["priority", __("Priority")],
      ["picking_strategy", __("Strategy")], ["status", __("Status")], ["delivery_count", __("Deliveries")],
      ["released_at", __("Released At")], ["released_by", __("Released By")], ["modified", __("Last Modified")],
      ["release", __(""), (row) => row.status === "Draft"
        ? `<button type="button" class="btn btn-xs btn-primary wms-mon-release-wave" data-wave="${frappe.utils.escape_html(row.name)}">${__("Release")}</button>` : ""],
    ], "WMS Wave"));
    $table.find(".wms-mon-release-wave").on("click", (e) => {
      const wave = e.currentTarget.dataset.wave;
      frappe.confirm(__("Release wave {0}? This allocates and creates pick tasks for every delivery in it.", [wave]), () => {
        frappe.call("frappe_wms.api.outbound.release_wave", { wave_name: wave }).then(() => {
          frappe.show_alert({ message: __("Wave released"), indicator: "green" });
          this.search_waves();
        });
      });
    });
  }

  // ---------- Stock Overview ----------
  async load_stock_overview() {
    const $wrap = this.body_for("stock");
    if (!$wrap.find(".wms-mon-stock-filters").length) {
      $wrap.html(`
        <div class="wms-mon-stock-summary" style="margin-bottom:16px;"></div>
        <div class="wms-mon-stock-filters form-inline" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;">
          <input class="form-control input-sm wms-mon-stock-product" placeholder="${__("Product")}" style="width:140px;">
          <input class="form-control input-sm wms-mon-stock-bin" placeholder="${__("Storage Bin")}" style="width:140px;">
          <input class="form-control input-sm wms-mon-stock-storage-type" placeholder="${__("Storage Type")}" style="width:140px;">
          <input class="form-control input-sm wms-mon-stock-stock-type" placeholder="${__("Stock Type")}" style="width:120px;">
          <input class="form-control input-sm wms-mon-stock-hu" placeholder="${__("Handling Unit")}" style="width:140px;">
          <button class="btn btn-primary btn-sm wms-mon-stock-search">${__("Search")}</button>
        </div>
        <div class="wms-mon-stock-table"></div>
      `);
      $wrap.find(".wms-mon-stock-search").on("click", () => this.search_stock_overview());
    }
    const summary = await frappe.call("frappe_wms.api.monitor.stock_overview_summary", { warehouse: this.warehouse }).then((r) => r.message || []);
    this.render_cards($wrap.find(".wms-mon-stock-summary"), summary.map((row) => ({
      label: __("{0} ({1} rows)", [row.stock_type || __("(no stock type)"), row.balance_rows]),
      value: `${flt(row.quantity)} / ${flt(row.available_quantity)} ${__("avail")}`,
    })));
    this.search_stock_overview();
  }

  async search_stock_overview() {
    if (!this.warehouse) return;
    const $wrap = this.body_for("stock");
    const args = {
      warehouse: this.warehouse,
      product: $wrap.find(".wms-mon-stock-product").val() || undefined,
      storage_bin: $wrap.find(".wms-mon-stock-bin").val() || undefined,
      storage_type: $wrap.find(".wms-mon-stock-storage-type").val() || undefined,
      stock_type: $wrap.find(".wms-mon-stock-stock-type").val() || undefined,
      handling_unit: $wrap.find(".wms-mon-stock-hu").val() || undefined,
    };
    const rows = await frappe.call("frappe_wms.api.monitor.stock_overview", args).then((r) => r.message || []);
    const $table = $wrap.find(".wms-mon-stock-table");
    if (!rows.length) { $table.html(`<div class="text-muted">${__("No stock found")}</div>`); return; }
    $table.empty().append(this.render_table(rows, [
      ["product", __("Product")], ["storage_bin", __("Bin")], ["handling_unit", __("HU")],
      ["batch_no", __("Batch")], ["serial_no", __("Serial")], ["stock_type", __("Stock Type")],
      ["quantity", __("Quantity")], ["allocated_quantity", __("Allocated")],
      ["available_quantity", __("Available")], ["stock_uom", __("UOM")], ["last_movement_date", __("Last Movement")],
    ]));
  }

  // ---------- Warehouse Tasks ----------
  async load_tasks() {
    const $wrap = this.body_for("tasks");
    if (!$wrap.find(".wms-mon-task-filters").length) {
      $wrap.html(`
        <div class="wms-mon-task-filters form-inline" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;">
          <input class="form-control input-sm wms-mon-task-product" placeholder="${__("Product")}" style="width:140px;">
          ${select_html("wms-mon-task-type", TASK_TYPES, __("Task Type"))}
          ${select_html("wms-mon-task-status", TASK_STATUSES, __("Status"))}
          ${select_html("wms-mon-task-priority", PRIORITIES, __("Priority"))}
          <input class="form-control input-sm wms-mon-task-resource" placeholder="${__("Resource")}" style="width:140px;">
          <input class="form-control input-sm wms-mon-task-batch" placeholder="${__("Batch No")}" style="width:120px;">
          <input class="form-control input-sm wms-mon-task-wave" placeholder="${__("Wave")}" style="width:120px;">
          <button class="btn btn-primary btn-sm wms-mon-task-search">${__("Search")}</button>
        </div>
        <div class="wms-mon-task-table"></div>
      `);
      $wrap.find(".wms-mon-task-type, .wms-mon-task-status, .wms-mon-task-priority").on("change", () => this.search_tasks());
      $wrap.find(".wms-mon-task-search").on("click", () => this.search_tasks());
    }
    this.search_tasks();
  }

  async search_tasks() {
    if (!this.warehouse) return;
    const $wrap = this.body_for("tasks");
    const args = {
      warehouse: this.warehouse,
      product: $wrap.find(".wms-mon-task-product").val() || undefined,
      task_type: $wrap.find(".wms-mon-task-type").val() || undefined,
      status: $wrap.find(".wms-mon-task-status").val() || undefined,
      priority: $wrap.find(".wms-mon-task-priority").val() || undefined,
      assigned_resource: $wrap.find(".wms-mon-task-resource").val() || undefined,
      batch_no: $wrap.find(".wms-mon-task-batch").val() || undefined,
      wave: $wrap.find(".wms-mon-task-wave").val() || undefined,
    };
    const rows = await frappe.call("frappe_wms.api.monitor.search_tasks", args).then((r) => r.message || []);
    const $table = $wrap.find(".wms-mon-task-table");
    if (!rows.length) { $table.html(`<div class="text-muted">${__("No tasks found")}</div>`); return; }
    $table.empty().append(this.render_table(rows, [
      ["name", __("Task")], ["task_type", __("Type")], ["status", __("Status")], ["product", __("Product")],
      ["planned_quantity", __("Planned")], ["confirmed_quantity", __("Confirmed")], ["stock_uom", __("UOM")],
      ["batch_no", __("Batch")], ["serial_no", __("Serial")],
      ["source_bin", __("Source Bin")], ["destination_bin", __("Destination Bin")],
      ["source_hu", __("Source HU")], ["destination_hu", __("Destination HU")],
      ["stock_type_from", __("Stock Type From")], ["stock_type_to", __("Stock Type To")],
      ["movement_type", __("Movement Type")], ["priority", __("Priority")], ["assigned_resource", __("Resource")],
      ["wave", __("Wave")], ["queue", __("Queue")], ["warehouse_order", __("Warehouse Order")], ["sequence", __("Sequence")],
      ["started_at", __("Started")], ["confirmed_at", __("Confirmed At")], ["confirmed_by", __("Confirmed By")],
      ["exception_code", __("Exception")], ["blocking_reason", __("Blocking Reason")], ["modified", __("Last Modified")],
    ], "Warehouse Task"));
  }

  // ---------- Handling Units ----------
  async load_handling_units() {
    const $wrap = this.body_for("hu");
    if (!$wrap.find(".wms-mon-hu-filters").length) {
      $wrap.html(`
        <div class="wms-mon-hu-filters form-inline" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;">
          <input class="form-control input-sm wms-mon-hu-number" placeholder="${__("HU Number")}" style="width:140px;">
          ${select_html("wms-mon-hu-status", HU_STATUSES, __("Status"))}
          ${select_html("wms-mon-hu-stock-status", HU_STOCK_STATUSES, __("Stock Status"))}
          <input class="form-control input-sm wms-mon-hu-type" placeholder="${__("HU Type")}" style="width:140px;">
          <input class="form-control input-sm wms-mon-hu-bin" placeholder="${__("Current Bin")}" style="width:140px;">
          <input class="form-control input-sm wms-mon-hu-workcenter" placeholder="${__("Work Center")}" style="width:140px;">
          <input class="form-control input-sm wms-mon-hu-obd" placeholder="${__("Outbound Delivery")}" style="width:160px;">
          <button class="btn btn-primary btn-sm wms-mon-hu-search">${__("Search")}</button>
        </div>
        <div class="wms-mon-hu-hint text-muted" style="margin-bottom:6px;font-size:12px;">${__("Click an HU to open its full repack detail: nesting, contents and serials, with copy buttons.")}</div>
        <div class="wms-mon-hu-table"></div>
      `);
      $wrap.find(".wms-mon-hu-status, .wms-mon-hu-stock-status").on("change", () => this.search_handling_units());
      $wrap.find(".wms-mon-hu-search").on("click", () => this.search_handling_units());
    }
    this.search_handling_units();
  }

  async search_handling_units() {
    if (!this.warehouse) return;
    const $wrap = this.body_for("hu");
    const args = {
      warehouse: this.warehouse,
      hu_number: $wrap.find(".wms-mon-hu-number").val() || undefined,
      status: $wrap.find(".wms-mon-hu-status").val() || undefined,
      stock_status: $wrap.find(".wms-mon-hu-stock-status").val() || undefined,
      hu_type: $wrap.find(".wms-mon-hu-type").val() || undefined,
      current_bin: $wrap.find(".wms-mon-hu-bin").val() || undefined,
      work_center: $wrap.find(".wms-mon-hu-workcenter").val() || undefined,
      outbound_delivery: $wrap.find(".wms-mon-hu-obd").val() || undefined,
    };
    const rows = await frappe.call("frappe_wms.api.monitor.search_handling_units", args).then((r) => r.message || []);
    const $table = $wrap.find(".wms-mon-hu-table");
    if (!rows.length) { $table.html(`<div class="text-muted">${__("No handling units found")}</div>`); return; }
    $table.empty().append(this.render_table(rows, [
      ["name", __("HU"), (row) => `<a href="#" class="wms-hu-open" data-hu="${frappe.utils.escape_html(row.name)}">${frappe.utils.escape_html(row.name)}</a>`],
      ["hu_type", __("Type")], ["current_bin", __("Bin")],
      ["parent_hu", __("Parent HU")], ["top_hu", __("Top HU")],
      ["status", __("Status")], ["stock_status", __("Stock Status")], ["outbound_delivery", __("Outbound Delivery")],
      ["shipment", __("Shipment")], ["closed", __("Closed")], ["loaded", __("Loaded")],
      ["gross_weight", __("Gross Weight")], ["net_weight", __("Net Weight")], ["seal_number", __("Seal")],
      ["external_reference", __("External Ref")], ["creation", __("Created")], ["modified", __("Last Modified")],
    ], "Handling Unit"));
    $table.find(".wms-hu-open").on("click", (e) => { e.preventDefault(); this.open_hu_detail($(e.currentTarget).data("hu")); });
  }

  // ---------- Repack Center: full recursive HU detail ----------
  async open_hu_detail(hu_name) {
    const dialog = new frappe.ui.Dialog({
      title: __("Handling Unit {0}", [hu_name]),
      size: "large",
      fields: [{ fieldtype: "HTML", fieldname: "body" }],
    });
    dialog.get_field("body").$wrapper.html(`<div class="text-muted">${__("Loading…")}</div>`);
    dialog.show();
    let node;
    try {
      node = await frappe.call("frappe_wms.api.monitor.handling_unit_tree", { hu_name }).then((r) => r.message);
    } catch (e) {
      dialog.get_field("body").$wrapper.html(`<div class="text-danger">${frappe.utils.escape_html(e.message || String(e))}</div>`);
      return;
    }
    const $body = dialog.get_field("body").$wrapper.empty();
    $body.append(`<div style="margin-bottom:10px;"><a href="/app/handling-unit/${encodeURIComponent(hu_name)}" target="_blank">${__("Open in Desk")}</a></div>`);
    $body.append(this.render_hu_node(node, 0));
  }

  copy_btn(value) {
    return $(`<button type="button" class="btn btn-xs btn-default wms-copy-btn" title="${__("Copy")}" style="padding:0 5px;margin-left:6px;line-height:1.6;">⧉</button>`)
      .on("click", () => frappe.utils.copy_to_clipboard(value, __("Copied {0}", [value])));
  }

  render_hu_node(node, depth) {
    const hu = node.hu;
    const $wrap = $(`<div class="wms-hu-node" style="margin-left:${depth * 18}px;border-left:${depth ? "2px solid var(--border-color)" : "none"};padding-left:${depth ? "12px" : "0"};margin-bottom:14px;"></div>`);
    const $head = $(`<div style="display:flex;align-items:center;flex-wrap:wrap;gap:4px;font-weight:${depth ? "normal" : "bold"};margin-bottom:6px;">
      <span>${frappe.utils.escape_html(hu.name)}</span></div>`);
    $head.append(this.copy_btn(hu.name));
    $head.append(`<span class="text-muted" style="margin-left:10px;">${frappe.utils.escape_html(hu.hu_type || "")} · ${frappe.utils.escape_html(hu.current_bin || "-")} · ${frappe.utils.escape_html(hu.status || "")}${hu.stock_status ? " · " + frappe.utils.escape_html(hu.stock_status) : ""}</span>`);
    $wrap.append($head);
    if (node.stock && node.stock.length) {
      $wrap.append(this.render_table(node.stock, [
        ["product", __("Product")], ["batch_no", __("Batch")], ["serial_no", __("Serial")],
        ["stock_type", __("Stock Type")], ["quantity", __("Qty")], ["stock_uom", __("UOM")],
      ], null));
    } else {
      $wrap.append(`<div class="text-muted" style="font-size:12px;">${__("No stock in this HU")}</div>`);
    }
    (node.children || []).forEach((child) => $wrap.append(this.render_hu_node(child, depth + 1)));
    return $wrap;
  }

  // ---------- Stock Movements ----------
  async load_movements() {
    const $wrap = this.body_for("movements");
    if (!$wrap.find(".wms-mon-ledger-filters").length) {
      $wrap.html(`
        <div class="wms-mon-ledger-filters form-inline" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;">
          <input class="form-control input-sm wms-mon-ledger-product" placeholder="${__("Product")}" style="width:140px;">
          <input class="form-control input-sm wms-mon-ledger-bin" placeholder="${__("Storage Bin")}" style="width:140px;">
          <input class="form-control input-sm wms-mon-ledger-hu" placeholder="${__("Handling Unit")}" style="width:140px;">
          <input class="form-control input-sm wms-mon-ledger-movement" placeholder="${__("Movement Type")}" style="width:120px;">
          <input type="date" class="form-control input-sm wms-mon-ledger-from" style="width:150px;">
          <input type="date" class="form-control input-sm wms-mon-ledger-to" style="width:150px;">
          <button class="btn btn-primary btn-sm wms-mon-ledger-search">${__("Search")}</button>
        </div>
        <div class="wms-mon-ledger-table"></div>
      `);
      $wrap.find(".wms-mon-ledger-search").on("click", () => this.search_ledger());
    }
    this.search_ledger();
  }

  async search_ledger() {
    if (!this.warehouse) return;
    const $wrap = this.body_for("movements");
    const args = {
      warehouse: this.warehouse,
      product: $wrap.find(".wms-mon-ledger-product").val() || undefined,
      storage_bin: $wrap.find(".wms-mon-ledger-bin").val() || undefined,
      handling_unit: $wrap.find(".wms-mon-ledger-hu").val() || undefined,
      movement_type: $wrap.find(".wms-mon-ledger-movement").val() || undefined,
      from_date: $wrap.find(".wms-mon-ledger-from").val() || undefined,
      to_date: $wrap.find(".wms-mon-ledger-to").val() || undefined,
    };
    const rows = await frappe.call("frappe_wms.api.monitor.search_ledger", args).then((r) => r.message || []);
    const $table = $wrap.find(".wms-mon-ledger-table");
    if (!rows.length) { $table.html(`<div class="text-muted">${__("No movements found")}</div>`); return; }
    $table.empty().append(this.render_table(rows, [
      ["name", __("Entry")], ["posting_datetime", __("Posted")], ["movement_type", __("Movement")], ["product", __("Product")],
      ["batch_no", __("Batch")], ["serial_no", __("Serial")], ["quantity", __("Qty")], ["stock_uom", __("UOM")],
      ["storage_bin", __("Bin")], ["handling_unit", __("HU")], ["stock_type", __("Stock Type")],
      ["reference_doctype", __("Reference Type")], ["reference_name", __("Reference")],
      ["warehouse_task", __("Warehouse Task")], ["posting_user", __("Posted By")],
    ], "WMS Stock Ledger Entry"));
  }

  // ---------- Resources & Queues ----------
  async load_resources() {
    const $wrap = this.body_for("resources");
    if (!$wrap.find(".wms-mon-resource-table").length) {
      $wrap.html(`
        <div style="display:flex;gap:24px;flex-wrap:wrap;">
          <div style="flex:1;min-width:320px;">
            <h6>${__("Resource Workload")}</h6>
            <div class="wms-mon-resource-table"></div>
          </div>
          <div style="flex:1;min-width:320px;">
            <h6>${__("Queues")}</h6>
            <div class="wms-mon-queue-table"></div>
          </div>
        </div>
      `);
    }
    this.load_resource_workload();
    this.load_queues();
  }

  async load_resource_workload() {
    if (!this.warehouse) return;
    const rows = await frappe.call("frappe_wms.api.monitor.resource_workload", { warehouse: this.warehouse }).then((r) => r.message || []);
    const $table = this.body_for("resources").find(".wms-mon-resource-table");
    if (!rows.length) { $table.html(`<div class="text-muted">${__("No active resources")}</div>`); return; }
    $table.empty().append(this.render_table(rows, [
      ["name", __("Resource")], ["resource_code", __("Code")], ["resource_type", __("Type")],
      ["resource_group", __("Resource Group")], ["user", __("User")], ["device_id", __("Device ID")],
      ["current_queue", __("Queue")], ["current_work_center", __("Work Center")], ["current_bin", __("Bin")],
      ["logged_in_at", __("Logged In At")], ["open_tasks", __("Open Tasks")],
    ], "WMS Resource"));
  }

  async load_queues() {
    if (!this.warehouse) return;
    const rows = await frappe.call("frappe_wms.api.monitor.search_queues", { warehouse: this.warehouse }).then((r) => r.message || []);
    const $table = this.body_for("resources").find(".wms-mon-queue-table");
    if (!rows.length) { $table.html(`<div class="text-muted">${__("No queues configured")}</div>`); return; }
    $table.empty().append(this.render_table(rows, [
      ["name", __("Queue")], ["queue_code", __("Code")], ["queue_name", __("Name")], ["activity", __("Activity")],
      ["storage_type", __("Storage Type")], ["activity_area", __("Activity Area")],
      ["resource_group", __("Resource Group")], ["sequence_rule", __("Sequence Rule")], ["active", __("Active")],
    ], "Warehouse Queue"));
  }

  // ---------- Difference Analyzer ----------
  async load_differences() {
    const $wrap = this.body_for("differences");
    if (!$wrap.find(".wms-mon-diff-filters").length) {
      $wrap.html(`
        <div class="wms-mon-diff-filters form-inline" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;">
          <input class="form-control input-sm wms-mon-diff-product" placeholder="${__("Product")}" style="width:140px;">
          <input type="date" class="form-control input-sm wms-mon-diff-from" style="width:150px;">
          <input type="date" class="form-control input-sm wms-mon-diff-to" style="width:150px;">
          <button class="btn btn-primary btn-sm wms-mon-diff-search">${__("Search")}</button>
        </div>
        <div class="wms-mon-diff-table"></div>
      `);
      $wrap.find(".wms-mon-diff-search").on("click", () => this.search_differences());
    }
    this.search_differences();
  }

  async search_differences() {
    if (!this.warehouse) return;
    const $wrap = this.body_for("differences");
    const args = {
      warehouse: this.warehouse,
      product: $wrap.find(".wms-mon-diff-product").val() || undefined,
      from_date: $wrap.find(".wms-mon-diff-from").val() || undefined,
      to_date: $wrap.find(".wms-mon-diff-to").val() || undefined,
    };
    const rows = await frappe.call("frappe_wms.api.inventory.analyze_differences", args).then((r) => r.message || []);
    const $table = $wrap.find(".wms-mon-diff-table");
    if (!rows.length) { $table.html(`<div class="text-muted">${__("No posted count variances found")}</div>`); return; }
    $table.empty().append(this.render_table(rows, [
      ["product", __("Product")], ["total_gain", __("Total Gain")], ["total_loss", __("Total Loss")],
      ["net_variance", __("Net Variance")], ["over_tolerance_events", __("Over-Tolerance Events")], ["line_count", __("Lines")],
    ], "WMS Physical Inventory Count"));
  }

  // ---------- KPIs ----------
  async load_kpis() {
    if (!this.warehouse) return;
    const $wrap = this.body_for("kpis");
    const pct = (v) => (v === null || v === undefined ? "-" : `${v}%`);
    const hrs = (v) => (v === null || v === undefined ? "-" : `${v}h`);
    const [kpis, resources] = await Promise.all([
      frappe.call("frappe_wms.api.monitor.warehouse_kpis", { warehouse: this.warehouse }).then((r) => r.message || {}),
      frappe.call("frappe_wms.api.monitor.resource_performance", { warehouse: this.warehouse }).then((r) => r.message || []),
    ]);
    $wrap.html(`
      <div class="wms-mon-kpi-cards"></div>
      <div style="margin-top:24px;">
        <h6>${__("Labor Performance")}</h6>
        <div class="wms-mon-kpi-resources"></div>
      </div>
    `);
    this.render_cards($wrap.find(".wms-mon-kpi-cards"), [
      { label: __("Task Throughput (total)"), value: kpis.task_throughput_total },
      { label: __("Task Throughput (per day)"), value: kpis.task_throughput_per_day ?? "-" },
      { label: __("Avg Task Cycle Time"), value: hrs(kpis.avg_task_cycle_time_hours) },
      { label: __("Avg Warehouse Order Cycle Time"), value: hrs(kpis.avg_wo_cycle_time_hours) },
      { label: __("Exception Rate"), value: pct(kpis.exception_rate_percent) },
      { label: __("Count Accuracy"), value: pct(kpis.count_accuracy_percent) },
    ]);
    const $resources = $wrap.find(".wms-mon-kpi-resources");
    if (!resources.length) { $resources.html(`<div class="text-muted">${__("No confirmed tasks in this period")}</div>`); return; }
    const rows = resources.map((r) => ({
      assigned_resource: r.assigned_resource, task_count: r.task_count,
      avg_task_cycle_time_hours: hrs(r.avg_task_cycle_time_hours), efficiency_percent: pct(r.efficiency_percent),
    }));
    $resources.empty().append(this.render_table(rows, [
      ["assigned_resource", __("Resource")], ["task_count", __("Tasks")],
      ["avg_task_cycle_time_hours", __("Avg Cycle Time")], ["efficiency_percent", __("Efficiency")],
    ], "WMS Resource"));
  }

  // ---------- Slotting ----------
  async load_slotting() {
    const $wrap = this.body_for("slotting");
    if (!$wrap.find(".wms-mon-slot-filters").length) {
      $wrap.html(`
        <div class="wms-mon-slot-filters form-inline" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;">
          <input type="date" class="form-control input-sm wms-mon-slot-from" style="width:150px;">
          <input type="date" class="form-control input-sm wms-mon-slot-to" style="width:150px;">
          <input type="number" min="1" class="form-control input-sm wms-mon-slot-min-picks" placeholder="${__("Min Picks")}" style="width:110px;" value="5">
          <button class="btn btn-primary btn-sm wms-mon-slot-search">${__("Search")}</button>
        </div>
        <div class="wms-mon-slot-table"></div>
      `);
      $wrap.find(".wms-mon-slot-search").on("click", () => this.search_slotting());
    }
    this.search_slotting();
  }

  async search_slotting() {
    if (!this.warehouse) return;
    const $wrap = this.body_for("slotting");
    const args = {
      warehouse: this.warehouse,
      from_date: $wrap.find(".wms-mon-slot-from").val() || undefined,
      to_date: $wrap.find(".wms-mon-slot-to").val() || undefined,
      min_picks: $wrap.find(".wms-mon-slot-min-picks").val() || 5,
    };
    const recommendations = await frappe.call("frappe_wms.api.slotting.analyze_slotting", args).then((r) => r.message || []);
    // A stable id independent of display order/filtering, so the "Generate" button below can
    // map a checked box back to its recommendation even after the grid's quick-filter reorders
    // or hides rows - a recommendation isn't a saved record, so there's no name to key off.
    recommendations.forEach((rec, idx) => { rec.__idx = idx; });
    this._slotting_recommendations = recommendations;
    const $table = $wrap.find(".wms-mon-slot-table");
    if (!recommendations.length) { $table.html(`<div class="text-muted">${__("No rearrangement recommendations")}</div>`); return; }
    $table.empty().append(this.render_table(recommendations, [
      ["check", __(""), (row) => `<input type="checkbox" class="wms-mon-slot-check" value="${row.__idx}">`],
      ["product", __("Product")], ["current_bin", __("Current Bin")], ["current_storage_type", __("Current Storage Type")],
      ["preferred_storage_type", __("Preferred Storage Type")], ["quantity", __("Quantity")], ["recent_picks", __("Recent Picks")],
    ]));
    $table.append(`<button type="button" class="btn btn-primary btn-sm wms-mon-slot-generate" style="margin-top:8px;">${__("Generate Rearrangement Tasks")}</button>`);
    // One mass action for this tab, matching the Alerts tab's "Approve Selected" pattern:
    // the whitelisted call takes the actual recommendation objects (not doctype names -
    // a recommendation isn't a saved record), sliced from the last search's own results.
    $table.find(".wms-mon-slot-generate").on("click", () => {
      const idxs = $table.find(".wms-mon-slot-check:checked").map((_, el) => Number(el.value)).get();
      if (!idxs.length) { frappe.show_alert({ message: __("Select at least one recommendation"), indicator: "orange" }); return; }
      const selected = idxs.map((i) => this._slotting_recommendations[i]);
      frappe.confirm(__("Generate {0} rearrangement task(s)?", [selected.length]), async () => {
        const created = await frappe.call("frappe_wms.api.slotting.generate_rearrangement_tasks",
          { warehouse: this.warehouse, recommendations: JSON.stringify(selected) }).then((r) => r.message || []);
        frappe.show_alert({ message: __("Created {0} task(s)", [created.length]), indicator: "green" });
        this.search_slotting();
      });
    });
  }

  // ---------- Bin Assignment ----------
  // A mass-maintenance screen (SAP EWM's own transactions for this are filter-then-apply,
  // not one-bin-at-a-time desk edits): filter bins down, select, assign one Activity Area to
  // all of them in a single call. The same filter/select/bulk-action shape as Slotting's
  // rearrangement-task generation and Alerts' bulk-approve.
  async load_bin_assignment() {
    const $wrap = this.body_for("bin_assignment");
    if (!$wrap.find(".wms-mon-bin-filters").length) {
      $wrap.html(`
        <div class="wms-mon-bin-filters form-inline" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;">
          <input class="form-control input-sm wms-mon-bin-storage-type" placeholder="${__("Storage Type")}" style="width:150px;">
          <input class="form-control input-sm wms-mon-bin-storage-section" placeholder="${__("Storage Section")}" style="width:150px;">
          <input class="form-control input-sm wms-mon-bin-aisle" placeholder="${__("Aisle")}" style="width:110px;">
          <input class="form-control input-sm wms-mon-bin-rack" placeholder="${__("Rack")}" style="width:110px;">
          <button class="btn btn-primary btn-sm wms-mon-bin-search">${__("Search")}</button>
        </div>
        <div class="wms-mon-bin-table"></div>
      `);
      $wrap.find(".wms-mon-bin-search").on("click", () => this.search_bin_assignment());
    }
    this.search_bin_assignment();
  }

  async search_bin_assignment() {
    if (!this.warehouse) return;
    const $wrap = this.body_for("bin_assignment");
    const args = {
      warehouse: this.warehouse,
      storage_type: $wrap.find(".wms-mon-bin-storage-type").val() || undefined,
      storage_section: $wrap.find(".wms-mon-bin-storage-section").val() || undefined,
      aisle: $wrap.find(".wms-mon-bin-aisle").val() || undefined,
      rack: $wrap.find(".wms-mon-bin-rack").val() || undefined,
    };
    const rows = await frappe.call("frappe_wms.api.bin_assignment.search_bins_for_assignment", args).then((r) => r.message || []);
    const $table = $wrap.find(".wms-mon-bin-table");
    if (!rows.length) { $table.html(`<div class="text-muted">${__("No bins match these filters")}</div>`); return; }
    $table.empty().append(this.render_table(rows, [
      ["check", __(""), (row) => `<input type="checkbox" class="wms-mon-bin-check" value="${frappe.utils.escape_html(row.name)}">`],
      ["name", __("Bin")], ["storage_type", __("Storage Type")], ["storage_section", __("Section")],
      ["activity_area", __("Activity Area")], ["aisle", __("Aisle")], ["rack", __("Rack")],
      ["level", __("Level")], ["position", __("Position")], ["bin_role", __("Role")],
      ["bin_type", __("Bin Type")], ["maximum_hus", __("Max HUs")], ["current_hu_count", __("Current HUs")],
    ], "Storage Bin"));
    $table.append(`
      <div class="form-inline" style="display:flex;gap:8px;align-items:center;margin-top:8px;">
        <input class="form-control input-sm wms-mon-bin-assign-value" placeholder="${__("Activity Area (blank to clear)")}" style="width:220px;">
        <button type="button" class="btn btn-primary btn-sm wms-mon-bin-assign">${__("Assign Activity Area to Selected")}</button>
      </div>
    `);
    $table.find(".wms-mon-bin-assign").on("click", () => {
      const names = $table.find(".wms-mon-bin-check:checked").map((_, el) => el.value).get();
      if (!names.length) { frappe.show_alert({ message: __("Select at least one bin"), indicator: "orange" }); return; }
      const activityArea = $table.find(".wms-mon-bin-assign-value").val().trim();
      frappe.confirm(__("Assign {0} to {1} selected bin(s)?", [activityArea || __("(blank)"), names.length]), async () => {
        const result = await frappe.call("frappe_wms.api.bin_assignment.mass_assign_activity_area",
          { bin_names: JSON.stringify(names), activity_area: activityArea || undefined }).then((r) => r.message);
        frappe.show_alert({ message: __("Updated {0} bin(s)", [result.updated]), indicator: "green" });
        this.search_bin_assignment();
      });
    });
  }

  // ---------- Kitting ----------
  async load_kitting() {
    const $wrap = this.body_for("kitting");
    if (!$wrap.find(".wms-mon-kit-form").length) {
      $wrap.html(`
        <div class="detail-section wms-mon-kit-form">
          <h6>${__("New Kitting Order")}</h6>
          <div class="form-inline" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;">
            <input class="form-control input-sm wms-mon-kit-item" placeholder="${__("Kit Item")}" style="width:150px;">
            <input class="form-control input-sm wms-mon-kit-bom" placeholder="${__("BOM")}" style="width:150px;">
            <input class="form-control input-sm wms-mon-kit-bin" placeholder="${__("Work Center Bin")}" style="width:150px;">
            <input type="number" min="0" step="any" class="form-control input-sm wms-mon-kit-qty" placeholder="${__("Quantity")}" style="width:110px;">
            <select class="form-control input-sm wms-mon-kit-direction" style="width:130px;">
              <option value="Assemble">${__("Assemble")}</option>
              <option value="Disassemble">${__("Disassemble")}</option>
            </select>
            <button class="btn btn-primary btn-sm wms-mon-kit-create">${__("Create")}</button>
          </div>
        </div>
        <div class="wms-mon-kit-table"></div>
      `);
      $wrap.find(".wms-mon-kit-create").on("click", () => this.create_kitting_order());
    }
    this.search_kitting();
  }

  async create_kitting_order() {
    if (!this.warehouse) { frappe.show_alert({ message: __("Select a warehouse first"), indicator: "orange" }); return; }
    const $wrap = this.body_for("kitting");
    const args = {
      warehouse: this.warehouse,
      kit_item: $wrap.find(".wms-mon-kit-item").val(),
      bom: $wrap.find(".wms-mon-kit-bom").val(),
      work_center_bin: $wrap.find(".wms-mon-kit-bin").val(),
      quantity: $wrap.find(".wms-mon-kit-qty").val(),
      direction: $wrap.find(".wms-mon-kit-direction").val(),
    };
    if (!args.kit_item || !args.bom || !args.work_center_bin || !flt(args.quantity)) {
      frappe.show_alert({ message: __("Fill in kit item, BOM, work center bin and quantity"), indicator: "orange" });
      return;
    }
    try {
      const name = await frappe.call("frappe_wms.api.kitting.create_kitting_order", args).then((r) => r.message);
      frappe.show_alert({ message: __("Created {0}", [name]), indicator: "green" });
      $wrap.find(".wms-mon-kit-item, .wms-mon-kit-bom, .wms-mon-kit-bin, .wms-mon-kit-qty").val("");
      this.search_kitting();
    } catch (e) {
      frappe.show_alert({ message: e.message || __("Failed to create Kitting Order"), indicator: "red" });
    }
  }

  async search_kitting() {
    if (!this.warehouse) return;
    const $wrap = this.body_for("kitting");
    const rows = await frappe.call("frappe_wms.api.kitting.list_open_kitting_orders").then((r) => r.message || []);
    const filtered = rows.filter((r) => r.warehouse === this.warehouse);
    const $table = $wrap.find(".wms-mon-kit-table");
    if (!filtered.length) { $table.html(`<div class="text-muted">${__("No open Kitting Orders")}</div>`); return; }
    $table.empty().append(this.render_table(filtered, [
      ["name", __("Order")], ["kit_item", __("Kit Item")], ["bom", __("BOM")], ["direction", __("Direction")],
      ["quantity", __("Quantity")], ["work_center_bin", __("Work Center Bin")], ["status", __("Status")],
      ["creation", __("Created")],
      ["complete", __(""), (row) => `<button type="button" class="btn btn-xs btn-primary wms-mon-kit-complete" data-order="${frappe.utils.escape_html(row.name)}">${__("Complete")}</button>`],
    ], "Kitting Order"));
    $table.find(".wms-mon-kit-complete").on("click", (e) => {
      const order = e.currentTarget.dataset.order;
      frappe.confirm(__("Complete Kitting Order {0}? This consumes/produces stock immediately.", [order]), () => {
        frappe.call("frappe_wms.api.kitting.complete_kitting_order", { kitting_order_name: order }).then(() => {
          frappe.show_alert({ message: __("Kitting Order completed"), indicator: "green" });
          this.search_kitting();
        }).catch((e) => frappe.show_alert({ message: e.message || __("Failed to complete"), indicator: "red" }));
      });
    });
  }

  // ---------- Billing ----------
  async load_billing() {
    const $wrap = this.body_for("billing");
    if (!$wrap.find(".wms-mon-bill-filters").length) {
      $wrap.html(`
        <div class="wms-mon-bill-filters form-inline" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;">
          <input class="form-control input-sm wms-mon-bill-customer" placeholder="${__("Customer")}" style="width:160px;">
          <input type="date" class="form-control input-sm wms-mon-bill-from" style="width:150px;">
          <input type="date" class="form-control input-sm wms-mon-bill-to" style="width:150px;">
          <button class="btn btn-primary btn-sm wms-mon-bill-preview">${__("Preview")}</button>
          <button class="btn btn-secondary btn-sm wms-mon-bill-invoice">${__("Create Sales Invoice")}</button>
        </div>
        <div class="wms-mon-bill-table"></div>
      `);
      $wrap.find(".wms-mon-bill-preview").on("click", () => this.preview_billing());
      $wrap.find(".wms-mon-bill-invoice").on("click", () => this.create_billing_invoice());
    }
  }

  _billing_args() {
    const $wrap = this.body_for("billing");
    return {
      warehouse: this.warehouse,
      customer: $wrap.find(".wms-mon-bill-customer").val(),
      from_date: $wrap.find(".wms-mon-bill-from").val(),
      to_date: $wrap.find(".wms-mon-bill-to").val(),
    };
  }

  async preview_billing() {
    if (!this.warehouse) { frappe.show_alert({ message: __("Select a warehouse first"), indicator: "orange" }); return; }
    const args = this._billing_args();
    if (!args.customer || !args.from_date || !args.to_date) {
      frappe.show_alert({ message: __("Fill in customer, from date and to date"), indicator: "orange" });
      return;
    }
    const $table = this.body_for("billing").find(".wms-mon-bill-table");
    const lines = await frappe.call("frappe_wms.api.billing.generate_billing_for_period", args).then((r) => r.message || []);
    if (!lines.length) { $table.html(`<div class="text-muted">${__("No billable activity found for this period")}</div>`); return; }
    $table.empty().append(this.render_table(lines, [
      ["activity", __("Activity")], ["task_count", __("Tasks")], ["quantity", __("Quantity")],
      ["uom_basis", __("Basis")], ["rate", __("Rate")], ["billed_quantity", __("Billed Qty")], ["charge", __("Charge")],
    ], "Warehouse Task"));
  }

  async create_billing_invoice() {
    if (!this.warehouse) { frappe.show_alert({ message: __("Select a warehouse first"), indicator: "orange" }); return; }
    const args = this._billing_args();
    if (!args.customer || !args.from_date || !args.to_date) {
      frappe.show_alert({ message: __("Fill in customer, from date and to date"), indicator: "orange" });
      return;
    }
    frappe.confirm(__("Create a Draft Sales Invoice for {0} covering {1} to {2}?", [args.customer, args.from_date, args.to_date]), async () => {
      try {
        const name = await frappe.call("frappe_wms.api.billing.create_billing_sales_invoice", args).then((r) => r.message);
        frappe.show_alert({ message: __("Created Draft Sales Invoice {0}", [name]), indicator: "green" });
        frappe.set_route("Form", "Sales Invoice", name);
      } catch (e) {
        frappe.show_alert({ message: e.message || __("Failed to create Sales Invoice"), indicator: "red" });
      }
    });
  }

  // ---------- Alerts ----------
  async load_alerts() {
    if (!this.warehouse) return;
    const $wrap = this.body_for("alerts");
    const alerts = await frappe.call("frappe_wms.api.monitor.get_alerts", { warehouse: this.warehouse }).then((r) => r.message || {});
    $wrap.html(`
      <div style="margin-bottom:24px;">
        <h6>${__("Counts Awaiting Approval")}</h6>
        <div class="wms-mon-alert-approval"></div>
      </div>
      <div style="margin-bottom:24px;">
        <h6>${__("Aged Exceptions")}</h6>
        <div class="wms-mon-alert-exceptions"></div>
      </div>
      <div>
        <h6>${__("Stalled Warehouse Orders")}</h6>
        <div class="wms-mon-alert-wos"></div>
      </div>
    `);
    this.render_pending_approval_alerts(alerts.pending_approval_counts || []);
    this.render_alert_table($wrap.find(".wms-mon-alert-exceptions"), alerts.aged_exceptions || [],
      [["name", __("Task")], ["task_type", __("Type")], ["product", __("Product")], ["source_bin", __("Source Bin")],
       ["destination_bin", __("Destination Bin")], ["assigned_resource", __("Resource")],
       ["exception_code", __("Exception")], ["blocking_reason", __("Reason")], ["modified", __("Since")]],
      "Warehouse Task", __("No aged exceptions"));
    this.render_alert_table($wrap.find(".wms-mon-alert-wos"), alerts.stalled_warehouse_orders || [],
      [["name", __("Warehouse Order")], ["activity", __("Activity")], ["queue", __("Queue")], ["priority", __("Priority")],
       ["status", __("Status")], ["task_count", __("Tasks")], ["creation", __("Created")]],
      "Warehouse Order", __("No stalled Warehouse Orders"));
  }

  render_alert_table($container, rows, columns, doctype, empty_message) {
    if (!rows.length) { $container.html(`<div class="text-muted">${empty_message}</div>`); return; }
    $container.empty().append(this.render_table(rows, columns, doctype));
  }

  // A supervisor's one bulk action in this page: select several Under Review counts and
  // approve them all in one click, instead of opening each one individually.
  render_pending_approval_alerts(rows) {
    const $container = this.body_for("alerts").find(".wms-mon-alert-approval");
    if (!rows.length) { $container.html(`<div class="text-muted">${__("No counts awaiting approval")}</div>`); return; }
    $container.empty().append(this.render_table(rows, [
      ["check", __(""), (row) => `<input type="checkbox" class="wms-mon-approve-check" value="${frappe.utils.escape_html(row.name)}">`],
      ["name", __("Count")], ["product", __("Product")], ["storage_bin", __("Bin")], ["storage_type", __("Storage Type")],
      ["count_date", __("Count Date")], ["modified", __("Last Modified")],
    ], "WMS Physical Inventory Count"));
    $container.append(`<button type="button" class="btn btn-primary btn-sm wms-mon-approve-selected" style="margin-top:8px;">${__("Approve Selected")}</button>`);
    // Sequential per-count approval (not Promise.all) so one failure doesn't silently
    // swallow the rest, and the final summary reflects exactly how many actually succeeded.
    $container.find(".wms-mon-approve-selected").on("click", () => {
      const names = $container.find(".wms-mon-approve-check:checked").map((_, el) => el.value).get();
      if (!names.length) { frappe.show_alert({ message: __("Select at least one count"), indicator: "orange" }); return; }
      frappe.confirm(__("Approve {0} selected count(s)? Their held variances will post to the stock ledger.", [names.length]), async () => {
        let succeeded = 0;
        for (const name of names) {
          try {
            await frappe.call("frappe_wms.api.inventory.approve_variance", { count_name: name });
            succeeded += 1;
          } catch (e) {
            frappe.show_alert({ message: __("Failed to approve {0}", [name]), indicator: "red" });
          }
        }
        frappe.show_alert({ message: __("Approved {0} of {1} count(s)", [succeeded, names.length]), indicator: succeeded === names.length ? "green" : "orange" });
        this.load_alerts();
      });
    });
  }

  // Returns a DataGrid's element (not an HTML string) - callers use .empty().append(...),
  // not .html(...), since the grid carries live selection/copy event handlers.
  render_table(rows, columns, doctype) {
    return new DataGrid(rows, columns, doctype).$el;
  }
}

function flt(v) { const n = parseFloat(v); return isNaN(n) ? 0 : n; }
