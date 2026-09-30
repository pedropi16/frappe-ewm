// Packing Station - the WMS Monitor's take on SAP EWM's packing work center (/SCWM/PACK).
//
// The packer logs on to a Work Center (a packing table = one Storage Bin). The screen has the
// three /SCWM/PACK areas: the HU tree of everything on the table (top left), the detail of the
// selected HU (top right) and the scanner area (bottom) with Pack Product / Pack HU / Create HU /
// Close HU tabs driven by scanning and Enter, so a packer never needs the mouse. Backend:
// frappe_wms/api/packing_station.py.
(function () {
  const API = "frappe_wms.api.packing_station";
  const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
  const num = (v) => { const n = parseFloat(v); return isNaN(n) ? 0 : Math.round(n * 1e6) / 1e6; };
  const PREF_KEY = "wms_packing_station_wc";

  let stylesDone = false;
  function styles() {
    if (stylesDone) return;
    stylesDone = true;
    $("<style>", { text: `
      .wps-top { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-bottom:10px; }
      .wps-main { display:flex; gap:12px; align-items:stretch; flex-wrap:wrap; }
      .wps-tree { flex:1 1 420px; min-width:0; border:1px solid var(--border-color); border-radius:8px; padding:8px; max-height:48vh; overflow:auto; }
      .wps-detail { flex:1 1 380px; min-width:0; border:1px solid var(--border-color); border-radius:8px; padding:10px; max-height:48vh; overflow:auto; }
      .wps-node { display:flex; align-items:center; gap:6px; padding:3px 6px; border-radius:6px; cursor:pointer; font-size:13px; }
      .wps-node:hover { background:var(--control-bg,#f5f5f5); }
      .wps-node.sel { background:rgba(59,130,246,.14); }
      .wps-node .wps-tag { font-size:11px; padding:0 6px; border-radius:10px; background:var(--control-bg,#eee); color:var(--text-muted); }
      .wps-node .wps-tag.src { background:rgba(234,179,8,.2); color:#92400e; }
      .wps-node .wps-tag.dst { background:rgba(34,197,94,.18); color:#166534; }
      .wps-node .wps-tag.closed { background:rgba(100,116,139,.2); }
      .wps-node .wps-tag.dlv { background:rgba(59,130,246,.12); color:#1d4ed8; }
      .wps-line { font-size:12px; color:var(--text-muted); padding:1px 6px; }
      .wps-scan { margin-top:12px; border:1px solid var(--border-color); border-radius:8px; padding:10px 12px; background:var(--card-bg,#fff); }
      .wps-tabs { display:flex; gap:4px; margin-bottom:10px; flex-wrap:wrap; }
      .wps-tab { padding:5px 12px; border:1px solid var(--border-color); border-radius:6px; cursor:pointer; font-size:13px; }
      .wps-tab.active { background:var(--primary,#2563eb); color:#fff; border-color:transparent; }
      .wps-fields { display:grid; grid-template-columns:repeat(auto-fill, minmax(190px, 1fr)); gap:8px; align-items:end; }
      .wps-fields label { font-size:11px; color:var(--text-muted); margin:0 0 2px; display:block; }
      .wps-fields input, .wps-fields select { height:34px; font-size:14px; }
      .wps-msg { margin-top:8px; font-size:13px; min-height:20px; }
      .wps-msg.ok { color:var(--green-600,#16a34a); } .wps-msg.err { color:var(--red-600,#dc2626); }
      .wps-orders { margin-top:10px; }
      .wps-kv { display:grid; grid-template-columns:130px 1fr; gap:2px 10px; font-size:13px; margin-bottom:8px; }
      .wps-kv div:nth-child(odd) { color:var(--text-muted); }
    ` }).appendTo("head");
  }

  class PackingStation {
    constructor($wrap, getWarehouse) {
      this.$wrap = $wrap;
      this.getWarehouse = getWarehouse;
      this.tab = "product";
      this.data = null;
      this.selected = null;
      this.expanded = new Set();
      this.form = { source: "", product: "", qty: "", dest: "", delivery: "", hu: "", hu_dest: "", type: "", number: "", for_delivery: "", close_hu: "", weight: "", move_to: "" };
      styles();
    }

    async render() {
      const wh = this.getWarehouse();
      const centers = wh ? (await frappe.call(`${API}.list_work_centers`, { warehouse: wh })).message || [] : [];
      let saved = null;
      try { saved = localStorage.getItem(PREF_KEY); } catch (e) { /* storage blocked */ }
      if (!this.wc || !centers.some((c) => c.name === this.wc)) this.wc = centers.some((c) => c.name === saved) ? saved : (centers.length === 1 ? centers[0].name : "");
      this.$wrap.html(`
        <div class="wps-top">
          <label style="margin:0;font-size:12px;" class="text-muted">${__("Work Center")}</label>
          <select class="form-control input-sm wps-wc" style="width:260px;">
            <option value="">${__("Log on to a work center…")}</option>
            ${centers.map((c) => `<option value="${esc(c.name)}" ${c.name === this.wc ? "selected" : ""}>${esc(c.work_center_name || c.work_center_code)} · ${esc(c.bin)}</option>`).join("")}
          </select>
          <button type="button" class="btn btn-default btn-sm wps-refresh">${__("Refresh")}</button>
          <span class="text-muted wps-summary" style="font-size:12px;"></span>
        </div>
        <div class="wps-body">${centers.length ? "" : `<div class="text-muted">${__("No active Work Center in this warehouse. Create one (Work Center doctype) pointing at a Packing bin.")}</div>`}</div>`);
      this.$wrap.find(".wps-wc").on("change", (e) => {
        this.wc = e.target.value;
        try { localStorage.setItem(PREF_KEY, this.wc); } catch (err) { /* storage blocked */ }
        this.selected = null; this.load();
      });
      this.$wrap.find(".wps-refresh").on("click", () => this.load());
      if (this.wc) await this.load();
    }

    async load() {
      const $body = this.$wrap.find(".wps-body");
      if (!this.wc) { $body.empty(); return; }
      this.data = (await frappe.call(`${API}.station_overview`, { work_center: this.wc })).message;
      const d = this.data;
      const count = (nodes) => nodes.reduce((a, n) => a + 1 + count(n.children || []), 0);
      this.$wrap.find(".wps-summary").text(__("{0} station · bin {1} · {2} HU(s) · {3} open packing order(s)", [__(d.work_center.work_center_type || "Packing"), d.work_center.bin, count(d.handling_units), d.packing_orders.length]));
      if (!$body.find(".wps-main").length) {
        $body.html(`
          <div class="wps-main"><div class="wps-tree"></div><div class="wps-detail"></div></div>
          <div class="wps-scan"></div>
          <div class="wps-orders"></div>`);
      }
      this.drawTree(); this.drawDetail(); this.drawScanner(); this.drawOrders();
    }

    // ---------- tree ----------
    findNode(name, nodes) {
      for (const n of nodes || (this.data ? this.data.handling_units : [])) {
        if (n.name === name) return n;
        const c = this.findNode(name, n.children || []);
        if (c) return c;
      }
      return null;
    }

    topDeliveries(name) {
      // deliveries are computed for top-level HUs; a nested HU inherits its top's
      const walk = (nodes, top) => { for (const n of nodes) { const t = top || n; if (n.name === name) return t.deliveries || []; const r = walk(n.children || [], t); if (r) return r; } return null; };
      return (this.data && walk(this.data.handling_units, null)) || [];
    }

    drawTree() {
      const d = this.data;
      const $t = this.$wrap.find(".wps-tree").empty();
      const row = (n, depth) => {
        const kids = (n.children || []).length + (n.stock || []).length;
        const open = this.expanded.has(n.name);
        const tags = [];
        if (n.name === this.form.source) tags.push(`<span class="wps-tag src">${__("source")}</span>`);
        if (n.name === this.form.dest) tags.push(`<span class="wps-tag dst">${__("destination")}</span>`);
        if (n.closed) tags.push(`<span class="wps-tag closed">\u{1F512} ${__("closed")}</span>`);
        if (!kids) tags.push(`<span class="wps-tag">${__("empty")}</span>`);
        (n.deliveries || []).forEach((x) => tags.push(`<span class="wps-tag dlv">${esc((d.deliveries[x] || {}).outbound_delivery_number || x)}</span>`));
        const $r = $(`<div class="wps-node ${this.selected === n.name ? "sel" : ""}" style="padding-left:${6 + depth * 18}px" data-hu="${esc(n.name)}">
            <span style="width:12px;display:inline-block">${kids ? (open ? "▾" : "▸") : ""}</span>
            \u{1F4E6} <b>${esc(n.hu_number || n.name)}</b> <span class="text-muted" style="font-size:11px;">${esc(n.hu_type || "")}</span> ${tags.join(" ")}
          </div>`);
        $r.on("click", () => { this.selected = n.name; if (kids) { if (open) this.expanded.delete(n.name); else this.expanded.add(n.name); } this.drawTree(); this.drawDetail(); });
        $t.append($r);
        if (open) {
          (n.stock || []).forEach((s) => $t.append(`<div class="wps-line" style="padding-left:${30 + depth * 18}px">${esc(s.product)} · <b>${num(s.quantity)}</b> ${esc(s.stock_uom)}${s.batch_no ? " · " + esc(s.batch_no) : ""}${s.serial_no ? " · SN " + esc(s.serial_no) : ""}</div>`));
          (n.children || []).forEach((c) => row(c, depth + 1));
        }
      };
      if (d.loose_stock.length) {
        $t.append(`<div class="wps-node" style="cursor:default"><b>${__("Loose on the table")}</b></div>`);
        d.loose_stock.forEach((s) => $t.append(`<div class="wps-line" style="padding-left:24px">${esc(s.product)} · <b>${num(s.quantity)}</b> ${esc(s.stock_uom)}${s.batch_no ? " · " + esc(s.batch_no) : ""}</div>`));
      }
      if ((d.arriving || []).length) {
        $t.append(`<div class="wps-node" style="cursor:default"><b>${__("Arriving at {0}", [esc(d.work_center.inbound_section_bin)])}</b></div>`);
        d.arriving.forEach((a) => {
          const $a = $(`<div class="wps-node" style="padding-left:24px">\u{1F4E6} ${esc(a.hu_number || a.name)} <span class="text-muted" style="font-size:11px;">${esc(a.hu_type || "")}</span>
            <button class="btn btn-xs btn-default" style="margin-left:auto">${__("Take to table")}</button></div>`);
          $a.find("button").on("click", (e) => { e.stopPropagation(); this.call("take_to_table", { hu_name: a.name }, __("{0} is on the table", [a.name])); });
          $t.append($a);
        });
      }
      d.handling_units.forEach((n) => row(n, 0));
      if (!d.handling_units.length && !d.loose_stock.length) $t.html(`<div class="text-muted">${__("The table is empty. Bring picked HUs here (pick with this bin as staging bin) or create a new HU below.")}</div>`);
    }

    // ---------- detail ----------
    drawDetail() {
      const $d = this.$wrap.find(".wps-detail").empty();
      const n = this.selected && this.findNode(this.selected);
      if (!n) { $d.html(`<div class="text-muted">${__("Select an HU in the tree to see its details.")}</div>`); return; }
      const cfg = this.data.work_center;
      const dl = this.topDeliveries(n.name).map((x) => { const i = this.data.deliveries[x] || {}; return `${esc(i.outbound_delivery_number || x)} · ${esc(i.customer || "")}`; }).join("<br>") || "-";
      $d.html(`
        <h5 style="margin-top:0">${esc(n.hu_number || n.name)} ${n.closed ? "\u{1F512}" : ""}</h5>
        <div class="wps-kv">
          <div>${__("Type")}</div><div>${esc(n.hu_type)}</div>
          <div>${__("Status")}</div><div>${esc(n.status)} · ${esc(n.stock_status || "")}</div>
          <div>${__("Delivery")}</div><div>${dl}</div>
          <div>${__("Gross / net / tare")}</div><div>${num(n.gross_weight)} / ${num(n.net_weight)} / ${num(n.tare_weight)}</div>
          <div>${__("Volume")}</div><div>${num(n.volume)}</div>
          ${n.sscc ? `<div>SSCC</div><div>${esc(n.sscc)}</div>` : ""}
        </div>
        <table class="table table-sm" style="font-size:12px;"><thead><tr><th>${__("Product")}</th><th>${__("Batch / Serial")}</th><th>${__("Stock type")}</th><th style="text-align:right">${__("Qty")}</th></tr></thead>
          <tbody>${(n.stock || []).map((s) => `<tr><td>${esc(s.product)}</td><td>${esc(s.batch_no || s.serial_no || "")}</td><td>${esc(s.stock_type)}</td><td style="text-align:right">${num(s.quantity)} ${esc(s.stock_uom)}</td></tr>`).join("") || `<tr><td colspan="4" class="text-muted">${__("No stock directly in this HU")}</td></tr>`}</tbody></table>
        <div style="display:flex;flex-wrap:wrap;gap:6px;">
          <button class="btn btn-xs btn-default wps-as-src">${__("Use as source")}</button>
          <button class="btn btn-xs btn-default wps-as-dst">${__("Use as destination")}</button>
          ${!cfg.allow_close_hu ? "" : n.closed ? `<button class="btn btn-xs btn-default wps-reopen">${__("Reopen")}</button>` : `<button class="btn btn-xs btn-primary wps-close">${__("Close HU…")}</button>`}
          ${n.parent_hu && cfg.allow_unpack ? `<button class="btn btn-xs btn-default wps-unpack">${__("Take out of {0}", [esc(n.parent_hu)])}</button>` : ""}
          ${cfg.allow_delete_empty_hu && !(n.stock || []).length && !(n.children || []).length ? `<button class="btn btn-xs btn-danger wps-delete">${__("Delete empty HU")}</button>` : ""}
          <button class="btn btn-xs btn-default wps-label">${__("Print label")}</button>
        </div>`);
      $d.find(".wps-as-src").on("click", () => { this.form.source = n.name; this.setTab("product"); });
      $d.find(".wps-as-dst").on("click", () => { this.form.dest = n.name; this.form.hu_dest = n.name; this.drawTree(); this.drawScanner(); });
      $d.find(".wps-close").on("click", () => { this.form.close_hu = n.name; this.setTab("close"); });
      $d.find(".wps-reopen").on("click", () => this.call("reopen_hu", { hu_name: n.name }, __("{0} reopened", [n.name])));
      $d.find(".wps-delete").on("click", () => frappe.confirm(__("Delete empty HU {0}?", [n.name]), () => { this.selected = null; this.call("delete_empty_hu", { hu_name: n.name }, __("{0} deleted", [n.name])); }));
      $d.find(".wps-unpack").on("click", () => this.call("unpack_hu", { hu_name: n.name }, __("{0} is loose on the table again", [n.name])));
      $d.find(".wps-label").on("click", async () => {
        // Printed through the warehouse's "Manual" Print Determination Rule; without one, show
        // the label so it can still be checked or copied to a printer by hand.
        try {
          const r = await frappe.call({ method: "frappe_wms.api.printing.request_print", args: { reference_doctype: "Handling Unit", reference_name: n.name }, error_handlers: { ValidationError: () => {} } });
          frappe.show_alert({ message: __("Label sent to the printer ({0})", [r.message]), indicator: "green" });
        } catch (e) {
          const r = await frappe.call("frappe_wms.api.labeling.render_hu_label_zpl", { hu_name: n.name });
          frappe.msgprint({ title: __("HU label (ZPL) - no printer set up for manual prints"), message: `<pre style="white-space:pre-wrap;font-size:11px;">${esc(r.message)}</pre>` });
        }
      });
    }

    // ---------- scanner area ----------
    setTab(tab) { this.tab = tab; this.drawTree(); this.drawScanner(); }

    field(key, label, opts = {}) {
      const list = opts.list ? `list="wps-dl-${key}"` : "";
      const dl = opts.list ? `<datalist id="wps-dl-${key}">${opts.list.map((v) => `<option value="${esc(v.value)}">${esc(v.label || "")}</option>`).join("")}</datalist>` : "";
      if (opts.options) {
        return `<div><label>${label}</label><select class="form-control wps-f" data-k="${key}">${opts.options.map((o) => `<option value="${esc(o.value)}" ${o.value === this.form[key] ? "selected" : ""}>${esc(o.label)}</option>`).join("")}</select></div>`;
      }
      return `<div><label>${label}</label><input class="form-control wps-f" data-k="${key}" ${list} value="${esc(this.form[key])}" placeholder="${esc(opts.ph || "")}" ${opts.type ? `type="${opts.type}"` : ""}>${dl}</div>`;
    }

    huList() {
      const out = [];
      const walk = (nodes) => nodes.forEach((n) => { out.push({ value: n.name, label: `${n.hu_type || ""}${n.closed ? " (closed)" : ""}` }); walk(n.children || []); });
      walk(this.data.handling_units);
      return out;
    }

    productsFor(source) {
      const src = source && this.findNode(source);
      return src ? (src.stock || []).map((s) => ({ value: s.product, label: `${num(s.quantity)} ${s.stock_uom}` }))
        : this.data.loose_stock.map((s) => ({ value: s.product, label: `${num(s.quantity)} ${s.stock_uom} loose` }));
    }

    stagingFor(hu) {
      const n = hu && this.findNode(hu);
      if (!n) return [];
      return [...new Set(this.topDeliveries(n.name).map((x) => (this.data.deliveries[x] || {}).staging_bin).filter((b) => b && b !== this.data.work_center.bin))];
    }

    drawScanner() {
      const $s = this.$wrap.find(".wps-scan");
      const hus = this.huList();
      const allDeliveries = Object.values(this.data.deliveries).map((x) => ({ value: x.name, label: `${x.outbound_delivery_number || x.name} · ${x.customer || ""}` }));
      const cfg = this.data.work_center;
      const tabs = [["product", __("Pack Product"), "allow_pack_product"], ["hu", __("Pack HU"), "allow_pack_hu"],
        ["instruction", __("By Instruction"), "allow_pack_by_instruction"], ["create", __("Create HU"), "allow_create_hu"],
        ["close", __("Close HU"), "allow_close_hu"], ["diff", __("Missing Qty"), "allow_differences"]].filter((t) => cfg[t[2]]);
      if (!tabs.length) { $s.html(`<div class="text-muted">${__("No packing functions are enabled for this work center.")}</div>`); return; }
      if (!tabs.some((t) => t[0] === this.tab)) this.tab = tabs[0][0];
      const oneByOne = cfg.quantity_proposal === "One Unit per Scan";
      if (this.tab === "create" && !this.form.type && cfg.default_hu_type) this.form.type = cfg.default_hu_type;
      let fields = "", action = "";
      if (this.tab === "product") {
        fields = this.field("source", __("Source HU (blank = loose on table)"), { list: hus, ph: __("Scan HU") })
          + this.field("product", __("Product"), { list: this.productsFor(this.form.source), ph: __("Scan product") })
          + this.field("qty", oneByOne ? __("Quantity (one unit per scan)") : __("Quantity (blank = all of it)"), { type: "number", ph: oneByOne ? "1" : "" })
          + this.field("dest", __("Destination HU"), { list: hus, ph: __("Scan HU") })
          + `<div class="wps-dlv-wrap">${this.field("delivery", __("For delivery"), { options: [{ value: "", label: "" }] })}</div>`;
        action = `<button class="btn btn-primary wps-go">${__("Pack")} ↵</button> <button class="btn btn-default wps-all">${__("Pack all of source")}</button>`;
      } else if (this.tab === "hu") {
        fields = this.field("hu", __("HU to pack"), { list: hus, ph: __("Scan HU") }) + this.field("hu_dest", __("Into HU"), { list: hus, ph: __("Scan HU") });
        action = `<button class="btn btn-primary wps-go">${__("Pack HU")} ↵</button>`;
      } else if (this.tab === "instruction") {
        fields = this.field("source", __("Source HU (blank = loose on table)"), { list: hus, ph: __("Scan HU") })
          + this.field("product", __("Product"), { list: this.productsFor(this.form.source), ph: __("Scan product") })
          + this.field("level", __("Packing instruction"), { options: [{ value: "", label: "" }] })
          + this.field("per_hu", __("Quantity per HU (blank = instruction)"), { type: "number", ph: "" })
          + `<div class="wps-dlv-wrap">${this.field("delivery", __("For delivery"), { options: [{ value: "", label: "" }] })}</div>`;
        action = `<button class="btn btn-primary wps-go">${__("Pack into new HUs")} ↵</button> <label style="font-size:12px;margin-left:8px;"><input type="checkbox" class="wps-close-each" ${this.form.close_each ? "checked" : ""}> ${__("Close each HU")}</label>`;
      } else if (this.tab === "diff") {
        fields = this.field("source", __("Source HU (blank = loose on table)"), { list: hus, ph: __("Scan HU") })
          + this.field("product", __("Product"), { list: this.productsFor(this.form.source), ph: __("Scan product") })
          + this.field("qty", __("Missing quantity"), { type: "number", ph: "" })
          + this.field("remarks", __("Remarks"), { ph: "" });
        action = `<button class="btn btn-danger wps-go">${__("Post to difference bin")} ↵</button> <span class="text-muted" style="font-size:12px;">${__("Only stock that is not picked for a delivery.")}</span>`;
      } else if (this.tab === "create") {
        const types = this.data.hu_types || [];
        fields = this.field("type", __("HU type / packaging"), { options: [{ value: "", label: __("Choose…") }].concat(types.map((x) => ({ value: x.name, label: x.hu_type_name || x.name }))) })
          + this.field("number", __("HU number"), { ph: "" })
          + this.field("for_delivery", __("For delivery (optional)"), { list: allDeliveries, ph: "" });
        action = `<button class="btn btn-primary wps-go">${__("Create")} ↵</button> <span class="text-muted" style="font-size:12px;">${__("The new HU becomes the packing destination.")}</span>`;
      } else {
        fields = this.field("close_hu", __("HU to close"), { list: hus.filter((h) => !h.label.includes("closed")), ph: __("Scan HU") })
          + this.field("weight", cfg.weigh_on_close === "Required" ? __("Gross weight (required)") : __("Gross weight (from the scale)"), { type: "number", ph: "" })
          + this.field("move_to", __("Move to bin (blank = station setting)"), { list: [], ph: __(cfg.close_follow_up || "") });
        action = `<button class="btn btn-primary wps-go">${__("Close HU")} ↵</button>`;
      }
      $s.html(`
        <div class="wps-tabs">${tabs.map(([k, l]) => `<span class="wps-tab ${k === this.tab ? "active" : ""}" data-tab="${k}">${l}</span>`).join("")}</div>
        <div class="wps-fields">${fields}</div>
        <div style="margin-top:10px;">${action}</div>
        <div class="wps-msg ${this.msg ? this.msg.kind : ""}">${esc(this.msg ? this.msg.text : "")}</div>`);
      $s.find(".wps-tab").on("click", (e) => { this.msg = null; this.setTab(e.currentTarget.dataset.tab); });
      const $inputs = $s.find(".wps-f");
      $inputs.on("input change", (e) => { this.form[e.target.dataset.k] = e.target.value; this.refreshDynamic(e.target.dataset.k); });
      // Enter moves to the next field, and on the last one runs the action - a scanner's CR suffix drives the whole flow.
      $inputs.on("keydown", (e) => {
        if (e.key !== "Enter") return;
        e.preventDefault();
        const all = this.$wrap.find(".wps-scan .wps-f").filter(":visible").toArray();
        const i = all.indexOf(e.target);
        this.form[e.target.dataset.k] = e.target.value;
        if (oneByOne && this.tab === "product" && e.target.dataset.k === "product" && this.form.dest) { this.form.qty = "1"; this.go(); return; }
        if (i < all.length - 1) all[i + 1].focus(); else this.go();
      });
      $s.find(".wps-go").on("click", () => this.go());
      $s.find(".wps-all").on("click", () => this.packAll());
      $s.find(".wps-close-each").on("change", (e) => { this.form.close_each = e.target.checked; });
      this.refreshDynamic();
      const first = $s.find(".wps-f:visible").filter((_, el) => !el.value).first();
      (first.length ? first : $s.find(".wps-f:visible").first()).trigger("focus");
    }

    // Updates the parts of the scanner area that depend on what was just scanned, in place -
    // redrawing the area would steal focus from the scan field and swallow a button click.
    refreshDynamic(changed) {
      const $s = this.$wrap.find(".wps-scan");
      const f = this.form;
      if (this.tab === "instruction" && (!changed || changed === "product" || changed === "source")) {
        const levels = (this.data.instructions || {})[f.product] || [];
        $s.find('.wps-f[data-k="level"]').html(levels.length ? levels.map((l) => `<option value="${esc(l.level_name)}" ${l.level_name === f.level ? "selected" : ""}>${esc(l.level_name)} · ${num(l.quantity_per_level)} / ${esc(l.hu_type || "")}</option>`).join("")
          : `<option value="">${esc(__("no Packaging Spec - enter quantity per HU"))}</option>`);
        if (levels.length && !levels.some((l) => l.level_name === f.level)) f.level = levels[0].level_name;
      }
      if (this.tab === "product" || this.tab === "instruction") {
        if (!changed || changed === "source") {
          $s.find("#wps-dl-product").html(this.productsFor(f.source).map((v) => `<option value="${esc(v.value)}">${esc(v.label)}</option>`).join(""));
          const dl = this.topDeliveries(f.source);
          if (!dl.includes(f.delivery)) f.delivery = "";
          $s.find(".wps-dlv-wrap").toggle(dl.length > 1);
          $s.find('.wps-f[data-k="delivery"]').html([`<option value="">${__("Choose the delivery…")}</option>`].concat(dl.map((x) =>
            `<option value="${esc(x)}" ${x === f.delivery ? "selected" : ""}>${esc((this.data.deliveries[x] || {}).outbound_delivery_number || x)} · ${esc((this.data.deliveries[x] || {}).customer || "")}</option>`)).join(""));
        }
        if (changed === "source" || changed === "dest") this.drawTree();
      } else if (this.tab === "create") {
        const t = (this.data.hu_types || []).find((x) => x.name === f.type);
        $s.find('.wps-f[data-k="number"]').attr("placeholder", t && t.numbering_mode === "Internal" ? __("blank = automatic number") : __("Scan the label, or blank"));
      } else if (this.tab === "close" && (!changed || changed === "close_hu")) {
        const cfg = this.data.work_center;
        const bins = this.stagingFor(f.close_hu).map((b) => [b, __("delivery staging bin")]).concat(cfg.outbound_section_bin ? [[cfg.outbound_section_bin, __("outbound section")]] : []);
        $s.find("#wps-dl-move_to").html(bins.map(([b, l]) => `<option value="${esc(b)}">${esc(l)}</option>`).join(""));
        const n = this.findNode(f.close_hu);
        $s.find('.wps-f[data-k="weight"]').attr("placeholder", n ? __("calculated {0}", [num(n.gross_weight)]) : "");
      }
    }

    async call(method, args, okText) {
      try {
        const r = await frappe.call({ method: `${API}.${method}`, args: Object.assign({ work_center: this.wc }, args) });
        this.msg = { kind: "ok", text: okText };
        frappe.show_alert({ message: okText, indicator: "green" });
        await this.load();
        return r.message;
      } catch (e) {
        this.msg = { kind: "err", text: __("Not done - see the message.") };
        this.drawScanner();
        return undefined;
      }
    }

    async go() {
      const f = this.form;
      if (this.tab === "product") {
        if (!f.product || !f.dest) { this.msg = { kind: "err", text: __("Scan a product and a destination HU.") }; return this.drawScanner(); }
        let qty = f.qty;
        if (!qty) {
          const src = f.source ? this.findNode(f.source) : null;
          const rows = (src ? src.stock : this.data.loose_stock).filter((s) => s.product === f.product);
          qty = rows.length === 1 ? rows[0].quantity : "";
          if (!qty) { this.msg = { kind: "err", text: __("Enter the quantity.") }; return this.drawScanner(); }
        }
        const ok = await this.call("pack_product", { product: f.product, quantity: qty, destination_hu: f.dest, source_hu: f.source || undefined,
          outbound_delivery: f.delivery || undefined, idempotency_key: `PS:${frappe.utils.get_random(12)}` }, __("Packed {0} {1} into {2}", [num(qty), f.product, f.dest]));
        if (ok !== undefined) { f.product = ""; f.qty = ""; this.drawScanner(); }
      } else if (this.tab === "instruction") {
        if (!f.product) { this.msg = { kind: "err", text: __("Scan the product.") }; return this.drawScanner(); }
        const r = await this.call("pack_by_instruction", { product: f.product, source_hu: f.source || undefined, level_name: f.level || undefined,
          quantity_per_hu: f.per_hu || undefined, outbound_delivery: f.delivery || undefined, close: f.close_each ? 1 : 0,
          idempotency_key: `PSINS:${frappe.utils.get_random(12)}` }, __("Packed by instruction"));
        if (r) { this.msg = { kind: "ok", text: __("{0} HU(s) created: {1}", [r.handling_units.length, r.handling_units.join(", ")]) }; f.product = ""; this.drawScanner(); }
      } else if (this.tab === "diff") {
        if (!f.product || !f.qty) { this.msg = { kind: "err", text: __("Scan the product and enter the missing quantity.") }; return this.drawScanner(); }
        if (!(await new Promise((res) => frappe.confirm(__("Post {0} {1} as missing (moves it to the difference bin)?", [f.qty, f.product]), () => res(true), () => res(false))))) return;
        const r = await this.call("post_difference", { product: f.product, quantity: f.qty, source_hu: f.source || undefined, remarks: f.remarks || undefined },
          __("Missing quantity posted"));
        if (r) { f.product = ""; f.qty = ""; f.remarks = ""; this.drawScanner(); }
      } else if (this.tab === "hu") {
        if (!f.hu || !f.hu_dest) { this.msg = { kind: "err", text: __("Scan both HUs.") }; return this.drawScanner(); }
        const ok = await this.call("pack_hu", { hu_name: f.hu, destination_hu: f.hu_dest }, __("{0} packed into {1}", [f.hu, f.hu_dest]));
        if (ok !== undefined) { f.hu = ""; this.drawScanner(); }
      } else if (this.tab === "create") {
        if (!f.type) { this.msg = { kind: "err", text: __("Choose the HU type.") }; return this.drawScanner(); }
        const r = await this.call("create_hu", { hu_type: f.type, hu_number: f.number || undefined, outbound_delivery: f.for_delivery || undefined }, __("HU created"));
        if (r) { f.dest = r.name; f.hu_dest = r.name; f.number = ""; this.selected = r.name; this.msg = { kind: "ok", text: __("{0} created - it is now the packing destination", [r.name]) }; this.tab = "product"; this.drawTree(); this.drawDetail(); this.drawScanner(); }
      } else {
        if (!f.close_hu) { this.msg = { kind: "err", text: __("Scan the HU to close.") }; return this.drawScanner(); }
        const args = { hu_name: f.close_hu, gross_weight: f.weight || undefined, move_to_bin: f.move_to || undefined };
        let r = await this.call("close_hu", args, __("{0} checked", [f.close_hu]));
        if (r && r.needs_confirmation) {
          const go = await new Promise((res) => frappe.confirm(esc(r.message), () => res(true), () => res(false)));
          if (!go) { this.msg = { kind: "err", text: r.message }; return this.drawScanner(); }
          r = await this.call("close_hu", { ...args, confirm_incomplete: 1 }, __("{0} closed", [f.close_hu]));
        }
        if (r && !r.needs_confirmation) {
          this.msg = { kind: "ok", text: r.moved_to ? __("{0} closed and moved to {1}", [f.close_hu, r.moved_to]) : __("{0} closed", [f.close_hu]) };
          if (r.print_spool) frappe.show_alert({ message: __("Label queued ({0})", [r.print_spool]), indicator: "blue" });
          if (f.dest === f.close_hu) f.dest = "";
          f.close_hu = ""; f.weight = ""; f.move_to = ""; this.drawTree(); this.drawScanner();
        }
      }
    }

    async packAll() {
      const f = this.form;
      if (!f.source || !f.dest) { this.msg = { kind: "err", text: __("Scan the source and destination HUs.") }; return this.drawScanner(); }
      await this.call("pack_all", { source_hu: f.source, destination_hu: f.dest, outbound_delivery: f.delivery || undefined, idempotency_key: `PSALL:${frappe.utils.get_random(12)}` },
        __("Everything in {0} packed into {1}", [f.source, f.dest]));
    }

    // ---------- packing orders ----------
    drawOrders() {
      const $o = this.$wrap.find(".wps-orders").empty();
      const orders = this.data.packing_orders;
      if (!orders.length) return;
      $o.append(`<h6>${__("Open packing orders at this station")}</h6>`);
      orders.forEach((o) => {
        const $r = $(`<div class="wps-node" style="cursor:pointer"><b>${esc(o.name)}</b> <span class="text-muted">${esc(o.outbound_delivery || "")}</span>
          <span style="font-size:12px;">${esc((o.source_hus || []).join(", "))} → ${esc((o.destination_hus || []).join(", "))}</span> <span class="wps-tag">${esc(o.status)}</span></div>`);
        $r.on("click", () => {
          this.form.source = (o.source_hus || [])[0] || ""; this.form.dest = (o.destination_hus || [])[0] || "";
          this.form.delivery = o.outbound_delivery || ""; this.tab = "product"; this.drawTree(); this.drawScanner();
        });
        $o.append($r);
      });
    }
  }

  window.WMSPackingStation = PackingStation;
})();
