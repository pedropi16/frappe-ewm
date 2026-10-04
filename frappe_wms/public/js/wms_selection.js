// SAP Business Client-style selection screens for the WMS Monitor.
//
// Every field on the screen holds a RANGES-like criterion: {include: [row...], exclude: [row...]},
// each row {op, low, high} with op one of eq ne gt ge lt le bt nb cp np. The single-line input
// accepts a shortcut syntax so power users never need the dialog:
//
//   A*  / *A / A+B   pattern ('*' any characters, '+' one character)
//   >=10  >10  <=10  <10  <>X  =   (a bare '=' means "is blank")
//   10..20           range (between)
//   a;b;c            several values (OR)
//   !X  !A*  !1..5   exclude
//
// and pasting a column copied from Excel into a field turns every line into one value.
// The server compiles the same structure (frappe_wms/services/selection.py).
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.wms_selection = Object.assign(root.wms_selection || {}, api);
})(typeof self !== "undefined" ? self : this, function () {
  const NUMERIC = new Set(["Int", "Float", "Currency", "Percent", "Check", "Rating", "Duration"]);
  const DATES = new Set(["Date", "Datetime"]);
  const OPS = [
    ["eq", "="], ["ne", "≠"], ["gt", ">"], ["ge", "≥"], ["lt", "<"], ["le", "≤"],
    ["bt", "[ ] between"], ["nb", "] [ not between"], ["cp", "* pattern"], ["np", "!* not pattern"],
  ];
  const PREFIX = { ">=": "ge", "<=": "le", "<>": "ne", "!=": "ne", ">": "gt", "<": "lt", "=": "eq" };
  const PREFIX_OF = { ge: ">=", le: "<=", ne: "<>", gt: ">", lt: "<" };

  function isText(fieldtype) { return !NUMERIC.has(fieldtype) && !DATES.has(fieldtype); }

  // One token of the shortcut syntax -> {sign: "include"|"exclude", row}
  function parseToken(token, fieldtype) {
    let t = String(token == null ? "" : token).trim();
    if (!t) return null;
    let sign = "include";
    if (t[0] === "!" && t[1] !== "=") { sign = "exclude"; t = t.slice(1).trim(); }
    let m = t.match(/^(>=|<=|<>|!=|>|<|=)(.*)$/);
    if (m) return { sign, row: { op: PREFIX[m[1]], low: m[2].trim() } };
    m = t.match(/^(.*?)\.\.(.*)$/);
    if (m) return { sign, row: { op: "bt", low: m[1].trim(), high: m[2].trim() } };
    if (isText(fieldtype) && /[*+]/.test(t)) return { sign, row: { op: "cp", low: t } };
    return { sign, row: { op: "eq", low: t } };
  }

  // "a;b;!c" -> {include: [...], exclude: [...]}; returns null for an empty input.
  function parseShortcut(text, fieldtype) {
    const crit = { include: [], exclude: [] };
    String(text == null ? "" : text).split(";").forEach((tok) => {
      const p = parseToken(tok, fieldtype);
      if (p) crit[p.sign].push(p.row);
    });
    return crit.include.length || crit.exclude.length ? crit : null;
  }

  // A pasted block (Excel column, or a row of cells) -> one value per line/cell.
  function parsePasted(text, fieldtype) {
    const crit = { include: [], exclude: [] };
    String(text || "").split(/\r?\n|\t/).forEach((line) => {
      const p = parseToken(line, fieldtype);
      if (p) crit[p.sign].push(p.row);
    });
    return crit;
  }

  function countOf(crit) { return crit ? (crit.include || []).length + (crit.exclude || []).length : 0; }

  // The inverse of parseShortcut, or null when the criterion can't be written on one line
  // faithfully (a value containing ';', an operator the syntax has no spelling for, ...).
  function formatShortcut(crit, fieldtype) {
    if (!countOf(crit)) return "";
    const out = [];
    const one = (row, sign) => {
      const low = row.low == null ? "" : String(row.low), high = row.high == null ? "" : String(row.high);
      if ([low, high].some((v) => v.includes(";") || v.includes(".."))) return null;
      const bang = sign === "exclude" ? "!" : "";
      if (row.op === "bt") return `${bang}${low}..${high}`;
      if (row.op === "cp") return /[*+]/.test(low) ? `${bang}${low}` : null;
      if (row.op === "eq") {
        if (low === "") return `${bang}=`;
        if (/^[!<>=]/.test(low) || (isText(fieldtype) && /[*+]/.test(low))) return null;
        return `${bang}${low}`;
      }
      if (sign === "include" && PREFIX_OF[row.op]) return `${PREFIX_OF[row.op]}${low}`;
      return null;
    };
    for (const sign of ["include", "exclude"]) {
      for (const row of crit[sign] || []) {
        const t = one(row, sign);
        if (t === null) return null;
        out.push(t);
      }
    }
    return out.join(";");
  }

  return { OPS, NUMERIC, DATES, parseToken, parseShortcut, parsePasted, formatShortcut, countOf };
});

