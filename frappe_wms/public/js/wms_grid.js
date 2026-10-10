// The data grid of the Monitor and the delivery screens (SAP ALV style): sorting, filters, grouping, totals, selection with quick actions. Shared so both pages use one.
(function () {
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
          <button type="button" class="btn btn-default btn-xs wms-grid-group-toggle" title="${__("Group the rows by one to three columns")}">${__("Group by")} &#9662;</button>
          <span class="wms-grid-groupsel" style="${this.groupBy.length ? "" : "display:none;"}">${[0, 1, 2].map((i) => `<select class="form-control input-xs wms-grid-group" data-lvl="${i}"><option value="">${i ? "› " + __("then") + "…" : __("(none)")}</option>${this.columns.map(([f, l]) => `<option value="${frappe.utils.escape_html(f)}" ${this.groupBy[i] === f ? "selected" : ""}>${frappe.utils.escape_html(l)}</option>`).join("")}</select>`).join("")}</span>
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
    $toolbar.find(".wms-grid-group-toggle").on("click", () => $toolbar.find(".wms-grid-groupsel").toggle());
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

  // SAP's message convention: success is a short notice that goes away by itself; a refusal is a popup that Enter (or Esc) dismisses, so the user can correct the rows and try again.
  function report(successText, errors) {
    if (successText) frappe.show_alert({ message: successText, indicator: errors && errors.length ? "orange" : "green" }, 6);
    if (!errors || !errors.length) return;
    const esc = frappe.utils.escape_html;
    const d = new frappe.ui.Dialog({ title: __("Not possible"), primary_action_label: __("OK"), primary_action: () => d.hide(),
      fields: [{ fieldtype: "HTML", fieldname: "list", options: `<div style="max-height:320px;overflow:auto">${errors.map(([what, why]) => `<div style="margin-bottom:6px"><b>${esc(what)}</b><br>${esc(why)}</div>`).join("")}</div>` }] });
    d.$wrapper.find(".modal-header .modal-title").prepend('<span class="indicator red" style="margin-right:6px"></span>');
    d.show();
    $(document).on("keydown.wmsreport", (e) => { if (e.key === "Enter") { e.preventDefault(); d.hide(); setTimeout(() => { if (d.$wrapper.hasClass("show")) d.hide(); }, 400); } });  // a hide during the fade-in is ignored, so try again  // Enter dismisses wherever the focus is
    d.$wrapper.one("hidden.bs.modal", () => $(document).off("keydown.wmsreport"));
    setTimeout(() => d.get_primary_btn().focus(), 300);
  }

  window.wms_grid = { DataGrid, report };
})();
