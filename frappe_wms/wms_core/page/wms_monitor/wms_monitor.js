frappe.pages["wms-monitor"].on_page_load = function (wrapper) {
  const page = frappe.ui.make_app_page({
    parent: wrapper,
    title: __("WMS Monitor"),
    single_column: true,
  });
  // The assets are served with a long max-age, so a browser kept running an old wms_selection.js next to
  // this (always fresh) page code after a deploy. A version in the URL that changes every 10 minutes
  // bounds that - cheap, and nothing to remember to bump. (frappe.require cannot take a query string.)
  const v = Math.floor(Date.now() / 600000);
  const load = (src) => new Promise((resolve, reject) => {
    const url = `${src}?v=${v}`;
    if (document.querySelector(`script[data-wms-src="${url}"]`)) { resolve(); return; }
    const el = document.createElement("script");
    el.src = url; el.dataset.wmsSrc = url; el.onload = () => resolve(); el.onerror = () => reject(new Error(`could not load ${src}`));
    document.head.appendChild(el);
  });
  load("/assets/frappe_wms/js/wms_selection.js")
    .then(() => load("/assets/frappe_wms/js/wms_packing_station.js"))
    .then(() => new WMSMonitor(page))
    .catch((e) => frappe.msgprint({ title: __("WMS Monitor"), indicator: "red", message: frappe.utils.escape_html(e.message) }));
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
  { key: "cockpit", label: __("Shipping & Receiving") },
  { key: "stock", label: __("Stock Overview") },
  { key: "tasks", label: __("Warehouse Tasks") },
  { key: "warehouse_orders", label: __("Warehouse Orders") },
  { key: "hu", label: __("Handling Units") },
  { key: "packing", label: __("Packing Center") },
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

// Rows being dragged out of one grid (the serial numbers list) onto another grid's rows (the Packing Center tree).
let WMS_EXTERNAL_DRAG = null;

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
    .wms-mon-title { display:none; }
    .wms-pc-modes { display:flex; flex-wrap:wrap; align-items:center; gap:6px; margin-bottom:8px; }
    .wms-pc-org { display:inline-flex; align-items:center; gap:6px; margin-left:auto; }
    .wms-pc-org label { margin:0; font-size:12px; color:var(--text-muted); }
    .wms-pc-defbin { width:200px; display:inline-block; }
    .wms-pc-defbin .frappe-control, .wms-pc-defbin .form-group { margin:0; }
    .wms-pc-split { --wms-pc-side:380px; display:flex; align-items:flex-start; gap:0; }
    .wms-pc-main { flex:1 1 0; min-width:0; max-width:100%; }
    .wms-monitor { max-width:100%; overflow-x:clip; }
    .wms-pc-side .frappe-control .awesomplete > ul { max-width:100%; }
    .wms-pc-resizer { flex:0 0 8px; align-self:stretch; min-height:200px; cursor:col-resize; margin:0 2px; border-radius:4px; background:linear-gradient(to right, transparent 3px, var(--border-color) 3px, var(--border-color) 5px, transparent 5px); }
    .wms-pc-resizer:hover { background:var(--primary,#3b82f6); opacity:.5; }
    .wms-pc { min-width:0; max-width:100%; }
    .wms-pc-split { max-width:100%; }
    .wms-pc-side { flex:0 1 var(--wms-pc-side); width:var(--wms-pc-side); max-width:60%; min-width:240px; display:flex; flex-direction:column; border:1px solid var(--border-color); border-radius:8px;
      background:var(--card-bg,#fff); height:calc(100vh - 230px); overflow:hidden; }
    .wms-pc-actions { flex:0 1 auto; max-height:56%; overflow:auto; min-height:90px; }
    .wms-pc-info { flex:1 1 0; min-height:150px; overflow:auto; border-top:2px solid var(--border-color); }
    .wms-pc-side .wms-grid-toolbar { padding:4px 6px; }
    .wms-pc-side .wms-grid-layout-toggle, .wms-pc-side .wms-grid-layoutbar { display:none !important; }
    .wms-grid-layoutbar { flex-wrap:wrap; max-width:100%; }
    .wms-sn-list { max-height:150px; overflow:auto; border:1px solid var(--border-color); border-radius:4px; padding:4px 6px; }
    .wms-sn-list label { display:block; margin:1px 0; font-weight:normal; font-size:12px; }
    .wms-sn-links { font-size:11px; margin:2px 0; }
    .wms-pc-collapsed .wms-pc-side { display:none; }
    .wms-pc-tabs { overflow-x:auto; display:flex; border-bottom:1px solid var(--border-color); position:sticky; top:0; background:var(--card-bg,#fff); z-index:1; }
    .wms-pc-tab { white-space:nowrap; padding:8px 10px; cursor:pointer; font-size:12.5px; color:var(--text-muted); border-bottom:2px solid transparent; }
    .wms-pc-tab.active { color:var(--primary,#2563eb); border-bottom-color:var(--primary,#3b82f6); font-weight:600; }
    .wms-pc-pane { padding:10px 12px; }
    .wms-pc-head { display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin-bottom:10px; }
    .wms-pc-sheet { grid-template-columns:repeat(auto-fill, minmax(140px, 1fr)); }
    .wms-pc-form > div { margin-bottom:5px; }
    .wms-pc-form label { font-size:12px; color:var(--text-muted); margin-bottom:2px; }
    .wms-pc-form .control-input-wrapper, .wms-pc-form .frappe-control { margin-bottom:0; }
    .wms-pc-pane .wms-grid-scroll { max-height:50vh; }
    .wms-grid-toolbar { gap:6px 8px; padding:6px 8px; border:1px solid var(--border-color); border-bottom:0; border-radius:8px 8px 0 0; background:var(--card-bg,#fff); margin-bottom:0; }
    .wms-grid-scroll { border-radius:0 0 8px 8px; }
    .wms-grid-groupbar, .wms-grid-extra { display:inline-flex; align-items:center; gap:4px; font-size:12px; }
    .wms-grid-group { height:24px; width:110px; font-size:12px; padding:0 4px; }
    .wms-grid-treebar { display:none; align-items:center; gap:4px; }
    .wms-tree-nocaret { display:inline-block; width:22px; }
    .wms-row-drag { cursor:grab; }
    .wms-drag-ghost { position:fixed; top:0; z-index:3000; pointer-events:none; }
    .wms-drag-image { position:absolute; top:-100px; padding:4px 10px; border-radius:6px; background:var(--primary,#3b82f6); color:#fff; font-size:12px; font-weight:600; }
    .wms-row-grip { cursor:grab; opacity:.5; font-size:10px; letter-spacing:-2px; margin-right:4px; }
    .wms-row-grip:hover { opacity:1; }
    .wms-grid-table tbody tr.wms-drop td { outline:2px dashed var(--primary,#3b82f6); outline-offset:-2px; background:var(--bg-light-blue,#eff6ff) !important; }
    .wms-grid-table tbody tr.wms-hier-bin td { background:var(--bg-light-blue,#e8f0fe) !important; font-weight:600; }
    .wms-grid-table tbody tr.wms-hier-hu td { font-weight:500; }
    .wms-grid-table tbody tr.wms-pc-new td { box-shadow:inset 0 0 0 9999px rgba(34,197,94,.10); }
    .wms-tree-caret { border:0; background:none; padding:0 4px; cursor:pointer; color:var(--text-muted); }
    .wms-grid-table tbody tr.wms-grp td { background:var(--control-bg,#f3f4f6) !important; font-weight:600; }
    .wms-grid-table tbody tr.wms-grp-0 td { background:var(--bg-light-blue,#e8f0fe) !important; }
    .wms-grid-table tbody tr.wms-grp td.wms-grid-selected { background:rgba(59,130,246,.25) !important; }
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
    .wms-grid-actionbar:empty { display:none; }
    .wms-grid-actionbar { flex-wrap:wrap; max-width:100%; }
    .wms-grid-toolbar > * { min-width:0; max-width:100%; }
    .wms-grid-table { margin-top:0 !important; }
    
    .wms-detail-panel { margin-top:14px; padding:10px 12px; border:1px solid var(--primary,#3b82f6); border-radius:10px; background:var(--card-bg,#fff); }
    .wms-detail-fields { display:grid; grid-template-columns:repeat(auto-fill, minmax(190px, 1fr)); gap:6px 18px; font-size:12.5px; }
    .wms-detail-field > .text-muted { font-size:11px; }
    .wms-sel-detail .wms-detail-panel { margin-top:12px; }
    .wms-detail-head { display:flex; align-items:center; gap:12px; margin-bottom:8px; }
    .wms-detail-head .wms-detail-close { margin-left:auto; }
    .wms-detail-serials { display:flex; flex-wrap:wrap; align-items:center; gap:4px; max-height:96px; overflow:auto; margin-bottom:8px; }
    .wms-chip { padding:0 8px; border-radius:10px; border:1px solid var(--border-color); font-size:11px; font-family:var(--font-stack-mono,monospace); }
    .wms-detail-panel .wms-grid-scroll { max-height:40vh; }
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
    this.groupBy = (this.opts.groupBy || []).filter((f) => this.columns.some(([c]) => c === f));
    this.expanded = new Set(); // keys of open group rows
    this._expandInit = false;
    // opts.hierarchy: rows are nodes of a tree (id, pid, depth, has_kids, kind) already in depth-first
    // order - a caret opens/closes a node, selected rows can be dragged onto another node (onDrop).
    this.hier = !!this.opts.hierarchy;
    if (this.hier) {
      this.groupBy = [];
      this.expanded = this.opts.expanded || this.expanded;
      this.parentOf = new Map(this.rows.map((r) => [r.id, r.pid]));
      this.childrenOf = new Map();
      this.rows.forEach((r) => { if (r.pid) { if (!this.childrenOf.has(r.pid)) this.childrenOf.set(r.pid, []); this.childrenOf.get(r.pid).push(r.id); } });
    }
    this._applyGroupOrder();
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
    if (this.groupBy.length && !(this.sortField && this.sortDir)) {
      const gb = this.groupBy; // no explicit sort: keep each group's lines together, in group order
      rows = rows.slice().sort((a, b) => { for (const f of gb) { const c = String(a[f] ?? "").localeCompare(String(b[f] ?? ""), undefined, { numeric: true }); if (c) return c; } return 0; });
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

  // Open nodes only; a quick filter / column filter / sort shows the matching rows flat instead.
  _hierRows(leaves) {
    this._flat = !!(this.filterText || Object.keys(this.colFilters).length || (this.sortField && this.sortDir));
    if (this._flat) return leaves;
    const open = (pid) => { while (pid) { if (!this.expanded.has(pid)) return false; pid = this.parentOf.get(pid); } return true; };
    return leaves.filter((r) => open(r.pid));
  }

  // Opens or closes the marked nodes with everything beneath them (every node when nothing is marked).
  _hierExpand(open) {
    const marked = Array.from(this._selectedRowIndices()).filter((r) => r >= 1 && r <= this._visRows.length).map((r) => this._visRows[r - 1]);
    const ids = new Set();
    const add = (id) => { ids.add(id); (this.childrenOf.get(id) || []).forEach(add); };
    if (marked.length) marked.forEach((r) => add(r.id)); else this.rows.forEach((r) => ids.add(r.id));
    ids.forEach((id) => { if (open) this.expanded.add(id); else this.expanded.delete(id); });
    this.sels = []; this._render();
  }

  // The rows a drag starting at row `r` takes along: the marked rows if `r` is one of them, else just `r`.
  _dragSet(r) {
    const marked = this._selectedRowIndices();
    let rows = (marked.has(r) ? Array.from(marked) : [r]).map((i) => this._visRows[i - 1]).filter((x) => x && (!this.opts.draggable || this.opts.draggable(x)));
    const ids = new Set(rows.map((x) => x.id));
    // a node whose ancestor travels as well goes along with that ancestor already
    return rows.filter((x) => { for (let p = x.pid; p; p = this.parentOf.get(p)) if (ids.has(p)) return false; return true; });
  }

  // Right-button drag (the browser's own drag-and-drop is left-button only): same rows, same targets,
  // but the drop is reported with {ask: true} - the caller asks how much to move.
  _rightDrag(e, r) {
    const rows = this._dragSet(r);
    if (!rows.length) return;
    e.preventDefault();
    const x0 = e.clientX, y0 = e.clientY;
    let moved = false, $ghost = null;
    const rowAt = (ev) => {
      const tr = $(document.elementFromPoint(ev.clientX, ev.clientY)).closest("tbody tr");
      const at = tr.length ? this.$table.find("tbody tr").index(tr[0]) : -1;
      const row = at >= 0 ? this._visRows[at] : null;
      return row && (!this.opts.droppable || this.opts.droppable(row)) ? { row, tr } : null;
    };
    $(document).on("mousemove.rdrag", (ev) => {
      if (!moved && Math.hypot(ev.clientX - x0, ev.clientY - y0) < 6) return;
      if (!moved) { moved = true; $ghost = $(`<div class="wms-drag-image wms-drag-ghost">${__("{0} row(s) - choose quantity", [rows.length])}</div>`).appendTo("body"); }
      $ghost.css({ left: ev.clientX + 14, top: ev.clientY + 14 });
      this.$table.find(".wms-drop").removeClass("wms-drop");
      const hit = rowAt(ev);
      if (hit) hit.tr.addClass("wms-drop");
    }).on("mouseup.rdrag", (ev) => {
      $(document).off(".rdrag");
      if ($ghost) $ghost.remove();
      this.$table.find(".wms-drop").removeClass("wms-drop");
      const hit = moved ? rowAt(ev) : null;
      if (hit) this.opts.onDrop(rows, hit.row, { ask: true });
    });
  }

  _bindDragOut() {
    if (!this.opts.dragOut) return;
    const rowNo = (el) => $(el).closest("tr").index() + 1;
    this.$table.find(".wms-row-grip-out").on("mousedown", (e) => e.stopPropagation())
      .on("click", (e) => {
        const r = rowNo(e.currentTarget), marked = this._selectedRowIndices(), rect = { r0: r, r1: r, c0: 1, c1: this._maxC };
        if (e.ctrlKey || e.metaKey) {
          const at = this.sels.findIndex((x) => this._sameRect(x, rect));
          if (at >= 0) this.sels.splice(at, 1); else this.sels.push(rect);
        } else if (marked.has(r) && marked.size === 1) return;
        else this.sels = [rect];
        this.anchor = { r, c: 1 }; this._applyHighlight(); this._renderActionBar();
      })
      .on("dragstart", (e) => {
        const r = rowNo(e.currentTarget), marked = this._selectedRowIndices();
        const rows = (marked.has(r) ? Array.from(marked) : [r]).map((i) => this._visRows[i - 1]).filter(Boolean);
        WMS_EXTERNAL_DRAG = this.opts.dragOut(rows);
        const dt = e.originalEvent.dataTransfer;
        dt.effectAllowed = "move"; dt.setData("text/plain", rows.map((x) => x.serial_no || x.name || "").join(", "));
        const $img = $(`<div class="wms-drag-image">${__("{0} row(s)", [rows.length])}</div>`).appendTo("body");
        dt.setDragImage($img[0], 10, 10); setTimeout(() => $img.remove(), 0);
      })
      .on("dragend", () => { WMS_EXTERNAL_DRAG = null; $(".wms-drop").removeClass("wms-drop"); });
  }

  _bindDrag() {
    if (!this.hier || !this.opts.onDrop) return;
    const rowOf = (el) => this._visRows[$(el).closest("tr").index()];
    const handles = this.$table.find(".wms-row-grip, .wms-row-drag");
    const rowNo = (el) => $(el).closest("tr").index() + 1;
    handles.on("mousedown", (e) => { e.stopPropagation(); if (e.button === 2) this._rightDrag(e, rowNo(e.currentTarget)); })
      .on("click", (e) => { // a click on a handle marks the row (ctrl/cmd adds or removes it)
        const r = rowNo(e.currentTarget), marked = this._selectedRowIndices(), rect = { r0: r, r1: r, c0: 1, c1: this._maxC };
        if (e.ctrlKey || e.metaKey) {
          const at = this.sels.findIndex((x) => this._sameRect(x, rect));
          if (at >= 0) this.sels.splice(at, 1); else this.sels.push(rect);
        } else if (marked.has(r) && marked.size === 1) return;
        else this.sels = [rect];
        this.anchor = { r, c: 1 };
        this._applyHighlight(); this._renderActionBar();
      })
      .on("contextmenu", (e) => e.preventDefault())
      .on("dragstart", (e) => {
        this._dragRows = this._dragSet(rowNo(e.currentTarget));
        const rows = this._dragRows;
        const dt = e.originalEvent.dataTransfer;
        dt.effectAllowed = "move";
        dt.setData("text/plain", rows.map((x) => x.name).join(", "));
        const $img = $(`<div class="wms-drag-image">${__("{0} row(s)", [rows.length])}</div>`).appendTo("body");
        dt.setDragImage($img[0], 10, 10);
        setTimeout(() => $img.remove(), 0);
      })
      .on("dragend", () => { this._dragRows = null; this.$table.find(".wms-drop").removeClass("wms-drop"); });
    this.$table.find("tbody tr")
      .on("dragover", (e) => {
        const row = rowOf(e.currentTarget);
        if (!(this._dragRows || (WMS_EXTERNAL_DRAG && this.opts.onExternalDrop)) || !row || (this.opts.droppable && !this.opts.droppable(row))) return;
        e.preventDefault(); $(e.currentTarget).addClass("wms-drop");
      })
      .on("dragleave", (e) => $(e.currentTarget).removeClass("wms-drop"))
      .on("drop", (e) => {
        const row = rowOf(e.currentTarget), rows = this._dragRows;
        $(e.currentTarget).removeClass("wms-drop");
        if (!rows && WMS_EXTERNAL_DRAG && this.opts.onExternalDrop && row && (!this.opts.droppable || this.opts.droppable(row))) {
          e.preventDefault();
          const payload = WMS_EXTERNAL_DRAG; WMS_EXTERNAL_DRAG = null;
          this.opts.onExternalDrop(payload, row);
          return;
        }
        if (!rows || !row || (this.opts.droppable && !this.opts.droppable(row))) return;
        e.preventDefault(); this._dragRows = null;
        this.opts.onDrop(rows, row, { ask: false }); // left button: everything, no questions
      });
  }

  // Group fields come first, so a group row sits in the column of the field it groups by.
  _applyGroupOrder() {
    if (!this.groupBy.length) return;
    const head = this.groupBy.map((f) => this.columns.find(([c]) => c === f));
    this.columns = head.concat(this.columns.filter(([c]) => !this.groupBy.includes(c)));
  }

  // Flat list of visible rows of the group tree: group rows (aggregated) interleaved with their
  // lines, a collapsed group hiding everything beneath it.
  _tree(leaves) {
    const gb = this.groupBy, out = [], parentOf = new Map(), keys = [];
    const listFields = new Set(this.opts.listFields || []);
    const aggregate = (f, v, list, depth, pkey, key) => {
      const g = { _group: true, _depth: depth, _key: key, _pkey: pkey, _gfield: f, _lines: list, _multi: new Set() };
      for (const [field] of this.columns) {
        if (this.numeric.has(field)) { g[field] = Math.round(list.reduce((a, r) => a + (parseFloat(r[field]) || 0), 0) * 1e6) / 1e6; continue; }
        let vals = list.map((r) => r[field]).filter((x) => x !== null && x !== undefined && x !== "");
        if (listFields.has(field)) vals = vals.flatMap((x) => String(x).split(", "));
        const set = Array.from(new Set(vals));
        if (set.length <= 1) g[field] = set[0] ?? "";
        else if (listFields.has(field) && set.length <= 3) g[field] = set.sort().join(", ");
        else { g[field] = __("{0} values", [set.length]); g._multi.add(field); }
      }
      return g;
    };
    const make = (items, depth, pkey) => {
      if (depth === gb.length) { items.forEach((r) => { r._group = false; r._pkey = pkey; out.push(r); }); return; }
      const f = gb[depth], groups = new Map();
      for (const r of items) { const v = r[f] ?? ""; if (!groups.has(v)) groups.set(v, []); groups.get(v).push(r); }
      for (const [v, list] of groups) {
        const key = `${pkey}\u0001${f}=${v}`;
        parentOf.set(key, pkey); keys.push([key, depth]);
        out.push(aggregate(f, v, list, depth, pkey, key));
        make(list, depth + 1, key);
      }
    };
    make(leaves, 0, "");
    if (!this._expandInit) { keys.filter(([, d]) => d < gb.length - 1).forEach(([k]) => this.expanded.add(k)); this._expandInit = true; }
    this._allKeys = keys.map(([k]) => k);
    const open = (pkey) => { while (pkey) { if (!this.expanded.has(pkey)) return false; pkey = parentOf.get(pkey) || ""; } return true; };
    return out.filter((r) => open(r._pkey));
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
        <span class="wms-grid-groupbar">
          <span class="text-muted">${__("Group by")}</span>
          ${[0, 1, 2].map((i) => `<select class="form-control input-xs wms-grid-group" data-lvl="${i}"><option value="">${i ? "› " + __("then") + "…" : __("(none)")}</option>${this.columns.map(([f, l]) => `<option value="${frappe.utils.escape_html(f)}" ${this.groupBy[i] === f ? "selected" : ""}>${frappe.utils.escape_html(l)}</option>`).join("")}</select>`).join("")}
        </span>
        <span class="wms-grid-treebar">
          <button type="button" class="btn btn-default btn-xs wms-grid-expand" title="${this.opts.hierarchy ? __("Expand the marked rows (everything when none is marked)") : __("Expand all groups")}">&#9662; ${__("Expand")}</button>
          <button type="button" class="btn btn-default btn-xs wms-grid-collapse" title="${this.opts.hierarchy ? __("Collapse the marked rows (everything when none is marked)") : __("Collapse all groups")}">&#9656; ${__("Collapse")}</button>
        </span>
        <span class="wms-grid-extra"></span>
        <button type="button" class="btn btn-default btn-xs wms-grid-layout-toggle" title="${__("Layouts, columns")}">${__("Layout")} &#9662;</button>
        <span class="wms-grid-layoutbar" style="display:none;"></span>
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
    else $toolbar.find(".wms-grid-layout-toggle").hide();
    $toolbar.find(".wms-grid-layout-toggle").on("click", () => $toolbar.find(".wms-grid-layoutbar").toggle());
    if (this.opts.noGroup) $toolbar.find(".wms-grid-groupbar").remove();
    if (this.opts.extraToolbar) $toolbar.find(".wms-grid-extra").append(this.opts.extraToolbar);
    $toolbar.find(".wms-grid-group").on("change", () => {
      this.groupBy = Array.from(new Set($toolbar.find(".wms-grid-group").map((_, el) => el.value).get().filter(Boolean)));
      this._applyGroupOrder(); this.expanded = new Set(); this._expandInit = false; this.sels = [];
      this._render(); this._layoutChanged();
    });
    $toolbar.find(".wms-grid-expand").on("click", () => {
      if (this.hier) { this._hierExpand(true); return; }
      this.expanded = new Set(this._allKeys || []); this.sels = []; this._render();
    });
    $toolbar.find(".wms-grid-collapse").on("click", () => {
      if (this.hier) { this._hierExpand(false); return; }
      this.expanded = new Set(); this.sels = []; this._render();
    });
    this.$el.on("click", ".wms-tree-caret", (e) => {
      const key = e.currentTarget.dataset.key;
      if (this.expanded.has(key)) this.expanded.delete(key); else this.expanded.add(key);
      this.sels = []; this._render();
    });
    $toolbar.find(".wms-grid-clear-filters").on("click", () => { this.colFilters = {}; this.sortField = null; this.sortDir = 0; this.sels = []; this._render(); this._layoutChanged(); });
    this._bindSelection();
    this.$el.on("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && (e.key === "c" || e.key === "C")) { e.preventDefault(); this._copy(); }
      if (e.key === "Escape") { this.sels = []; this._applyHighlight(); this._renderActionBar(); }
    });
    this._render();
  }

  _render() {
    this._leaves = this._visibleRows();
    const rows = this.hier ? this._hierRows(this._leaves) : this.groupBy.length ? this._tree(this._leaves) : this._leaves;
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
      const grip = this.hier && this.opts.onDrop && (!this.opts.draggable || this.opts.draggable(row)) ? `<span class="wms-row-grip" draggable="true" title="${__("Drag to repack")}">&#8942;&#8942;</span>`
        : this.opts.dragOut ? `<span class="wms-row-grip wms-row-grip-out" draggable="true" title="${__("Drag the marked rows onto a Handling Unit to repack them")}">&#8942;&#8942;</span>` : "";
      const cells = [`<th class="wms-grid-rowhead" data-r="${ri + 1}" data-c="0">${grip}${ri + 1}</th>`].concat(
        this.columns.map(([field, , renderFn], ci) => {
          const raw = row[field];
          let inner;
          if (row._group) {
            const base = raw === "" || raw === null || raw === undefined ? "" : (renderFn && !row._multi.has(field) ? renderFn(row, ri) : frappe.utils.escape_html(String(raw)));
            if (row._gfield === field) {
              inner = `<button type="button" class="wms-tree-caret" data-key="${frappe.utils.escape_html(row._key)}">${this.expanded.has(row._key) ? "&#9662;" : "&#9656;"}</button> <b>${base || __("(blank)")}</b> <span class="text-muted">(${row._lines.length})</span>`;
            } else inner = ci < row._depth ? "" : base;
          } else if (renderFn) {
            inner = renderFn(row, ri);
          } else if ((field === "name" || field === "reference_name") && raw) {
            const target_doctype = field === "reference_name" ? row.reference_doctype : this.doctype;
            inner = target_doctype
              ? `<a href="/app/${frappe.router.slug(target_doctype)}/${encodeURIComponent(raw)}">${frappe.utils.escape_html(String(raw))}</a>`
              : frappe.utils.escape_html(String(raw));
          } else {
            inner = raw === null || raw === undefined ? "" : frappe.utils.escape_html(String(raw));
          }
          if (this.hier && ci === 0) {
            const pad = this._flat ? 0 : (row.depth || 0) * 16;
            if (this.opts.onDrop && (!this.opts.draggable || this.opts.draggable(row))) inner = `<span class="wms-row-drag" draggable="true" title="${__("Drag to repack everything - drag with the right mouse button to choose the quantity")}">${inner}</span>`;
            inner = `<span style="display:inline-block;width:${pad}px"></span>${row.has_kids && !this._flat ? `<button type="button" class="wms-tree-caret" data-key="${frappe.utils.escape_html(row.id)}">${this.expanded.has(row.id) ? "&#9662;" : "&#9656;"}</button>` : `<span class="wms-tree-nocaret"></span>`}${inner}`;
          }
          return `<td class="wms-grid-cell${this.numeric.has(field) ? " wms-grid-num" : ""}" data-r="${ri + 1}" data-c="${ci + 1}">${inner}</td>`;
        })
      ).join("");
      const rowCls = row._group ? `wms-grp wms-grp-${Math.min(row._depth, 2)}` : this.hier ? `wms-hier-${row.kind || "row"}${this.opts.rowClass ? " " + (this.opts.rowClass(row) || "") : ""}` : "";
      return `<tr${rowCls ? ` class="${rowCls}"` : ""}>${cells}</tr>`;
    }).join("");
    let foot = "";
    if (this.showTotals && this._leaves.length) {
      const cells = this.columns.map(([field]) => {
        if (!this.numeric.has(field)) return `<td></td>`;
        const total = this._leaves.filter((r) => !this.opts.totalFilter || this.opts.totalFilter(r)).reduce((acc, row) => acc + (parseFloat(row[field]) || 0), 0);
        return `<td class="wms-grid-num">${frappe.utils.escape_html(String(Math.round(total * 1e6) / 1e6))}</td>`;
      });
      foot = `<tfoot><tr><th class="wms-grid-rowhead">&Sigma;</th>${cells.join("")}</tr></tfoot>`;
    }
    this.$table.html(`<thead><tr>${head}</tr></thead><tbody>${body}</tbody>${foot}`);
    this._maxR = maxR; this._maxC = maxC;
    this.$el.find(".wms-grid-hint").text(this._leaves.length === this.rows.length ? __("{0} row(s)", [this._leaves.length]) : __("{0} of {1} row(s)", [this._leaves.length, this.rows.length]));
    this.$el.find(".wms-grid-clear-filters").toggle(!!(this.sortField || Object.keys(this.colFilters).length));
    this.$el.find(".wms-grid-treebar").toggle(this.groupBy.length > 0 || this.hier);
    this._bindHeaderControls();
    this._bindDrag();
    this._bindDragOut();
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
      if ($(e.target).closest(".wms-row-drag").length) return; // let the browser start a drag
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
    const rowIdx = Array.from(this._selectedRowIndices()).filter((r) => r >= 1 && r <= this._visRows.length);
    if (this.opts.onSelect) this.opts.onSelect(rowIdx.map((r) => this._visRows[r - 1]));
    if (!actions.length) { this.$actionbar.empty(); return; }
    const seen = new Set(), selectedRows = [];
    if (this.hier) rowIdx.forEach((r) => selectedRows.push(this._visRows[r - 1]));
    else rowIdx.forEach((r) => { const row = this._visRows[r - 1]; const leaves = (x) => x._lines ? x._lines.flatMap(leaves) : [x];
      leaves(row).forEach((l) => { if (!seen.has(l)) { seen.add(l); selectedRows.push(l); } }); });
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
    return { columns: this.columns.map(([f]) => f), sort: this.sortField ? [this.sortField, this.sortDir] : null, totals: this.showTotals, groupBy: this.groupBy };
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

const PACK_ICON = { section: "\u{1F4C2}", bin: "\u{1F5C4}", hu: "\u{1F4E6}", product: "\u{1F3F7}" };
// Everything the Packing Center tree can show: [field, label, numeric]. Columns are picked, ordered
// and saved as layouts from here ("Layout" > Columns...), like in every other monitor.
const PACK_COLUMNS = [
  ["name", __("Node")], ["kind", __("Type")], ["storage_bin", __("Storage Bin")], ["storage_type", __("Storage Type")],
  ["handling_unit", __("Handling Unit")], ["hu_type", __("HU Type")], ["packaging_material", __("Packing Material")],
  ["hu_status", __("HU Status")], ["stock_status", __("HU Content")], ["parent_hu", __("Higher HU")], ["top_hu", __("Highest HU")], ["hierarchy_level", __("HU Level"), 1],
  ["product", __("Product")], ["product_name", __("Product Name")], ["product_group", __("Product Group")], ["batch_no", __("Batch")], ["serial_count", __("Serial Nos"), 1],
  ["stock_type", __("Stock Type")], ["quantity", __("Quantity"), 1], ["allocated_quantity", __("Allocated"), 1], ["available_quantity", __("Available"), 1], ["stock_uom", __("UoM")],
  ["first_receipt_date", __("GR Date / Time")], ["shelf_life_expiry_date", __("Expiry")], ["last_movement_date", __("Last Movement")],
  ["document", __("Document")], ["sales_order", __("Sales Order")],
  ["gross_weight", __("Gross Weight"), 1], ["net_weight", __("Net Weight"), 1], ["tare_weight", __("Tare Weight"), 1], ["volume", __("Volume"), 1],
  ["outbound_delivery", __("Outbound Delivery")], ["shipment", __("Shipment")], ["seal_number", __("Seal No")], ["external_reference", __("External Reference")], ["sscc", __("SSCC")],
  ["closed", __("Closed")], ["loaded", __("Loaded")], ["owner", __("Created By")], ["creation", __("Created On")], ["modified", __("Modified")],
  ["storage_section", __("Storage Section")], ["bin_type", __("Bin Type")], ["maximum_hus", __("Max HUs"), 1], ["current_hu_count", __("HUs in Bin"), 1],
  ["creation_date", __("Created On")], ["creation_time", __("Created At")], ["gr_date", __("GR Date")], ["gr_time", __("GR Time")],
  ["stock_type_name", __("Description Stock Type")], ["country_of_origin", __("Country of Origin")], ["serial_control", __("Serial No. Requirement")],
  ["hu_category", __("Handling Unit Category")], ["product_items", __("Number of Product Items"), 1], ["hu_count", __("Number of Handling Units"), 1],
  ["putaway_blocked", __("Putaway Blocked")], ["removal_blocked", __("Removal Blocked")], ["inventory_blocked", __("Inventory Blocked")], ["active", __("Active")],
];
const PACK_DEFAULT_COLUMNS = ["name", "kind", "storage_type", "hu_type", "hu_status", "stock_status", "product", "product_name", "batch_no", "serial_count",
  "stock_type", "quantity", "allocated_quantity", "available_quantity", "stock_uom", "first_receipt_date", "parent_hu", "top_hu", "document"];

class WMSMonitor {
  constructor(page) {
    this.page = page;
    this.warehouse = null;
    this.view = "overview";
    this.pack = { roots: [], extraBins: new Set(), extraHus: new Set(), scope: null, rows: [], expanded: new Set(), sel: [], tab: "create", infoTab: "details", newIds: new Set(), materials: null };

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
      ".wms-mon-task-table", ".wms-mon-wo-table", ".wms-mon-hu-table", ".wms-mon-ledger-table", ".wms-mon-pack-table",
    ];
    selectors.forEach((sel) => { const $el = this.$body.find(sel); if ($el.length) $el.html(sap_unexecuted_html()); });
    this.$body.find(".wms-mon-obd-detail, .wms-mon-stock-detail").empty();
    Object.values(this.selections || {}).forEach((p) => p.then((sel) => { sel.lastRows = null; }));
    this.pack_new_search();
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
      packing: () => this.load_packing_center(),
      movements: () => this.load_movements(),
      resources: () => this.load_resources(),
      differences: () => this.load_differences(),
      kpis: () => this.load_kpis(),
      slotting: () => this.load_slotting(),
      bin_assignment: () => this.load_bin_assignment(),
      kitting: () => this.load_kitting(),
      yard: () => this.load_yard(),
      cockpit: () => this.search_cockpit(),
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
        onExecute: () => {
          const panel = { outbound: ".wms-mon-obd-detail", stock: ".wms-mon-stock-detail" }[view];
          if (panel) this.body_for(view).find(panel).empty();
          if (view === "packing") this.pack_new_search();
        },
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

  // Stock Overview in three steps, so one product's stock reads at a glance:
  //  1. one row per product / bin / stock type / document / sales order (no HU split), with the
  //     allocated part on its own row carrying the delivery and sales order that reserve it;
  //  2. "Expand" on the marked rows: per handling unit, with its parent and top HU and GR date/time;
  //  3. "Serial Numbers" on the marked HU rows: the serial numbers themselves.
  // ponytail: rolled up client-side over the loaded hits (Max. hits); move to SQL if hits get huge.
  stock_decorate() {
    const esc = frappe.utils.escape_html;
    const gr = (row) => esc(String(row.first_receipt_date || "").slice(0, 16));
    const R = {
      product: this.link_cell("Item", "product"), storage_bin: this.link_cell("Storage Bin", "storage_bin"),
      document: this.link_cell("Outbound Delivery", "document"), sales_order: this.link_cell("Sales Order", "sales_order"),
      handling_unit: this.hu_link_cell("handling_unit"), parent_hu: this.hu_link_cell("parent_hu"), top_hu: this.hu_link_cell("top_hu"),
      serial_no: this.link_cell("Serial No", "serial_no"), first_receipt_date: gr,
    };
    const col = (f, l) => [f, l, R[f]];
    // A balance row split by what reserves it: one part per allocation, the rest is free stock.
    const parts = (rows) => rows.flatMap((r) => {
      const out = [];
      let rest = flt(r.quantity);
      for (const a of r.allocs || []) {
        const q = Math.min(flt(a.qty), rest);
        if (q > 0) { out.push({ ...r, quantity: q, allocated_quantity: q, available_quantity: 0, document: a.delivery, sales_order: a.sales_order }); rest -= q; }
      }
      if (rest > 1e-9) out.push({ ...r, quantity: rest, allocated_quantity: 0, available_quantity: rest, document: "", sales_order: "" });
      return out;
    });
    this.stock_roll = (lines, keys, carry) => {
      const map = new Map();
      for (const r of lines) {
        const k = keys.map((f) => r[f] || "").join("\u0001");
        let o = map.get(k);
        if (!o) { o = { _lines: [], _seen: {}, quantity: 0, allocated_quantity: 0, available_quantity: 0 }; keys.forEach((f) => { o[f] = r[f] || ""; }); carry.forEach((f) => { o._seen[f] = new Set(); }); map.set(k, o); }
        o._lines.push(r);
        ["quantity", "allocated_quantity", "available_quantity"].forEach((f) => { o[f] += flt(r[f]); });
        carry.forEach((f) => { if (r[f]) o._seen[f].add(r[f]); });
      }
      return Array.from(map.values()).map((o) => {
        ["quantity", "allocated_quantity", "available_quantity"].forEach((f) => { o[f] = Math.round(o[f] * 1e6) / 1e6; });
        carry.forEach((f) => { const v = Array.from(o._seen[f]).sort(); o[f] = f === "first_receipt_date" ? (v[0] || "") : v.length > 1 ? __("{0} values", [v.length]) : (v[0] || ""); });
        delete o._seen;
        return o;
      }).sort((a, b) => keys.map((f) => String(a[f]).localeCompare(String(b[f]), undefined, { numeric: true })).find((c) => c) || 0);
    };
    return {
      noDetails: true, totals: true,
      transform: (rows) => ({
        rows: this.stock_roll(parts(rows), ["product", "storage_bin", "stock_type", "document", "sales_order"], ["stock_uom"]),
        columns: [col("product", __("Product")), col("storage_bin", __("Storage Bin")), ["stock_type", __("Stock Type")], col("document", __("Document")),
          col("sales_order", __("Sales Order")), ["quantity", __("Quantity")], ["allocated_quantity", __("Allocated")], ["available_quantity", __("Available")], ["stock_uom", __("UoM")]],
        numeric: ["quantity", "allocated_quantity", "available_quantity"],
        actions: [
          { label: __("Expand"), kind: "primary", run: (lines) => this.stock_expand(lines) },
          { label: __("Movements"), run: (lines) => {
            const uniq = (f) => Array.from(new Set(lines.map((l) => l[f]).filter(Boolean)));
            return this.jump("movements", { product: uniq("product"), storage_bin: uniq("storage_bin") });
          } },
        ],
      }),
    };
  }

  // Step 2: the marked rows per handling unit - where it sits in the HU nesting and when it arrived.
  stock_expand(lines) {
    const $d = this.body_for("stock").find(".wms-mon-stock-detail").empty();
    const rows = this.stock_roll(lines, ["product", "storage_bin", "stock_type", "handling_unit", "document", "sales_order"], ["parent_hu", "top_hu", "first_receipt_date", "batch_no", "stock_uom"]);
    const gr = (r) => frappe.utils.escape_html(String(r.first_receipt_date || "").slice(0, 16));
    const hu = (f) => this.hu_link_cell(f);
    const $panel = $(`<div class="wms-detail-panel">
      <div class="wms-detail-head"><b>${__("Expanded")}</b><span class="text-muted">${__("{0} row(s) by handling unit - mark rows and press Serial Numbers to go one level deeper", [rows.length])}</span>
        <button type="button" class="btn btn-default btn-xs wms-detail-close">&times;</button></div>
      <div class="wms-detail-lines"></div><div class="wms-detail-serials-host"></div></div>`).appendTo($d);
    $panel.find(".wms-detail-close").on("click", () => $d.empty());
    $panel.find(".wms-detail-lines").append(this.render_table(rows, [
      ["product", __("Product"), this.link_cell("Item", "product")], ["storage_bin", __("Storage Bin"), this.link_cell("Storage Bin", "storage_bin")], ["stock_type", __("Stock Type")],
      ["handling_unit", __("Handling Unit"), hu("handling_unit")], ["parent_hu", __("Higher HU"), hu("parent_hu")], ["top_hu", __("Highest HU"), hu("top_hu")],
      ["first_receipt_date", __("GR Date / Time"), gr], ["batch_no", __("Batch")],
      ["document", __("Document"), this.link_cell("Outbound Delivery", "document")], ["sales_order", __("Sales Order"), this.link_cell("Sales Order", "sales_order")],
      ["quantity", __("Quantity")], ["allocated_quantity", __("Allocated")], ["available_quantity", __("Available")],
    ], null, { numeric: ["quantity", "allocated_quantity", "available_quantity"], totals: true, noGroup: true, exportName: "stock-by-hu",
      actions: [{ label: __("Serial Numbers"), kind: "primary", run: (l) => this.stock_serials($panel.find(".wms-detail-serials-host"), l) }] }));
    $panel[0].scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  // Step 3: the serial numbers behind the marked HU rows.
  stock_serials($host, lines) {
    $host.empty();
    const serial = lines.filter((l) => l.serial_no);
    if (!serial.length) { $host.html(`<div class="text-muted" style="margin-top:10px;">${__("No serial numbers on the marked rows.")}</div>`); return; }
    const gr = (r) => frappe.utils.escape_html(String(r.first_receipt_date || "").slice(0, 16));
    $host.append(`<h6 style="margin:12px 0 4px;">${__("Serial Numbers")} (${serial.length})</h6>`).append(this.render_table(serial, [
      ["serial_no", __("Serial No"), this.link_cell("Serial No", "serial_no")], ["batch_no", __("Batch")], ["handling_unit", __("Handling Unit"), this.hu_link_cell("handling_unit")],
      ["storage_bin", __("Storage Bin"), this.link_cell("Storage Bin", "storage_bin")], ["first_receipt_date", __("GR Date / Time"), gr],
      ["document", __("Document"), this.link_cell("Outbound Delivery", "document")], ["sales_order", __("Sales Order"), this.link_cell("Sales Order", "sales_order")],
      ["quantity", __("Quantity")], ["allocated_quantity", __("Allocated")],
    ], null, { numeric: ["quantity", "allocated_quantity"], noGroup: true, exportName: "stock-serials" }));
    $host.find(".wms-grid-toolbar").append(this.copy_btn(serial.map((l) => l.serial_no).join("\n")));
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

  // ---------- Packing Center ----------
  // One place to look at, repack and create Handling Units. The selection screen finds Storage Bins
  // (by bin, HU, HU type, packing material, product, batch, serial); the tree shows what is in them:
  // bin > HU > nested HU > product. Mark rows (HUs and products alike) and drag them onto an HU to
  // pack/nest them or onto a bin to move them; the side panel shows details, serial numbers and the
  // HU creation form, and can be resized. Columns are configurable like every other monitor.
  // The old work-center packing station (public/js/wms_packing_station.js) is the second tab.
  async load_packing_center() {
    const $wrap = this.body_for("packing");
    if (!$wrap.find(".wms-pc-bench").length) {
      $wrap.html(`
        <div class="wms-pc-modes">
          <button type="button" class="btn btn-sm btn-primary wms-pc-mode" data-mode="bench">${__("Workbench")}</button>
          <button type="button" class="btn btn-sm btn-default wms-pc-mode" data-mode="station">${__("Work Center Station")}</button>
          <span class="wms-pc-org"><label>${__("Default Storage Bin")}</label><span class="wms-pc-defbin"></span>
            <button type="button" class="btn btn-sm btn-default wms-pc-emptyhu" title="${__("Create a Handling Unit in the default storage bin")}">${__("Empty HU")}</button></span>
        </div>
        <div class="wms-pc-bench">
          <div class="wms-pc-sel"></div>
          <div class="wms-pc-split">
            <div class="wms-pc-main wms-mon-pack-table">${sap_unexecuted_html()}</div>
            <div class="wms-pc-resizer" title="${__("Drag to resize the side panel (double-click to hide it)")}"></div>
            <div class="wms-pc-side"></div>
          </div>
        </div>
        <div class="wms-pc-station" style="display:none;"></div>`);
      $wrap.find(".wms-pc-mode").on("click", (e) => this.pack_set_mode(e.currentTarget.dataset.mode));
      this.selection("packing", $wrap.find(".wms-pc-sel"), $wrap.find(".wms-pc-main"), this.pack_decorate());
      this.pack_init_resizer($wrap);
      this.pack_render_side();
      this.pack_build_default_bin($wrap);
    }
    if (this.pack_mode === "station") await this.pack_load_station();
  }

  // SAP's "default storage bin": new Handling Units are created there unless another bin is typed.
  pack_build_default_bin($wrap) {
    const key = () => `wms_pc_default_bin_${this.warehouse || ""}`;
    this.pack.defBin = this.pack_link_ctl($wrap.find(".wms-pc-defbin"), "Storage Bin", __("Storage Bin"), () => {
      const v = this.pack.defBin.get_value();
      try { localStorage.setItem(key(), v || ""); } catch (e) { /* storage blocked: not remembered */ }
      this.pack_prefill_create(true);
    });
    try { const v = localStorage.getItem(key()); if (v) this.pack.defBin.set_value(v); } catch (e) { /* ignore */ }
    $wrap.find(".wms-pc-emptyhu").on("click", () => { this.pack.tab = "create"; this.pack_render_side(); this.pack_prefill_create(true); this.$body.find(".wms-pc-form .wms-pc-material").trigger("focus"); });
  }

  pack_link_ctl($parent, doctype, placeholder, onchange) {
    return frappe.ui.form.make_control({ parent: $parent, only_input: true, render_input: true,
      df: { fieldtype: "Link", options: doctype, fieldname: `pc_${doctype}`.replace(/\W/g, "_"), placeholder, change: onchange || (() => {}),
        get_query: () => ({ filters: { warehouse: this.warehouse } }) } });
  }

  pack_set_mode(mode) {
    this.pack_mode = mode;
    const $wrap = this.body_for("packing");
    $wrap.find(".wms-pc-mode").each((_, el) => $(el).toggleClass("btn-primary", el.dataset.mode === mode).toggleClass("btn-default", el.dataset.mode !== mode));
    $wrap.find(".wms-pc-bench").toggle(mode !== "station");
    $wrap.find(".wms-pc-station").toggle(mode === "station");
    if (mode === "station") this.pack_load_station();
  }

  async pack_load_station() {
    const $st = this.body_for("packing").find(".wms-pc-station");
    if (!this.packing_station) this.packing_station = new WMSPackingStation($st, () => this.warehouse);
    if (this.packing_station_wh !== this.warehouse) {
      this.packing_station_wh = this.warehouse;
      await this.packing_station.render();
    } else if (this.packing_station.wc) {
      await this.packing_station.load();
    }
  }

  pack_init_resizer($wrap) {
    const $split = $wrap.find(".wms-pc-split");
    let saved = NaN;
    try { saved = parseInt(localStorage.getItem("wms_pc_side_w"), 10); } catch (e) { /* storage blocked: default width */ }
    if (saved) $split.css("--wms-pc-side", saved + "px");
    $wrap.find(".wms-pc-resizer").on("mousedown", (e) => {
      e.preventDefault();
      const right = $split[0].getBoundingClientRect().right;
      let w = 0;
      $(document).on("mousemove.pcres", (ev) => { w = Math.round(Math.max(240, Math.min(right - ev.clientX, $split.width() * 0.75))); $split.css("--wms-pc-side", w + "px"); })
        .on("mouseup.pcres", () => {
          $(document).off(".pcres");
          if (w) { try { localStorage.setItem("wms_pc_side_w", String(w)); } catch (err) { /* ignore */ } }
        });
    }).on("dblclick", () => $split.toggleClass("wms-pc-collapsed"));
  }

  pack_decorate() {
    return {
      catalog: PACK_COLUMNS.map(([fieldname, label]) => ({ fieldname, label })), defaultColumns: PACK_DEFAULT_COLUMNS,
      render: (sel, rows, $res) => this.pack_render(sel, rows, $res),
    };
  }

  // A new search starts from scratch: the previous tree, marks and extra bins belong to the old result.
  pack_new_search() {
    this.pack = { ...this.pack, roots: [], extraBins: new Set(), extraHus: new Set(), scope: null, rows: [], expanded: new Set(), sel: [], newIds: new Set(), loadedRows: null, drawn: false };
    this.pack_render_side();
  }

  async pack_render(sel, rows, $res) {
    this.pack.sel_screen = sel; this.pack.$res = $res;
    if (this.pack.loadedRows === rows && this.pack.rows.length) { this.pack_draw(true); return; } // a layout change only redraws
    this.pack.loadedRows = rows;
    this.pack.roots = rows.map((r) => r.name);
    this.pack.extraBins = new Set(); this.pack.extraHus = new Set();
    this.pack.scope = sel.scope;
    await this.pack_reload(true, true);
  }

  async pack_reload(initial, fromRender) {
    const bins = Array.from(new Set([...this.pack.roots, ...this.pack.extraBins]));
    const scope = this.pack.scope || {};
    const r = await frappe.call("frappe_wms.api.packing_center.packing_tree", { warehouse: this.warehouse, bins: JSON.stringify(bins),
      hus: scope.hus ? JSON.stringify(scope.hus) : undefined, balances: scope.balances ? JSON.stringify(scope.balances) : undefined,
      extra_hus: JSON.stringify(Array.from(this.pack.extraHus)) }).then((x) => x.message);
    this.pack.rows = r.rows; this.pack.truncated = r.truncated;
    this.pack.children = new Map();
    r.rows.forEach((x) => { if (x.pid) { if (!this.pack.children.has(x.pid)) this.pack.children.set(x.pid, []); this.pack.children.get(x.pid).push(x); } });
    this.pack.byId = new Map(r.rows.map((x) => [x.id, x]));
    if (initial) {
      const all = r.rows.length <= 600; // a small result opens completely, a big one only to its bins
      this.pack.expanded = new Set(r.rows.filter((x) => x.has_kids && (all || x.kind === "bin" || x.kind === "section")).map((x) => x.id));
    }
    this.pack_draw(fromRender);
  }

  pack_renderers() {
    const esc = frappe.utils.escape_html;
    const dt = (f) => (r) => esc(String(r[f] || "").slice(0, 16));
    const yes = (f) => (r) => (r[f] ? "&#10003;" : "");
    const many = (dtype, f) => (r) => String(r[f] || "").split(", ").filter(Boolean).map((v) => /\s/.test(v) ? esc(v)
      : `<a href="/app/${frappe.router.slug(dtype)}/${encodeURIComponent(v)}" target="_blank" rel="noopener">${esc(v)}</a>`).join(", ");
    const pill = (f) => (r) => wms_selection.pill(r[f]);
    const R = {
      name: (r) => `${PACK_ICON[r.kind]} ${r.kind === "product" ? esc(r.name) : `<b>${esc(r.name)}</b>`}`,
      creation_date: (r) => esc(r.creation_date || ""), creation_time: (r) => esc(r.creation_time || ""), gr_date: (r) => esc(r.gr_date || ""), gr_time: (r) => esc(r.gr_time || ""),
      kind: (r) => wms_selection.pill({ section: __("Section"), bin: __("Bin"), hu: __("HU"), product: __("Product") }[r.kind], { section: "gray", bin: "blue", hu: "orange", product: "green" }[r.kind]),
      storage_bin: this.link_cell("Storage Bin", "storage_bin"), handling_unit: this.hu_link_cell("handling_unit"),
      parent_hu: this.hu_link_cell("parent_hu"), top_hu: this.hu_link_cell("top_hu"),
      product: this.link_cell("Item", "product"), packaging_material: this.link_cell("Packaging Material", "packaging_material"),
      hu_type: this.link_cell("Handling Unit Type", "hu_type"), outbound_delivery: this.link_cell("Outbound Delivery", "outbound_delivery"),
      shipment: this.link_cell("WMS Shipment", "shipment"), document: many("Outbound Delivery", "document"), sales_order: many("Sales Order", "sales_order"),
      hu_status: pill("hu_status"), stock_status: pill("stock_status"),
      first_receipt_date: dt("first_receipt_date"), last_movement_date: dt("last_movement_date"), creation: dt("creation"), modified: dt("modified"),
    };
    ["closed", "loaded", "putaway_blocked", "removal_blocked", "inventory_blocked", "active"].forEach((f) => { R[f] = yes(f); });
    return R;
  }

  pack_draw(fromRender) {
    const sel = this.pack.sel_screen;
    if (!sel) return;
    const $res = this.pack.$res || this.body_for("packing").find(".wms-pc-main");
    if (!fromRender && !this.pack.drawn) $res.empty();
    this.pack.drawn = true;
    $res.find(".wms-pc-grid").remove();
    const R = this.pack_renderers();
    const byField = Object.fromEntries(PACK_COLUMNS.map((c) => [c[0], c]));
    const cols = sel.shownColumns().map((f) => [f, byField[f][1], R[f]]);
    const $grid = this.render_table(this.pack.rows, cols, null, {
      hierarchy: true, noGroup: true, expanded: this.pack.expanded, exportName: `packing-center-${frappe.datetime.now_date()}`,
      numeric: PACK_COLUMNS.filter((c) => c[2]).map((c) => c[0]), totalFilter: (r) => r.kind === "product",
      sort: sel.layout && sel.layout.sort, totals: sel.layout ? sel.layout.totals : false,
      layoutBar: sel.layoutBar(), onLayoutChange: (state) => sel.gridLayoutChanged(state),
      draggable: (r) => r.kind === "hu" || r.kind === "product", droppable: (r) => r.kind !== "section", onDrop: (rows, target, opts) => this.pack_drop(rows, target, opts), onExternalDrop: (payload, target) => this.pack_serials_drop(payload, target),
      onSelect: (rows) => this.pack_selected(rows), rowClass: (r) => (this.pack.newIds.has(r.id) ? "wms-pc-new" : ""),
      actions: this.pack_actions(),
    });
    $res.append($(`<div class="wms-pc-grid"></div>`).append(this.pack.truncated ? `<div class="text-warning" style="font-size:12px;">${__("Very large result - not all stock lines are shown. Narrow the selection.")}</div>` : "", $grid));
  }

  // The marked rows drive the side panel (debounced: a drag-select fires this for every row passed).
  pack_selected(rows) {
    this.pack.sel = rows;
    clearTimeout(this.pack._selTimer);
    this.pack._selTimer = setTimeout(() => this.pack_render_side(), 120);
  }

  // ---- side panel ----
  pack_render_side() {
    const $side = this.$body.find(".wms-pc-side");
    if (!$side.length) return;
    const pane = (k) => `<div class="wms-pc-pane" data-pane="${k}"></div>`;
    if (!$side.find(".wms-pc-actions").length) {
      $side.html(`<div class="wms-pc-actions"><div class="wms-pc-tabs"></div>${["create", "repack_hu", "repack_product", "difference"].map(pane).join("")}</div>
        <div class="wms-pc-info"><div class="wms-pc-tabs"></div>${["details", "serials"].map(pane).join("")}</div>`);
      this.pack_build_create($side.find('[data-pane="create"]'));
      this.pack_build_repack_hu($side.find('[data-pane="repack_hu"]'));
      this.pack_build_repack_product($side.find('[data-pane="repack_product"]'));
      this.pack_build_difference($side.find('[data-pane="difference"]'));
    }
    const groups = [
      [".wms-pc-actions", "tab", [["create", __("Create HU")], ["repack_hu", __("Repack HU")], ["repack_product", __("Repack Product")], ["difference", __("Difference")]]],
      [".wms-pc-info", "infoTab", [["details", __("Details")], ["serials", __("Serial Numbers")]]],
    ];
    groups.forEach(([sel, key, tabs]) => {
      const $g = $side.find(sel);
      $g.children(".wms-pc-tabs").html(tabs.map(([k, l]) => `<span class="wms-pc-tab ${this.pack[key] === k ? "active" : ""}" data-tab="${k}">${l}</span>`).join(""))
        .find(".wms-pc-tab").on("click", (e) => { this.pack[key] = e.currentTarget.dataset.tab; this.pack_render_side(); });
      $g.children(".wms-pc-pane").each((_, el) => $(el).toggle(el.dataset.pane === this.pack[key]));
    });
    // the information below follows the marked rows; the form above only refreshes its list of marked rows
    if (this.pack.infoTab === "serials") this.pack_render_serials($side.find('[data-pane="serials"]').empty());
    else this.pack_render_details($side.find('[data-pane="details"]').empty());
    if (this.pack.tab === "repack_hu") this.pack_refresh_repack_hu();
    else if (this.pack.tab === "repack_product") this.pack_refresh_repack_product();
    else if (this.pack.tab === "difference") this.pack_refresh_difference();
    else this.pack_prefill_create();
  }

  pack_render_details($pane) {
    const rows = this.pack.sel, esc = frappe.utils.escape_html;
    if (!rows.length) { $pane.html(`<div class="text-muted">${__("Mark a row in the tree to see its details here.")}</div>`); return; }
    if (rows.length === 1) {
      const r = rows[0], R = this.pack_renderers();
      const form = { bin: ["Storage Bin", r.name], hu: ["Handling Unit", r.handling_unit], product: ["Item", r.product] }[r.kind];
      $pane.append(`<div class="wms-pc-head">${PACK_ICON[r.kind]} <b>${esc(r.name)}</b> ${R.kind(r)}
        ${form ? `<a href="/app/${frappe.router.slug(form[0])}/${encodeURIComponent(form[1])}" target="_blank" rel="noopener">${__("Open form")}</a>` : ""}</div>`);
      $pane.find(".wms-pc-head").append(this.copy_btn(r.name));
      const $sheet = $(`<div class="wms-detail-fields wms-pc-sheet"></div>`).appendTo($pane);
      PACK_COLUMNS.forEach(([f, label]) => {
        if (f === "name" || f === "kind" || r[f] === null || r[f] === undefined || r[f] === "" || r[f] === 0) return;
        $sheet.append(`<div class="wms-detail-field"><div class="text-muted">${esc(label)}</div><div>${R[f] ? R[f](r) : esc(String(r[f]))}</div></div>`);
      });
      if (r.kind === "product") $sheet.append(`<div class="wms-detail-field"><div class="text-muted">${__("Stock lines")}</div><div>${r.lines.length}</div></div>`);
      return;
    }
    const count = (k) => rows.filter((r) => r.kind === k).length;
    $pane.append(`<div class="wms-pc-head"><b>${__("{0} rows marked", [rows.length])}</b>
      <span class="text-muted">${__("{0} bin(s), {1} HU(s), {2} product line(s)", [count("bin") + count("section"), count("hu"), count("product")])}</span></div>`);
    const totals = new Map();
    rows.filter((r) => r.kind === "product").forEach((r) => { const k = `${r.product}\u0001${r.stock_uom || ""}`; totals.set(k, (totals.get(k) || 0) + flt(r.quantity)); });
    if (totals.size) {
      $pane.append(this.render_table(Array.from(totals, ([k, q]) => { const [product, uom] = k.split("\u0001"); return { product, quantity: Math.round(q * 1e6) / 1e6, stock_uom: uom }; }),
        [["product", __("Product")], ["quantity", __("Quantity")], ["stock_uom", __("UoM")]], null, { numeric: ["quantity"], noGroup: true }));
    }
    $pane.append(`<div class="text-muted" style="margin-top:8px;font-size:12px;">${esc(rows.slice(0, 40).map((r) => r.name).join(", "))}${rows.length > 40 ? " …" : ""}</div>`);
  }

  // The serial lines inside the marked rows (a bin or HU brings everything beneath it).
  pack_serial_lines() {
    const lines = new Set();
    const collect = (r) => { if (r.kind === "product") r.lines.forEach((l) => lines.add(l)); else (this.pack.children.get(r.id) || []).forEach(collect); };
    this.pack.sel.forEach(collect);
    return Array.from(lines).filter((l) => l.serial_no).map((l) => ({ ...l, handling_unit: l.source_hu, storage_bin: l.source_bin }));
  }

  // Serial-numbered stock is repacked by serial number: filter, mark several, drag them onto an HU in
  // the tree (or Move to...) - a bare quantity would not say which serial numbers moved.
  pack_render_serials($pane) {
    const serial = this.pack_serial_lines();
    if (!this.pack.sel.length) { $pane.html(`<div class="text-muted">${__("Mark rows in the tree to list their serial numbers.")}</div>`); return; }
    if (!serial.length) { $pane.html(`<div class="text-muted">${__("No serial-numbered stock in the marked rows.")}</div>`); return; }
    $pane.append(`<div class="wms-pc-head"><b>${__("{0} serial number(s)", [serial.length])}</b>
      <span class="text-muted" style="font-size:12px;">${__("Mark serial numbers, then drag them by ⋮⋮ onto a Handling Unit in the tree, or use Move to…")}</span></div>`);
    $pane.find(".wms-pc-head").append(this.copy_btn(serial.map((l) => l.serial_no).join("\n")));
    $pane.append(this.render_table(serial, [
      ["serial_no", __("Serial No"), this.link_cell("Serial No", "serial_no")], ["product", __("Product"), this.link_cell("Item", "product")], ["batch_no", __("Batch")],
      ["handling_unit", __("Handling Unit"), this.hu_link_cell("handling_unit")], ["storage_bin", __("Storage Bin"), this.link_cell("Storage Bin", "storage_bin")],
      ["first_receipt_date", __("GR Date / Time"), (r) => frappe.utils.escape_html(String(r.first_receipt_date || "").slice(0, 16))],
    ], null, {
      noGroup: true, exportName: "packing-serials", dragOut: (rows) => ({ serials: rows }),
      actions: [
        { label: __("Move to…"), kind: "primary", run: (rows) => this.pack_dest_dialog(__("Move {0} serial number(s) to…", [rows.length]), (kind, name) => this.pack_move_lines(rows, kind, name)) },
        { label: __("Missing"), kind: "danger", confirm: (rows) => __("Move {0} missing serial number(s) to the difference bin?", [rows.length]),
          run: (rows) => this.pack_post_differences(rows.map((l) => ({ label: l.serial_no, lines: [{ ...l, quantity: 1 }] })), "") },
      ],
    }));
  }

  pack_serials_drop(payload, target) {
    const dest = this.pack_dest_of(target);
    return this.pack_move_lines(payload.serials, dest.kind, dest.name);
  }

  pack_move_lines(lines, kind, name) {
    return this.pack_run_move([{ kind: "stock", label: __("{0} serial number(s)", [lines.length]), lines: lines.map((l) => ({ ...l, quantity: 1 })) }], kind, name);
  }

  async pack_post_differences(items, remarks) {
    const res = await frappe.call("frappe_wms.api.packing_center.post_differences", { warehouse: this.warehouse, items: JSON.stringify(items),
      remarks: remarks || undefined, idempotency_key: `PCD-${Date.now()}-${Math.random().toString(36).slice(2, 8)}` }).then((r) => r.message);
    frappe.show_alert({ message: __("Posted {0} difference(s)", [res.posted]), indicator: res.errors.length ? "orange" : "green" });
    if (res.errors.length) frappe.msgprint({ title: __("Not posted"), indicator: "orange", message: res.errors.map((e) => `<b>${frappe.utils.escape_html(e.item)}</b>: ${frappe.utils.escape_html(e.error)}`).join("<br>") });
    await this.pack_reload(false);
  }

  // ---- creating Handling Units: packing material, number (empty = generated), bin, amount ----
  pack_build_create($pane) {
    $pane.html(`
      <div class="wms-pc-form">
        <div><label>${__("HU Type")}</label><select class="form-control input-sm wms-pc-material"><option value="">${__("Loading…")}</option></select>
          <div class="text-muted wms-pc-mat-hint"></div></div>
        <div><label>${__("HU")}</label><input class="form-control input-sm wms-pc-num" placeholder="${__("Leave empty to generate a new number")}">
          <div class="text-muted wms-pc-num-hint"></div></div>
        <div><label>${__("HU / Storage Bin")}</label><div class="wms-pc-bin"></div>
          <div class="text-muted">${__("A Handling Unit is always created in a bin.")}</div></div>
        <div><label>${__("Inside HU (optional)")}</label><div class="wms-pc-parent"></div></div>
        <div><label>${__("Number of HUs")}</label><input type="number" min="1" max="200" value="1" class="form-control input-sm wms-pc-qty" style="width:100px;"></div>
        <button type="button" class="btn btn-primary btn-sm wms-pc-create">${__("Create")}</button>
      </div>`);
    this.pack.ctl = { bin: this.pack_link_ctl($pane.find(".wms-pc-bin"), "Storage Bin", __("Storage Bin")), parent: this.pack_link_ctl($pane.find(".wms-pc-parent"), "Handling Unit", __("Handling Unit")) };
    const $mat = $pane.find(".wms-pc-material"), $num = $pane.find(".wms-pc-num"), $qty = $pane.find(".wms-pc-qty");
    const typeOf = () => (this.pack.materials || []).find((x) => x.name === $mat.val()) || null;
    const refresh = () => {
      const m = typeOf();
      $pane.find(".wms-pc-mat-hint").text(m && m.hu_type_name && m.hu_type_name !== m.name ? m.hu_type_name : "");
      const internal = m && m.numbering_mode === "Internal";
      $num.prop("disabled", !!internal).attr("placeholder", internal ? __("Numbered automatically") : __("Leave empty to generate a new number"));
      if (internal) $num.val("");
      $pane.find(".wms-pc-num-hint").text($num.val() ? __("A given number creates exactly one Handling Unit.") : "");
      $qty.prop("disabled", !!$num.val());
      if ($num.val()) $qty.val(1);
    };
    $mat.on("change", refresh); $num.on("input", refresh);
    (async () => {
      this.pack.materials = await frappe.db.get_list("Handling Unit Type", { fields: ["name", "hu_type_name", "numbering_mode"], filters: { active: 1 }, limit_page_length: 200, order_by: "name asc" });
      $mat.html(`<option value="">${__("Choose…")}</option>` + this.pack.materials.map((m) => `<option value="${frappe.utils.escape_html(m.name)}">${frappe.utils.escape_html(m.name)}${m.hu_type_name && m.hu_type_name !== m.name ? " - " + frappe.utils.escape_html(m.hu_type_name) : ""}</option>`).join(""));
      if (this.pack.materials.length === 1) { $mat.val(this.pack.materials[0].name); refresh(); }
    })();
    $pane.find(".wms-pc-create").on("click", async (e) => {
      const $btn = $(e.currentTarget);
      const hu_type = $mat.val();
      const bin = this.pack.ctl.bin.get_value(), parent = this.pack.ctl.parent.get_value();
      if (!hu_type) { frappe.show_alert({ message: __("Choose the HU type"), indicator: "orange" }); return; }
      if (!bin && !parent) { frappe.show_alert({ message: __("Choose the storage bin"), indicator: "orange" }); return; }
      $btn.prop("disabled", true);
      try {
        const created = await frappe.call("frappe_wms.api.packing_center.create_hus", {
          warehouse: this.warehouse, storage_bin: bin || undefined, parent_hu: parent || undefined, hu_type, hu_number: $num.val() || undefined, quantity: $qty.val() || 1 }).then((r) => r.message);
        frappe.show_alert({ message: __("Created {0}", [created.map((h) => h.hu_number).join(", ")]), indicator: "green" });
        $num.val(""); refresh();
        await this.pack_show_new(created.map((h) => `hu:${h.name}`), created[0].current_bin);
      } catch (err) { /* frappe shows the server's reason */ }
      $btn.prop("disabled", false);
    });
  }

  // New HUs (and anything else that must be visible after a change) join the tree: their bin is
  // added as a root if it was not in the result, and every node above them opens.
  async pack_show_new(ids, bin) {
    if (!this.pack.sel_screen) this.pack.sel_screen = await this.selections.packing;
    if (bin && !this.pack.roots.includes(bin)) this.pack.extraBins.add(bin);
    ids.forEach((id) => this.pack.extraHus.add(id.replace(/^hu:/, "")));
    this.pack.newIds = new Set(ids);
    await this.pack_reload(false);
    this.pack_open_path(ids);
    this.pack_draw();
  }

  pack_open_path(ids) {
    ids.forEach((id) => { for (let p = (this.pack.byId.get(id) || {}).pid; p; p = (this.pack.byId.get(p) || {}).pid) this.pack.expanded.add(p); });
  }

  // The create form follows the marked row: its bin (and HU) as defaults, while the fields are still untouched.
  pack_prefill_create(force) {
    const ctl = this.pack.ctl;
    if (!ctl) return;
    const def = this.pack.defBin && this.pack.defBin.get_value();
    const bin = def || (this.pack.sel[0] && this.pack.sel[0].storage_bin) || "";
    if (bin && (force || !ctl.bin.get_value() || ctl.bin.get_value() === this.pack.autoBin)) { ctl.bin.set_value(bin); this.pack.autoBin = bin; }
  }

  // Where marked rows go: a Handling Unit (pack / nest into it) or a Storage Bin.
  pack_dest_picker($host) {
    $host.html(`<label>${__("Destination")}</label>
      <select class="form-control input-sm wms-dp-kind" style="margin-bottom:4px;"><option value="hu">${__("Handling Unit")}</option><option value="bin">${__("Storage Bin")}</option></select>
      <div class="wms-dp-hu"></div><div class="wms-dp-bin" style="display:none;"></div>`);
    const hu = this.pack_link_ctl($host.find(".wms-dp-hu"), "Handling Unit", __("Handling Unit")), bin = this.pack_link_ctl($host.find(".wms-dp-bin"), "Storage Bin", __("Storage Bin"));
    $host.find(".wms-dp-kind").on("change", (e) => { $host.find(".wms-dp-hu").toggle(e.target.value === "hu"); $host.find(".wms-dp-bin").toggle(e.target.value === "bin"); });
    return () => { const kind = $host.find(".wms-dp-kind").val(); return { kind, name: (kind === "hu" ? hu : bin).get_value() }; };
  }

  // ---- Repack HU: the marked Handling Units go into an HU / a bin ----
  pack_build_repack_hu($pane) {
    $pane.html(`<div class="wms-pc-form"><div class="wms-pc-list"></div><div class="wms-pc-dest"></div>
      <button type="button" class="btn btn-primary btn-sm wms-pc-go">${__("Repack")}</button></div>`);
    const dest = this.pack_dest_picker($pane.find(".wms-pc-dest"));
    $pane.find(".wms-pc-go").on("click", async () => {
      const rows = this.pack.sel.filter((r) => r.kind === "hu"), d = dest();
      if (!rows.length) { frappe.show_alert({ message: __("Mark the Handling Units to repack"), indicator: "orange" }); return; }
      if (!d.name) { frappe.show_alert({ message: __("Choose the destination"), indicator: "orange" }); return; }
      await this.pack_move(rows, d.kind, d.name);
    });
  }

  pack_refresh_repack_hu() {
    const rows = this.pack.sel.filter((r) => r.kind === "hu");
    this.$body.find('[data-pane="repack_hu"] .wms-pc-list').html(rows.length
      ? `<div class="text-muted">${__("Marked Handling Units")} (${rows.length})</div><div class="wms-detail-serials">${rows.map((r) => `<span class="wms-chip">${frappe.utils.escape_html(r.name)}</span>`).join("")}</div>`
      : `<div class="text-muted">${__("Mark the Handling Units to repack in the tree.")}</div>`);
  }

  // The marked product lines with a quantity each (all of it by default) - shared by Repack Product and Difference.
  pack_qty_table(rows, label, blank) {
    const esc = frappe.utils.escape_html;
    if (!rows.length) return `<div class="text-muted">${__("Mark product lines in the tree.")}</div>`;
    return `<table class="table table-sm"><thead><tr><th>${__("Product")}</th><th>${__("In")}</th><th style="text-align:right;">${esc(label)}</th></tr></thead><tbody>
      ${rows.map((r, i) => `<tr><td>${esc(r.product)}<div class="text-muted" style="font-size:11px;">${esc(r.batch_no || "")} ${esc(r.stock_type || "")}</div></td><td>${esc(r.handling_unit || r.storage_bin || "")}</td>
        <td style="text-align:right;white-space:nowrap;">${r.serial_count
          ? `<span class="text-muted" style="font-size:11px;white-space:normal;">${blank ? __("{0} serial numbers - mark the missing ones under Serial Numbers › Missing", [r.serial_count]) : __("all {0} serial numbers - to repack only some, use Serial Numbers", [r.serial_count])}</span>`
          : `<input type="number" class="form-control input-sm wms-pc-q" style="width:90px;display:inline-block;" data-i="${i}" min="0" max="${r.quantity}" step="any" value="${blank ? "" : r.quantity}"> / ${r.quantity} ${esc(r.stock_uom || "")}`}</td></tr>`).join("")}</tbody></table>`;
  }

  pack_read_qtys($pane, rows, requireAll) {
    const out = new Map();
    let bad = false;
    $pane.find(".wms-pc-q").each((_, el) => {
      const row = rows[Number(el.dataset.i)], q = parseFloat(el.value);
      if (!el.value && !requireAll) return;
      if (!(q > 0) || q > flt(row.quantity) + 1e-9) bad = true; else out.set(row.id, q);
    });
    if (bad) { frappe.show_alert({ message: __("Enter a quantity above 0, at most what is there"), indicator: "orange" }); return null; }
    if (requireAll) rows.forEach((r) => { if (r.serial_count) out.set(r.id, flt(r.quantity)); }); // serial lines: all of them
    return out;
  }

  // ---- Repack Product ----
  pack_build_repack_product($pane) {
    $pane.html(`<div class="wms-pc-form"><div class="wms-pc-list"></div><div class="wms-pc-dest"></div>
      <button type="button" class="btn btn-primary btn-sm wms-pc-go">${__("Repack")}</button></div>`);
    const dest = this.pack_dest_picker($pane.find(".wms-pc-dest"));
    $pane.find(".wms-pc-go").on("click", async () => {
      const rows = this.pack.sel.filter((r) => r.kind === "product"), d = dest();
      if (!rows.length) { frappe.show_alert({ message: __("Mark the product lines to repack"), indicator: "orange" }); return; }
      if (!d.name) { frappe.show_alert({ message: __("Choose the destination"), indicator: "orange" }); return; }
      const qtys = this.pack_read_qtys($pane, rows, true);
      if (qtys) await this.pack_move(rows, d.kind, d.name, qtys);
    });
  }

  pack_refresh_repack_product() {
    const $pane = this.$body.find('[data-pane="repack_product"]');
    $pane.find(".wms-pc-list").html(this.pack_qty_table(this.pack.sel.filter((r) => r.kind === "product"), __("Quantity"), false));
  }

  // ---- Difference: the marked lines are short - the missing quantity goes to the difference bin ----
  pack_build_difference($pane) {
    $pane.html(`<div class="wms-pc-form"><div class="wms-pc-list"></div>
      <div><label>${__("Remarks")}</label><input class="form-control input-sm wms-pc-remarks"></div>
      <button type="button" class="btn btn-danger btn-sm wms-pc-go">${__("Post difference")}</button></div>`);
    $pane.find(".wms-pc-go").on("click", async () => {
      const rows = this.pack.sel.filter((r) => r.kind === "product");
      const qtys = this.pack_read_qtys($pane, rows, false);
      if (!qtys) return;
      if (!qtys.size) { frappe.show_alert({ message: __("Enter the missing quantity of at least one line"), indicator: "orange" }); return; }
      frappe.confirm(__("Move the missing quantity of {0} line(s) to the difference bin?", [qtys.size]), async () => {
        const items = rows.filter((r) => qtys.has(r.id)).map((r) => ({ label: r.product, lines: this.pack_take(r, qtys.get(r.id)) }));
        await this.pack_post_differences(items, $pane.find(".wms-pc-remarks").val());
      });
    });
  }

  pack_refresh_difference() {
    this.$body.find('[data-pane="difference"] .wms-pc-list').html(this.pack_qty_table(this.pack.sel.filter((r) => r.kind === "product"), __("Missing"), true));
  }

  // ---- moving: drag & drop onto a node, or "Move to…" ----
  pack_dest_of(row) {
    if (row.kind === "bin") return { kind: "bin", name: row.name };
    if (row.kind === "hu") return { kind: "hu", name: row.handling_unit };
    return row.handling_unit ? { kind: "hu", name: row.handling_unit } : { kind: "bin", name: row.storage_bin };
  }

  // Left-button drag moves everything; right-button drag (opts.ask) asks how much of each product line.
  pack_drop(rows, target, opts) {
    const dest = this.pack_dest_of(target);
    const full = opts && opts.ask === false ? new Map(rows.filter((r) => r.kind === "product").map((r) => [r.id, flt(r.quantity)])) : undefined;
    return this.pack_move(rows, dest.kind, dest.name, full);
  }

  pack_move_dialog(rows) {
    this.pack_dest_dialog(__("Move {0} row(s) to…", [rows.length]), (kind, name) => this.pack_move(rows, kind, name));
  }

  pack_dest_dialog(title, onPick) {
    const d = new frappe.ui.Dialog({
      title,
      fields: [
        { fieldname: "kind", fieldtype: "Select", label: __("Destination"), options: "Handling Unit\nStorage Bin", default: "Handling Unit" },
        { fieldname: "hu", fieldtype: "Link", options: "Handling Unit", label: __("Handling Unit"), depends_on: "eval:doc.kind=='Handling Unit'", get_query: () => ({ filters: { warehouse: this.warehouse } }) },
        { fieldname: "bin", fieldtype: "Link", options: "Storage Bin", label: __("Storage Bin"), depends_on: "eval:doc.kind=='Storage Bin'", get_query: () => ({ filters: { warehouse: this.warehouse } }) },
      ],
      primary_action_label: __("Move"),
      primary_action: (v) => {
        const name = v.kind === "Handling Unit" ? v.hu : v.bin;
        if (!name) { frappe.show_alert({ message: __("Choose the destination"), indicator: "orange" }); return; }
        d.hide();
        onPick(v.kind === "Handling Unit" ? "hu" : "bin", name);
      },
    });
    d.show();
  }

  // Partial quantities: each product line asks how much (all of it by default). A serial-numbered
  // line asks WHICH serial numbers instead - a bare quantity would not say which ones moved.
  pack_ask_quantities(stockRows, destName) {
    return new Promise((resolve) => {
      let done = false;
      const esc = frappe.utils.escape_html;
      const d = new frappe.ui.Dialog({
        title: __("Repack into {0}", [destName]), fields: [{ fieldtype: "HTML", fieldname: "body" }], primary_action_label: __("Repack"),
        primary_action: () => {
          const out = new Map();
          let bad = false;
          const $b = $(d.fields_dict.body.wrapper);
          $b.find("input.wms-pc-q").each((_, el) => {
            const row = stockRows[Number(el.dataset.i)], q = parseFloat(el.value);
            if (!(q > 0) || q > flt(row.quantity) + 1e-9) bad = true; else out.set(row.id, q);
          });
          $b.find(".wms-sn-pick").each((_, el) => {
            const row = stockRows[Number(el.dataset.i)];
            const lines = $(el).find("input[type=checkbox]:checked").map((_, c) => row.lines[Number(c.dataset.k)]).get();
            if (!lines.length) bad = true; else out.set(row.id, { lines });
          });
          if (bad) { frappe.show_alert({ message: __("Enter a quantity above 0, at most what is there - and choose at least one serial number for serial-numbered lines"), indicator: "orange" }); return; }
          done = true; d.hide(); resolve(out);
        },
      });
      d.onhide = () => { if (!done) resolve(null); };
      const cell = (r, i) => r.serial_count
        ? `<div class="wms-sn-pick" data-i="${i}"><div class="text-muted" style="font-size:11px;">${__("Choose the serial numbers to repack")}</div>
            <input class="form-control input-sm wms-sn-filter" placeholder="${__("Filter serial numbers")}" style="margin:2px 0;">
            <div class="wms-sn-links"><a href="#" class="wms-sn-all">${__("all")}</a> · <a href="#" class="wms-sn-none">${__("none")}</a> · <span class="wms-sn-count"></span></div>
            <div class="wms-sn-list">${r.lines.map((l, k) => `<label><input type="checkbox" data-k="${k}" data-sn="${esc(l.serial_no || "")}" checked> ${esc(l.serial_no || "")}</label>`).join("")}</div></div>`
        : `<input type="number" class="form-control input-sm wms-pc-q" style="width:100px;display:inline-block;" data-i="${i}" min="0" max="${r.quantity}" step="any" value="${r.quantity}"> / ${r.quantity} ${esc(r.stock_uom || "")}`;
      const $b = $(d.fields_dict.body.wrapper);
      $b.html(`<table class="table table-sm"><thead><tr><th>${__("Product")}</th><th>${__("From")}</th><th>${__("Batch")}</th><th>${__("Stock Type")}</th><th>${__("Quantity")}</th></tr></thead><tbody>
        ${stockRows.map((r, i) => `<tr><td>${esc(r.product)}</td><td>${esc(r.handling_unit || r.storage_bin || "")}</td><td>${esc(r.batch_no || "")}</td><td>${esc(r.stock_type || "")}</td><td style="min-width:220px;">${cell(r, i)}</td></tr>`).join("")}</tbody></table>`);
      $b.find(".wms-sn-pick").each((_, el) => {
        const $p = $(el), count = () => $p.find(".wms-sn-count").text(__("{0} chosen", [$p.find("input[type=checkbox]:checked").length]));
        count();
        $p.find(".wms-sn-filter").on("input", (e) => { const q = e.target.value.toLowerCase(); $p.find("label").each((_, l) => $(l).toggle(!q || String($(l).find("input").data("sn")).toLowerCase().includes(q))); });
        $p.find(".wms-sn-all").on("click", (e) => { e.preventDefault(); $p.find("label:visible input").prop("checked", true); count(); });
        $p.find(".wms-sn-none").on("click", (e) => { e.preventDefault(); $p.find("label:visible input").prop("checked", false); count(); });
        $p.on("change", "input[type=checkbox]", count);
      });
      d.show();
    });
  }

  // `quantity` of a product row, taken from its stock lines one after the other.
  pack_take(row, quantity) {
    if (quantity && quantity.lines) return quantity.lines.map((l) => ({ ...l, quantity: flt(l.quantity) }));
    let need = quantity, out = [];
    for (const ln of row.lines) {
      if (need <= 1e-9) break;
      const q = Math.min(flt(ln.quantity), need);
      out.push({ ...ln, quantity: q }); need -= q;
    }
    return out;
  }

  async pack_move(rows, kind, name, presetQtys) {
    rows = rows.filter((r) => r.kind === "hu" || r.kind === "product");
    if (!rows.length) { frappe.show_alert({ message: __("Mark Handling Units or product lines to move"), indicator: "orange" }); return; }
    if (kind === "hu" && rows.some((r) => r.kind === "hu" && r.handling_unit === name)) { frappe.show_alert({ message: __("A Handling Unit cannot be packed into itself"), indicator: "orange" }); return; }
    const stockRows = rows.filter((r) => r.kind === "product");
    let qtys = new Map();
    if (stockRows.length) { qtys = presetQtys || await this.pack_ask_quantities(stockRows, name); if (!qtys) return; }
    const items = rows.map((r) => r.kind === "hu" ? { kind: "hu", name: r.handling_unit, label: r.name }
      : { kind: "stock", label: r.product, lines: this.pack_take(r, qtys.get(r.id)) });
    return this.pack_run_move(items, kind, name);
  }

  async pack_run_move(items, kind, name) {
    const res = await frappe.call("frappe_wms.api.packing_center.move_nodes", {
      warehouse: this.warehouse, items: JSON.stringify(items), destination_kind: kind, destination: name,
      idempotency_key: `PC-${Date.now()}-${Math.random().toString(36).slice(2, 8)}` }).then((r) => r.message);
    frappe.show_alert({ message: __("Moved {0} of {1}", [res.moved, items.length]), indicator: res.errors.length ? "orange" : "green" });
    if (res.errors.length) frappe.msgprint({ title: __("Not moved"), indicator: "orange", message: res.errors.map((e) => `<b>${frappe.utils.escape_html(e.item)}</b>: ${frappe.utils.escape_html(e.error)}`).join("<br>") });
    const destRow = kind === "hu" ? this.pack.byId.get(`hu:${name}`) : null;
    const destBin = kind === "bin" ? name : destRow && destRow.storage_bin;
    if (destBin && !this.pack.roots.includes(destBin)) this.pack.extraBins.add(destBin);
    if (kind === "hu") this.pack.extraHus.add(name);
    this.pack.newIds = new Set();
    await this.pack_reload(false);
    this.pack.expanded.add(kind === "hu" ? `hu:${name}` : `bin:${name}`);
    this.pack_open_path([kind === "hu" ? `hu:${name}` : `bin:${name}`]);
    this.pack_draw();
  }

  pack_actions() {
    const each = (label, method, msg) => async (rows) => {
      let ok = 0;
      for (const r of rows) {
        try { await frappe.call(`frappe_wms.api.handling_unit.${method}`, { hu_name: r.handling_unit }); ok++; } catch (e) { /* frappe shows the reason */ }
      }
      frappe.show_alert({ message: __(msg, [ok, rows.length]), indicator: ok === rows.length ? "green" : "orange" });
      await this.pack_reload(false);
    };
    const allHu = (rows) => rows.every((r) => r.kind === "hu");
    return [
      { label: __("Move to…"), kind: "primary", appliesTo: (r) => r.kind === "hu" || r.kind === "product", run: (rows) => this.pack_move_dialog(rows) },
      { label: __("Block"), appliesTo: (r) => r.kind === "hu" && r.hu_status !== "Blocked", run: each("Block", "block_handling_unit", "Blocked {0} of {1} Handling Unit(s)") },
      { label: __("Unblock"), appliesTo: (r) => r.kind === "hu" && r.hu_status === "Blocked", run: each("Unblock", "unblock_handling_unit", "Unblocked {0} of {1} Handling Unit(s)") },
      { label: __("Unnest"), appliesTo: (r) => r.kind === "hu" && !!r.parent_hu, run: each("Unnest", "unnest_handling_unit", "Unnested {0} of {1} Handling Unit(s)") },
      { label: __("Delete HU"), kind: "danger", appliesTo: (r) => r.kind === "hu",
        confirm: (rows) => __("Delete (recycle) {0} Handling Unit(s)? Each must be empty, unnested and without nested HUs. Frees their numbers for reuse; cannot be undone.", [rows.length]),
        run: each("Delete", "recycle_handling_unit", "Deleted {0} of {1} Handling Unit(s)") },
    ];
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

  // ---------- Shipping & Receiving cockpit (services/yard.py cockpit/plan_truck) ----------
  async search_cockpit() {
    if (!this.warehouse) return;
    const $wrap = this.body_for("cockpit");
    const esc = frappe.utils.escape_html;
    const [data, board] = await Promise.all([
      frappe.call("frappe_wms.api.yard.cockpit", { warehouse: this.warehouse }).then((r) => r.message),
      frappe.call("frappe_wms.api.yard.yard_board", { warehouse: this.warehouse }).then((r) => r.message)]);
    this.yard = board;
    const plan = (kind, name) => `<button type="button" class="btn btn-xs btn-primary wms-mon-cp-plan" data-k="${kind}" data-n="${esc(name)}">${__("Plan Truck")}</button>`;
    const section = (title, rows, cols) => `<h6 style="margin:14px 0 6px;">${title} (${rows.length})</h6>` + (rows.length ? "" : `<div class="text-muted">${__("Nothing waiting")}</div>`);
    $wrap.empty();
    const add = (title, rows, columns, doctype) => {
      $wrap.append(section(title, rows));
      if (rows.length) $wrap.append(this.render_table(rows, columns, doctype));
    };
    add(__("Trucks"), data.trucks, [["name", __("Appointment")], ["direction", __("Direction")], ["status", __("Status")], ["units", __("Units")], ["activity_status", __("Activity")], ["door", __("Door")], ["vehicle_registration", __("Vehicle")], ["ref", __("Carries"), (r) => esc(r.inbound_delivery || r.shipment || "")],
      ["actions", "", (r) => (["Planned", "Checked In", "At Door"].includes(r.status) ? `<button type="button" class="btn btn-xs btn-default wms-mon-cp-add" data-a="${esc(r.name)}" data-d="${esc(r.direction)}">${__("Add Delivery")}</button> ` : "") + (r.next_action ? `<button type="button" class="btn btn-xs btn-default wms-mon-cp-act" data-a="${esc(r.name)}" data-m="${{ "Check In": "check_in", "To Door": "to_door", "Complete": "complete", "Check Out": "check_out" }[r.next_action]}">${__(r.next_action)}</button>` : "")]], "WMS Dock Appointment");
    add(__("Inbound deliveries without a truck"), data.inbound_without_truck, [["name", __("Delivery")], ["supplier", __("Supplier")], ["expected_arrival", __("Expected")], ["status", __("Status")],
      ["actions", "", (r) => plan("Inbound", r.name)]], "Inbound Delivery");
    add(__("Shipments without a truck"), data.shipments_without_truck, [["name", __("Shipment")], ["carrier", __("Carrier")], ["route", __("Route")], ["status", __("Status")],
      ["actions", "", (r) => plan("Shipment", r.name)]], "WMS Shipment");
    add(__("Picked deliveries without a shipment"), data.deliveries_without_shipment, [["name", __("Delivery")], ["customer", __("Customer")], ["route", __("Route")], ["delivery_date", __("Delivery Date")],
      ["actions", "", (r) => plan("Delivery", r.name)]], "Outbound Delivery");
    $wrap.find(".wms-mon-cp-act").on("click", (e) => this.yard_action(e.currentTarget.dataset.m, e.currentTarget.dataset.a));
    $wrap.find(".wms-mon-cp-add").on("click", (e) => this.add_to_truck_dialog(e.currentTarget.dataset.a, e.currentTarget.dataset.d));
    $wrap.find(".wms-mon-cp-plan").on("click", (e) => this.plan_truck_dialog(e.currentTarget.dataset.k, e.currentTarget.dataset.n));
  }

  add_to_truck_dialog(appointment, direction) {
    const inbound = direction === "Inbound";
    const d = new frappe.ui.Dialog({ title: __("Add a delivery to {0}", [appointment]), fields: [
      { fieldname: "ref", fieldtype: "Link", options: inbound ? "Inbound Delivery" : "WMS Shipment", label: inbound ? __("Inbound Delivery") : __("Shipment"), reqd: 1 }],
      primary_action_label: __("Add"), primary_action: (v) => {
        frappe.call("frappe_wms.api.yard.add_to_truck", Object.assign({ appointment }, inbound ? { inbound_delivery: v.ref } : { shipment: v.ref }))
          .then(() => { d.hide(); frappe.show_alert({ message: __("Added to the truck"), indicator: "green" }); this.search_cockpit(); }); } });
    d.show();
  }

  plan_truck_dialog(kind, name) {
    const d = new frappe.ui.Dialog({ title: __("Plan Truck for {0}", [name]), fields: [
      { fieldname: "planned_start", fieldtype: "Datetime", label: __("Arrival"), reqd: 1, default: moment().add(1, "hours").format("YYYY-MM-DD HH:mm:ss") },
      { fieldname: "carrier", fieldtype: "Data", label: __("Carrier") }, { fieldname: "vehicle_registration", fieldtype: "Data", label: __("Vehicle Registration") },
      { fieldname: "trailer_number", fieldtype: "Data", label: __("Trailer") }, { fieldname: "driver_name", fieldtype: "Data", label: __("Driver") },
      { fieldname: "door", fieldtype: "Select", label: __("Door"), options: [""].concat((this.yard.doors || []).map((x) => x.door)), description: __("Empty: the first free door") },
      { fieldname: "means_of_transport", fieldtype: "Link", options: "Means of Transport", label: __("Means of Transport") },
      ...(kind === "Delivery" ? [] : [{ fieldname: "also_carries", fieldtype: "Small Text", label: kind === "Inbound" ? __("More inbound deliveries on this vehicle") : __("More shipments on this vehicle"), description: __("One per line - each gets its own transportation unit") }])],
      primary_action_label: __("Plan"), primary_action: (v) => {
        const more = (v.also_carries || "").split("\n").map((x) => x.trim()).filter(Boolean);
        delete v.also_carries;
        const args = Object.assign({ warehouse: this.warehouse, direction: kind === "Inbound" ? "Inbound" : "Outbound" }, v);
        if (more.length) args[kind === "Inbound" ? "inbound_deliveries" : "shipments"] = more;
        if (kind === "Inbound") args.inbound_delivery = name; else if (kind === "Shipment") args.shipment = name; else args.outbound_deliveries = [name];
        frappe.call("frappe_wms.api.yard.plan_truck", args).then(() => { d.hide(); frappe.show_alert({ message: __("Truck planned"), indicator: "green" }); this.search_cockpit(); });
      } });
    d.show();
  }

  async yard_action(method, appointment) {
    const call = (args) => frappe.call(`frappe_wms.api.yard.${method}`, Object.assign({ appointment }, args || {}))
      .then(() => { frappe.show_alert({ message: __("Done"), indicator: "green" }); this.search_yard(); this.search_cockpit(); });
    if (method === "check_in") {
      const d = new frappe.ui.Dialog({ title: __("Check in {0}", [appointment]), fields: [
        { fieldname: "yard_bin", fieldtype: "Select", label: __("Yard spot"), options: [""].concat(this.yard.yard_spots || []) },
        { fieldname: "checkpoint", fieldtype: "Select", label: __("Checkpoint"), options: [""].concat((this.yard && this.yard.checkpoints) || []) }],
        primary_action_label: __("Check in"), primary_action: (v) => { d.hide(); frappe.call("frappe_wms.api.yard.check_in", { warehouse: this.warehouse, appointment, yard_bin: v.yard_bin || undefined, checkpoint: v.checkpoint || undefined })
          .then(() => { frappe.show_alert({ message: __("Checked in"), indicator: "green" }); this.search_yard(); this.search_cockpit(); }); } });
      d.show(); return;
    }
    if (method === "check_out" && ((this.yard && this.yard.checkpoints) || []).length) {
      const d = new frappe.ui.Dialog({ title: __("Check out {0}", [appointment]), fields: [
        { fieldname: "checkpoint", fieldtype: "Select", label: __("Checkpoint"), options: [""].concat((this.yard && this.yard.checkpoints) || []) }],
        primary_action_label: __("Check out"), primary_action: (v) => { d.hide(); call({ checkpoint: v.checkpoint || undefined }); } });
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
      { fieldname: "checkpoint", fieldtype: "Select", label: __("Checkpoint"), options: [""].concat((this.yard && this.yard.checkpoints) || []) },
    ], primary_action_label: __("Check in"), primary_action: async (v) => {
      const args = Object.assign({ warehouse: this.warehouse }, v, { yard_bin: v.yard_bin || undefined, checkpoint: v.checkpoint || undefined });
      let r = (await frappe.call("frappe_wms.api.yard.check_in", args)).message;
      if (r.needs_confirmation) {
        if (!(await new Promise((res) => frappe.confirm(r.needs_confirmation, () => res(true), () => res(false))))) return;
        r = (await frappe.call("frappe_wms.api.yard.check_in", Object.assign(args, { confirm_without_appointment: 1 }))).message;
      }
      d.hide(); frappe.show_alert({ message: __("Checked in as {0}", [r.appointment]), indicator: "green" }); this.search_yard(); this.search_cockpit();
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
