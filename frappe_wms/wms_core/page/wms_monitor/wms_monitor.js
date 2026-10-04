frappe.pages["wms-monitor"].on_page_load = function (wrapper) {
  const page = frappe.ui.make_app_page({
    parent: wrapper,
    title: __("WMS Monitor"),
    single_column: true,
  });
  frappe.require(["/assets/frappe_wms/js/wms_selection.js", "/assets/frappe_wms/js/wms_packing_station.js"], () => new WMSMonitor(page));
};

// SAP EWM-style Warehouse Management Monitor: one warehouse selector, a node tree of
// independent views down the left (Overview / Inbound / Outbound / Stock Overview /
// Warehouse Tasks / Handling Units / Stock Movements / Resources & Queues) instead of one
// long page you scroll through. Each view loads its own data only when selected.
const VIEWS = [
  { key: "overview", label: __("Overview") },
  { key: "inbound", label: __("Inbound Monitor") },
  { key: "outbound", label: __("Outbound Monitor") },
  { key: "yard", label: __("Yard & Doors") },
  { key: "stock", label: __("Stock Overview") },
  { key: "tasks", label: __("Warehouse Tasks") },
  { key: "warehouse_orders", label: __("Warehouse Orders") },
  { key: "hu", label: __("Handling Units") },
  { key: "packing", label: __("Repack Center") },
  { key: "repack", label: __("HU Workbench") },
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
    .wms-grid-toolbar { display:flex; align-items:center; flex-wrap:wrap; gap:8px; margin-bottom:6px; }
    .wms-grid-toolbar .wms-grid-hint { font-size:12px; }
    .wms-grid-scroll { overflow:auto; border:1px solid var(--border-color); }
    .wms-grid-table { margin-bottom:0; user-select:none; }
    .wms-grid-table th, .wms-grid-table td { white-space:nowrap; }
    .wms-grid-corner, .wms-grid-colhead, .wms-grid-rowhead { cursor:pointer; background:var(--control-bg,#f5f5f5); }
    .wms-grid-colhead:hover, .wms-grid-rowhead:hover { background:var(--bg-color,#e9ecef); }
    .wms-grid-cell { cursor:cell; }
    .wms-grid-selected { background:rgba(59,130,246,.18) !important; }
    .wms-grid-anchor { outline:1px solid rgba(59,130,246,.7); outline-offset:-1px; }
    .wms-grid-colhead-inner { display:flex; align-items:center; gap:4px; justify-content:space-between; }
    .wms-grid-sort, .wms-grid-filter-btn { cursor:pointer; opacity:.45; font-size:11px; padding:0 2px; }
    .wms-grid-sort:hover, .wms-grid-filter-btn:hover { opacity:1; }
    .wms-grid-sort.active, .wms-grid-filter-btn.active { opacity:1; color:var(--blue-500,#3b82f6); }
    .wms-grid-actionbar { display:flex; align-items:center; gap:6px; padding:4px 0; }
    .wms-grid-actionbar .wms-grid-selcount { font-size:12px; font-weight:600; margin-right:2px; }
    .wms-grid-filter-pop { position:fixed; z-index:2000; background:var(--card-bg,#fff); border:1px solid var(--border-color); border-radius:6px;
      box-shadow:var(--shadow-lg,0 4px 16px rgba(0,0,0,.18)); padding:8px; width:220px; }
    .wms-grid-filter-pop .wms-grid-filter-search { width:100%; margin-bottom:6px; }
    .wms-grid-filter-pop .wms-grid-filter-list { max-height:220px; overflow:auto; border:1px solid var(--border-color); border-radius:4px; padding:4px 6px; margin-bottom:6px; }
    .wms-grid-filter-pop .wms-grid-filter-list label { display:block; font-size:12px; font-weight:normal; margin:2px 0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .wms-grid-filter-pop .wms-grid-filter-links { display:flex; justify-content:space-between; font-size:11px; margin-bottom:6px; }
    .wms-grid-filter-pop .wms-grid-filter-links a { cursor:pointer; }
    .wms-grid-filter-pop .wms-grid-filter-actions { display:flex; justify-content:flex-end; gap:6px; }
    .wms-grid-grip { cursor:grab; opacity:.35; font-size:10px; letter-spacing:-2px; padding-right:2px; }
    .wms-grid-grip:hover { opacity:1; }
    .wms-grid-colhead.wms-grid-dragover { box-shadow: inset 3px 0 0 var(--blue-500,#3b82f6); }
    .wms-grid-table tfoot td, .wms-grid-table tfoot th { font-weight:600; background:var(--control-bg,#f5f5f5); position:sticky; bottom:0; }
    .wms-grid-table thead th { position:sticky; top:0; z-index:1; }
    .wms-grid-num { text-align:right; font-variant-numeric: tabular-nums; }
    .wms-grid-layoutbar { display:flex; align-items:center; gap:6px; }
  ` }).appendTo("head");
}

let _monitor_styles_injected = false;
function ensure_monitor_styles() {
  if (_monitor_styles_injected) return;
  _monitor_styles_injected = true;
  $("<style>", { text: `
    .wms-monitor-filters { display:flex; align-items:center; gap:10px; margin-bottom:12px; }
    .wms-monitor-filters label { margin:0; color:var(--text-muted); font-size:12px; }
    .wms-monitor-filters .wms-mon-warehouse { width:240px; }
    .wms-monitor-shell { display:flex; gap:16px; align-items:flex-start; }
    .wms-monitor-nav { flex:0 0 176px; position:sticky; top:70px; }
    .wms-mon-nav-list { border:1px solid var(--border-color); border-radius:10px; overflow:hidden; background:var(--card-bg,#fff); }
    .wms-mon-nav-item { display:block; padding:7px 14px; font-size:13px; color:var(--text-color); border-left:3px solid transparent; text-decoration:none; }
    .wms-mon-nav-item:hover { background:var(--control-bg,#f5f5f5); text-decoration:none; }
    .wms-mon-nav-item.active { background:var(--bg-light-blue,#eff6ff); border-left-color:var(--primary,#3b82f6); font-weight:600; color:var(--primary,#2563eb); }
    .wms-monitor-content { flex:1; min-width:0; }
    .wms-mon-title { font-size:16px; font-weight:700; margin:0 0 10px; }
    .wms-mon-card { display:inline-block; min-width:140px; margin:0 10px 10px 0; padding:10px 16px; border:1px solid var(--border-color); border-radius:10px; background:var(--card-bg,#fff); }
    .wms-mon-card.clickable { cursor:pointer; transition:box-shadow .15s, transform .15s; }
    .wms-mon-card.clickable:hover { box-shadow:var(--shadow-md,0 4px 12px rgba(0,0,0,.12)); transform:translateY(-1px); }
    .wms-mon-card-value { font-size:22px; font-weight:700; line-height:1.2; }
    .wms-mon-card-label { font-size:12px; color:var(--text-muted); }
    .wms-mon-stock-summary { margin-bottom:8px; }
    .wms-grid-scroll { max-height:calc(100vh - 270px); border-radius:8px; background:var(--card-bg,#fff); }
    .wms-grid-table td, .wms-grid-table th { padding:4px 10px !important; font-size:12.5px; }
    .wms-grid-table thead th { background:var(--control-bg,#f5f5f5); font-weight:600; border-bottom:2px solid var(--border-color); }
    .wms-grid-table tbody tr:nth-child(even) td { background:var(--subtle-fg,rgba(0,0,0,.02)); }
    .wms-grid-table tbody tr:hover td { background:var(--bg-light-blue,#eff6ff); }
    .wms-grid-table tbody td.wms-grid-selected { background:rgba(59,130,246,.18) !important; }
    .wms-grid-actionbar { padding:2px 0; }
    .wms-stock-group { display:flex; flex-wrap:wrap; align-items:center; gap:4px 14px; padding:4px 2px 8px; font-size:12px; }
    .wms-stock-group label { margin:0; font-weight:normal; display:inline-flex; align-items:center; gap:4px; cursor:pointer; }
    .wms-stock-group input { margin:0; }
    .wms-stock-group-sep { flex:0 0 1px; height:16px; background:var(--border-color); }
    .wms-detail-panel { margin-top:14px; padding:10px 12px; border:1px solid var(--primary,#3b82f6); border-radius:10px; background:var(--card-bg,#fff); }
    .wms-detail-head { display:flex; align-items:center; gap:12px; margin-bottom:8px; }
    .wms-detail-head .wms-detail-close { margin-left:auto; }
    .wms-detail-serials { display:flex; flex-wrap:wrap; align-items:center; gap:4px; max-height:96px; overflow:auto; margin-bottom:8px; }
    .wms-chip { padding:0 8px; border-radius:10px; border:1px solid var(--border-color); font-size:11px; font-family:var(--font-stack-mono,monospace); }
    .wms-detail-panel .wms-grid-scroll { max-height:40vh; }
  ` }).appendTo("head");
}

let _repack_styles_injected = false;
function ensure_repack_styles() {
  if (_repack_styles_injected) return;
  _repack_styles_injected = true;
  $("<style>", { text: `
    .wms-repack-row:hover { background:var(--control-bg,#f5f5f5); }
    .wms-repack-row.wms-repack-dragging { opacity:.4; }
    .wms-repack-tree-row:hover { background:var(--control-bg,#f5f5f5); }
    .wms-repack-tree-row.wms-repack-dragging { opacity:.4; }
    .wms-repack-tree-row.wms-repack-dragover { outline:2px dashed rgba(59,130,246,.7); outline-offset:-2px; background:rgba(59,130,246,.06); }
    .wms-repack-tree-row.wms-repack-tree-selected { background:rgba(59,130,246,.15); font-weight:bold; }
    .wms-repack-table td, .wms-repack-table th { vertical-align:middle; }
  ` }).appendTo("head");
}

// Excel-like grid: click a cell to select it, shift+click or drag to extend a rectangular
// range, Ctrl/Cmd+click (or Ctrl+drag) a cell, row header or column header to ADD or REMOVE it
// from the selection instead of replacing it - so several non-adjacent rows or columns can be
// selected at once, exactly like Excel or a SAP GUI ALV grid. Click a column/row header (or the
// corner) without a modifier to select a whole column/row/everything, same as before.
// Ctrl/Cmd+C or the Copy button copies the selection as tab-separated text - pastes straight
// into a spreadsheet. Row/reference links still navigate normally; only the surrounding cell
// area starts a drag-select. A quick-filter box narrows visible rows client-side (substring
// match across every column, independent of whatever server-side filters a view also has); each
// column header also gets a real per-column filter (an Excel-style checkbox list of that
// column's distinct values) and a sort toggle (ascending / descending / none).
// opts.actions: [{label, kind, confirm, appliesTo(row), run(selectedRows, grid)}] - SAP EWM-style
// quick actions. Shown in the toolbar once at least one row is selected (any row touched by any
// selected cell/row/column), each button enabled only when every selected row satisfies its
// appliesTo (default: always applicable) - the action itself decides what "applicable" means.
class DataGrid {
  constructor(rows, columns, doctype, opts = {}) {
    this.rows = rows || [];
    this.columns = columns || [];
    this.doctype = doctype;
    this.opts = opts || {};
    this.filterText = "";
    this.colFilters = {}; // {field: Set(allowedValues)} - absent/undefined = no filter on that column
    this.sortField = (this.opts.sort && this.opts.sort[0]) || null;
    this.sortDir = (this.opts.sort && this.opts.sort[1]) || 0; // 1 asc, -1 desc
    this.showTotals = !!this.opts.totals;
    this.numeric = new Set(this.opts.numeric || []);
    this.sels = []; // [{r0,r1,c0,c1}, ...] - r0 includes the header row (0); c0 excludes the gutter (starts at 1)
    this.anchor = null;
    this.$el = $(`<div class="wms-grid"></div>`);
    ensure_grid_styles();
    this._build();
  }

  _distinctValues(field) {
    const seen = new Map();
    for (const row of this.rows) {
      const v = row[field];
      const key = v === null || v === undefined || v === "" ? "" : String(v);
      if (!seen.has(key)) seen.set(key, key === "" ? __("(blank)") : key);
    }
    return Array.from(seen.entries()).sort((a, b) => a[1].localeCompare(b[1], undefined, { numeric: true }));
  }

  _visibleRows() {
    let rows = this.rows;
    if (this.filterText) {
      const needle = this.filterText.toLowerCase();
      rows = rows.filter((row) => this.columns.some(([f]) => String(row[f] ?? "").toLowerCase().includes(needle)));
    }
    for (const [field, allowed] of Object.entries(this.colFilters)) {
      if (!allowed) continue;
      rows = rows.filter((row) => {
        const v = row[field];
        const key = v === null || v === undefined || v === "" ? "" : String(v);
        return allowed.has(key);
      });
    }
    if (this.sortField && this.sortDir) {
      const field = this.sortField, dir = this.sortDir;
      rows = rows.slice().sort((a, b) => {
        const av = a[field], bv = b[field];
        const aEmpty = av === null || av === undefined || av === "", bEmpty = bv === null || bv === undefined || bv === "";
        if (aEmpty && bEmpty) return 0;
        if (aEmpty) return 1; // blanks always sort last, in either direction
        if (bEmpty) return -1;
        const an = Number(av), bn = Number(bv);
        const cmp = (!isNaN(an) && !isNaN(bn) && av !== "" && bv !== "") ? an - bn : String(av).localeCompare(String(bv), undefined, { numeric: true });
        return cmp * dir;
      });
    }
    return rows;
  }

  // Every row index (1-based, matching data-r) touched by any selected rectangle.
  _selectedRowIndices() {
    const out = new Set();
    for (const s of this.sels) {
      const lo = Math.max(1, Math.min(s.r0, s.r1)), hi = Math.max(s.r0, s.r1);
      for (let r = lo; r <= hi; r++) out.add(r);
    }
    return out;
  }

  _build() {
    const $toolbar = $(`
      <div class="wms-grid-toolbar">
        <input type="text" class="form-control input-sm wms-grid-filter" style="width:220px;" placeholder="${__("Filter visible rows...")}">
        <button type="button" class="btn btn-default btn-xs wms-grid-copy">${__("Copy")}</button>
        <button type="button" class="btn btn-default btn-xs wms-grid-csv" title="${__("Download the visible rows and columns as CSV")}">${__("Export")}</button>
        <button type="button" class="btn btn-default btn-xs wms-grid-totals" title="${__("Totals of numeric columns")}">&Sigma;</button>
        <span class="wms-grid-layoutbar"></span>
        <button type="button" class="btn btn-default btn-xs wms-grid-clear-filters" style="display:none;">${__("Clear filters/sort")}</button>
        <span class="text-muted wms-grid-hint"></span>
        <div class="wms-grid-actionbar"></div>
      </div>
    `);
    this.$scroll = $(`<div class="wms-grid-scroll"><table class="table table-bordered table-sm wms-grid-table"></table></div>`);
    this.$el.empty().append($toolbar, this.$scroll);
    this.$table = this.$scroll.find("table");
    this.$actionbar = $toolbar.find(".wms-grid-actionbar");
    this.$el.attr("tabindex", 0).css("outline", "none");
    $toolbar.find(".wms-grid-filter").on("input", (e) => { this.filterText = e.target.value; this.sels = []; this._render(); });
    $toolbar.find(".wms-grid-copy").on("click", () => this._copy());
    $toolbar.find(".wms-grid-csv").on("click", () => this._exportCsv());
    $toolbar.find(".wms-grid-totals").toggleClass("active", this.showTotals).on("click", (e) => {
      this.showTotals = !this.showTotals; $(e.currentTarget).toggleClass("active", this.showTotals); this._render(); this._layoutChanged();
    });
    if (this.opts.layoutBar) $toolbar.find(".wms-grid-layoutbar").append(this.opts.layoutBar);
    $toolbar.find(".wms-grid-clear-filters").on("click", () => { this.colFilters = {}; this.sortField = null; this.sortDir = 0; this.sels = []; this._render(); this._layoutChanged(); });
    this._bindSelection();
    this.$el.on("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && (e.key === "c" || e.key === "C")) { e.preventDefault(); this._copy(); }
      if (e.key === "Escape") { this.sels = []; this._applyHighlight(); this._renderActionBar(); }
    });
    this._render();
  }

  _render() {
    const rows = this._visibleRows();
    this._visRows = rows;
    const maxR = rows.length, maxC = this.columns.length;
    const head = [`<th class="wms-grid-corner" data-r="0" data-c="0"></th>`].concat(
      this.columns.map(([field, label], ci) => {
        const sortCls = this.sortField === field ? (this.sortDir === 1 ? "active" : this.sortDir === -1 ? "active" : "") : "";
        const sortIcon = this.sortField === field && this.sortDir === -1 ? "▼" : this.sortField === field && this.sortDir === 1 ? "▲" : "⇅";
        const filterActive = !!this.colFilters[field];
        return `<th class="wms-grid-colhead" data-r="0" data-c="${ci + 1}" data-field="${frappe.utils.escape_html(field)}">
          <div class="wms-grid-colhead-inner">
            <span><span class="wms-grid-grip" draggable="true" data-c="${ci}" title="${__("Drag to move this column")}">&#8942;&#8942;</span><span class="wms-grid-colhead-label">${frappe.utils.escape_html(label)}</span></span>
            <span>
              <span class="wms-grid-sort ${sortCls}" data-field="${frappe.utils.escape_html(field)}" title="${__("Sort")}">${sortIcon}</span>
              <span class="wms-grid-filter-btn ${filterActive ? "active" : ""}" data-field="${frappe.utils.escape_html(field)}" title="${__("Filter")}">▾</span>
            </span>
          </div>
        </th>`;
      })
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
          return `<td class="wms-grid-cell${this.numeric.has(field) ? " wms-grid-num" : ""}" data-r="${ri + 1}" data-c="${ci + 1}">${inner}</td>`;
        })
      ).join("");
      return `<tr>${cells}</tr>`;
    }).join("");
    let foot = "";
    if (this.showTotals && rows.length) {
      const cells = this.columns.map(([field]) => {
        if (!this.numeric.has(field)) return `<td></td>`;
        const total = rows.reduce((acc, row) => acc + (parseFloat(row[field]) || 0), 0);
        return `<td class="wms-grid-num">${frappe.utils.escape_html(String(Math.round(total * 1e6) / 1e6))}</td>`;
      });
      foot = `<tfoot><tr><th class="wms-grid-rowhead">&Sigma;</th>${cells.join("")}</tr></tfoot>`;
    }
    this.$table.html(`<thead><tr>${head}</tr></thead><tbody>${body}</tbody>${foot}`);
    this._maxR = maxR; this._maxC = maxC;
    this.$el.find(".wms-grid-hint").text(rows.length === this.rows.length ? __("{0} row(s)", [rows.length]) : __("{0} of {1} row(s)", [rows.length, this.rows.length]));
    this.$el.find(".wms-grid-clear-filters").toggle(!!(this.sortField || Object.keys(this.colFilters).length));
    this._bindHeaderControls();
    this._applyHighlight();
    this._renderActionBar();
  }

  _bindHeaderControls() {
    this.$table.find(".wms-grid-sort").on("mousedown", (e) => {
      e.preventDefault(); e.stopPropagation();
      const field = $(e.currentTarget).data("field");
      if (this.sortField !== field) { this.sortField = field; this.sortDir = 1; }
      else if (this.sortDir === 1) this.sortDir = -1;
      else if (this.sortDir === -1) { this.sortField = null; this.sortDir = 0; }
      else this.sortDir = 1;
      this._render();
      this._layoutChanged();
    });
    // Column move: drag the grip in a header onto another header (SAP ALV "drag a column").
    this.$table.find(".wms-grid-grip").on("mousedown", (e) => e.stopPropagation())
      .on("dragstart", (e) => { this._dragCol = Number(e.currentTarget.dataset.c); e.originalEvent.dataTransfer.effectAllowed = "move"; e.originalEvent.dataTransfer.setData("text/plain", "col"); });
    this.$table.find(".wms-grid-colhead")
      .on("dragover", (e) => { if (this._dragCol == null) return; e.preventDefault(); $(e.currentTarget).addClass("wms-grid-dragover"); })
      .on("dragleave", (e) => $(e.currentTarget).removeClass("wms-grid-dragover"))
      .on("drop", (e) => {
        e.preventDefault();
        const from = this._dragCol, to = Number(e.currentTarget.dataset.c) - 1;
        this._dragCol = null;
        if (from == null || to < 0 || from === to) { this._render(); return; }
        const [col] = this.columns.splice(from, 1);
        this.columns.splice(to, 0, col);
        this.sels = [];
        this._render();
        this._layoutChanged();
      });
    this.$table.find(".wms-grid-filter-btn").on("mousedown", (e) => {
      e.preventDefault(); e.stopPropagation();
      this._openColumnFilter($(e.currentTarget).data("field"), $(e.currentTarget));
    });
  }

  _openColumnFilter(field, $btn) {
    $(".wms-grid-filter-pop").remove();
    const values = this._distinctValues(field);
    const current = this.colFilters[field]; // Set of allowed keys, or undefined = all allowed
    const $pop = $(`
      <div class="wms-grid-filter-pop">
        <input type="text" class="form-control input-sm wms-grid-filter-search" placeholder="${__("Search values...")}">
        <div class="wms-grid-filter-links"><a class="wms-grid-filter-all">${__("Select all")}</a><a class="wms-grid-filter-none">${__("Clear")}</a></div>
        <div class="wms-grid-filter-list"></div>
        <div class="wms-grid-filter-actions">
          <button type="button" class="btn btn-default btn-xs wms-grid-filter-cancel">${__("Cancel")}</button>
          <button type="button" class="btn btn-primary btn-xs wms-grid-filter-apply">${__("Apply")}</button>
        </div>
      </div>
    `);
    const $list = $pop.find(".wms-grid-filter-list");
    const renderList = (needle) => {
      $list.empty();
      const n = (needle || "").toLowerCase();
      values.filter(([, label]) => !n || label.toLowerCase().includes(n)).forEach(([key, label]) => {
        const checked = !current || current.has(key);
        $list.append(`<label><input type="checkbox" class="wms-grid-filter-val" value="${frappe.utils.escape_html(key)}" ${checked ? "checked" : ""}> ${frappe.utils.escape_html(label)}</label>`);
      });
    };
    renderList("");
    $pop.find(".wms-grid-filter-search").on("input", (e) => renderList(e.target.value));
    $pop.find(".wms-grid-filter-all").on("click", () => $list.find(".wms-grid-filter-val").prop("checked", true));
    $pop.find(".wms-grid-filter-none").on("click", () => $list.find(".wms-grid-filter-val").prop("checked", false));
    $pop.find(".wms-grid-filter-cancel").on("click", () => $pop.remove());
    $pop.find(".wms-grid-filter-apply").on("click", () => {
      // Applies against the FULL distinct-value list (values), not just what the search box narrowed
      // to - unchecking after a search still only unchecks what was visible; anything the search box
      // hid stays whatever it already was.
      const checkedNow = new Set($pop.find(".wms-grid-filter-val:checked").map((_, el) => el.value).get());
      const searched = (($pop.find(".wms-grid-filter-search").val() || "")).toLowerCase();
      const allowed = new Set(current ? Array.from(current) : values.map(([k]) => k));
      values.forEach(([key, label]) => {
        if (searched && !label.toLowerCase().includes(searched)) return; // untouched by this pass
        if (checkedNow.has(key)) allowed.add(key); else allowed.delete(key);
      });
      this.colFilters[field] = allowed.size === values.length ? undefined : allowed;
      if (!this.colFilters[field]) delete this.colFilters[field];
      this.sels = [];
      $pop.remove();
      this._render();
    });
    $("body").append($pop);
    const rect = $btn[0].getBoundingClientRect();
    const popW = 220;
    $pop.css({ top: rect.bottom + 4, left: Math.min(rect.left, window.innerWidth - popW - 12) });
    setTimeout(() => $(document).on("mousedown.wmsgridfilter", (e) => {
      if ($(e.target).closest(".wms-grid-filter-pop").length) return;
      $pop.remove(); $(document).off("mousedown.wmsgridfilter");
    }), 0);
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
    for (const s of this.sels) {
      for (const [r, c] of this._cellsInRange(s.r0, s.r1, s.c0, s.c1)) {
        this.$table.find(`[data-r="${r}"][data-c="${c}"]`).addClass("wms-grid-selected");
      }
    }
    if (this.anchor) this.$table.find(`[data-r="${this.anchor.r}"][data-c="${this.anchor.c}"]`).addClass("wms-grid-anchor");
  }

  _sameRect(a, b) { return a.r0 === b.r0 && a.r1 === b.r1 && a.c0 === b.c0 && a.c1 === b.c1; }

  // Ctrl/Cmd+click (or +drag) a cell/row/column ADDS or REMOVES it from the selection instead of
  // replacing it - the actual "non-adjacent rows/columns" behavior. A plain click/drag still
  // replaces the whole selection with just what was clicked, matching the old single-range model.
  _bindSelection() {
    let dragging = false, liveIndex = -1;
    this.$table.on("mousedown", "th, td", (e) => {
      if ($(e.currentTarget).closest("tfoot").length) return; // the totals row is not selectable data
      if ($(e.target).is("a, button, input, select, textarea, label")) return; // let interactive controls work normally, don't hijack them into a selection
      const $cell = $(e.currentTarget);
      const r = Number($cell.data("r")), c = Number($cell.data("c"));
      const toggling = e.ctrlKey || e.metaKey;
      this.$el.trigger("focus");
      let rect;
      if (c === 0 && r === 0) { this.sels = [{ r0: 0, r1: this._maxR, c0: 1, c1: this._maxC }]; this.anchor = { r: 0, c: 1 }; this._applyHighlight(); this._renderActionBar(); e.preventDefault(); return; }
      else if (c === 0) rect = { r0: r, r1: r, c0: 1, c1: this._maxC };
      else if (r === 0) rect = { r0: 0, r1: this._maxR, c0: c, c1: c };
      else if (e.shiftKey && this.anchor && !toggling) { this.sels = this.sels.length ? this.sels.slice(0, -1) : []; this.sels.push({ r0: this.anchor.r, r1: r, c0: this.anchor.c, c1: c }); this._applyHighlight(); this._renderActionBar(); e.preventDefault(); return; }
      else rect = { r0: r, r1: r, c0: c, c1: c };
      const anchorFor = (r === 0) ? { r: 0, c } : { r, c: c === 0 ? 1 : c };
      if (toggling) {
        const existingIdx = this.sels.findIndex((s) => this._sameRect(s, rect));
        if (existingIdx >= 0) { this.sels.splice(existingIdx, 1); liveIndex = -1; }
        else { this.sels.push(rect); liveIndex = this.sels.length - 1; this.anchor = anchorFor; }
      } else {
        this.sels = [rect]; liveIndex = 0; this.anchor = anchorFor;
      }
      if (r > 0 && c > 0) dragging = true;
      this._applyHighlight();
      this._renderActionBar();
      e.preventDefault();
    });
    this.$table.on("mouseenter", "td.wms-grid-cell", (e) => {
      if (!dragging || !this.anchor || liveIndex < 0 || !this.sels[liveIndex]) return;
      const $cell = $(e.currentTarget);
      this.sels[liveIndex] = { r0: this.anchor.r, r1: Number($cell.data("r")), c0: this.anchor.c, c1: Number($cell.data("c")) };
      this._applyHighlight();
      this._renderActionBar();
    });
    $(document).on("mouseup", () => { dragging = false; liveIndex = -1; });
  }

  _renderActionBar() {
    if (!this.$actionbar) return;
    const actions = this.opts.actions || [];
    if (!actions.length) { this.$actionbar.empty(); return; }
    const rowIdx = Array.from(this._selectedRowIndices()).filter((r) => r >= 1 && r <= this._visRows.length);
    const selectedRows = rowIdx.map((r) => this._visRows[r - 1]);
    if (!selectedRows.length) { this.$actionbar.empty(); return; }
    this.$actionbar.empty().append(`<span class="wms-grid-selcount">${__("{0} selected", [selectedRows.length])}</span>`);
    actions.forEach((action) => {
      const enabled = selectedRows.every((row) => (action.appliesTo ? action.appliesTo(row) : true));
      const $btn = $(`<button type="button" class="btn btn-${action.kind || "default"} btn-xs" ${enabled ? "" : "disabled"}>${frappe.utils.escape_html(action.label)}</button>`);
      if (enabled) {
        $btn.on("click", () => {
          const go = () => Promise.resolve(action.run(selectedRows, this)).then(() => { this.sels = []; this._applyHighlight(); });
          if (action.confirm) frappe.confirm(typeof action.confirm === "function" ? action.confirm(selectedRows) : action.confirm, go);
          else go();
        });
      }
      this.$actionbar.append($btn);
    });
  }

  layoutState() {
    return { columns: this.columns.map(([f]) => f), sort: this.sortField ? [this.sortField, this.sortDir] : null, totals: this.showTotals };
  }

  _layoutChanged() { if (this.opts.onLayoutChange) this.opts.onLayoutChange(this.layoutState()); }

  _exportCsv() {
    const esc = (v) => { const t = v === null || v === undefined ? "" : String(v); return /[",\n;]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t; };
    const lines = [this.columns.map(([, label]) => esc(label)).join(",")].concat(
      this._visRows.map((row) => this.columns.map(([f]) => esc(row[f])).join(",")));
    const blob = new Blob(["\ufeff" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${(this.opts.exportName || this.doctype || "export").replace(/[^\w-]+/g, "_")}.csv`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  _copy() {
    if (!this.sels.length) { frappe.show_alert({ message: __("Select a cell, row, or column first"), indicator: "orange" }); return; }
    const cellSet = new Map(); // "r,c" -> [r,c], de-duplicated across overlapping/multiple rectangles
    for (const s of this.sels) for (const [r, c] of this._cellsInRange(s.r0, s.r1, s.c0, s.c1)) cellSet.set(`${r},${c}`, [r, c]);
    const byRow = {};
    for (const [r, c] of cellSet.values()) (byRow[r] || (byRow[r] = new Set())).add(c);
    const lines = Object.keys(byRow).map(Number).sort((a, b) => a - b).map((r) => {
      const cs = Array.from(byRow[r]).sort((a, b) => a - b);
      return cs.map((c) => {
        if (r === 0) return this.columns[c - 1][1];
        const row = this._visRows[r - 1];
        const value = row[this.columns[c - 1][0]];
        return value === null || value === undefined ? "" : String(value);
      }).join("\t");
    });
    const text = lines.join("\n");
    const done = () => frappe.show_alert({ message: __("Copied {0} cell(s)", [cellSet.size]), indicator: "green" });
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

// SAP selection-screen convention used across every search tab: nothing loads until Execute is
// pressed (no auto-run on tab entry, no auto-run on a filter changing), and any text field takes
// '*' as an explicit wildcard placeholder (no '*' -> exact match; 'AB*'/'*AB'/'*AB*' -> prefix/
// suffix/contains). This one-line hint plus the empty-until-executed state is shared everywhere.
function sap_search_hint() {
  return `<div class="text-muted" style="margin-bottom:8px;font-size:12px;">${__("Build your search, then click Execute. Use * as a wildcard in text fields (e.g. AB*, *AB, *AB*) - without it, text fields match exactly.")}</div>`;
}
function sap_unexecuted_html() {
  return `<div class="text-muted">${__("Not executed yet - set your criteria and click Execute.")}</div>`;
}

class WMSMonitor {
  constructor(page) {
    this.page = page;
    this.warehouse = null;
    this.view = "overview";
    this.repack = { roots: [], expanded: new Set(), childrenOf: {}, selected: null, detail: null, target: null, hu_types: null, newDest: null, _drag: null, selectedRows: new Map() };

    this.$body = $(`
      <div class="wms-monitor">
        <div class="wms-monitor-filters form-inline"></div>
        <div class="wms-monitor-shell">
          <div class="wms-monitor-nav">
            <div class="list-group wms-mon-nav-list"></div>
          </div>
          <div class="wms-monitor-content">
            ${VIEWS.map((v) => `
              <div class="wms-mon-view" data-view="${v.key}" style="display:none;">
                <h4 class="wms-mon-title">${frappe.utils.escape_html(v.label)}</h4>
                <div class="wms-mon-view-body" data-view-body="${v.key}"></div>
              </div>
            `).join("")}
          </div>
        </div>
      </div>
    `).appendTo(this.page.main);

    ensure_monitor_styles();
    // Every HU reference in every grid (any view, any dialog result) opens the contents viewer.
    this.$body.on("click", ".wms-open-hu-viewer, .wms-hu-open", (e) => { e.preventDefault(); this.open_hu_detail($(e.currentTarget).data("hu")); });
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
      <button type="button" class="btn btn-default btn-sm wms-mon-navtoggle" title="${__("Show / hide the menu")}">&#9776;</button>
      <label>${__("Warehouse")}</label>
      <select class="form-control input-sm wms-mon-warehouse"><option value="">${__("Select a warehouse")}</option>${options}</select>
    `);
    $filters.find(".wms-mon-navtoggle").on("click", () => {
      const hidden = this.$body.find(".wms-monitor-nav").toggle().is(":hidden");
      try { localStorage.setItem("wms_monitor_nav_hidden", hidden ? "1" : ""); } catch (e) { /* storage blocked: the menu just reopens next time */ }
    });
    try { if (localStorage.getItem("wms_monitor_nav_hidden")) this.$body.find(".wms-monitor-nav").hide(); } catch (e) { /* ignore */ }
    $filters.find(".wms-mon-warehouse").on("change", (e) => {
      this.warehouse = e.target.value || null;
      this.reset_unexecuted_searches();
      if (this.warehouse) this.load_view(this.view);
    });
    this.show_view(this.view);
    if (warehouses.length === 1) {
      $filters.find(".wms-mon-warehouse").val(warehouses[0].name).trigger("change");
    }
  }

  // The warehouse changed - any already-executed results belong to the OLD warehouse and would
  // otherwise sit there looking current. Every "big list" tab goes back to its not-executed
  // state instead of silently keeping stale rows around.
  reset_unexecuted_searches() {
    const selectors = [
      ".wms-mon-ind-table", ".wms-mon-obd-table", ".wms-mon-wave-table", ".wms-mon-stock-table",
      ".wms-mon-task-table", ".wms-mon-wo-table", ".wms-mon-hu-table", ".wms-mon-ledger-table",
    ];
    selectors.forEach((sel) => { const $el = this.$body.find(sel); if ($el.length) $el.html(sap_unexecuted_html()); });
    this.$body.find(".wms-mon-obd-detail, .wms-mon-stock-detail").empty();
    Object.values(this.selections || {}).forEach((p) => p.then((sel) => { sel.lastRows = null; }));
    if (this.repack.roots.length || this.repack.selected) {
      this.repack = { roots: [], expanded: new Set(), childrenOf: {}, selected: null, detail: null, target: null, hu_types: this.repack.hu_types, newDest: null, _drag: null, selectedRows: new Map() };
      const $tree = this.$body.find(".wms-repack-tree");
      if ($tree.length) $tree.html(sap_unexecuted_html());
      this.$body.find(".wms-repack-detail").empty();
    }
  }

  load_view(view) {
    const loaders = {
      overview: () => this.load_overview(),
      inbound: () => this.load_inbound(),
      outbound: () => this.load_outbound(),
      stock: () => this.load_stock_overview(),
      tasks: () => this.load_tasks(),
      warehouse_orders: () => this.load_warehouse_orders(),
      hu: () => this.load_handling_units(),
      packing: () => this.load_packing_station(),
      repack: () => this.load_repack_center(),
      movements: () => this.load_movements(),
      resources: () => this.load_resources(),
      differences: () => this.load_differences(),
      kpis: () => this.load_kpis(),
      slotting: () => this.load_slotting(),
      bin_assignment: () => this.load_bin_assignment(),
      kitting: () => this.load_kitting(),
      yard: () => this.load_yard(),
      billing: () => this.load_billing(),
      alerts: () => this.load_alerts(),
    };
    (loaders[view] || (() => {}))();
  }

  body_for(view) { return this.$body.find(`.wms-mon-view-body[data-view-body="${view}"]`); }

  // SAP selection screen (public/js/wms_selection.js) for one monitor view: multiple selection
  // with include/exclude and operators, pasted lists, any field, saved variants and layouts.
  selection(view, $mount, $results, decorate) {
    this.selections = this.selections || {};
    if (!this.selections[view]) {
      $mount.html(`<div class="text-muted">${__("Loading selection screen…")}</div>`);
      this.selections[view] = new wms_selection.SelectionScreen({
        view, $mount, $results, decorate: decorate || {},
        autoOpen: () => !this._jumping,
        getWarehouse: () => this.warehouse,
        makeGrid: (rows, columns, opts, doctype) => this.render_table(rows, columns, doctype, opts),
      }).init();
    }
    return this.selections[view];
  }

  async execute_selection(view) {
    if (!this.selections || !this.selections[view]) return;
    const sel = await this.selections[view];
    return sel.execute();
  }

  // Link between views: open `view` and run it for {field: [values]} (no selection popup).
  async jump(view, values) {
    this._jumping = true;
    try {
      this.show_view(view);
      const sel = await this.selections[view];
      await sel.runWith(values);
    } finally { this._jumping = false; }
  }

  // A column renderer linking a Link field to its form (new tab: executed results stay put).
  link_cell(doctype, fieldname) {
    return (row) => row[fieldname]
      ? `<a href="/app/${frappe.router.slug(doctype)}/${encodeURIComponent(row[fieldname])}" target="_blank" rel="noopener">${frappe.utils.escape_html(row[fieldname])}</a>`
      : "";
  }

  render_cards($container, cards) {
    $container.empty();
    cards.forEach((card) => {
      const $card = $(`
        <div class="wms-mon-card ${card.route ? "clickable" : ""}">
          <div class="wms-mon-card-value">${card.value}</div>
          <div class="wms-mon-card-label">${frappe.utils.escape_html(card.label)}</div>
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
    if (!$wrap.find(".wms-mon-ind-sel").length) {
      $wrap.html(`<div class="wms-mon-ind-sel"></div><div class="wms-mon-ind-table">${sap_unexecuted_html()}</div>`);
      this.selection("inbound", $wrap.find(".wms-mon-ind-sel"), $wrap.find(".wms-mon-ind-table"));
    }
  }

  search_inbound_deliveries() { return this.execute_selection("inbound"); }

  // ---------- Outbound Monitor ----------
  async load_outbound() {
    const $wrap = this.body_for("outbound");
    if (!$wrap.find(".wms-mon-obd-sel").length) {
      $wrap.html(`
        <div class="wms-mon-obd-sel"></div>
        <div class="wms-mon-obd-table" style="margin-bottom:12px;">${sap_unexecuted_html()}</div>
        <div class="wms-mon-obd-detail" style="margin-bottom:24px;"></div>
        <h5>${__("Waves")}</h5>
        <div class="wms-mon-wave-sel"></div>
        <div class="wms-mon-wave-table">${sap_unexecuted_html()}</div>
      `);
      this.selection("outbound", $wrap.find(".wms-mon-obd-sel"), $wrap.find(".wms-mon-obd-table"), {
        renderers: {
          name: (row) => `<a href="/app/outbound-delivery/${encodeURIComponent(row.name)}">${frappe.utils.escape_html(row.name)}</a>
            <button type="button" class="btn btn-xs btn-default wms-mon-obd-view" data-delivery="${frappe.utils.escape_html(row.name)}">${__("View")}</button>`,
        },
        // Nothing anywhere - not the RF app, not this page until now - could create a WMS
        // Shipment at all: confirm_hu_loaded/depart_shipment only ever LIST and ACT ON one that
        // already exists (#/load), and post_goods_issue itself requires the HU's status to be
        // "Loaded" (only a Shipment ever sets that) - so a picked delivery had no reachable path
        // to Goods Issue whatsoever without this. A desk/supervisor action, same spirit as the
        // Waves "Release" button just below: pick the deliveries for one truck run, create the
        // Shipment, then an RF Loader takes it from #/load.
        actions: [{
          label: __("Create Shipment"), kind: "primary",
          appliesTo: (row) => row.picking_status === "Picked" && row.loading_status !== "Loaded" && row.goods_issue_status !== "Posted",
          run: async (rows) => {
            try {
              const shipment = await frappe.call("frappe_wms.api.shipping.create_shipment", {
                warehouse: this.warehouse, outbound_deliveries: JSON.stringify(rows.map((r) => r.name)),
              }).then((r) => r.message);
              frappe.show_alert({ message: __("Shipment {0} created for {1} delivery(ies)", [shipment, rows.length]), indicator: "green" });
              this.search_outbound_deliveries();
            } catch (e) { /* frappe already shows the server error */ }
          },
        }],
        afterRender: ($res) => $res.find(".wms-mon-obd-view").on("click", (e) => this.load_delivery_detail(e.currentTarget.dataset.delivery)),
      });
      this.selection("waves", $wrap.find(".wms-mon-wave-sel"), $wrap.find(".wms-mon-wave-table"), {
        extraColumns: [["_release", __("Release"), (row) => row.status === "Draft"
          ? `<button type="button" class="btn btn-xs btn-primary wms-mon-release-wave" data-wave="${frappe.utils.escape_html(row.name)}">${__("Release")}</button>` : ""]],
        afterRender: ($res) => $res.find(".wms-mon-release-wave").on("click", (e) => {
          const wave = e.currentTarget.dataset.wave;
          frappe.confirm(__("Release wave {0}? This allocates and creates pick tasks for every delivery in it.", [wave]), () => {
            frappe.call("frappe_wms.api.outbound.release_wave", { wave_name: wave }).then(() => {
              frappe.show_alert({ message: __("Wave released"), indicator: "green" });
              this.search_waves();
            });
          });
        }),
      });
    }
  }

  search_outbound_deliveries() { return this.execute_selection("outbound"); }

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

    // picking_status "Not Relevant" means exactly this: no Pick Task/Warehouse Order above ever
    // existed for (some or all of) this delivery because it's being fulfilled straight off a
    // receipt instead - this section is where that work actually lives, so "no Pick Tasks" above
    // doesn't read as "nothing is happening".
    if (status.cross_dock_requests.length) {
      $wrap.append(`<h6 style="margin-top:10px;">${__("Cross Dock (fulfilled from receiving, not picked)")}</h6>`);
      $wrap.append(this.render_table(status.cross_dock_requests, [
        ["name", __("Request")], ["status", __("Status")], ["product", __("Product")],
        ["requested_quantity", __("Quantity")], ["stock_uom", __("UOM")],
        ["source_bin", __("Source")], ["destination_bin", __("Destination")],
      ], "Warehouse Request"));
      if (status.cross_dock_tasks.length) {
        $wrap.append(this.render_table(status.cross_dock_tasks, [
          ["name", __("Task")], ["status", __("Status")], ["planned_quantity", __("Planned")],
          ["confirmed_quantity", __("Confirmed")], ["assigned_resource", __("Resource")],
        ], "Warehouse Task"));
        const crossDockWos = Array.from(new Set(status.cross_dock_tasks.map((t) => t.warehouse_order).filter(Boolean)));
        if (crossDockWos.length) {
          $wrap.append(`<div><b>${__("Cross Dock Warehouse Orders")}:</b> ${crossDockWos.map((wo) =>
            `<a href="/app/warehouse-order/${encodeURIComponent(wo)}">${frappe.utils.escape_html(wo)}</a>`).join(", ")}</div>`);
        }
      }
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

  search_waves() { return this.execute_selection("waves"); }

  // ---------- Stock Overview ----------
  async load_stock_overview() {
    const $wrap = this.body_for("stock");
    if (!$wrap.find(".wms-mon-stock-sel").length) {
      $wrap.html(`
        <div class="wms-mon-stock-summary"></div>
        <div class="wms-mon-stock-sel"></div>
        <div class="wms-mon-stock-table">${sap_unexecuted_html()}</div>
        <div class="wms-mon-stock-detail"></div>
      `);
      this.selection("stock", $wrap.find(".wms-mon-stock-sel"), $wrap.find(".wms-mon-stock-table"), this.stock_decorate());
    }
    const summary = await frappe.call("frappe_wms.api.monitor.stock_overview_summary", { warehouse: this.warehouse }).then((r) => r.message || []);
    this.render_cards($wrap.find(".wms-mon-stock-summary"), summary.map((row) => ({
      label: __("{0} ({1} rows)", [row.stock_type || __("(no stock type)"), row.balance_rows]),
      value: `${flt(row.quantity)} / ${flt(row.available_quantity)} ${__("avail")}`,
    })));
  }

  // One serial-numbered product is one balance row PER serial, so the raw list is mostly the same
  // product over and over. Here the hits are grouped by whatever the user ticks (storage type /
  // bin / HU / product / batch / stock type), optionally with allocated and free stock split into
  // their own lines, and "Details" opens the underlying lines (serial numbers...) of the marked rows.
  // ponytail: grouped client-side over the loaded hits (Max. hits); move to SQL if hits get huge.
  stock_decorate() {
    const DIMS = [
      ["storage_type", __("Storage Type")], ["storage_bin", __("Storage Bin")], ["handling_unit", __("Handling Unit")],
      ["product", __("Product")], ["batch_no", __("Batch")], ["stock_type", __("Stock Type")],
    ];
    const g = this.stock_group = this.stock_group || { dims: ["storage_type", "storage_bin", "product"], split: true };
    const pill = (v) => v ? wms_selection.pill(__(v), v === "Allocated" ? "blue" : "green") : "";
    const docs = (row) => (row.documents || "").split(", ").filter(Boolean).map((d) =>
      `<a href="/app/outbound-delivery/${encodeURIComponent(d)}" target="_blank" rel="noopener">${frappe.utils.escape_html(d)}</a>`).join(", ");
    const R = {
      storage_bin: this.link_cell("Storage Bin", "storage_bin"), product: this.link_cell("Item", "product"),
      handling_unit: this.hu_link_cell("handling_unit"), alloc: (row) => pill(row.alloc), documents: docs,
      batch_no: this.link_cell("Batch", "batch_no"), serial_no: this.link_cell("Serial No", "serial_no"),
    };
    const label = Object.fromEntries(DIMS);
    const split = (rows) => rows.flatMap((r) => {
      const q = flt(r.quantity), a = Math.min(flt(r.allocated_quantity), q), out = [];
      if (a > 0) out.push({ ...r, quantity: a, allocated_quantity: a, available_quantity: 0, alloc: "Allocated" });
      if (q - a > 1e-9) out.push({ ...r, quantity: q - a, allocated_quantity: 0, available_quantity: q - a, documents: "", alloc: "Free" });
      return out;
    });
    const group = (lines) => {
      const map = new Map();
      for (const r of lines) {
        const k = g.dims.map((d) => r[d] || "").concat(r.alloc || "").join("\u0001");
        let o = map.get(k);
        if (!o) {
          o = { alloc: r.alloc, lines: 0, quantity: 0, allocated_quantity: 0, available_quantity: 0, stock_uom: r.stock_uom,
            last_movement_date: "", _lines: [], _docs: new Set(), _serials: new Set() };
          g.dims.forEach((d) => { o[d] = r[d]; });
          map.set(k, o);
        }
        o.lines++; o._lines.push(r);
        ["quantity", "allocated_quantity", "available_quantity"].forEach((f) => { o[f] += flt(r[f]); });
        (r.documents || "").split(", ").filter(Boolean).forEach((d) => o._docs.add(d));
        if (r.serial_no) o._serials.add(r.serial_no);
        if ((r.last_movement_date || "") > o.last_movement_date) o.last_movement_date = r.last_movement_date;
      }
      const rows = Array.from(map.values());
      rows.forEach((o) => { o.serials = o._serials.size; o.documents = Array.from(o._docs).sort().join(", "); });
      const key = (o) => g.dims.map((d) => o[d] || "").join("\u0001");
      return rows.sort((a, b) => key(a).localeCompare(key(b), undefined, { numeric: true }) || String(a.alloc).localeCompare(String(b.alloc)));
    };
    return {
      toolbar: (sel) => {
        const $t = $(`<div class="wms-stock-group">
          <span class="text-muted">${__("Group by")}</span>
          ${DIMS.map(([f, l]) => `<label><input type="checkbox" data-dim="${f}" ${g.dims.includes(f) ? "checked" : ""}> ${l}</label>`).join("")}
          <span class="wms-stock-group-sep"></span>
          <label><input type="checkbox" class="wms-stock-split" ${g.split ? "checked" : ""}> ${__("Split allocated / free")}</label>
        </div>`);
        $t.on("change", "input", () => {
          g.dims = DIMS.map(([f]) => f).filter((f) => $t.find(`[data-dim="${f}"]`).prop("checked"));
          g.split = $t.find(".wms-stock-split").prop("checked");
          sel.drawResults();
        });
        return $t;
      },
      transform: (rows, columns, numeric) => {
        const lines = (g.split ? split(rows) : rows.map((r) => ({ ...r, alloc: flt(r.allocated_quantity) > 0 ? "Allocated" : "Free" }))).map((r) => ({ ...r, _lines: [r] }));
        const actions = this.stock_actions();
        const num = ["quantity", "allocated_quantity", "available_quantity"];
        if (!g.dims.length) {
          const cols = columns.filter(([f]) => f !== "name").concat([["storage_type", __("Storage Type")]]);
          if (g.split) cols.push(["alloc", __("Allocation"), R.alloc]);
          cols.push(["documents", __("Document"), R.documents]);
          return { rows: lines, columns: cols.map(([f, l, fn]) => [f, l, R[f] || fn]), numeric: num, actions };
        }
        const cols = g.dims.map((d) => [d, label[d], R[d]]);
        if (g.split) cols.push(["alloc", __("Allocation"), R.alloc]);
        cols.push(["lines", __("Lines")], ["serials", __("Serial Nos")], ["quantity", __("Quantity")], ["allocated_quantity", __("Allocated")],
          ["available_quantity", __("Available")], ["stock_uom", __("UoM")], ["documents", __("Document"), R.documents], ["last_movement_date", __("Last Movement")]);
        return { rows: group(lines), columns: cols, numeric: ["lines", "serials"].concat(num), actions };
      },
    };
  }

  stock_actions() {
    return [
      { label: __("Details"), kind: "primary", run: (rows) => this.show_stock_details(rows) },
      { label: __("Movements"), run: (rows) => {
        const uniq = (f) => Array.from(new Set(rows.flatMap((r) => r._lines.map((l) => l[f])).filter(Boolean)));
        return this.jump("movements", { product: uniq("product"), storage_bin: uniq("storage_bin"), handling_unit: uniq("handling_unit") });
      } },
    ];
  }

  // The panel under the grid for the marked rows: every underlying balance line (serial numbers,
  // batches, which HU / bin / document), plus the last postings when the lines are one position.
  async show_stock_details(rows) {
    const lines = rows.flatMap((r) => r._lines);
    const $d = this.body_for("stock").find(".wms-mon-stock-detail").empty();
    const serials = Array.from(new Set(lines.map((l) => l.serial_no).filter(Boolean)));
    const sum = (f) => Math.round(lines.reduce((a, l) => a + flt(l[f]), 0) * 1e6) / 1e6;
    const esc = frappe.utils.escape_html;
    const $panel = $(`<div class="wms-detail-panel">
      <div class="wms-detail-head"><b>${__("Details")}</b>
        <span class="text-muted">${__("{0} line(s)", [lines.length])} &middot; ${__("{0} serial no(s)", [serials.length])} &middot; ${__("quantity {0}", [sum("quantity")])} &middot; ${__("allocated {0}", [sum("allocated_quantity")])}</span>
        <button type="button" class="btn btn-default btn-xs wms-detail-close">&times;</button></div>
      ${serials.length ? `<div class="wms-detail-serials"><span class="text-muted">${__("Serial Nos")}</span>
        ${serials.slice(0, 300).map((x) => `<span class="wms-chip">${esc(x)}</span>`).join("")}${serials.length > 300 ? `<span class="text-muted">+${serials.length - 300}</span>` : ""}</div>` : ""}
      <div class="wms-detail-lines"></div><div class="wms-detail-history"></div></div>`).appendTo($d);
    $panel.find(".wms-detail-close").on("click", () => $d.empty());
    if (serials.length) $panel.find(".wms-detail-serials").append(this.copy_btn(serials.join("\n")));
    const cols = [["product", __("Product"), this.link_cell("Item", "product")], ["serial_no", __("Serial No"), this.link_cell("Serial No", "serial_no")],
      ["batch_no", __("Batch"), this.link_cell("Batch", "batch_no")], ["handling_unit", __("Handling Unit"), this.hu_link_cell("handling_unit")],
      ["storage_bin", __("Storage Bin"), this.link_cell("Storage Bin", "storage_bin")], ["storage_type", __("Storage Type")], ["stock_type", __("Stock Type")],
      ["alloc", __("Allocation"), (r) => wms_selection.pill(__(r.alloc), r.alloc === "Allocated" ? "blue" : "green")],
      ["quantity", __("Quantity")], ["allocated_quantity", __("Allocated")],
      ["documents", __("Document"), (r) => (r.documents || "").split(", ").filter(Boolean).map((x) =>
        `<a href="/app/outbound-delivery/${encodeURIComponent(x)}" target="_blank" rel="noopener">${esc(x)}</a>`).join(", ")],
      ["shelf_life_expiry_date", __("Expiry")], ["last_movement_date", __("Last Movement")]];
    $panel.find(".wms-detail-lines").append(this.render_table(lines, cols, null, { numeric: ["quantity", "allocated_quantity"], exportName: "stock-details" }));
    $panel[0].scrollIntoView({ behavior: "smooth", block: "nearest" });
    const same = (f) => new Set(lines.map((l) => l[f] || "")).size === 1;
    if (["product", "stock_type", "storage_bin", "handling_unit", "batch_no"].every(same)) {
      const l = lines[0];
      const hist = await frappe.call("frappe_wms.api.monitor.stock_line_history", { product: l.product, stock_type: l.stock_type,
        storage_bin: l.storage_bin || undefined, handling_unit: l.handling_unit || undefined, batch_no: l.batch_no || undefined, limit: 15 }).then((r) => r.message || []);
      if (hist.length) $panel.find(".wms-detail-history").append(`<h6 style="margin:12px 0 4px;">${__("Last postings")}</h6>`).append(this.render_table(hist, [
        ["posting_datetime", __("Posted")], ["movement_type", __("Movement")], ["quantity", __("Quantity")], ["reference_name", __("Document")],
        ["warehouse_task", __("Task"), this.link_cell("Warehouse Task", "warehouse_task")], ["posting_user", __("User")]], null, { numeric: ["quantity"] }));
    }
  }

  search_stock_overview() { return this.execute_selection("stock"); }

  // ---------- Warehouse Tasks ----------
  async load_tasks() {
    const $wrap = this.body_for("tasks");
    if (!$wrap.find(".wms-mon-task-sel").length) {
      $wrap.html(`<div class="wms-mon-task-sel"></div><div class="wms-mon-task-table">${sap_unexecuted_html()}</div>`);
      this.selection("tasks", $wrap.find(".wms-mon-task-sel"), $wrap.find(".wms-mon-task-table"), {
        renderers: { source_hu: this.hu_link_cell("source_hu"), destination_hu: this.hu_link_cell("destination_hu") },
        actions: this.task_quick_actions(),
      });
    }
  }

  search_tasks() { return this.execute_selection("tasks"); }

  // Quick actions for the Warehouse Tasks grid - SAP EWM Monitor-style: act on whatever is
  // currently selected without opening each task. Each server call runs one row at a time
  // (never Promise.all) so one failure doesn't silently swallow the rest, and the final tally
  // reflects exactly how many actually succeeded.
  task_quick_actions() {
    const TERMINAL = ["Confirmed", "Cancelled"];
    return [
      {
        label: __("Raise Exception"), kind: "danger",
        appliesTo: (row) => !TERMINAL.includes(row.status) && row.status !== "Exception",
        run: async (rows) => {
          const codes = await frappe.call("frappe_wms.api.scanner.list_exception_codes", {}).then((r) => r.message || []);
          if (!codes.length) { frappe.show_alert({ message: __("No active Exception Codes configured"), indicator: "orange" }); return; }
          const values = await new Promise((resolve) => frappe.prompt([
            { fieldname: "exception_code", label: __("Exception Code"), fieldtype: "Select", reqd: 1,
              options: codes.map((c) => ({ value: c.name, label: c.exception_name })) },
            { fieldname: "remarks", label: __("Remarks"), fieldtype: "Small Text" },
          ], (v) => resolve(v), __("Raise Exception on {0} task(s)", [rows.length])));
          if (!values) return;
          let ok = 0;
          for (const row of rows) {
            try { await frappe.call("frappe_wms.api.scanner.raise_exception", { task_name: row.name, exception_code: values.exception_code, remarks: values.remarks || undefined }); ok++; }
            catch (e) { /* frappe already shows the server error */ }
          }
          frappe.show_alert({ message: __("Raised exception on {0} of {1} task(s)", [ok, rows.length]), indicator: ok === rows.length ? "green" : "orange" });
          this.search_tasks();
        },
      },
      {
        label: __("Reverse"), kind: "danger",
        appliesTo: (row) => row.status === "Confirmed",
        confirm: (rows) => __("Reverse {0} confirmed task(s)? This posts a compensating move back to source for each.", [rows.length]),
        run: async (rows) => {
          let ok = 0;
          for (const row of rows) {
            try { await frappe.call("frappe_wms.api.scanner.reverse_task", { task_name: row.name }); ok++; }
            catch (e) { /* frappe already shows the server error */ }
          }
          frappe.show_alert({ message: __("Reversed {0} of {1} task(s)", [ok, rows.length]), indicator: ok === rows.length ? "green" : "orange" });
          this.search_tasks();
        },
      },
      // Deliberately no "Unassign" here: a task's assigned_resource is driven by its parent
      // Warehouse Order (attach_task), and there is no service function that unassigns one
      // while keeping the Warehouse Order and its other tasks consistent - a raw field write
      // from here would silently desync them. Add a real service function first if this is
      // needed.
    ];
  }

  // ---------- Warehouse Orders ----------
  async load_warehouse_orders() {
    const $wrap = this.body_for("warehouse_orders");
    if (!$wrap.find(".wms-mon-wo-sel").length) {
      $wrap.html(`<div class="wms-mon-wo-sel"></div><div class="wms-mon-wo-table">${sap_unexecuted_html()}</div>`);
      this.selection("warehouse_orders", $wrap.find(".wms-mon-wo-sel"), $wrap.find(".wms-mon-wo-table"), {
        actions: this.wo_quick_actions(),
      });
    }
  }

  search_warehouse_orders() { return this.execute_selection("warehouse_orders"); }

  // Hold/Resume for the Warehouse Order Monitor - same bulk idiom as task_quick_actions. No
  // separate drill-down into one WO's tasks here: filter the Warehouse Tasks tab by Warehouse
  // Order instead, which is already a selectable field there.
  wo_quick_actions() {
    const TERMINAL = ["Completed", "Cancelled"];
    return [
      {
        label: __("Put On Hold"), kind: "danger",
        appliesTo: (row) => !TERMINAL.includes(row.status) && row.status !== "On Hold",
        run: async (rows) => {
          const values = await new Promise((resolve) => frappe.prompt(
            [{ fieldname: "reason", label: __("Reason"), fieldtype: "Data" }],
            (v) => resolve(v), __("Put {0} Warehouse Order(s) On Hold", [rows.length]),
          ));
          if (!values) return;
          let ok = 0;
          for (const row of rows) {
            try { await frappe.call("frappe_wms.api.warehouse_order.block_warehouse_order", { wo_name: row.name, reason: values.reason || undefined }); ok++; }
            catch (e) { /* frappe already shows the server error */ }
          }
          frappe.show_alert({ message: __("Put {0} of {1} on hold", [ok, rows.length]), indicator: ok === rows.length ? "green" : "orange" });
          this.search_warehouse_orders();
        },
      },
      {
        label: __("Resume"),
        appliesTo: (row) => row.status === "On Hold",
        run: async (rows) => {
          let ok = 0;
          for (const row of rows) {
            try { await frappe.call("frappe_wms.api.warehouse_order.resume_warehouse_order", { wo_name: row.name }); ok++; }
            catch (e) { /* frappe already shows the server error */ }
          }
          frappe.show_alert({ message: __("Resumed {0} of {1}", [ok, rows.length]), indicator: ok === rows.length ? "green" : "orange" });
          this.search_warehouse_orders();
        },
      },
    ];
  }

  // ---------- Handling Units ----------
  async load_handling_units() {
    const $wrap = this.body_for("hu");
    if (!$wrap.find(".wms-mon-hu-sel").length) {
      $wrap.html(`
        <div class="wms-mon-hu-sel"></div>
        <div class="wms-mon-hu-hint text-muted" style="margin-bottom:6px;font-size:12px;">${__("Click an HU to open its full repack detail: nesting, contents and serials, with copy buttons.")}</div>
        <div class="wms-mon-hu-table">${sap_unexecuted_html()}</div>
      `);
      this.selection("hu", $wrap.find(".wms-mon-hu-sel"), $wrap.find(".wms-mon-hu-table"), {
        renderers: {
          name: (row) => `<a href="#" class="wms-hu-open" data-hu="${frappe.utils.escape_html(row.name)}">${frappe.utils.escape_html(row.name)}</a>`,
          parent_hu: this.hu_link_cell("parent_hu"), top_hu: this.hu_link_cell("top_hu"),
        },
        actions: this.hu_quick_actions(),
      });
    }
  }

  search_handling_units() { return this.execute_selection("hu"); }

  hu_quick_actions() {
    return [
      {
        label: __("Block"), kind: "danger",
        appliesTo: (row) => row.status !== "Blocked",
        run: async (rows) => {
          const values = await new Promise((resolve) => frappe.prompt(
            [{ fieldname: "remarks", label: __("Reason"), fieldtype: "Small Text" }],
            (v) => resolve(v), __("Block {0} Handling Unit(s)", [rows.length]),
          ));
          if (!values) return;
          let ok = 0;
          for (const row of rows) {
            try { await frappe.call("frappe_wms.api.handling_unit.block_handling_unit", { hu_name: row.name, remarks: values.remarks || undefined }); ok++; }
            catch (e) { /* frappe already shows the server error */ }
          }
          frappe.show_alert({ message: __("Blocked {0} of {1} Handling Unit(s)", [ok, rows.length]), indicator: ok === rows.length ? "green" : "orange" });
          this.search_handling_units();
        },
      },
      {
        label: __("Unblock"),
        appliesTo: (row) => row.status === "Blocked",
        run: async (rows) => {
          let ok = 0;
          for (const row of rows) {
            try { await frappe.call("frappe_wms.api.handling_unit.unblock_handling_unit", { hu_name: row.name }); ok++; }
            catch (e) { /* frappe already shows the server error */ }
          }
          frappe.show_alert({ message: __("Unblocked {0} of {1} Handling Unit(s)", [ok, rows.length]), indicator: ok === rows.length ? "green" : "orange" });
          this.search_handling_units();
        },
      },
      {
        label: __("Recycle"), kind: "danger",
        appliesTo: (row) => row.stock_status === "Empty" && !row.parent_hu,
        confirm: (rows) => __("Recycle {0} Handling Unit(s)? Frees their numbers for reuse; cannot be undone.", [rows.length]),
        run: async (rows) => {
          let ok = 0;
          for (const row of rows) {
            try { await frappe.call("frappe_wms.api.handling_unit.recycle_handling_unit", { hu_name: row.name }); ok++; }
            catch (e) { /* frappe already shows the server error */ }
          }
          frappe.show_alert({ message: __("Recycled {0} of {1} Handling Unit(s)", [ok, rows.length]), indicator: ok === rows.length ? "green" : "orange" });
          this.search_handling_units();
        },
      },
    ];
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

  // A grid column renderFn that turns an HU-reference field (e.g. a Warehouse Task's source_hu)
  // into a link opening the same read-only nesting viewer Repack Center uses - a view-only look
  // at where that HU is and what's in it, without leaving the current tab or risking an edit.
  hu_link_cell(fieldname) {
    return (row) => row[fieldname]
      ? `<a href="#" class="wms-open-hu-viewer" data-hu="${frappe.utils.escape_html(row[fieldname])}">${frappe.utils.escape_html(row[fieldname])}</a>`
      : "";
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

  // ---------- Repack Center: the SAP EWM packing work center /SCWM/PACK (public/js/wms_packing_station.js) ----------
  async load_packing_station() {
    const $wrap = this.body_for("packing");
    if (!this.packing_station) this.packing_station = new WMSPackingStation($wrap, () => this.warehouse);
    if (this.packing_station_wh !== this.warehouse) {
      this.packing_station_wh = this.warehouse;
      await this.packing_station.render();
    } else if (this.packing_station.wc) {
      await this.packing_station.load();
    }
  }

  // ---------- Repack Center: modeled on SAP EWM's /SCWM/PACK repacking workstation - one
  // navigation tree on the left with Storage Bins, Handling Units AND their individual stock
  // lines all as rows you can select or drag directly; a tabbed detail panel on the right shows
  // the SAME data as the selected row's children (Contents/Unpacked Products, Details/Info,
  // Destination HUs) the way SAP's own tab set does, plus partial-quantity dragging. A pinned
  // 🎯 target plus "Repack All" mirrors moving everything at once. ----------
  async load_repack_center() {
    const $wrap = this.body_for("repack");
    if (!$wrap.find(".wms-repack-filters").length) {
      ensure_repack_styles();
      $wrap.html(`
        <div class="wms-repack-filters form-inline" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:6px;">
          <input class="form-control input-sm wms-repack-search-number" placeholder="${__("HU Number")}" style="width:140px;">
          <input class="form-control input-sm wms-repack-search-type" placeholder="${__("HU Type")}" style="width:140px;">
          <input class="form-control input-sm wms-repack-search-bin" placeholder="${__("Storage Bin")}" style="width:140px;">
          <input class="form-control input-sm wms-repack-search-storagetype" placeholder="${__("Storage Type")}" style="width:140px;">
          <input class="form-control input-sm wms-repack-search-workcenter" placeholder="${__("Work Center")}" style="width:140px;">
          <button class="btn btn-primary btn-sm wms-repack-search">${__("Execute")}</button>
        </div>
        ${sap_search_hint()}
        <div class="text-muted" style="margin-bottom:8px;font-size:12px;">${__("The tree shows bins, Handling Units and their stock lines - click any row to see it on the right, drag any row onto another to repack it. Pin a 🎯 target, then Repack All moves everything from the selected row at once.")}</div>
        <div style="display:flex;gap:16px;align-items:flex-start;">
          <div style="flex:0 0 380px;min-width:0;">
            <div style="display:flex;gap:6px;margin-bottom:6px;">
              <button type="button" class="btn btn-default btn-xs wms-repack-expand-all">${__("Expand All")}</button>
              <button type="button" class="btn btn-default btn-xs wms-repack-collapse-all">${__("Collapse All")}</button>
            </div>
            <div class="wms-repack-tree" style="max-height:65vh;overflow:auto;border:1px solid var(--border-color);border-radius:6px;padding:8px;">${sap_unexecuted_html()}</div>
          </div>
          <div class="wms-repack-detail" style="flex:1 1 480px;max-width:560px;min-width:0;max-height:65vh;overflow:auto;border:1px solid var(--border-color);border-radius:6px;padding:10px;"></div>
        </div>
      `);
      $wrap.find(".wms-repack-search").on("click", () => this.search_repack_center());
      $wrap.find(".wms-repack-expand-all").on("click", () => this.repack_expand_all());
      $wrap.find(".wms-repack-collapse-all").on("click", () => this.repack_collapse_all());
      this.ensure_hu_types();
    }
  }

  async ensure_hu_types() {
    if (!this.repack.hu_types) this.repack.hu_types = await frappe.db.get_list("Handling Unit Type", { fields: ["name", "numbering_mode"], limit_page_length: 50 });
    return this.repack.hu_types;
  }

  repack_key(kind, name) { return JSON.stringify([kind, name]); }
  item_key(row) { return JSON.stringify(["item", row.parentKind, row.parentName, row.product, row.batch_no || "", row.serial_no || "", row.stock_type]); }

  fetch_repack_overview(kind, name) {
    return kind === "hu"
      ? frappe.call("frappe_wms.api.scanner.hu_overview", { hu_number: name }).then((r) => r.message)
      : frappe.call("frappe_wms.api.scanner.bin_overview", { bin_code: name }).then((r) => r.message);
  }

  // Where a node's contents physically are right now: a bin, and an HU if it's inside one.
  repack_locator(node) {
    return node.kind === "hu"
      ? { bin: node.overview.handling_unit.current_bin, hu: node.overview.handling_unit.name }
      : { bin: node.overview.storage_bin.name, hu: null };
  }

  // Normalizes an HU's or a Bin's contents into one row shape. `nested` marks whether an HU
  // row is already nested under something (needs unnesting before renesting) - an HU's
  // children always are (that's how they got listed); a bin's top-level HUs never are.
  repack_rows(node) {
    if (node.kind === "hu") {
      return [
        ...node.overview.children.map((c) => ({ kind: "hu", nested: true, ...c })),
        ...node.overview.stock.map((s) => ({ kind: "item", ...s })),
      ];
    }
    return [
      ...node.overview.handling_units.map((h) => ({ kind: "hu", nested: false, ...h })),
      ...node.overview.stock.filter((s) => !s.handling_unit).map((s) => ({ kind: "item", ...s })),
    ];
  }

  async search_repack_center() {
    if (!this.warehouse) return;
    const $wrap = this.body_for("repack");
    const hu_number = $wrap.find(".wms-repack-search-number").val() || undefined;
    const hu_type = $wrap.find(".wms-repack-search-type").val() || undefined;
    const bin_code = $wrap.find(".wms-repack-search-bin").val() || undefined;
    const storage_type = $wrap.find(".wms-repack-search-storagetype").val() || undefined;
    const work_center = $wrap.find(".wms-repack-search-workcenter").val() || undefined;
    // Only search the object type the criteria actually asks about - HU Number/Type alone means
    // "find this HU", not "and also list every bin in the warehouse"; Storage Bin/Type/Work
    // Center alone means "find this bin", not "and also list unrelated HUs". Blank criteria
    // (Execute with nothing filled in) still searches both, same as a blank SAP selection screen.
    const wantsHu = !!(hu_number || hu_type);
    const wantsBin = !!(bin_code || storage_type || work_center);
    const searchHu = wantsHu || !wantsBin;
    const searchBin = wantsBin || !wantsHu;
    const huArgs = { warehouse: this.warehouse, current_bin: bin_code, storage_type, work_center, hu_number, hu_type, limit: 50 };
    const binArgs = { warehouse: this.warehouse, bin_code, storage_type, work_center, limit: 50 };
    const [hus, bins] = await Promise.all([
      searchHu ? frappe.call("frappe_wms.api.monitor.search_handling_units", huArgs).then((r) => r.message || []) : [],
      searchBin ? frappe.call("frappe_wms.api.monitor.search_bins", binArgs).then((r) => r.message || []) : [],
    ]);
    // Everything lives under one Warehouse root, the way SAP's own tree is organized top-down
    // (warehouse -> bin -> HU -> nested HU...) rather than a flat mix of unrelated top-level
    // rows. A matched HU - nested or not - is always shown inside its actual bin: current_bin is
    // correct at any nesting depth, so even a several-levels-deep match resolves to the right bin.
    const wk = this.repack_key("warehouse", this.warehouse);
    this.repack.roots = [{ kind: "warehouse", name: this.warehouse, meta: "" }];
    this.repack.expanded = new Set([wk]);
    this.repack.childrenOf = {};
    this.repack.selected = null;
    this.repack.detail = null;
    this.repack.target = null;
    this.repack.selectedRows = new Map();
    const binMeta = {};
    bins.forEach((b) => { binMeta[b.name] = b.storage_type || ""; });
    hus.forEach((h) => { if (h.current_bin && !(h.current_bin in binMeta)) binMeta[h.current_bin] = ""; });
    this.repack.childrenOf[wk] = Object.keys(binMeta).sort().map((name) => ({ kind: "bin", name, meta: binMeta[name] }));
    for (const h of hus) await this.reveal_repack_hu(h.name);
    if (hus.length === 1) await this.select_repack_node("hu", hus[0].name);
    else if (!hus.length && bins.length === 1) await this.select_repack_node("bin", bins[0].name);
    this.render_repack_tree();
    this.render_repack_detail();
  }

  async ensure_repack_expanded(kind, name) {
    const k = this.repack_key(kind, name);
    this.repack.expanded.add(k);
    if (kind !== "warehouse" && !this.repack.childrenOf[k]) await this.load_repack_children(kind, name);
  }

  // Walks every bin/HU currently reachable from the roots, expanding (and fetching, where not
  // already loaded) each one in turn - deep nesting means this can take a moment on a large
  // result, but it's a deliberate one-shot action, not something that runs on every render.
  async repack_expand_all() {
    const walk = async (nodes) => {
      for (const n of nodes) {
        if (n.kind === "item") continue;
        await this.ensure_repack_expanded(n.kind, n.name);
        await walk(this.repack.childrenOf[this.repack_key(n.kind, n.name)] || []);
      }
    };
    await walk(this.repack.roots);
    this.render_repack_tree();
  }

  // Back to just the Warehouse root open - everything else (bins, HUs, nested HUs) collapses,
  // without discarding any already-fetched children so re-expanding stays instant.
  repack_collapse_all() {
    this.repack.expanded = new Set([this.repack_key("warehouse", this.warehouse)]);
    this.render_repack_tree();
  }

  // Reveals a nested search hit in place: adds its bin as a root if not already one, then
  // expands every ancestor along the way so the matched HU shows up as a visible tree row.
  async reveal_repack_hu(hu_name) {
    const { bin, chain } = await frappe.call("frappe_wms.api.monitor.hu_ancestor_chain", { hu_name }).then((r) => r.message);
    if (!bin || !chain.length) return;
    let curKind = "bin", curName = bin;
    for (const link of chain) {
      await this.ensure_repack_expanded(curKind, curName);
      curKind = "hu"; curName = link;
    }
  }

  // Children of a tree node are BOTH nested Handling Units and its own stock lines - each
  // stock line becomes its own leaf row (with the info SAP shows per line: product, batch,
  // serial, stock type, quantity), carrying its own bin/HU location so it can be dragged
  // straight from the tree without needing its parent selected first.
  async load_repack_children(kind, name) {
    const overview = await this.fetch_repack_overview(kind, name);
    const loc = this.repack_locator({ kind, overview });
    const children = this.repack_rows({ kind, overview }).map((r) => r.kind === "hu"
      ? { kind: "hu", name: r.name, nested: r.nested, meta: `${r.hu_type || ""} · ${loc.bin || "-"} · ${r.status || ""}${r.stock_status ? " · " + r.stock_status : ""}` }
      : { kind: "item", ...r, srcBin: loc.bin, srcHu: loc.hu, parentKind: kind, parentName: name });
    this.repack.childrenOf[this.repack_key(kind, name)] = children;
    if (this.repack.selected && this.repack.selected.kind === kind && this.repack.selected.name === name) this.repack.detail = overview;
    return children;
  }

  async toggle_repack_node(kind, name) {
    const k = this.repack_key(kind, name);
    if (this.repack.expanded.has(k)) { this.repack.expanded.delete(k); this.render_repack_tree(); return; }
    this.repack.expanded.add(k);
    // The Warehouse root's children are the search-result bins, computed directly in
    // search_repack_center - there's no hu_overview/bin_overview equivalent to fetch for it.
    if (kind !== "warehouse" && !this.repack.childrenOf[k]) await this.load_repack_children(kind, name);
    this.render_repack_tree();
  }

  async select_repack_node(kind, name) {
    this.repack.selected = { kind, name };
    this.repack.activeTab = kind === "bin" ? "unpacked" : "contents";
    this.repack.selectedRows = new Map();
    this.repack.detail = await this.fetch_repack_overview(kind, name);
    this.render_repack_tree();
    this.render_repack_detail();
  }

  select_repack_item(row) {
    this.repack.selected = { kind: "item", row };
    this.repack.activeTab = "product";
    this.repack.selectedRows = new Map();
    this.render_repack_tree();
    this.render_repack_detail();
  }

  async refresh_repack_after_move() {
    for (const k of this.repack.expanded) {
      const [kind, name] = JSON.parse(k);
      if (kind === "warehouse") continue;
      await this.load_repack_children(kind, name);
    }
    const sel = this.repack.selected;
    if (sel && sel.kind !== "item") {
      this.repack.detail = await this.fetch_repack_overview(sel.kind, sel.name);
    } else if (sel && sel.kind === "item") {
      // Re-resolve the selected line against its (just refreshed) parent's children - its
      // quantity may have changed, or it may be gone entirely if it was fully moved out.
      const parentKey = this.repack_key(sel.row.parentKind, sel.row.parentName);
      const siblings = this.repack.childrenOf[parentKey] || (await this.load_repack_children(sel.row.parentKind, sel.row.parentName));
      const match = siblings.find((c) => c.kind === "item" && this.item_key(c) === this.item_key(sel.row));
      this.repack.selected = match ? { kind: "item", row: match } : null;
    }
    this.render_repack_tree();
    this.render_repack_detail();
  }

  render_repack_tree() {
    const $tree = this.body_for("repack").find(".wms-repack-tree").empty();
    if (!this.repack.roots.length) { $tree.html(`<div class="text-muted">${__("Execute a search to browse the warehouse.")}</div>`); return; }
    this.repack.roots.forEach((n) => $tree.append(this.render_repack_tree_node(n, 0)));
  }

  // The single root: not draggable, not a drop target, click only toggles - its children are
  // the search-result bins, already computed directly (see search_repack_center).
  render_repack_tree_warehouse_node(node, depth) {
    const k = this.repack_key(node.kind, node.name);
    const expanded = this.repack.expanded.has(k);
    const $wrap = $(`<div></div>`);
    const $row = $(`<div class="wms-repack-tree-row" style="display:flex;align-items:center;gap:6px;padding:3px 4px;margin-left:${depth * 16}px;border-radius:4px;cursor:pointer;font-weight:bold;"></div>`);
    const $toggle = $(`<span style="width:14px;display:inline-block;text-align:center;">${expanded ? "▾" : "▸"}</span>`);
    $row.append($toggle, `<span>🏭</span>`, `<span>${frappe.utils.escape_html(node.name)}</span>`);
    $row.on("click", () => this.toggle_repack_node(node.kind, node.name));
    $wrap.append($row);
    if (expanded) {
      const children = this.repack.childrenOf[k] || [];
      if (!children.length) $wrap.append(`<div class="text-muted" style="margin-left:${(depth + 1) * 16 + 18}px;font-size:11px;">${__("No Handling Units or Storage Bins matched")}</div>`);
      children.forEach((c) => $wrap.append(this.render_repack_tree_node(c, depth + 1)));
    }
    return $wrap;
  }

  render_repack_tree_node(node, depth) {
    if (node.kind === "item") return this.render_repack_tree_item_node(node, depth);
    if (node.kind === "warehouse") return this.render_repack_tree_warehouse_node(node, depth);
    const k = this.repack_key(node.kind, node.name);
    const expanded = this.repack.expanded.has(k);
    const selected = this.repack.selected && this.repack.selected.kind !== "item" && this.repack_key(this.repack.selected.kind, this.repack.selected.name) === k;
    const isTarget = this.repack.target && this.repack_key(this.repack.target.kind, this.repack.target.name) === k;
    const $wrap = $(`<div></div>`);
    const $row = $(`<div class="wms-repack-tree-row${selected ? " wms-repack-tree-selected" : ""}" style="display:flex;align-items:center;gap:6px;padding:3px 4px;margin-left:${depth * 16}px;border-radius:4px;cursor:pointer;"></div>`);
    const $toggle = $(`<span style="width:14px;display:inline-block;text-align:center;">${expanded ? "▾" : "▸"}</span>`);
    $toggle.on("click", (e) => { e.stopPropagation(); this.toggle_repack_node(node.kind, node.name); });
    $row.append($toggle, `<span>${node.kind === "hu" ? "📦" : "🗄"}</span>`, `<span>${frappe.utils.escape_html(node.name)}</span>`);
    if (node.meta) $row.append(`<span class="text-muted" style="font-size:11px;">${frappe.utils.escape_html(node.meta)}</span>`);
    if (isTarget) $row.append(`<span title="${__("Repack All target")}">🎯</span>`);
    const $targetBtn = $(`<a href="#" style="margin-left:auto;font-size:11px;" title="${__("Pin as Repack All target")}">${__("Target")}</a>`);
    $targetBtn.on("click", (e) => { e.preventDefault(); e.stopPropagation(); this.repack.target = { kind: node.kind, name: node.name }; this.render_repack_tree(); this.render_repack_detail(); });
    $row.append($targetBtn);
    $row.on("click", () => this.select_repack_node(node.kind, node.name));
    if (node.kind === "hu") {
      $row.attr("draggable", "true");
      $row.on("dragstart", (e) => {
        this.repack._drag = { kind: "hu", name: node.name, nested: !!node.nested };
        e.originalEvent.dataTransfer.effectAllowed = "move";
        e.originalEvent.dataTransfer.setData("text/plain", node.name);
        $row.addClass("wms-repack-dragging");
      });
      $row.on("dragend", () => { $row.removeClass("wms-repack-dragging"); this.repack._drag = null; });
    }
    $row.on("dragover", (e) => { if (this.repack._drag) { e.preventDefault(); e.originalEvent.dataTransfer.dropEffect = "move"; $row.addClass("wms-repack-dragover"); } });
    $row.on("dragleave", () => $row.removeClass("wms-repack-dragover"));
    $row.on("drop", (e) => { e.preventDefault(); e.stopPropagation(); $row.removeClass("wms-repack-dragover"); this.handle_repack_drop(node.kind, node.name); });
    $wrap.append($row);
    if (expanded) {
      const children = this.repack.childrenOf[k] || [];
      if (!children.length) $wrap.append(`<div class="text-muted" style="margin-left:${(depth + 1) * 16 + 18}px;font-size:11px;">${__("Empty")}</div>`);
      children.forEach((c) => $wrap.append(this.render_repack_tree_node(c, depth + 1)));
    }
    return $wrap;
  }

  // A stock line as its own tree leaf: same info a Contents-tab row shows (product, batch,
  // serial, stock type, quantity), draggable at its full quantity - no expand arrow, and not a
  // drop target itself.
  render_repack_tree_item_node(node, depth) {
    const selected = this.repack.selected && this.repack.selected.kind === "item" && this.item_key(this.repack.selected.row) === this.item_key(node);
    const $row = $(`<div class="wms-repack-tree-row${selected ? " wms-repack-tree-selected" : ""}" draggable="true" style="display:flex;align-items:center;gap:6px;padding:3px 4px;margin-left:${depth * 16 + 14}px;border-radius:4px;cursor:grab;"></div>`);
    $row.append(`<span>🏷️</span>`, `<span>${frappe.utils.escape_html(node.product)}</span>`);
    const meta = [node.batch_no, node.serial_no, node.stock_type, `${node.quantity} ${node.stock_uom || ""}`.trim()].filter(Boolean);
    $row.append(`<span class="text-muted" style="font-size:11px;">${frappe.utils.escape_html(meta.join(" · "))}</span>`);
    $row.on("click", () => this.select_repack_item(node));
    $row.on("dragstart", (e) => {
      this.repack._drag = { kind: "item", ...node };
      e.originalEvent.dataTransfer.effectAllowed = "move";
      e.originalEvent.dataTransfer.setData("text/plain", node.product);
      $row.addClass("wms-repack-dragging");
    });
    $row.on("dragend", () => { $row.removeClass("wms-repack-dragging"); this.repack._drag = null; });
    return $row;
  }

  render_repack_tabs(tabs) {
    const $bar = $(`<div style="display:flex;gap:4px;border-bottom:1px solid var(--border-color);margin-bottom:10px;"></div>`);
    tabs.forEach(([key, label]) => {
      const active = this.repack.activeTab === key;
      const $t = $(`<div style="padding:5px 10px;cursor:pointer;font-size:12px;${active ? "border-bottom:2px solid #3b82f6;font-weight:bold;" : "color:var(--text-muted);"}">${frappe.utils.escape_html(label)}</div>`);
      $t.on("click", () => { this.repack.activeTab = key; this.render_repack_detail(); });
      $bar.append($t);
    });
    return $bar;
  }

  render_repack_detail() {
    const $detail = this.body_for("repack").find(".wms-repack-detail").empty();
    const sel = this.repack.selected;
    if (!sel) { $detail.html(`<div class="text-muted">${__("Select a row in the tree to see its details.")}</div>`); return; }
    if (sel.kind === "item") { this.render_repack_item_detail($detail, sel.row); return; }

    const overview = this.repack.detail;
    if (!overview) { $detail.html(`<div class="text-muted">${__("Loading…")}</div>`); return; }
    const { kind, name } = sel;

    const $head = $(`<div style="display:flex;align-items:center;flex-wrap:wrap;gap:6px;margin-bottom:8px;"></div>`);
    $head.append(`<span>${kind === "hu" ? "📦" : "🗄"}</span>`, `<b>${frappe.utils.escape_html(name)}</b>`);
    $head.append(this.copy_btn(name));
    $detail.append($head);

    const target = this.repack.target;
    const sameAsTarget = target && target.kind === kind && target.name === name;
    const $actions = $(`<div style="display:flex;gap:8px;align-items:center;margin-bottom:10px;flex-wrap:wrap;"></div>`);
    const $allBtn = $(`<button type="button" class="btn btn-xs btn-primary">${target ? __("Repack All → {0}", [target.name]) : __("Repack All (pin a 🎯 target first)")}</button>`);
    $allBtn.prop("disabled", !target || sameAsTarget);
    $allBtn.on("click", () => this.repack_all());
    $actions.append($allBtn, this.render_repack_new_hu_controls());
    $detail.append($actions);

    const tabs = kind === "bin"
      ? [["unpacked", __("Unpacked Products")], ["info", __("Info")]]
      : [["contents", __("Contents")], ["details", __("Details")], ["destinations", __("Destination HUs")]];
    $detail.append(this.render_repack_tabs(tabs));

    const active = this.repack.activeTab;
    if (kind === "bin") {
      if (active === "info") this.render_repack_tab_bin_info($detail, overview);
      else this.render_repack_tab_stock($detail, { kind, overview }, (r) => r.kind === "item");
    } else if (active === "details") {
      this.render_repack_tab_hu_details($detail, overview);
    } else if (active === "destinations") {
      this.render_repack_tab_destinations($detail, sel);
    } else {
      this.render_repack_tab_stock($detail, { kind, overview }, () => true);
    }
  }

  // The Contents / Unpacked Products tab: the same lines the tree shows for this node, again -
  // this copy is where partial-quantity dragging happens (a qty field per line), matching SAP's
  // own "double-click the node, open Contents, enter a partial quantity, drag that row" flow.
  render_repack_tab_stock($detail, node, filterFn) {
    const loc = this.repack_locator(node);
    const rows = this.repack_rows(node).filter(filterFn);
    if (!rows.length) { $detail.append(`<div class="text-muted" style="font-size:12px;">${__("Empty")}</div>`); return; }
    const $bulkBar = $(`<div class="wms-repack-bulk-bar" style="margin-bottom:8px;"></div>`);
    $detail.append($bulkBar);
    this.update_repack_bulk_bar($bulkBar);
    const $table = $(`
      <table class="table table-sm table-bordered wms-repack-table" style="margin-bottom:0;">
        <thead><tr>
          <th style="width:24px;"></th>
          <th>${__("Item")}</th>
          <th>${__("Details")}</th>
          <th style="text-align:right;">${__("Quantity")}</th>
          <th style="width:110px;"></th>
        </tr></thead>
        <tbody></tbody>
      </table>
    `);
    const $tbody = $table.find("tbody");
    rows.forEach((row) => {
      const enriched = row.kind === "item" ? { ...row, srcBin: loc.bin, srcHu: loc.hu } : { ...row, atBin: loc.bin };
      $tbody.append(this.render_repack_detail_row(enriched));
    });
    $detail.append($table);
  }

  // A stable identity for a row regardless of whether it came from the tree or a detail tab, so
  // checkbox selection survives a re-render as long as the same line is still there.
  repack_row_key(row) {
    return row.kind === "hu"
      ? JSON.stringify(["hu", row.name])
      : JSON.stringify(["item", row.srcBin, row.srcHu || "", row.product, row.batch_no || "", row.serial_no || "", row.stock_type]);
  }

  update_repack_bulk_bar($bar) {
    const n = this.repack.selectedRows.size;
    const target = this.repack.target;
    $bar.empty();
    if (!n) { $bar.append(`<span class="text-muted" style="font-size:11px;">${__("Check lines to move or delete several at once")}</span>`); return; }
    const $btn = $(`<button type="button" class="btn btn-xs btn-primary">${target ? __("Move Selected ({0}) → {1}", [n, target.name]) : __("Move Selected ({0}) - pin a 🎯 target first", [n])}</button>`);
    $btn.prop("disabled", !target);
    $btn.on("click", () => this.repack_move_selected());
    $bar.append($btn);
    // Deletion only makes sense for Handling Unit rows - a checked stock line just skips it -
    // so the button only shows, and only counts, the HUs actually checked.
    const huCount = [...this.repack.selectedRows.values()].filter((r) => r.kind === "hu").length;
    if (huCount) {
      const $del = $(`<button type="button" class="btn btn-xs btn-danger" style="margin-left:6px;">${__("Delete Selected HU(s) ({0})", [huCount])}</button>`);
      $del.on("click", () => this.repack_delete_selected());
      $bar.append($del);
    }
  }

  // The bulk equivalent of the old per-HU "Delete (Recycle)" button - was one-at-a-time in the
  // Details tab, forcing a full reload between each; now checks every selected HU row and
  // recycles them all in one pass, matching Move Selected's pattern.
  async repack_delete_selected() {
    const rows = [...this.repack.selectedRows.values()].filter((r) => r.kind === "hu");
    if (!rows.length) { frappe.show_alert({ message: __("Check at least one Handling Unit first"), indicator: "orange" }); return; }
    frappe.confirm(__("Delete (recycle) {0} Handling Unit(s)? Each must be empty, unnested, with no nested HUs of its own. Frees their numbers for reuse; cannot be undone.", [rows.length]), async () => {
      let ok = 0, fail = 0;
      for (const row of rows) {
        try { await frappe.call("frappe_wms.api.handling_unit.recycle_handling_unit", { hu_name: row.name }); ok++; }
        catch (e) { fail++; frappe.show_alert({ message: e.message || String(e), indicator: "red" }); }
      }
      this.repack.selectedRows = new Map();
      if (this.repack.target && rows.some((r) => r.name === this.repack.target.name)) this.repack.target = null;
      if (this.repack.selected && this.repack.selected.kind === "hu" && rows.some((r) => r.name === this.repack.selected.name)) {
        this.repack.selected = null;
        this.repack.detail = null;
      }
      frappe.show_alert({ message: fail ? __("Deleted {0}, {1} failed", [ok, fail]) : __("Deleted {0} Handling Unit(s)", [ok]), indicator: fail ? "orange" : "green" });
      await this.refresh_repack_after_move();
    });
  }

  render_repack_info_table(fields) {
    const $t = $(`<table class="table table-sm" style="margin-bottom:0;"></table>`);
    fields.forEach(([label, val]) => $t.append(`<tr><th style="width:150px;">${frappe.utils.escape_html(label)}</th><td>${frappe.utils.escape_html(String(val === null || val === undefined ? "" : val))}</td></tr>`));
    return $t;
  }

  render_repack_tab_bin_info($detail, overview) {
    const b = overview.storage_bin;
    $detail.append(this.render_repack_info_table([
      [__("Warehouse"), b.warehouse], [__("Storage Type"), b.storage_type], [__("Storage Section"), b.storage_section],
      [__("Bin Type"), b.bin_type], [__("Max HUs"), b.maximum_hus], [__("Current HUs"), b.current_hu_count],
      [__("Putaway Blocked"), b.putaway_blocked ? __("Yes") : __("No")], [__("Removal Blocked"), b.removal_blocked ? __("Yes") : __("No")],
      [__("Inventory Blocked"), b.inventory_blocked ? __("Yes") : __("No")],
    ]));
  }

  render_repack_tab_hu_details($detail, overview) {
    const hu = overview.handling_unit;
    const $actions = $(`<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px;"></div>`);
    $actions.append(`<a href="/app/handling-unit/${encodeURIComponent(hu.name)}" target="_blank" class="btn btn-xs btn-default">${__("Open in Desk")}</a>`);
    const $blockBtn = $(`<button type="button" class="btn btn-xs btn-default">${hu.status === "Blocked" ? __("Unblock") : __("Block")}</button>`);
    $blockBtn.on("click", () => this.repack_toggle_block_hu(hu.name, hu.status === "Blocked"));
    $actions.append($blockBtn);
    $detail.append($actions);
    $detail.append(`<div class="text-muted" style="font-size:11px;margin-bottom:6px;">${__("To delete this HU, check its row where it's listed (in its parent bin or HU) and use Delete Selected there - deletion is a bulk action now.")}</div>`);
    $detail.append(this.render_repack_info_table([
      [__("Type"), hu.hu_type], [__("Status"), hu.status], [__("Stock Status"), hu.stock_status],
      [__("Current Bin"), hu.current_bin], [__("Parent HU"), hu.parent_hu || "-"], [__("Top HU"), hu.top_hu],
      [__("Gross Weight"), hu.gross_weight], [__("Net Weight"), hu.net_weight], [__("Volume"), hu.volume],
      [__("Closed"), hu.closed ? __("Yes") : __("No")], [__("Loaded"), hu.loaded ? __("Yes") : __("No")],
      [__("Outbound Delivery"), hu.outbound_delivery || "-"], [__("Shipment"), hu.shipment || "-"],
    ]));
    $detail.append(`<div class="text-muted" style="font-size:11px;margin-top:6px;">${__("Editing other fields (weight, packaging material, etc.) isn't available here yet - use Open in Desk.")}</div>`);
  }

  async repack_toggle_block_hu(hu_name, currentlyBlocked) {
    try {
      await frappe.call(`frappe_wms.api.handling_unit.${currentlyBlocked ? "unblock_handling_unit" : "block_handling_unit"}`, { hu_name });
    } catch (e) { frappe.show_alert({ message: e.message || String(e), indicator: "red" }); return; }
    frappe.show_alert({ message: currentlyBlocked ? __("Unblocked {0}", [hu_name]) : __("Blocked {0}", [hu_name]), indicator: "green" });
    await this.refresh_repack_after_move();
  }

  // "Possible destination HUs": every other HU sitting in the same bin, with a click-to-move
  // button - a non-drag alternative to Drag&Drop, same idea as SAP's own tab.
  async render_repack_tab_destinations($detail, sel) {
    // Own sub-container so the async gap below only ever touches its own content - $detail
    // itself already carries the head/actions/tabs bar that render_repack_detail() built before
    // calling this, and must never be wiped out from under them (that's what made the whole
    // panel - tabs included - disappear after opening this tab).
    const $box = $(`<div></div>`);
    $box.append(`<div class="text-muted">${__("Loading…")}</div>`);
    $detail.append($box);
    const bin = sel.kind === "hu" ? this.repack.detail.handling_unit.current_bin : sel.row.srcBin;
    const excludeName = sel.kind === "hu" ? sel.name : sel.row.srcHu;
    if (!bin) { $box.empty().append(`<div class="text-muted">${__("No bin context for this line")}</div>`); return; }
    const candidates = await frappe.call("frappe_wms.api.monitor.search_handling_units", { warehouse: this.warehouse, current_bin: bin, limit: 50 })
      .then((r) => (r.message || []).filter((h) => h.name !== excludeName));
    $box.empty();
    if (!candidates.length) { $box.append(`<div class="text-muted">${__("No other Handling Units in this bin")}</div>`); return; }
    candidates.forEach((c) => {
      const $row = $(`<div style="display:flex;align-items:center;gap:8px;padding:5px 8px;border:1px solid var(--border-color);border-radius:4px;margin-bottom:4px;"></div>`);
      $row.append(`<b>${frappe.utils.escape_html(c.name)}</b>`, `<span class="text-muted">${frappe.utils.escape_html(c.hu_type || "")} · ${frappe.utils.escape_html(c.status || "")}</span>`);
      const $go = $(`<button type="button" class="btn btn-xs btn-default" style="margin-left:auto;">${__("Move Here")}</button>`);
      $go.on("click", () => this.repack_quick_move(sel, c.name));
      $row.append($go);
      $box.append($row);
    });
  }

  async repack_quick_move(sel, targetHuName) {
    this.repack._drag = sel.kind === "hu"
      ? { kind: "hu", name: sel.name, nested: !!this.repack.detail.handling_unit.parent_hu }
      : { kind: "item", ...sel.row };
    await this.handle_repack_drop("hu", targetHuName);
  }

  // The click-driven equivalent of dragging a stock line - moves the qty field's value (or the
  // whole line if left blank) to whatever's pinned as the 🎯 target, same rules as a real drop.
  async repack_click_move_item(row, $qty) {
    const target = this.repack.target;
    if (!target) { frappe.show_alert({ message: __("Pin a 🎯 target first"), indicator: "orange" }); return; }
    const q = parseFloat($qty.val());
    const quantity = q > 0 && q <= row.quantity ? q : row.quantity;
    const srcLoc = { bin: row.srcBin, hu: row.srcHu || null };
    try {
      const destOverview = await this.fetch_repack_overview(target.kind, target.name);
      const destLoc = this.repack_locator({ kind: target.kind, overview: destOverview });
      if (srcLoc.bin === destLoc.bin && (srcLoc.hu || null) === (destLoc.hu || null)) { frappe.show_alert({ message: __("Already there"), indicator: "orange" }); return; }
      await this.repack_move_item(srcLoc, destLoc, { ...row, quantity });
      frappe.show_alert({ message: __("Moved {0} into {1}", [row.product, target.name]), indicator: "green" });
    } catch (e) {
      frappe.show_alert({ message: e.message || String(e), indicator: "red" });
      return;
    }
    await this.refresh_repack_after_move();
  }

  render_repack_item_detail($detail, row) {
    const $head = $(`<div style="display:flex;align-items:center;gap:6px;margin-bottom:8px;"></div>`);
    $head.append(`<span>🏷️</span>`, `<b>${frappe.utils.escape_html(row.product)}</b>`);
    $detail.append($head);
    $detail.append($(`<div style="margin-bottom:10px;"></div>`).append(this.render_repack_new_hu_controls()));
    $detail.append(this.render_repack_tabs([["product", __("Product Info")], ["destinations", __("Destination HUs")]]));
    if (this.repack.activeTab === "destinations") { this.render_repack_tab_destinations($detail, { kind: "item", row }); return; }
    $detail.append(this.render_repack_info_table([
      [__("Product"), row.product], [__("Batch"), row.batch_no || "-"], [__("Serial"), row.serial_no || "-"],
      [__("Stock Type"), row.stock_type], [__("Quantity"), row.quantity], [__("UOM"), row.stock_uom || ""],
      [__("Storage Bin"), row.srcBin || "-"], [__("Handling Unit"), row.srcHu || "-"],
    ]));
    $detail.append(`<h6 style="margin-top:14px;">${__("Recent Activity")}</h6>`);
    const $history = $(`<div class="text-muted">${__("Loading…")}</div>`);
    $detail.append($history);
    frappe.call("frappe_wms.api.monitor.stock_line_history", {
      product: row.product, stock_type: row.stock_type, storage_bin: row.srcBin || undefined,
      handling_unit: row.srcHu || undefined, batch_no: row.batch_no || undefined, serial_no: row.serial_no || undefined,
    }).then((r) => {
      const rows = r.message || [];
      if (!rows.length) { $history.html(`<div class="text-muted">${__("No ledger history for this line")}</div>`); return; }
      $history.empty().append(this.render_table(rows, [
        ["posting_datetime", __("Posted")], ["quantity", __("Qty")], ["movement_type", __("Movement")],
        ["reference_doctype", __("Reference Type")], ["reference_name", __("Reference")],
        ["warehouse_task", __("Task")], ["posting_user", __("By")],
      ], "WMS Stock Ledger Entry"));
    });
  }

  // Matches SAP's own "create Handling Units" screen: Type (required), Number (only meaningful
  // for an External type - one barcode, one HU), Storage Bin (wherever's selected), and a
  // Quantity of how many to create in one go - useful for staging several empty HUs ahead of a
  // physical process where you'll repack one piece (or a different quantity) into each.
  render_repack_new_hu_controls() {
    if (!this.repack.newDest) this.repack.newDest = { hu_type: "", hu_number: "", quantity: "1" };
    const nd = this.repack.newDest;
    const types = this.repack.hu_types || [];
    const type = types.find((t) => t.name === nd.hu_type);
    const internal = type && type.numbering_mode === "Internal";
    const $wrap = $(`<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;"></div>`);
    const $select = $(`<select class="form-control input-sm" style="width:150px;">
      <option value="">${__("New HU type…")}</option>
      ${types.map((t) => `<option value="${frappe.utils.escape_html(t.name)}" ${t.name === nd.hu_type ? "selected" : ""}>${frappe.utils.escape_html(t.name)}</option>`).join("")}
    </select>`);
    $select.on("change", (e) => { nd.hu_type = e.target.value; this.render_repack_detail(); });
    const $number = $(`<input class="form-control input-sm" style="width:160px;" placeholder="${internal ? __("Auto-assigned") : __("Scan blank HU barcode")}" ${internal ? "disabled" : ""}>`).val(nd.hu_number);
    $number.on("input", (e) => { nd.hu_number = e.target.value; });
    const $qty = $(`<input type="number" min="1" step="1" class="form-control input-sm" style="width:70px;" placeholder="${__("Qty")}" title="${__("How many to create - only one at a time for an External-numbering type")}" ${internal ? "" : "disabled"}>`).val(nd.quantity);
    $qty.on("input", (e) => { nd.quantity = e.target.value; });
    const $create = $(`<button type="button" class="btn btn-xs btn-default">${__("+ New HU here")}</button>`);
    $create.on("click", () => this.repack_create_here());
    $wrap.append($select, $number, $qty, $create);
    return $wrap;
  }

  async repack_create_here() {
    const nd = this.repack.newDest || {};
    if (!nd.hu_type) { frappe.show_alert({ message: __("Pick a Handling Unit type"), indicator: "orange" }); return; }
    const type = (this.repack.hu_types || []).find((t) => t.name === nd.hu_type);
    const internal = type && type.numbering_mode === "Internal";
    if (!internal && !(nd.hu_number || "").trim()) { frappe.show_alert({ message: __("Scan or type the barcode of the blank Handling Unit"), indicator: "orange" }); return; }
    const sel = this.repack.selected;
    if (!sel) { frappe.show_alert({ message: __("Select a row first"), indicator: "orange" }); return; }
    const storage_bin = sel.kind === "item" ? sel.row.srcBin : this.repack_locator({ kind: sel.kind, overview: this.repack.detail }).bin;
    const qty = internal ? Math.max(1, parseInt(nd.quantity, 10) || 1) : 1;
    const created = [];
    let lastError = null;
    for (let i = 0; i < qty; i++) {
      try {
        const hu = await frappe.call("frappe_wms.api.handling_unit.create_handling_unit", {
          hu_type: nd.hu_type, hu_number: (nd.hu_number || "").trim() || undefined, storage_bin,
        }).then((r) => r.message);
        created.push(hu.name);
      } catch (e) { lastError = e; break; }
    }
    if (!created.length) { frappe.show_alert({ message: (lastError && (lastError.message || String(lastError))) || __("Failed to create Handling Unit"), indicator: "red" }); return; }
    frappe.show_alert({
      message: created.length > 1 ? __("Created {0} Handling Units: {1}", [created.length, created.join(", ")]) : __("Created {0}", [created[0]]),
      indicator: created.length === qty ? "green" : "orange",
    });
    this.repack.newDest = { hu_type: "", hu_number: "", quantity: "1" };
    // Auto-pin the freshly created HU as the move target - the natural next step after making a
    // new empty HU is almost always to move something (a partial quantity, several checked
    // lines, or a whole other HU) straight into it.
    this.repack.target = { kind: "hu", name: created[created.length - 1] };
    await this.refresh_repack_after_move();
    await this.select_repack_node("hu", created[created.length - 1]);
  }

  // One <tr> per line, with real columns (checkbox / item / details / quantity / action) instead
  // of a flexbox strip - a row with several pieces of info (batch, serial, stock type, bin,
  // status) had nowhere consistent to put them and effectively vanished into unlabeled text.
  render_repack_detail_row(row) {
    const $row = $(`<tr class="wms-repack-row" draggable="true" style="cursor:grab;"></tr>`);
    const rowKey = this.repack_row_key(row);
    const $checkCell = $(`<td></td>`);
    const $check = $(`<input type="checkbox" title="${__("Select for Move Selected / Delete Selected")}">`).prop("checked", this.repack.selectedRows.has(rowKey));
    $check.on("mousedown click", (e) => e.stopPropagation());
    $check.on("change", (e) => {
      if (e.target.checked) this.repack.selectedRows.set(rowKey, row); else this.repack.selectedRows.delete(rowKey);
      this.update_repack_bulk_bar($row.closest(".wms-repack-detail").find(".wms-repack-bulk-bar"));
    });
    $checkCell.append($check);
    $row.append($checkCell);

    if (row.kind === "hu") {
      $row.append(`<td style="cursor:pointer;">📦 <b>${frappe.utils.escape_html(row.name)}</b></td>`);
      $row.append(`<td class="text-muted" style="font-size:12px;">${frappe.utils.escape_html(row.hu_type || "")} · ${frappe.utils.escape_html(row.atBin || row.current_bin || "-")} · ${frappe.utils.escape_html(row.status || "")}${row.stock_status ? " · " + frappe.utils.escape_html(row.stock_status) : ""}</td>`);
      $row.append(`<td></td>`);
      const $actionCell = $(`<td style="text-align:right;"></td>`);
      const $browse = $(`<a href="#">${__("Browse")}</a>`);
      $browse.on("click", (e) => { e.preventDefault(); e.stopPropagation(); this.select_repack_node("hu", row.name); });
      $actionCell.append($browse);
      $row.append($actionCell);
      $row.on("click", () => this.select_repack_node("hu", row.name));
      $row.on("dragstart", (e) => {
        this.repack._drag = { kind: "hu", name: row.name, nested: row.nested };
        e.originalEvent.dataTransfer.effectAllowed = "move";
        e.originalEvent.dataTransfer.setData("text/plain", row.name);
        $row.addClass("wms-repack-dragging");
      });
    } else {
      $row.append(`<td style="cursor:pointer;" title="${__("Click for full details")}">🏷️ ${frappe.utils.escape_html(row.product)}</td>`);
      const meta = [row.batch_no, row.serial_no, row.stock_type].filter(Boolean).map((v) => frappe.utils.escape_html(v)).join(" · ");
      $row.append(`<td class="text-muted" style="font-size:12px;">${meta}</td>`);
      const $qtyCell = $(`<td style="text-align:right;white-space:nowrap;"></td>`);
      $qtyCell.append(`<span style="font-weight:bold;font-size:14px;">${row.quantity}</span> <span class="text-muted">${frappe.utils.escape_html(row.stock_uom || "")}</span>`);
      const $qty = $(`<input type="number" step="any" min="0" max="${row.quantity}" placeholder="${__("qty")}" title="${__("Partial quantity (default: all of it)")}" class="form-control input-sm" style="width:70px;display:inline-block;margin-left:8px;">`);
      $qty.on("mousedown click", (e) => e.stopPropagation());
      $qtyCell.append($qty);
      $row.append($qtyCell);
      const $actionCell = $(`<td style="text-align:right;"></td>`);
      // A click-driven alternative to dragging, for a partial quantity too - moves to whatever's
      // pinned as the 🎯 target, since drag-and-drop isn't reliable on every input device.
      const $move = $(`<button type="button" class="btn btn-xs btn-default" title="${__("Move to the pinned 🎯 target")}">${__("Move")}</button>`);
      $move.on("mousedown", (e) => e.stopPropagation());
      $move.on("click", (e) => { e.stopPropagation(); this.repack_click_move_item(row, $qty); });
      $actionCell.append($move);
      $row.append($actionCell);
      $row.on("click", () => this.select_repack_item(row));
      $row.on("dragstart", (e) => {
        const q = parseFloat($qty.val());
        const quantity = q > 0 && q <= row.quantity ? q : row.quantity;
        this.repack._drag = { kind: "item", ...row, quantity };
        e.originalEvent.dataTransfer.effectAllowed = "move";
        e.originalEvent.dataTransfer.setData("text/plain", row.product);
        $row.addClass("wms-repack-dragging");
      });
    }
    $row.on("dragend", () => { $row.removeClass("wms-repack-dragging"); this.repack._drag = null; });
    return $row;
  }

  // The bulk equivalent of the per-line "Move" button - moves every checked line (each at its
  // own qty, or the whole line if left blank) to whatever's pinned as the 🎯 target in one go.
  async repack_move_selected() {
    const target = this.repack.target;
    if (!target) { frappe.show_alert({ message: __("Pin a 🎯 target first"), indicator: "orange" }); return; }
    const rows = [...this.repack.selectedRows.values()];
    if (!rows.length) { frappe.show_alert({ message: __("Check at least one line first"), indicator: "orange" }); return; }
    let ok = 0, fail = 0;
    for (const row of rows) {
      try {
        if (row.kind === "hu") { await this.repack_relocate_hu(row, target.kind, target.name); }
        else {
          const srcLoc = { bin: row.srcBin, hu: row.srcHu || null };
          const destOverview = await this.fetch_repack_overview(target.kind, target.name);
          const destLoc = this.repack_locator({ kind: target.kind, overview: destOverview });
          if (srcLoc.bin === destLoc.bin && (srcLoc.hu || null) === (destLoc.hu || null)) { fail++; continue; }
          await this.repack_move_item(srcLoc, destLoc, row);
        }
        ok++;
      } catch (e) { fail++; frappe.show_alert({ message: e.message || String(e), indicator: "red" }); }
    }
    this.repack.selectedRows = new Map();
    frappe.show_alert({ message: fail ? __("Moved {0} line(s), {1} failed", [ok, fail]) : __("Moved {0} line(s)", [ok]), indicator: fail ? "orange" : "green" });
    await this.refresh_repack_after_move();
  }

  async repack_nest_row(row, destHuName) {
    if (row.nested) await frappe.call("frappe_wms.api.handling_unit.unnest_handling_unit", { hu_name: row.name });
    await frappe.call("frappe_wms.api.handling_unit.nest_handling_unit", { hu_name: row.name, parent_hu: destHuName });
  }

  // Dropping a Handling Unit row onto another HU nests it there; dropping it onto a bin
  // relocates it there directly (SAP EWM's MOVE_HU) - both take any nested descendants along.
  async repack_relocate_hu(row, targetKind, targetName) {
    if (targetKind === "hu") await this.repack_nest_row(row, targetName);
    else await frappe.call("frappe_wms.api.handling_unit.relocate_handling_unit", { hu_name: row.name, destination_bin: targetName });
  }

  repack_idem() { return `MON-REPACK-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`; }

  // Same bin (whichever side, or both, is an HU) -> repack_loose, a direct ledger move with
  // no Warehouse Task (a Warehouse Task's "Internal Move" requires source and destination bins
  // to differ, which same-spot repacking by definition never does). Different bins -> a real
  // ad-hoc Internal Move task via create_and_confirm_move.
  async repack_move_item(srcLoc, destLoc, line) {
    const items = [{ item: line.product, batch_no: line.batch_no || undefined, serial_no: line.serial_no || undefined, stock_type: line.stock_type, stock_uom: line.stock_uom, quantity: line.quantity }];
    if (srcLoc.bin === destLoc.bin) {
      await frappe.call("frappe_wms.api.scanner.repack_loose", { storage_bin: srcLoc.bin, source_hu: srcLoc.hu || undefined, destination_hu: destLoc.hu || undefined, items: JSON.stringify(items), idempotency_key: this.repack_idem() });
    } else {
      await frappe.call("frappe_wms.api.scanner.create_and_confirm_move", {
        warehouse: this.warehouse, product: line.product, quantity: line.quantity, stock_uom: line.stock_uom, stock_type: line.stock_type,
        source_bin: srcLoc.bin, source_hu: srcLoc.hu || undefined, destination_bin: destLoc.bin, destination_hu: destLoc.hu || undefined,
        batch_no: line.batch_no || undefined, serial_no: line.serial_no || undefined, idempotency_key: this.repack_idem(),
      });
    }
  }

  async repack_move_all_items(srcLoc, destLoc, lines) {
    if (srcLoc.bin === destLoc.bin) {
      const items = lines.map((l) => ({ item: l.product, batch_no: l.batch_no || undefined, serial_no: l.serial_no || undefined, stock_type: l.stock_type, stock_uom: l.stock_uom, quantity: l.quantity }));
      await frappe.call("frappe_wms.api.scanner.repack_loose", { storage_bin: srcLoc.bin, source_hu: srcLoc.hu || undefined, destination_hu: destLoc.hu || undefined, items: JSON.stringify(items), idempotency_key: this.repack_idem() });
      return { ok: items.length, fail: 0 };
    }
    let ok = 0, fail = 0;
    for (const line of lines) {
      try { await this.repack_move_item(srcLoc, destLoc, line); ok++; } catch (e) { fail++; frappe.show_alert({ message: e.message || String(e), indicator: "red" }); }
    }
    return { ok, fail };
  }

  async handle_repack_drop(targetKind, targetName) {
    const drag = this.repack._drag;
    if (!drag) return;
    try {
      if (drag.kind === "hu") {
        if (drag.name === targetName) { frappe.show_alert({ message: __("Can't move a Handling Unit into itself"), indicator: "orange" }); return; }
        await this.repack_relocate_hu(drag, targetKind, targetName);
        frappe.show_alert({ message: targetKind === "hu" ? __("Nested {0} into {1}", [drag.name, targetName]) : __("Moved {0} to {1}", [drag.name, targetName]), indicator: "green" });
      } else {
        const srcLoc = { bin: drag.srcBin, hu: drag.srcHu || null };
        const destOverview = await this.fetch_repack_overview(targetKind, targetName);
        const destLoc = this.repack_locator({ kind: targetKind, overview: destOverview });
        if (srcLoc.bin === destLoc.bin && (srcLoc.hu || null) === (destLoc.hu || null)) { frappe.show_alert({ message: __("Already there"), indicator: "orange" }); return; }
        await this.repack_move_item(srcLoc, destLoc, drag);
        frappe.show_alert({ message: __("Moved {0} into {1}", [drag.product, targetName]), indicator: "green" });
      }
    } catch (e) {
      frappe.show_alert({ message: e.message || String(e), indicator: "red" });
      return;
    }
    await this.refresh_repack_after_move();
  }

  repack_all() {
    const sel = this.repack.selected, target = this.repack.target;
    if (!sel || sel.kind === "item") { frappe.show_alert({ message: __("Select a Handling Unit or Storage Bin first"), indicator: "orange" }); return; }
    if (!target) { frappe.show_alert({ message: __("Pin a 🎯 Repack All target in the tree first"), indicator: "orange" }); return; }
    if (sel.kind === target.kind && sel.name === target.name) { frappe.show_alert({ message: __("Source and target are the same"), indicator: "orange" }); return; }
    frappe.confirm(__("Move everything in {0} into {1}?", [sel.name, target.name]), async () => {
      const srcNode = { kind: sel.kind, overview: this.repack.detail };
      const targetOverview = await this.fetch_repack_overview(target.kind, target.name);
      const destNode = { kind: target.kind, overview: targetOverview };
      const srcLoc = this.repack_locator(srcNode), destLoc = this.repack_locator(destNode);
      let ok = 0, fail = 0;
      const rows = this.repack_rows(srcNode);
      const huRows = rows.filter((r) => r.kind === "hu");
      for (const row of huRows) {
        try { await this.repack_relocate_hu(row, target.kind, target.name); ok++; } catch (e) { fail++; frappe.show_alert({ message: e.message || String(e), indicator: "red" }); }
      }
      const stockRows = rows.filter((r) => r.kind === "item");
      if (stockRows.length) {
        const r = await this.repack_move_all_items(srcLoc, destLoc, stockRows);
        ok += r.ok; fail += r.fail;
      }
      frappe.show_alert({ message: fail ? __("Moved {0} line(s), {1} failed", [ok, fail]) : __("Moved {0} line(s)", [ok]), indicator: fail ? "orange" : "green" });
      await this.refresh_repack_after_move();
    });
  }

  // ---------- Stock Movements ----------
  async load_movements() {
    const $wrap = this.body_for("movements");
    if (!$wrap.find(".wms-mon-ledger-sel").length) {
      $wrap.html(`<div class="wms-mon-ledger-sel"></div><div class="wms-mon-ledger-table">${sap_unexecuted_html()}</div>`);
      this.selection("movements", $wrap.find(".wms-mon-ledger-sel"), $wrap.find(".wms-mon-ledger-table"), {
        renderers: { handling_unit: this.hu_link_cell("handling_unit") },
      });
    }
  }

  search_ledger() { return this.execute_selection("movements"); }

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

  // ---------- Yard & Doors (dock appointments, services/yard.py) ----------
  async load_yard() {
    const $wrap = this.body_for("yard");
    if (!$wrap.find(".wms-mon-yard-bar").length) {
      $wrap.html(`
        <div class="wms-mon-yard-bar" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;align-items:center;">
          <input type="date" class="form-control input-sm wms-mon-yard-date" style="width:160px;" value="${frappe.datetime.get_today()}">
          <button class="btn btn-primary btn-sm wms-mon-yard-new">${__("New Appointment")}</button>
          <button class="btn btn-default btn-sm wms-mon-yard-walkin">${__("Truck Without Appointment")}</button>
          <button class="btn btn-default btn-sm wms-mon-yard-refresh">${__("Refresh")}</button>
        </div>
        <div class="wms-mon-yard-doors" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px;"></div>
        <div class="wms-mon-yard-table"></div>`);
      $wrap.find(".wms-mon-yard-date").on("change", () => this.search_yard());
      $wrap.find(".wms-mon-yard-refresh").on("click", () => this.search_yard());
      $wrap.find(".wms-mon-yard-new").on("click", () => this.new_appointment_dialog());
      $wrap.find(".wms-mon-yard-walkin").on("click", () => this.walk_in_dialog());
    }
    this.search_yard();
  }

  async search_yard() {
    if (!this.warehouse) return;
    const $wrap = this.body_for("yard");
    const esc = frappe.utils.escape_html;
    const board = await frappe.call("frappe_wms.api.yard.yard_board", { warehouse: this.warehouse, date: $wrap.find(".wms-mon-yard-date").val() }).then((r) => r.message);
    this.yard = board;
    const byName = Object.fromEntries(board.appointments.map((a) => [a.name, a]));
    $wrap.find(".wms-mon-yard-doors").html(board.doors.length ? board.doors.map((d) => {
      const a = byName[d.occupied_by];
      return `<div style="border:1px solid var(--border-color);border-radius:8px;padding:8px 12px;min-width:150px;background:${a ? "var(--bg-orange)" : "var(--bg-green)"};">
        <div style="font-weight:600;">${esc(d.door)}</div>
        <div class="text-muted" style="font-size:12px;">${a ? `${esc(a.vehicle_registration || a.name)} · ${__(a.direction)}` : __("Free")}</div></div>`;
    }).join("") : `<div class="text-muted">${__("No doors: give a Storage Type the Door role and add bins to it.")}</div>`);
    const $table = $wrap.find(".wms-mon-yard-table");
    if (!board.appointments.length) { $table.html(`<div class="text-muted">${__("No appointments for this day")}</div>`); return; }
    const actions = { "Planned": [["check_in", __("Check in")], ["to_door", __("To door")], ["cancel", __("Cancel")]],
      "Checked In": [["to_door", __("To door")], ["check_out", __("Check out")]], "At Door": [["complete", __("Complete")], ["check_out", __("Check out")]],
      "Completed": [["check_out", __("Check out")]] };
    const time = (v) => (v ? frappe.datetime.str_to_user(v).split(" ").pop().slice(0, 5) : "");
    $table.empty().append(this.render_table(board.appointments, [
      ["planned_start", __("Slot"), (r) => `${time(r.planned_start)}–${time(r.planned_end)}`], ["name", __("Appointment")], ["direction", __("Direction")],
      ["door", __("Door")], ["vehicle_registration", __("Vehicle")], ["carrier", __("Carrier")], ["status", __("Status")],
      ["ref", __("Carries"), (r) => esc(r.inbound_delivery || r.shipment || "")],
      ["arrival_delay_minutes", __("Arrival"), (r) => (r.checked_in_at ? (r.arrival_delay_minutes > 0 ? __("{0} min late", [r.arrival_delay_minutes]) : __("on time")) : "")],
      ["actions", "", (r) => (actions[r.status] || []).map(([m, l]) => `<button type="button" class="btn btn-xs btn-default wms-mon-yard-act" data-m="${m}" data-a="${esc(r.name)}">${l}</button>`).join(" ")],
    ], "WMS Dock Appointment"));
    $table.find(".wms-mon-yard-act").on("click", (e) => this.yard_action(e.currentTarget.dataset.m, e.currentTarget.dataset.a));
  }

  async yard_action(method, appointment) {
    const call = (args) => frappe.call(`frappe_wms.api.yard.${method}`, Object.assign({ appointment }, args || {}))
      .then(() => { frappe.show_alert({ message: __("Done"), indicator: "green" }); this.search_yard(); });
    if (method === "check_in") {
      const d = new frappe.ui.Dialog({ title: __("Check in {0}", [appointment]), fields: [
        { fieldname: "yard_bin", fieldtype: "Select", label: __("Yard spot"), options: [""].concat(this.yard.yard_spots || []) }],
        primary_action_label: __("Check in"), primary_action: (v) => { d.hide(); frappe.call("frappe_wms.api.yard.check_in", { warehouse: this.warehouse, appointment, yard_bin: v.yard_bin || undefined })
          .then(() => { frappe.show_alert({ message: __("Checked in"), indicator: "green" }); this.search_yard(); }); } });
      d.show(); return;
    }
    if (method === "to_door") {
      const free = (this.yard.doors || []).filter((x) => !x.occupied_by).map((x) => x.door);
      const d = new frappe.ui.Dialog({ title: __("Send {0} to a door", [appointment]), fields: [
        { fieldname: "door", fieldtype: "Select", label: __("Door"), options: [""].concat(free), description: __("Empty: its booked door, or the first free one") }],
        primary_action_label: __("Send"), primary_action: (v) => { d.hide(); call({ door: v.door || undefined }); } });
      d.show(); return;
    }
    if (method === "cancel") { frappe.confirm(__("Cancel appointment {0}?", [appointment]), () => call()); return; }
    call();
  }

  new_appointment_dialog() {
    if (!this.warehouse) { frappe.show_alert({ message: __("Select a warehouse first"), indicator: "orange" }); return; }
    const d = new frappe.ui.Dialog({ title: __("New dock appointment"), fields: [
      { fieldname: "direction", fieldtype: "Select", label: __("Direction"), options: "Inbound\nOutbound", reqd: 1, default: "Inbound" },
      { fieldname: "planned_start", fieldtype: "Datetime", label: __("Start"), reqd: 1 },
      { fieldname: "planned_end", fieldtype: "Datetime", label: __("End"), description: __("Empty: the warehouse's default slot length") },
      { fieldname: "door", fieldtype: "Select", label: __("Door"), options: [""].concat((this.yard && this.yard.doors || []).map((x) => x.door)), description: __("Empty: the first free door") },
      { fieldtype: "Column Break" },
      { fieldname: "vehicle_registration", fieldtype: "Data", label: __("Vehicle") },
      { fieldname: "carrier", fieldtype: "Data", label: __("Carrier") },
      { fieldname: "trailer_number", fieldtype: "Data", label: __("Trailer / Container") },
      { fieldname: "driver_name", fieldtype: "Data", label: __("Driver") },
      { fieldtype: "Section Break" },
      { fieldname: "inbound_delivery", fieldtype: "Link", options: "Inbound Delivery", label: __("Inbound Delivery"), depends_on: "eval:doc.direction=='Inbound'",
        get_query: () => ({ filters: { warehouse: this.warehouse } }) },
      { fieldname: "shipment", fieldtype: "Link", options: "WMS Shipment", label: __("Shipment"), depends_on: "eval:doc.direction=='Outbound'",
        get_query: () => ({ filters: { warehouse: this.warehouse } }) },
    ], primary_action_label: __("Book"), primary_action: (v) => {
      frappe.call("frappe_wms.api.yard.create_appointment", Object.assign({ warehouse: this.warehouse }, v)).then((r) => {
        d.hide(); frappe.show_alert({ message: __("Booked {0}", [r.message]), indicator: "green" }); this.search_yard();
      });
    } });
    d.show();
  }

  walk_in_dialog(confirmed) {
    if (!this.warehouse) { frappe.show_alert({ message: __("Select a warehouse first"), indicator: "orange" }); return; }
    const d = new frappe.ui.Dialog({ title: __("Truck without appointment"), fields: [
      { fieldname: "vehicle_registration", fieldtype: "Data", label: __("Vehicle"), reqd: 1 },
      { fieldname: "direction", fieldtype: "Select", label: __("Direction"), options: "Inbound\nOutbound", reqd: 1 },
      { fieldname: "carrier", fieldtype: "Data", label: __("Carrier") },
      { fieldname: "yard_bin", fieldtype: "Select", label: __("Yard spot"), options: [""].concat((this.yard && this.yard.yard_spots) || []) },
    ], primary_action_label: __("Check in"), primary_action: async (v) => {
      const args = Object.assign({ warehouse: this.warehouse }, v, { yard_bin: v.yard_bin || undefined });
      let r = (await frappe.call("frappe_wms.api.yard.check_in", args)).message;
      if (r.needs_confirmation) {
        if (!(await new Promise((res) => frappe.confirm(r.needs_confirmation, () => res(true), () => res(false))))) return;
        r = (await frappe.call("frappe_wms.api.yard.check_in", Object.assign(args, { confirm_without_appointment: 1 }))).message;
      }
      d.hide(); frappe.show_alert({ message: __("Checked in as {0}", [r.appointment]), indicator: "green" }); this.search_yard();
    } });
    d.show();
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
        <h6>${__("ERPNext Postings Not Yet Done")}</h6>
        <div class="wms-mon-alert-erp-sync"></div>
      </div>
      <div style="margin-bottom:24px;">
        <h6>${__("Warehouse Requests Without Tasks")}</h6>
        <div class="wms-mon-alert-unplanned"></div>
      </div>
      <div style="margin-bottom:24px;">
        <h6>${__("Counts Awaiting Approval")}</h6>
        <div class="wms-mon-alert-approval"></div>
      </div>
      <div style="margin-bottom:24px;">
        <h6>${__("Open Differences")}</h6>
        <div class="wms-mon-alert-differences"></div>
      </div>
      <div style="margin-bottom:24px;">
        <h6>${__("Aged Exceptions")}</h6>
        <div class="wms-mon-alert-exceptions"></div>
      </div>
      <div style="margin-bottom:24px;">
        <h6>${__("Aged Open Tasks")}</h6>
        <div class="wms-mon-alert-aged-tasks"></div>
      </div>
      <div style="margin-bottom:24px;">
        <h6>${__("Stock In Interim Bins")}</h6>
        <div class="wms-mon-alert-interim"></div>
      </div>
      <div style="margin-bottom:24px;">
        <h6>${__("Negative Quants")}</h6>
        <div class="wms-mon-alert-negative"></div>
      </div>
      <div>
        <h6>${__("Stalled Warehouse Orders")}</h6>
        <div class="wms-mon-alert-wos"></div>
      </div>
    `);
    this.render_pending_approval_alerts(alerts.pending_approval_counts || []);
    this.render_alert_table($wrap.find(".wms-mon-alert-erp-sync"), alerts.erp_sync_problems || [],
      [["name", __("Log")], ["operation", __("Operation")], ["status", __("Status")], ["reference_doctype", __("Document Type")],
       ["reference_name", __("Document")], ["attempts", __("Attempts")], ["next_retry_at", __("Next Retry")],
       ["last_error", __("Last Error"), (row) => `<span title="${frappe.utils.escape_html(row.last_error || "")}">${frappe.utils.escape_html((row.last_error || "").split("\n").filter(Boolean).pop() || "")}</span>`]],
      "WMS ERP Sync Log", __("Nothing waiting - every posting reached ERPNext"), { actions: [{
        label: __("Retry now"), appliesTo: (row) => ["Failed", "Queued"].includes(row.status),
        run: async (rows) => {
          let ok = 0;
          for (const row of rows) { try { const r = await frappe.call("frappe_wms.api.erp_integration.retry_erp_posting", { log_name: row.name }); if (r.message === "Done") ok++; } catch (e) { /* shown by frappe */ } }
          frappe.show_alert({ message: __("{0} of {1} posted to ERPNext", [ok, rows.length]), indicator: ok === rows.length ? "green" : "orange" });
          this.load_alerts();
        } }] });
    this.render_alert_table($wrap.find(".wms-mon-alert-unplanned"), alerts.unplanned_requests || [],
      [["name", __("Request")], ["request_type", __("Type")], ["product", __("Product")], ["requested_quantity", __("Quantity")],
       ["source_bin", __("Source Bin")], ["source_hu", __("HU")], ["reference_doctype", __("Reference Type")], ["reference_name", __("Reference")],
       ["creation", __("Since")]],
      "Warehouse Request", __("Every request has its tasks"), { actions: [{
        label: __("Create tasks"), appliesTo: () => true,
        run: async (rows) => {
          const r = await frappe.call("frappe_wms.api.monitor.plan_warehouse_requests", { names: rows.map((row) => row.name) });
          const out = r.message || {};
          frappe.show_alert({ message: __("{0} of {1} planned", [out.planned || 0, rows.length]) + (out.errors && out.errors.length ? ` - ${out.errors[0]}` : ""),
            indicator: out.planned === rows.length ? "green" : "orange" });
          this.load_alerts();
        } }] });
    this.render_differences_alerts(alerts.open_differences || []);
    this.render_alert_table($wrap.find(".wms-mon-alert-exceptions"), alerts.aged_exceptions || [],
      [["name", __("Task")], ["task_type", __("Type")], ["product", __("Product")], ["source_bin", __("Source Bin")],
       ["destination_bin", __("Destination Bin")], ["assigned_resource", __("Resource")],
       ["exception_code", __("Exception")], ["blocking_reason", __("Reason")], ["modified", __("Since")]],
      "Warehouse Task", __("No aged exceptions"), { actions: this.task_quick_actions() });
    this.render_alert_table($wrap.find(".wms-mon-alert-aged-tasks"), alerts.aged_open_tasks || [],
      [["name", __("Task")], ["task_type", __("Type")], ["status", __("Status")], ["product", __("Product")],
       ["assigned_resource", __("Resource")], ["warehouse_order", __("Warehouse Order")], ["modified", __("Since")]],
      "Warehouse Task", __("No aged open tasks"), { actions: this.task_quick_actions() });
    this.render_alert_table($wrap.find(".wms-mon-alert-interim"), alerts.stock_in_interim_bins || [],
      [["product", __("Product")], ["storage_bin", __("Bin")], ["stock_type", __("Stock Type")],
       ["handling_unit", __("HU"), this.hu_link_cell("handling_unit")], ["quantity", __("Quantity")], ["last_movement_date", __("Since")]],
      "WMS Stock Balance", __("No stock stuck in interim bins"));
    this.render_alert_table($wrap.find(".wms-mon-alert-negative"), alerts.negative_quants || [],
      [["product", __("Product")], ["storage_bin", __("Bin")], ["stock_type", __("Stock Type")],
       ["handling_unit", __("HU"), this.hu_link_cell("handling_unit")], ["quantity", __("Quantity")], ["modified", __("Since")]],
      "WMS Stock Balance", __("No negative quants"));
    this.render_alert_table($wrap.find(".wms-mon-alert-wos"), alerts.stalled_warehouse_orders || [],
      [["name", __("Warehouse Order")], ["activity", __("Activity")], ["queue", __("Queue")], ["priority", __("Priority")],
       ["status", __("Status")], ["task_count", __("Tasks")], ["creation", __("Created")]],
      "Warehouse Order", __("No stalled Warehouse Orders"));
  }

  render_alert_table($container, rows, columns, doctype, empty_message, opts) {
    if (!rows.length) { $container.html(`<div class="text-muted">${empty_message}</div>`); return; }
    $container.empty().append(this.render_table(rows, columns, doctype, opts));
  }

  // Open over/short task-confirmation differences (services/difference.py), with the two
  // clearing actions right there in the list instead of a separate screen. A Short difference
  // just needs acknowledging (nothing to move - the shortfall never physically existed); an Over
  // difference needs somewhere to put the real stock it found, so that one prompts once for a
  // destination bin and applies it to every selected Over row.
  render_differences_alerts(rows) {
    const $container = this.body_for("alerts").find(".wms-mon-alert-differences");
    const actions = [
      {
        label: __("Clear (Short)"),
        appliesTo: (row) => row.direction === "Short",
        run: async (rows) => {
          let ok = 0;
          for (const row of rows) {
            try { await frappe.call("frappe_wms.api.difference.clear_short_difference", { name: row.name }); ok++; }
            catch (e) { /* frappe already shows the server error */ }
          }
          frappe.show_alert({ message: __("Cleared {0} of {1} difference(s)", [ok, rows.length]), indicator: ok === rows.length ? "green" : "orange" });
          this.load_alerts();
        },
      },
      {
        label: __("Clear (Over)"), kind: "primary",
        appliesTo: (row) => row.direction === "Over",
        run: async (rows) => {
          const values = await new Promise((resolve) => frappe.prompt(
            [{ fieldname: "destination_bin", label: __("Destination Bin"), fieldtype: "Link", options: "Storage Bin", reqd: 1 }],
            (v) => resolve(v), __("Clear {0} Over difference(s) to a bin", [rows.length]),
          ));
          if (!values) return;
          let ok = 0;
          for (const row of rows) {
            try { await frappe.call("frappe_wms.api.difference.clear_over_difference", { name: row.name, destination_bin: values.destination_bin }); ok++; }
            catch (e) { /* frappe already shows the server error */ }
          }
          frappe.show_alert({ message: __("Cleared {0} of {1} difference(s)", [ok, rows.length]), indicator: ok === rows.length ? "green" : "orange" });
          this.load_alerts();
        },
      },
    ];
    this.render_alert_table($container, rows,
      [["name", __("Difference")], ["warehouse_task", __("Task")], ["task_type", __("Type")], ["product", __("Product")],
       ["direction", __("Direction")], ["difference_quantity", __("Quantity")], ["storage_bin", __("Difference Bin")],
       ["exception_code", __("Exception")], ["creation", __("Raised")]],
      "WMS Task Difference", __("No open differences"), { actions });
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
  // not .html(...), since the grid carries live selection/copy event handlers. opts.actions:
  // see DataGrid's own doc comment - SAP EWM-style quick actions on the current selection.
  render_table(rows, columns, doctype, opts) {
    return new DataGrid(rows, columns, doctype, opts).$el;
  }
}

function flt(v) { const n = parseFloat(v); return isNaN(n) ? 0 : n; }