// ---------------------------------------------------------------------------------------------
// The selection-screen widget (desk only).
if (typeof frappe !== "undefined") (function () {
  const S = window.wms_selection;
  const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
  const API = "frappe_wms.api.selection";
  const MAX_ROWS_RENDERED = 300;

  const TONES = [
    ["green", /^(completed|confirmed|posted|picked|released|done|loaded|shipped|active|unrestricted|available|yes|full)/i],
    ["orange", /(partial|in process|in progress|under review|pending|draft|planned|quality|started)/i],
    ["red", /(exception|blocked|cancel|on hold|reversed|failed|rejected|error|difference)/i],
    ["blue", /^(open|assigned|allocated|new|not started|scheduled|requested)/i],
  ];
  function pill(v, tone) {
    if (v == null || v === "") return "";
    const t = tone || (TONES.find(([, re]) => re.test(String(v))) || ["gray"])[0];
    return `<span class="wms-pill ${t}">${esc(v)}</span>`;
  }
  S.pill = pill;

  let stylesDone = false;
  function styles() {
    if (stylesDone) return;
    stylesDone = true;
    $("<style>", { text: `
      .wms-pill { display:inline-block; padding:1px 9px; border-radius:10px; font-size:11px; font-weight:600; line-height:18px; background:var(--gray-100,#f3f4f6); color:var(--gray-700,#374151); }
      .wms-pill.green { background:var(--green-100,#dcfce7); color:var(--green-700,#15803d); }
      .wms-pill.orange { background:var(--orange-100,#ffedd5); color:var(--orange-700,#c2410c); }
      .wms-pill.red { background:var(--red-100,#fee2e2); color:var(--red-700,#b91c1c); }
      .wms-pill.blue { background:var(--blue-100,#dbeafe); color:var(--blue-700,#1d4ed8); }
      .wms-sel-compact { display:flex; flex-wrap:wrap; align-items:center; gap:8px; padding:6px 10px; margin-bottom:8px; border:1px solid var(--border-color); border-radius:8px; background:var(--card-bg,#fff); }
      .wms-sel-summary { font-size:12px; flex:1; min-width:200px; overflow:hidden; text-overflow:ellipsis; }
      .wms-sel { border:1px solid var(--border-color); border-radius:8px; padding:10px 12px; margin-bottom:10px; background:var(--card-bg,#fff); }
      .wms-sel-bar { display:flex; flex-wrap:wrap; align-items:center; gap:6px; margin-bottom:8px; }
      .wms-sel-bar .wms-sel-spacer { flex:1; }
      .wms-sel-bar select, .wms-sel-bar input { height:28px; }
      .wms-sel-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(430px, 1fr)); gap:4px 18px; }
      .wms-sel-row { display:grid; grid-template-columns:150px 1fr 22px 1fr 44px 20px; align-items:center; gap:4px; }
      .wms-sel-row label { margin:0; font-size:12px; color:var(--text-muted); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
      .wms-sel-row input { height:26px; font-size:12px; }
      .wms-sel-row .wms-sel-to-lbl { font-size:11px; color:var(--text-muted); text-align:center; }
      .wms-sel-multi { height:26px; padding:0 6px; font-size:12px; }
      .wms-sel-multi.has-incl { color:var(--green-600,#16a34a); border-color:var(--green-500,#22c55e); }
      .wms-sel-multi.has-excl { color:var(--red-600,#dc2626); border-color:var(--red-500,#ef4444); }
      .wms-sel-remove { cursor:pointer; opacity:.35; text-align:center; }
      .wms-sel-remove:hover { opacity:1; }
      .wms-sel-help { font-size:11px; color:var(--text-muted); margin-top:6px; }
      .wms-sel-help code { font-size:11px; padding:0 3px; }
      .wms-sel-status { font-size:12px; margin:4px 0; }
      .wms-ms-tabs { display:flex; gap:4px; margin-bottom:8px; }
      .wms-ms-tab { padding:4px 12px; border-radius:6px; cursor:pointer; border:1px solid var(--border-color); font-size:12px; }
      .wms-ms-tab.active.inc { background:rgba(34,197,94,.12); border-color:#22c55e; }
      .wms-ms-tab.active.exc { background:rgba(239,68,68,.12); border-color:#ef4444; }
      .wms-ms-rows { max-height:45vh; overflow:auto; border:1px solid var(--border-color); border-radius:6px; padding:6px; }
      .wms-ms-row { display:grid; grid-template-columns:150px 1fr 1fr 24px; gap:4px; margin-bottom:3px; }
      .wms-ms-row select, .wms-ms-row input { height:26px; font-size:12px; }
      .wms-ms-actions { display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; align-items:center; }
      .wms-ms-paste { width:100%; min-height:54px; font-size:12px; margin-top:8px; }
      .wms-cols-list { max-height:50vh; overflow:auto; border:1px solid var(--border-color); border-radius:6px; padding:4px 8px; }
      .wms-cols-item { display:flex; align-items:center; gap:6px; padding:2px 0; font-size:12px; }
      .wms-cols-item .wms-cols-move { cursor:pointer; opacity:.5; }
      .wms-cols-item .wms-cols-move:hover { opacity:1; }
      .wms-cols-item label { margin:0; font-weight:normal; flex:1; }
    ` }).appendTo("head");
  }

  class SelectionScreen {
    // opts: {view, $mount, $results, getWarehouse(), makeGrid(rows, columns, gridOpts), decorate: {renderers, extraColumns, actions, afterRender}}
    constructor(opts) {
      this.opts = opts;
      this.view = opts.view;
      this.criteria = {};
      this.dirty = new Set();
      this.maxHits = 500;
      this.layout = null;
      this.variant = "";
      this.layoutVariant = "";
      this.lastRows = null;
      this.fetchedColumns = null;
      styles();
    }

    async init() {
      const r = await frappe.call(`${API}.get_selection_screen`, { view: this.view });
      const m = r.message;
      this.meta = m;
      this.fields = {};
      m.fields.forEach((f) => { this.fields[f.fieldname] = f; });
      this.selFields = m.selection.filter((f) => this.fields[f]);
      this.criteria = JSON.parse(JSON.stringify(m.default_criteria || {}));
      this.variants = m.variants || [];
      const defSel = this.variants.find((v) => v.variant_type === "Selection" && v.mine && v.is_default);
      const defLay = this.variants.find((v) => v.variant_type === "Layout" && v.mine && v.is_default);
      if (defSel) this.applyVariant(defSel, false);
      if (defLay) { this.layout = defLay.layout; this.layoutVariant = defLay.name; }
      this.render();
      if (!this.opts.autoOpen || this.opts.autoOpen()) this.openDialog();
      return this;
    }

    // ---------- rendering ----------
    render() {
      const varOpts = [`<option value="">${__("(no variant)")}</option>`].concat(this.variants.filter((v) => v.variant_type === "Selection").map((v) =>
        `<option value="${esc(v.name)}" ${v.name === this.variant ? "selected" : ""}>${esc(v.variant_name)}${v.is_global ? " \u{1F310}" : ""}${v.is_default ? " ★" : ""}</option>`));
      this.$el = $(`
        <div class="wms-sel" tabindex="-1">
          <div class="wms-sel-bar">
            <span class="text-muted" style="font-size:12px;">${__("Variant")}</span>
            <select class="form-control input-sm wms-sel-variant" style="width:200px;">${varOpts.join("")}</select>
            <button type="button" class="btn btn-default btn-xs wms-sel-save">${__("Save as Variant")}</button>
            <button type="button" class="btn btn-default btn-xs wms-sel-delete" ${this.variantDoc() && this.variantDoc().mine ? "" : "disabled"}>${__("Delete")}</button>
            <button type="button" class="btn btn-default btn-xs wms-sel-fields">${__("Fields…")}</button>
            <button type="button" class="btn btn-default btn-xs wms-sel-clear">${__("Clear")}</button>
            <span class="wms-sel-spacer"></span>
            <span class="text-muted" style="font-size:12px;">${__("Max. hits")}</span>
            <input type="number" min="1" max="50000" class="form-control input-sm wms-sel-maxhits" style="width:90px;" value="${esc(this.maxHits)}">
          </div>
          <div class="wms-sel-grid"></div>
          <div class="wms-sel-help">${__("Syntax")}: <code>A*</code> ${__("pattern")} · <code>&gt;=10</code> <code>&lt;&gt;X</code> · <code>10..20</code> ${__("range")} · <code>a;b;c</code> ${__("list")} · <code>!X</code> ${__("exclude")} · <code>=</code> ${__("blank")} · ${__("paste a column from Excel into any field")} · ${__("the arrow button opens multiple selection with include/exclude")}</div>
        </div>`);
      this.renderRows();
      this.$el.find(".wms-sel-variant").on("change", (e) => {
        const v = this.variants.find((x) => x.name === e.target.value);
        if (v) this.applyVariant(v, true); else { this.variant = ""; this.render(); }
      });
      this.$el.find(".wms-sel-save").on("click", () => this.saveVariantDialog());
      this.$el.find(".wms-sel-delete").on("click", () => this.deleteVariant());
      this.$el.find(".wms-sel-fields").on("click", () => this.fieldsDialog());
      this.$el.find(".wms-sel-clear").on("click", () => { this.criteria = {}; this.dirty.clear(); this.renderRows(); });
      this.$el.find(".wms-sel-maxhits").on("change", (e) => { this.maxHits = Math.max(1, Math.min(50000, parseInt(e.target.value, 10) || 500)); });
      this.$el.on("keydown", (e) => { if (e.key === "F8") { e.preventDefault(); this.execute(); } });
      if (this.$dlgBody) this.$dlgBody.empty().append(this.$el);
      this.renderBar();
    }

    // The selection screen itself lives in a dialog; the page only keeps this one-line bar so the
    // results get the whole monitor.
    renderBar() {
      const $m = this.opts.$mount.empty();
      const v = this.variantDoc();
      const $bar = $(`
        <div class="wms-sel-compact">
          <button type="button" class="btn btn-primary btn-sm wms-sel-open">${__("Selection…")}</button>
          <button type="button" class="btn btn-default btn-sm wms-sel-run" title="${__("Run the current selection again")}">&#8635; ${__("Refresh")}</button>
          ${v ? `<span class="wms-pill blue">${esc(v.variant_name)}</span>` : ""}
          <span class="wms-sel-summary text-muted">${this.summaryHtml()}</span>
        </div>`).appendTo($m);
      $bar.find(".wms-sel-open").on("click", () => this.openDialog());
      $bar.find(".wms-sel-run").on("click", () => this.execute());
    }

    summaryHtml() {
      const parts = Object.entries(this.criteria).filter(([, c]) => S.countOf(c)).map(([f, c]) => {
        const df = this.fields[f]; if (!df) return "";
        const t = S.formatShortcut(c, df.fieldtype);
        return `<b>${esc(df.label)}</b> ${t === null ? __("{0} values", [S.countOf(c)]) : esc(t)}`;
      }).filter(Boolean);
      return parts.length ? parts.join(" &middot; ") : __("No restrictions - everything in the warehouse");
    }

    openDialog() {
      if (!this.dialog) {
        this.dialog = new frappe.ui.Dialog({
          title: __("Selection - {0}", [this.meta.title]), size: "extra-large",
          fields: [{ fieldtype: "HTML", fieldname: "body" }],
          primary_action_label: __("Execute") + " (F8)", primary_action: () => this.execute(),
        });
        this.$dlgBody = $(this.dialog.fields_dict.body.wrapper);
        this.$dlgBody.append(this.$el);
      }
      this.dialog.show();
    }

    closeDialog() { if (this.dialog) this.dialog.hide(); }

    // Programmatic search (links between monitor views): {field: [values]} -> equals-any criteria.
    async runWith(values) {
      this.criteria = {};
      Object.entries(values).forEach(([f, vals]) => {
        if (!this.fields[f] || !vals.length) return;
        if (!this.selFields.includes(f)) this.selFields.push(f);
        this.criteria[f] = { include: vals.map((low) => ({ op: "eq", low })), exclude: [] };
      });
      this.variant = "";
      this.dirty.clear();
      this.render();
      return this.execute();
    }

    variantDoc() { return this.variants.find((v) => v.name === this.variant); }

    renderRows() {
      const $grid = this.$el.find(".wms-sel-grid").empty();
      this.selFields.forEach((f) => $grid.append(this.renderRow(f)));
    }

    renderRow(fieldname) {
      const df = this.fields[fieldname];
      const crit = this.criteria[fieldname];
      const single = crit && (crit.include || []).length === 1 && !(crit.exclude || []).length && crit.include[0].op === "bt" ? crit.include[0] : null;
      const text = single ? single.low : S.formatShortcut(crit, df.fieldtype);
      const complex = text === null;
      const n = S.countOf(crit);
      const hasExcl = crit && (crit.exclude || []).length;
      const listId = df.fieldtype === "Select" && df.options ? `wms-sel-dl-${this.view}-${fieldname}` : null;
      const ph = S.DATES.has(df.fieldtype) ? "YYYY-MM-DD" : "";
      const $row = $(`
        <div class="wms-sel-row" data-field="${esc(fieldname)}">
          <label title="${esc(df.label)} (${esc(fieldname)})">${esc(df.label)}</label>
          <input class="form-control input-sm wms-sel-from" ${listId ? `list="${listId}"` : ""} placeholder="${ph}" value="${esc(complex ? "" : text)}" ${complex ? `readonly placeholder="${esc(__("multiple selection"))}"` : ""}>
          <span class="wms-sel-to-lbl">${__("to")}</span>
          <input class="form-control input-sm wms-sel-to" placeholder="${ph}" value="${esc(single ? single.high : "")}" ${complex ? "readonly" : ""}>
          <button type="button" class="btn btn-default btn-xs wms-sel-multi ${n > 1 || (n && complex) ? "has-incl" : ""} ${hasExcl ? "has-excl" : ""}" title="${__("Multiple selection")}">⇨${n > 1 || hasExcl ? " " + n : ""}</button>
          <span class="wms-sel-remove" title="${__("Remove field from screen")}">×</span>
          ${listId ? `<datalist id="${listId}">${String(df.options).split("\n").filter(Boolean).map((o) => `<option value="${esc(o)}">`).join("")}</datalist>` : ""}
        </div>`);
      if (complex) $row.find(".wms-sel-from").attr("placeholder", __("{0} values - use the arrow", [n]));
      $row.find("input").on("input", () => this.dirty.add(fieldname))
        .on("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); this.execute(); } });
      $row.find(".wms-sel-from").on("paste", (e) => {
        const text = (e.originalEvent.clipboardData || window.clipboardData).getData("text");
        if (!/[\r\n\t]/.test(text.trim())) return; // a single value pastes normally
        e.preventDefault();
        const crit2 = S.parsePasted(text, df.fieldtype);
        this.criteria[fieldname] = crit2;
        this.dirty.delete(fieldname);
        this.replaceRow(fieldname);
        frappe.show_alert({ message: __("{0} value(s) pasted into {1}", [S.countOf(crit2), df.label]), indicator: "green" });
      });
      $row.find(".wms-sel-multi").on("click", () => { this.syncInputs(); this.multiDialog(fieldname); });
      $row.find(".wms-sel-remove").on("click", () => { this.syncInputs(); this.selFields = this.selFields.filter((f) => f !== fieldname); delete this.criteria[fieldname]; $row.remove(); });
      return $row;
    }

    replaceRow(fieldname) { this.$el.find(`.wms-sel-row[data-field="${fieldname}"]`).replaceWith(this.renderRow(fieldname)); }

    // Reads every edited single-line input back into this.criteria.
    syncInputs() {
      for (const f of Array.from(this.dirty)) {
        const $row = this.$el.find(`.wms-sel-row[data-field="${f}"]`);
        const from = ($row.find(".wms-sel-from").val() || "").trim(), to = ($row.find(".wms-sel-to").val() || "").trim();
        const ft = this.fields[f].fieldtype;
        let crit = null;
        if (to) crit = { include: [{ op: "bt", low: from, high: to }], exclude: [] };
        else crit = S.parseShortcut(from, ft);
        if (crit) this.criteria[f] = crit; else delete this.criteria[f];
        this.paintMulti(f);
      }
      this.dirty.clear();
    }

    // Recolours a field's multiple-selection button without redrawing the row (keeps focus).
    paintMulti(fieldname) {
      const crit = this.criteria[fieldname];
      const n = S.countOf(crit), hasExcl = !!(crit && (crit.exclude || []).length);
      const simple = n <= 1 && !hasExcl;
      this.$el.find(`.wms-sel-row[data-field="${fieldname}"] .wms-sel-multi`)
        .toggleClass("has-incl", !simple).toggleClass("has-excl", hasExcl)
        .text("\u21E8" + (simple ? "" : " " + n));
    }

    activeCriteria() {
      this.syncInputs();
      const out = {};
      for (const [f, c] of Object.entries(this.criteria)) if (S.countOf(c)) out[f] = c;
      return out;
    }

    // ---------- multiple selection ----------
    multiDialog(fieldname) {
      const df = this.fields[fieldname];
      const work = JSON.parse(JSON.stringify(this.criteria[fieldname] || { include: [], exclude: [] }));
      work.include = work.include || []; work.exclude = work.exclude || [];
      let tab = "include";
      const d = new frappe.ui.Dialog({
        title: __("Multiple Selection for {0}", [df.label]), size: "large",
        fields: [{ fieldtype: "HTML", fieldname: "body" }],
        primary_action_label: __("Copy"),
        primary_action: () => {
          readRows();
          const clean = (rows) => rows.filter((r) => !(r.op !== "eq" && r.op !== "ne" && (r.low === "" || r.low == null) && (r.high === "" || r.high == null)));
          work.include = clean(work.include); work.exclude = clean(work.exclude);
          if (S.countOf(work)) this.criteria[fieldname] = work; else delete this.criteria[fieldname];
          this.dirty.delete(fieldname);
          this.replaceRow(fieldname);
          d.hide();
        },
      });
      const $b = $(d.fields_dict.body.wrapper);
      const opOptions = (sel) => S.OPS.map(([k, l]) => `<option value="${k}" ${k === sel ? "selected" : ""}>${esc(__(l))}</option>`).join("");
      const readRows = () => {
        $b.find(".wms-ms-row").each((_, el) => {
          const i = Number(el.dataset.i), row = work[tab][i];
          if (!row) return;
          row.op = $(el).find("select").val();
          row.low = $(el).find(".wms-ms-low").val();
          row.high = $(el).find(".wms-ms-high").val();
          if (row.op !== "bt" && row.op !== "nb") delete row.high;
        });
      };
      const draw = () => {
        const rows = work[tab];
        const shown = rows.slice(0, MAX_ROWS_RENDERED);
        $b.html(`
          <div class="wms-ms-tabs">
            <span class="wms-ms-tab inc ${tab === "include" ? "active" : ""}" data-tab="include">✅ ${__("Select values")} (${work.include.length})</span>
            <span class="wms-ms-tab exc ${tab === "exclude" ? "active" : ""}" data-tab="exclude">⛔ ${__("Exclude values")} (${work.exclude.length})</span>
          </div>
          <div class="wms-ms-rows">${shown.map((r, i) => `
            <div class="wms-ms-row" data-i="${i}">
              <select class="form-control input-sm">${opOptions(r.op || "eq")}</select>
              <input class="form-control input-sm wms-ms-low" value="${esc(r.low)}" placeholder="${__("Value")}">
              <input class="form-control input-sm wms-ms-high" value="${esc(r.high)}" placeholder="${__("Upper limit")}" style="${r.op === "bt" || r.op === "nb" ? "" : "visibility:hidden"}">
              <span class="wms-sel-remove wms-ms-del" title="${__("Delete row")}">×</span>
            </div>`).join("")}
            ${rows.length > shown.length ? `<div class="text-muted" style="font-size:12px;">${__("…and {0} more value(s), kept as they are.", [rows.length - shown.length])}</div>` : ""}
            ${rows.length ? "" : `<div class="text-muted" style="font-size:12px;">${__("No values yet.")}</div>`}
          </div>
          <div class="wms-ms-actions">
            <button type="button" class="btn btn-default btn-xs wms-ms-add">+ ${__("Add row")}</button>
            <button type="button" class="btn btn-default btn-xs wms-ms-clip">${__("Paste from clipboard")}</button>
            <button type="button" class="btn btn-default btn-xs wms-ms-clear">${__("Delete all")}</button>
            <span class="text-muted" style="font-size:11px;">${__("Pasted lines are read with the same syntax as the field: A*, >=10, 1..5, !X")}</span>
          </div>
          <textarea class="form-control wms-ms-paste" placeholder="${__("…or paste a column here (Ctrl+V)")}"></textarea>`);
        $b.find(".wms-ms-tab").on("click", (e) => { readRows(); tab = e.currentTarget.dataset.tab; draw(); });
        $b.find(".wms-ms-row select").on("change", (e) => {
          const op = e.target.value; $(e.target).closest(".wms-ms-row").find(".wms-ms-high").css("visibility", op === "bt" || op === "nb" ? "" : "hidden");
        });
        $b.find(".wms-ms-del").on("click", (e) => { readRows(); work[tab].splice(Number($(e.target).closest(".wms-ms-row").data("i")), 1); draw(); });
        $b.find(".wms-ms-add").on("click", () => { readRows(); work[tab].push({ op: "eq", low: "" }); draw(); $b.find(".wms-ms-low").last().trigger("focus"); });
        $b.find(".wms-ms-clear").on("click", () => { work[tab] = []; draw(); });
        const addPasted = (text) => {
          readRows();
          const p = S.parsePasted(text, df.fieldtype);
          // A paste goes into the tab being shown: pasting "X" on the exclude tab excludes X.
          const rows = tab === "include" ? p.include : p.include.concat(p.exclude);
          work[tab] = work[tab].filter((r) => r.low !== "" || r.op !== "eq").concat(rows);
          if (tab === "include") work.exclude = work.exclude.concat(p.exclude);
          draw();
          frappe.show_alert({ message: __("{0} value(s) added", [rows.length]), indicator: "green" });
        };
        $b.find(".wms-ms-paste").on("paste", (e) => {
          e.preventDefault();
          addPasted((e.originalEvent.clipboardData || window.clipboardData).getData("text"));
        });
        $b.find(".wms-ms-clip").on("click", async () => {
          try { addPasted(await navigator.clipboard.readText()); }
          catch (err) { frappe.show_alert({ message: __("The browser blocked clipboard access - click the box below and press Ctrl+V"), indicator: "orange" }); $b.find(".wms-ms-paste").trigger("focus"); }
        });
      };
      if (!work.include.length && !work.exclude.length) work.include.push({ op: "eq", low: "" });
      draw();
      d.show();
    }

    // ---------- fields on the screen ----------
    fieldsDialog() {
      this.syncInputs();
      const all = Object.values(this.fields).slice().sort((a, b) => String(a.label).localeCompare(String(b.label)));
      const chosen = new Set(this.selFields);
      const d = new frappe.ui.Dialog({
        title: __("Fields on the selection screen"),
        fields: [{ fieldtype: "Data", fieldname: "q", label: __("Search"), placeholder: __("Type to filter fields") }, { fieldtype: "HTML", fieldname: "list" }],
        primary_action_label: __("Apply"),
        primary_action: () => {
          const keep = this.selFields.filter((f) => chosen.has(f));
          all.forEach((f) => { if (chosen.has(f.fieldname) && !keep.includes(f.fieldname)) keep.push(f.fieldname); });
          Object.keys(this.criteria).forEach((f) => { if (!chosen.has(f)) delete this.criteria[f]; });
          this.selFields = keep;
          this.renderRows();
          d.hide();
        },
      });
      const $list = $(d.fields_dict.list.wrapper);
      const draw = (q) => {
        const n = (q || "").toLowerCase();
        $list.html(`<div class="wms-cols-list">${all.filter((f) => !n || f.label.toLowerCase().includes(n) || f.fieldname.includes(n)).map((f) => `
          <div class="wms-cols-item"><input type="checkbox" data-f="${esc(f.fieldname)}" ${chosen.has(f.fieldname) ? "checked" : ""}>
            <label>${esc(f.label)} <span class="text-muted">${esc(f.fieldname)}${f.virtual ? " · " + __("related") : ""}</span></label></div>`).join("")}</div>`);
        $list.find("input[type=checkbox]").on("change", (e) => { if (e.target.checked) chosen.add(e.target.dataset.f); else chosen.delete(e.target.dataset.f); });
      };
      d.fields_dict.q.$input.on("input", (e) => draw(e.target.value));
      draw("");
      d.show();
    }

    // ---------- selection variants ----------
    applyVariant(v, rerender) {
      this.variant = v.name;
      this.criteria = JSON.parse(JSON.stringify(v.criteria || {}));
      if (v.selection_fields && v.selection_fields.length) this.selFields = v.selection_fields.filter((f) => this.fields[f]);
      Object.keys(this.criteria).forEach((f) => { if (this.fields[f] && !this.selFields.includes(f)) this.selFields.push(f); });
      if (v.max_hits) this.maxHits = v.max_hits;
      this.dirty.clear();
      if (rerender) this.render();
    }

    async reloadVariants() {
      const r = await frappe.call(`${API}.list_variants`, { view: this.view });
      this.variants = r.message || [];
    }

    saveVariantDialog() {
      const criteria = this.activeCriteria();
      const cur = this.variantDoc();
      frappe.prompt([
        { fieldname: "variant_name", fieldtype: "Data", label: __("Variant Name"), reqd: 1, default: cur && cur.mine ? cur.variant_name : "" },
        { fieldname: "description", fieldtype: "Data", label: __("Description"), default: cur && cur.mine ? cur.description : "" },
        { fieldname: "is_default", fieldtype: "Check", label: __("Use as my default when this view opens"), default: cur && cur.mine ? cur.is_default : 0 },
        { fieldname: "is_global", fieldtype: "Check", label: __("Global - visible to every user (supervisors only)"), default: cur && cur.mine ? cur.is_global : 0 },
      ], async (v) => {
        const r = await frappe.call(`${API}.save_variant`, {
          view: this.view, variant_type: "Selection", variant_name: v.variant_name, description: v.description,
          criteria, selection_fields: this.selFields, max_hits: this.maxHits, is_default: v.is_default, is_global: v.is_global,
        });
        await this.reloadVariants();
        this.variant = r.message;
        this.render();
        frappe.show_alert({ message: __("Variant {0} saved", [v.variant_name]), indicator: "green" });
      }, __("Save as Variant"), __("Save"));
    }

    deleteVariant() {
      const v = this.variantDoc();
      if (!v || !v.mine) return;
      frappe.confirm(__("Delete variant {0}?", [esc(v.variant_name)]), async () => {
        await frappe.call(`${API}.delete_variant`, { variant: v.name });
        await this.reloadVariants();
        this.variant = "";
        this.render();
      });
    }

    // ---------- execute + results ----------
    columnsToFetch() {
      const base = (this.layout && this.layout.columns && this.layout.columns.length) ? this.layout.columns : this.meta.columns;
      return base.filter((c) => this.fields[c] && !this.fields[c].virtual);
    }

    async execute() {
      const wh = this.opts.getWarehouse();
      if (!wh) { frappe.show_alert({ message: __("Select a warehouse first"), indicator: "orange" }); return; }
      const criteria = this.activeCriteria();
      const columns = this.columnsToFetch();
      const $res = this.opts.$results;
      this.closeDialog();
      this.renderBar();
      $res.html(`<div class="text-muted">${__("Selecting…")}</div>`);
      let r;
      try {
        r = await frappe.call({ method: `${API}.execute_selection`, args: { view: this.view, warehouse: wh, criteria, columns, max_hits: this.maxHits } });
      } catch (e) { $res.html(`<div class="text-danger">${__("The selection failed - check the values entered.")}</div>`); this.openDialog(); return; }
      const res = r.message;
      this.lastRows = res.rows;
      this.fetchedColumns = columns;
      this.truncated = res.truncated;
      this.lastQuery = { wh, criteria, columns };
      this.drawResults();
    }

    // The next page of the same selection, appended to what is shown.
    async loadMore() {
      const q = this.lastQuery;
      if (!q) return;
      let r;
      try {
        r = await frappe.call({ method: `${API}.execute_selection`, args: { view: this.view, warehouse: q.wh, criteria: q.criteria, columns: q.columns,
          max_hits: this.maxHits, start: (this.lastRows || []).length } });
      } catch (e) { return; }
      this.lastRows = (this.lastRows || []).concat(r.message.rows);
      this.truncated = r.message.truncated;
      this.drawResults();
    }

    drawResults() {
      const $res = this.opts.$results.empty();
      const rows = this.lastRows || [];
      const status = rows.length
        ? (this.truncated ? `<div class="wms-sel-status text-warning">${__("{0} hits so far - there are more.", [rows.length])} <button class="btn btn-xs btn-default wms-sel-more">${__("Load next {0}", [this.maxHits])}</button></div>` : "")
        : `<div class="wms-sel-status text-muted">${__("No data found for this selection.")}</div>`;
      $res.append(status);
      $res.find(".wms-sel-more").on("click", () => this.loadMore());
      if (!rows.length) return;
      const dec = this.opts.decorate || {};
      const renderers = dec.renderers || {};
      let shown = rows;
      let columns = this.fetchedColumns.map((f) => [f, (this.fields[f] && this.fields[f].label) || f, renderers[f] || this.cellRenderer(f)]).concat(dec.extraColumns || []);
      let numeric = this.fetchedColumns.filter((f) => this.fields[f] && ["Int", "Float", "Currency", "Percent"].includes(this.fields[f].fieldtype));
      // transform(rows, columns) -> {rows, columns, numeric, actions, listFields}: a view may present
      // the hits differently (the Stock Overview adds columns and splits lines) in the same grid.
      let actions = dec.actions, listFields;
      if (dec.transform) ({ rows: shown, columns, numeric, actions, listFields } = dec.transform(rows, columns, numeric, dec.actions));
      // Every view gets a Details button: the full record of each marked line (a view may bring its own).
      if (!(actions || []).some((a) => a.label === __("Details"))) actions = [{ label: __("Details"), kind: "primary", run: (r) => this.showDetails(r) }].concat(actions || []);
      const $el = this.opts.makeGrid(shown, columns, {
        groupBy: (this.layout && this.layout.groupBy) || dec.groupBy || [], listFields,
        extraToolbar: dec.toolbar && dec.toolbar(this),
        actions, numeric, exportName: `${this.view}-${frappe.datetime.now_date()}`,
        sort: this.layout && this.layout.sort, totals: this.layout && this.layout.totals,
        layoutBar: this.layoutBar(),
        onLayoutChange: (state) => { this.layout = state; this.$layoutSel && this.$layoutSel.find("option:selected").text(this.layoutLabel(true)); },
      }, this.meta.doctype);
      $res.append($el, `<div class="wms-sel-detail"></div>`);
      if (dec.afterRender) dec.afterRender($res, rows);
    }

    // Every Link column becomes a link: an HU opens the contents viewer, anything else opens its
    // form in a new tab (the executed results stay where they are). Status-like selects get a pill.
    cellRenderer(f) {
      const df = this.fields[f];
      if (!df) return null;
      if (df.fieldtype === "Link" && df.options) {
        return (row) => {
          const v = row[f];
          if (v == null || v === "") return "";
          if (df.options === "Handling Unit") return `<a href="#" class="wms-open-hu-viewer" data-hu="${esc(v)}">${esc(v)}</a>`;
          return `<a href="/app/${frappe.router.slug(df.options)}/${encodeURIComponent(v)}" target="_blank" rel="noopener">${esc(v)}</a>`;
        };
      }
      if (df.fieldtype === "Select" && /status|state$/.test(f)) return (row) => pill(row[f]);
      return null;
    }

    // ---------- details: the whole record of each marked line ----------
    showDetails(rows) {
      const $host = this.opts.$results.find(".wms-sel-detail").empty();
      const picked = rows.filter((r) => r.name).slice(0, 5);
      if (!picked.length) return;
      const doctype = this.meta.doctype;
      return new Promise((resolve) => frappe.model.with_doctype(doctype, async () => {
        for (const r of picked) {
          const doc = (await frappe.call("frappe.client.get", { doctype, name: r.name })).message;
          $host.append(this.docSheet(doctype, doc));
        }
        if (rows.length > picked.length) $host.append(`<div class="text-muted" style="font-size:12px;">${__("Showing the first {0} of {1} marked lines.", [picked.length, rows.length])}</div>`);
        $host[0].scrollIntoView({ behavior: "smooth", block: "nearest" });
        resolve();
      }));
    }

    docSheet(doctype, doc) {
      const meta = frappe.get_meta(doctype);
      const SKIP = new Set(["Section Break", "Column Break", "Tab Break", "HTML", "Button", "Heading", "Image", "Attach Image", "Table", "Table MultiSelect", "Geolocation", "Code", "JSON"]);
      const shown = meta.fields.filter((df) => !df.hidden && !SKIP.has(df.fieldtype) && doc[df.fieldname] !== null && doc[df.fieldname] !== undefined && doc[df.fieldname] !== "");
      const value = (df) => {
        const v = doc[df.fieldname];
        if (df.fieldtype === "Link" && df.options) return this.cellRenderer(df.fieldname) ? this.cellRenderer(df.fieldname)(doc) : esc(v);
        if (df.fieldtype === "Select" && /status|state$/.test(df.fieldname)) return pill(v);
        if (df.fieldtype === "Check") return v ? "\u2713" : "";
        return frappe.format(v, df, { inline: true }, doc);
      };
      const $d = $(`<div class="wms-detail-panel">
        <div class="wms-detail-head"><b>${esc(doc.name)}</b>
          <a href="/app/${frappe.router.slug(doctype)}/${encodeURIComponent(doc.name)}" target="_blank" rel="noopener">${__("Open form")}</a>
          <button type="button" class="btn btn-default btn-xs wms-detail-close">&times;</button></div>
        <div class="wms-detail-fields">${shown.map((df) => `<div class="wms-detail-field"><div class="text-muted">${esc(__(df.label))}</div><div>${value(df)}</div></div>`).join("")}</div>
      </div>`);
      $d.find(".wms-detail-close").on("click", () => $d.remove());
      meta.fields.filter((df) => df.fieldtype === "Table" && (doc[df.fieldname] || []).length).forEach((df) => {
        const cm = frappe.get_meta(df.options);
        const cf = (cm ? cm.fields : []).filter((c) => !c.hidden && !SKIP.has(c.fieldtype));
        const cols = (cf.some((c) => c.in_list_view) ? cf.filter((c) => c.in_list_view) : cf).slice(0, 9).map((c) => [c.fieldname, __(c.label || c.fieldname)]);
        $d.append(`<h6 style="margin:12px 0 4px;">${esc(__(df.label))} (${doc[df.fieldname].length})</h6>`).append(this.opts.makeGrid(doc[df.fieldname], cols, { noGroup: true }, df.options));
      });
      return $d;
    }

    // ---------- layouts ----------
    layoutLabel(changed) {
      const v = this.variants.find((x) => x.name === this.layoutVariant);
      return (v ? v.variant_name : __("Standard layout")) + (changed ? " *" : "");
    }

    layoutBar() {
      const layouts = this.variants.filter((v) => v.variant_type === "Layout");
      const $bar = $(`
        <span style="display:inline-flex;gap:4px;align-items:center;">
          <select class="form-control input-xs wms-lay-sel" style="height:24px;font-size:12px;width:170px;">
            <option value="">${__("Standard layout")}</option>
            ${layouts.map((v) => `<option value="${esc(v.name)}" ${v.name === this.layoutVariant ? "selected" : ""}>${esc(v.variant_name)}${v.is_global ? " \u{1F310}" : ""}${v.is_default ? " ★" : ""}</option>`).join("")}
          </select>
          <button type="button" class="btn btn-default btn-xs wms-lay-cols">${__("Columns…")}</button>
          <button type="button" class="btn btn-default btn-xs wms-lay-save">${__("Save layout")}</button>
          <button type="button" class="btn btn-default btn-xs wms-lay-del" ${layouts.some((v) => v.name === this.layoutVariant && v.mine) ? "" : "style=\"display:none\""}>${__("Delete layout")}</button>
        </span>`);
      this.$layoutSel = $bar.find(".wms-lay-sel");
      this.$layoutSel.on("change", (e) => {
        const v = layouts.find((x) => x.name === e.target.value);
        this.layoutVariant = v ? v.name : "";
        this.layout = v ? JSON.parse(JSON.stringify(v.layout || {})) : null;
        this.applyLayoutToResults();
      });
      $bar.find(".wms-lay-cols").on("click", () => this.columnsDialog());
      $bar.find(".wms-lay-save").on("click", () => this.saveLayoutDialog());
      $bar.find(".wms-lay-del").on("click", () => {
        frappe.confirm(__("Delete this layout?"), async () => {
          await frappe.call(`${API}.delete_variant`, { variant: this.layoutVariant });
          await this.reloadVariants(); this.layoutVariant = ""; this.layout = null; this.applyLayoutToResults();
        });
      });
      return $bar;
    }

    // Columns already fetched -> just redraw; a newly shown column -> fetch again.
    applyLayoutToResults() {
      if (!this.lastRows) return;
      const want = this.columnsToFetch();
      const have = new Set(this.fetchedColumns || []);
      if (want.every((c) => have.has(c))) { this.fetchedColumns = want; this.drawResults(); }
      else this.execute();
    }

    columnsDialog() {
      const all = Object.values(this.fields).filter((f) => !f.virtual);
      const order = this.columnsToFetch().slice();
      all.forEach((f) => { if (!order.includes(f.fieldname)) order.push(f.fieldname); });
      const shown = new Set(this.columnsToFetch());
      const d = new frappe.ui.Dialog({
        title: __("Change Layout - Columns"),
        fields: [{ fieldtype: "HTML", fieldname: "list" }],
        primary_action_label: __("Apply"),
        primary_action: () => {
          this.layout = Object.assign({}, this.layout || {}, { columns: order.filter((f) => shown.has(f)) });
          d.hide();
          this.applyLayoutToResults();
          if (this.$layoutSel) this.$layoutSel.find("option:selected").text(this.layoutLabel(true));
        },
      });
      const $list = $(d.fields_dict.list.wrapper);
      const draw = () => {
        $list.html(`<div class="text-muted" style="font-size:12px;margin-bottom:6px;">${__("Tick the columns to show; use the arrows to order them. You can also drag column headers in the result grid.")}</div>
          <div class="wms-cols-list">${order.map((f, i) => `
          <div class="wms-cols-item" data-i="${i}">
            <input type="checkbox" ${shown.has(f) ? "checked" : ""} data-f="${esc(f)}">
            <label>${esc(this.fields[f] ? this.fields[f].label : f)} <span class="text-muted">${esc(f)}</span></label>
            <span class="wms-cols-move" data-d="-1">▲</span><span class="wms-cols-move" data-d="1">▼</span>
          </div>`).join("")}</div>`);
        $list.find("input[type=checkbox]").on("change", (e) => { if (e.target.checked) shown.add(e.target.dataset.f); else shown.delete(e.target.dataset.f); });
        $list.find(".wms-cols-move").on("click", (e) => {
          const i = Number($(e.target).closest(".wms-cols-item").data("i")), j = i + Number(e.target.dataset.d);
          if (j < 0 || j >= order.length) return;
          [order[i], order[j]] = [order[j], order[i]];
          draw();
        });
      };
      draw();
      d.show();
    }

    saveLayoutDialog() {
      const cur = this.variants.find((x) => x.name === this.layoutVariant);
      frappe.prompt([
        { fieldname: "variant_name", fieldtype: "Data", label: __("Layout Name"), reqd: 1, default: cur && cur.mine ? cur.variant_name : "" },
        { fieldname: "is_default", fieldtype: "Check", label: __("Default layout for me"), default: cur && cur.mine ? cur.is_default : 1 },
        { fieldname: "is_global", fieldtype: "Check", label: __("Global - visible to every user (supervisors only)"), default: cur && cur.mine ? cur.is_global : 0 },
      ], async (v) => {
        const layout = Object.assign({ columns: this.columnsToFetch() }, this.layout || {});
        const r = await frappe.call(`${API}.save_variant`, { view: this.view, variant_type: "Layout", variant_name: v.variant_name, layout, is_default: v.is_default, is_global: v.is_global });
        await this.reloadVariants();
        this.layoutVariant = r.message;
        this.drawResults();
        frappe.show_alert({ message: __("Layout {0} saved", [v.variant_name]), indicator: "green" });
      }, __("Save Layout"), __("Save"));
    }
  }

  window.wms_selection.SelectionScreen = SelectionScreen;
})();
