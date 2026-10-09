// Create Warehouse Task without reference, SAP style (Fiori "Create HU Warehouse Task" / /SCWM/ADHU, "Create Product Warehouse Task" / /SCWM/ADPROD):
// find bar on top (quick find or advanced search), the worklist as a table with the destination data per row, a detail form per row,
// tabs below (created tasks, content, master data / status) and a status bar. Create makes the tasks line by line: a locked or refused line does not stop the rest.
window.wms_workbench = (function () {
  const esc = frappe.utils.escape_html;
  const call = (method, args, opts) => frappe.call({ method: `frappe_wms.api.${method}`, args, ...opts }).then((r) => r.message);
  const MODES = {
    hu: {
      title: (wh) => __("Create HU Warehouse Task in Warehouse Number {0}", [wh]), view: "hu", finds: [["handling_unit", __("Handling Unit")], ["storage_bin", __("Storage Bin")], ["product", __("Product")]],
      cols: [["open_wt", __("Open WT")], ["handling_unit", __("Source HU")], ["top_hu", __("Highest HU")], ["storage_type", __("Source Storage Type")], ["storage_section", __("SrcStorSec")], ["source_bin", __("Source Bin")]],
      tabs: [["created", __("Created HU WTs")], ["content", __("Content")], ["master", __("Master Data/Status")]],
      detail: [[["handling_unit", __("Source HU")], ["source_bin", __("Source Bin")], ["storage_type", __("Source Storage Type")], ["storage_section", __("Source Storage Section")], ["top_hu", __("Highest HU")]], []],
    },
    stock: {
      title: (wh) => __("Create Product Warehouse Task in Warehouse Number {0}", [wh]), view: "stock", finds: [["product", __("Product")], ["storage_bin", __("Storage Bin")], ["handling_unit", __("Handling Unit")]],
      cols: [["product", __("Product")], ["batch_no", __("Batch")], ["stock_type", __("Stock Type")], ["handling_unit", __("Source HU")], ["storage_type", __("Source Storage Type")], ["source_bin", __("Source Bin")], ["available", __("Available")]],
      tabs: [["created", __("Created Product WTs")], ["content", __("Stock in Source Bin")]],
      detail: [[["product", __("Product")], ["batch_no", __("Batch")], ["stock_type", __("Stock Type")], ["handling_unit", __("Source HU")], ["source_bin", __("Source Bin")]], []],
    },
  };
  const CSS = `.wms-wb .wb-bar select.form-control,.wms-wb .wb-bar input.wb-value{width:auto;display:inline-block}.wms-wb .wb-title{margin:4px 0 10px}.wms-wb .wb-bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:8px}.wms-wb .wb-bar label{margin:0;color:var(--text-muted);font-size:12px}
    .wms-wb .wb-strip{border-top:1px solid var(--border-color);border-bottom:1px solid var(--border-color);padding:6px 0}.wms-wb .wb-table{overflow:auto;max-height:340px;border:1px solid var(--border-color);margin:8px 0}
    .wms-wb table{margin:0;font-size:12px}.wms-wb th{background:var(--control-bg);white-space:nowrap}.wms-wb tr.sel td{background:var(--highlight-color,#eef)}.wms-wb input.wb-cell{width:110px;height:24px;font-size:12px}
    .wms-wb .wb-detail{display:none;border:1px solid var(--border-color);padding:12px;margin:8px 0}.wms-wb .wb-detail .col{display:inline-block;vertical-align:top;width:48%}.wms-wb .wb-detail .f{margin-bottom:6px}.wms-wb .wb-detail .f span.l{display:inline-block;width:170px;color:var(--text-muted)}
    .wms-wb .wb-tabs{display:flex;gap:2px;border-bottom:1px solid var(--border-color)}.wms-wb .wb-tab{padding:6px 14px;cursor:pointer}.wms-wb .wb-tab.active{border-bottom:2px solid var(--primary);font-weight:600}
    .wms-wb .wb-pane{padding:8px 0;min-height:90px}.wms-wb .wb-status{margin-top:8px;padding:6px 10px;background:var(--control-bg);font-size:12px;border-left:4px solid var(--gray-400)}
    .wms-wb .wb-status.ok{border-color:var(--green-500)}.wms-wb .wb-status.warn{border-color:var(--orange-500)}.wms-wb .wb-status.err{border-color:var(--red-500)}`;

  function mount(page, mode) {
    const M = MODES[mode];
    if (!document.getElementById("wms-wb-css")) $(`<style id="wms-wb-css">${CSS}</style>`).appendTo(document.head);
    const state = { rows: [], sel: new Set(), cur: 0, warehouses: [], created: [], selection: null, results: new Map(), detail: false, tab: M.tabs[0][0] };
    const hist = () => { try { return JSON.parse(localStorage.getItem(`wms_wb_${mode}`) || "[]"); } catch (e) { return []; } };
    const $root = $(`<div class="wms-wb">
      <h4 class="wb-title"></h4>
      <div class="wb-bar">
        <label>${__("Warehouse")}</label><select class="form-control input-sm wb-wh"></select>
        <label>${__("Show")}</label><select class="form-control input-sm wb-show"><option value="">${__("All")}</option><option value="free">${__("Without open WT")}</option><option value="open">${__("With open WT")}</option></select>
        <label>${__("Find")}</label><select class="form-control input-sm wb-by">${M.finds.map(([k, l]) => `<option value="${k}">${esc(l)}</option>`).join("")}</select>
        <input class="form-control input-sm wb-value" style="width:220px" placeholder="${__("Number or pattern, * = any")}">
        <button class="btn btn-primary btn-sm wb-go">${__("Go")}</button>
        <span class="dropdown"><button class="btn btn-default btn-sm" data-toggle="dropdown" title="${__("Recent searches")}">&#8634;</button><ul class="dropdown-menu wb-hist"></ul></span>
        <button class="btn btn-default btn-sm wb-adv">${__("Open Advanced Search")}</button>
      </div>
      <div class="wb-bar wb-strip">
        <button class="btn btn-default btn-xs wb-prev" title="${__("Previous row")}">&#9664;</button><button class="btn btn-default btn-xs wb-next" title="${__("Next row")}">&#9654;</button>
        <button class="btn btn-default btn-xs wb-toggle">${__("Detail")}</button>
        <button class="btn btn-default btn-xs wb-del">${__("Delete Row")}</button>
        <button class="btn btn-default btn-xs wb-refresh">${__("Refresh")}</button>
        <span class="wb-defaults"></span>
        <button class="btn btn-primary btn-xs wb-create">${__("Create")}</button>
        <button class="btn btn-default btn-xs wb-create-confirm">${__("Create + Confirm")}</button>
      </div>
      <div class="wb-table"></div>
      <div class="wb-detail"></div>
      <div class="wb-tabs">${M.tabs.map(([k, l], i) => `<div class="wb-tab ${i ? "" : "active"}" data-tab="${k}">${esc(l)}</div>`).join("")}</div>
      <div class="wb-pane"></div>
      <div class="wb-status">${__("Enter a search and press Go, or open the advanced search.")}</div>
      <div class="wb-adv-sel" style="display:none"></div><div class="wb-adv-res" style="display:none"></div></div>`).appendTo(page.main);
    const wh = () => $root.find(".wb-wh").val();
    const setTitle = () => { $root.find(".wb-title").text(M.title(wh())); page.set_title(M.title(wh())); };
    const status = (text, kind) => $root.find(".wb-status").attr("class", `wb-status ${kind || ""}`).text(text);
    const shown = () => { const f = $root.find(".wb-show").val(); return state.rows.filter((r) => !f || (f === "open" ? flt(r.open_wt) > 0 : !flt(r.open_wt))); };

    // defaults for rows that leave destination / process type blank
    const def = {};
    const mk = (name, label, doctype, query) => { const c = frappe.ui.form.make_control({ df: { fieldtype: "Link", fieldname: name, label, options: doctype, get_query: query }, parent: $root.find(".wb-defaults"), render_input: true, only_input: false }); c.$wrapper.css({ display: "inline-block", width: "190px", margin: "0 6px 0 0" }); def[name] = c; return c; };
    mk("process_type", __("Whse Proc. Type"), "Warehouse Process Type", () => ({ filters: { activity: ["in", ["Internal Move", "Putaway"]], active: 1 } }));
    mk("destination_bin", __("Destination Bin"), "Storage Bin", () => ({ filters: { warehouse: wh() } }));

    function draw() {
      const rows = shown();
      const head = `<tr><th></th><th>${__("No.")}</th>${M.cols.map(([, l]) => `<th>${esc(l)}</th>`).join("")}${mode === "stock" ? `<th>${__("Quantity")}</th>` : ""}<th>${__("Whse Proc. Type")}</th><th>${__("Destination Bin")}</th><th>${__("Task Confirmation")}</th><th></th></tr>`;
      const body = rows.map((r, i) => {
        const res = state.results.get(r.key);
        return `<tr data-k="${esc(r.key)}" class="${state.sel.has(r.key) ? "sel" : ""}"><td><input type="checkbox" class="wb-pick" ${state.sel.has(r.key) ? "checked" : ""}></td><td>${i + 1}</td>
          ${M.cols.map(([f]) => `<td>${esc(r[f] ?? "")}</td>`).join("")}
          ${mode === "stock" ? `<td><input class="form-control wb-cell wb-q" data-f="quantity" value="${esc(r.quantity ?? "")}" style="width:70px"></td>` : ""}
          <td><input class="form-control wb-cell" data-f="process_type" value="${esc(r.process_type || "")}" style="width:90px"></td>
          <td><input class="form-control wb-cell" data-f="destination_bin" value="${esc(r.destination_bin || "")}"></td>
          <td style="text-align:center"><input type="checkbox" data-f="confirm" ${r.confirm ? "checked" : ""}></td>
          <td>${res ? (res.error ? `<span class="text-danger" title="${esc(res.error)}">&#10008; ${esc(res.error.slice(0, 60))}</span>` : `<span class="text-success">&#10004; ${esc(res.tasks.join(", "))}</span>`) : ""}</td></tr>`;
      }).join("");
      $root.find(".wb-table").html(`<table class="table table-bordered table-sm"><thead>${head}</thead><tbody>${body || `<tr><td colspan="${M.cols.length + 6}" class="text-muted">${__("No rows")}</td></tr>`}</tbody></table>`);
      if (state.cur >= rows.length) state.cur = Math.max(rows.length - 1, 0);
      drawDetail(); drawPane();
    }
    const rowOf = (k) => state.rows.find((r) => r.key === k);
    const current = () => shown()[state.cur];

    function drawDetail() {
      const r = current(), n = shown().length;
      if (!r) { $root.find(".wb-detail").empty(); return; }
      const f = ([k, l]) => `<div class="f"><span class="l">${esc(l)}</span>${esc(r[k] ?? "")}</div>`;
      const inp = (k, l, extra = "") => `<div class="f"><span class="l">${esc(l)}</span><input class="form-control input-sm d-in" data-f="${k}" value="${esc(r[k] || "")}" style="width:200px;display:inline-block" ${extra}></div>`;
      $root.find(".wb-detail").html(`<div class="col">${M.detail[0].map(f).join("")}${inp("destination_bin", __("Destination Storage Bin"))}${mode === "stock" ? inp("quantity", __("Quantity")) : ""}</div>
        <div class="col">${inp("process_type", __("Whse Proc. Type"))}${inp("priority", __("Priority"))}${inp("reason", __("Reason"))}
          <div class="f"><span class="l">${__("Open HU WT")}</span><input type="checkbox" disabled ${flt(r.open_wt) ? "checked" : ""}></div>
          <div class="f"><span class="l">${__("Confirmation")}</span><input type="checkbox" class="d-conf" ${r.confirm ? "checked" : ""}></div>
          <div class="text-muted" style="text-align:right">${state.cur + 1} / ${n}</div></div>`);
    }

    async function drawPane() {
      const $p = $root.find(".wb-pane"), r = current();
      $root.find(".wb-tab").removeClass("active").filter(`[data-tab="${state.tab}"]`).addClass("active");
      const table = (cols, rows) => `<table class="table table-bordered table-sm"><thead><tr>${cols.map(([, l]) => `<th>${esc(l)}</th>`).join("")}</tr></thead><tbody>${rows.map((x) => `<tr>${cols.map(([k]) => `<td>${esc(x[k] ?? "")}</td>`).join("")}</tr>`).join("") || `<tr><td class="text-muted">${__("Nothing to show")}</td></tr>`}</tbody></table>`;
      if (state.tab === "created") {
        const rows = state.created.length ? await call("adhoc.task_status", { names: JSON.stringify(state.created) }) : [];
        $p.html(table([["name", __("Task")], ["task_type", __("Type")], ["status", __("Status")], ["product", __("Product")], ["planned_quantity", __("Quantity")], ["source_bin", __("Source Bin")], ["destination_bin", __("Destination Bin")], ["source_hu", __("Source HU")], ["warehouse_order", __("Warehouse Order")], ["reason", __("Reason")]], rows));
      } else if (!r) { $p.empty(); }
      else if (state.tab === "content") {
        const rows = mode === "hu" ? await call("adhoc.hu_content", { handling_unit: r.handling_unit }) : await call("adhoc.find_rows", { warehouse: wh(), mode: "stock", by: "storage_bin", value: r.source_bin });
        $p.html(table(mode === "hu" ? [["product", __("Product")], ["batch_no", __("Batch")], ["serial_no", __("Serial No.")], ["stock_type", __("Stock Type")], ["quantity", __("Quantity")], ["allocated_quantity", __("Allocated")], ["stock_uom", __("UoM")]]
          : [["product", __("Product")], ["batch_no", __("Batch")], ["stock_type", __("Stock Type")], ["handling_unit", __("HU")], ["available", __("Available")], ["stock_uom", __("UoM")]], rows));
      } else if (state.tab === "master") {
        const m = await call("adhoc.hu_master", { handling_unit: r.handling_unit }) || {};
        $p.html(`<table class="table table-bordered table-sm"><tbody>${Object.entries(m).map(([k, v]) => `<tr><td style="width:240px" class="text-muted">${esc(frappe.model.unscrub(k))}</td><td>${esc(v ?? "")}</td></tr>`).join("")}</tbody></table>`);
      }
    }

    async function load(args) {
      status(__("Searching…"));
      const rows = await call("adhoc.find_rows", { warehouse: wh(), mode: M.view === "hu" ? "hu" : "stock", ...args });
      const keep = new Map(state.rows.map((r) => [r.key, r]));
      rows.forEach((r) => Object.assign(r, { destination_bin: keep.get(r.key)?.destination_bin, process_type: keep.get(r.key)?.process_type, confirm: keep.get(r.key)?.confirm }));
      return rows;
    }
    async function find() {
      const by = $root.find(".wb-by").val(), value = $root.find(".wb-value").val().trim();
      if (!value) { status(__("Enter what to find, or * for everything."), "warn"); return; }
      const rows = await load({ by, value });
      const have = new Set(state.rows.map((r) => r.key));
      state.rows = state.rows.concat(rows.filter((r) => !have.has(r.key)));  // a find adds to the worklist, like SAP
      state.cur = state.rows.findIndex((r) => r.key === (rows[0] && rows[0].key)); state.cur = Math.max(state.cur, 0);
      const h = [{ by, value }, ...hist().filter((x) => !(x.by === by && x.value === value))].slice(0, 10);
      try { localStorage.setItem(`wms_wb_${mode}`, JSON.stringify(h)); } catch (e) { /* history is a convenience */ }
      draw(); status(__("Selection resulted in {0} hit(s)", [rows.length]), rows.length ? "ok" : "warn");
    }
    async function advanced() {
      if (!state.selection) {
        state.selection = await new wms_selection.SelectionScreen({
          view: M.view, $mount: $root.find(".wb-adv-sel"), $results: $root.find(".wb-adv-res"), autoOpen: () => false, decorate: { noDetails: true }, getWarehouse: wh,
          makeGrid: (rows) => { Promise.resolve(load({ names: JSON.stringify(rows.map((r) => r.name)) })).then((found) => { state.rows = found; state.sel.clear(); state.cur = 0; state.results.clear(); draw(); status(__("Selection resulted in {0} hit(s)", [found.length]), found.length ? "ok" : "warn"); }); return $("<div></div>"); },
        }).init();
      }
      state.selection.openDialog();
    }

    async function create(confirmNow) {
      const picked = shown().filter((r) => state.sel.has(r.key));
      if (!picked.length) { status(__("Select at least one row."), "warn"); return; }
      const lines = picked.map((r) => ({ ...(mode === "hu" ? { handling_unit: r.handling_unit } : { name: r.name, quantity: flt(r.quantity) }), destination_bin: r.destination_bin, process_type: r.process_type, priority: r.priority, reason: r.reason, confirm: confirmNow || r.confirm ? 1 : 0 }));
      const res = await call("adhoc.process_lines", { lines: JSON.stringify(lines), defaults: JSON.stringify({ destination_bin: def.destination_bin.get_value(), process_type: def.process_type.get_value() }) }, { freeze: true });
      state.results.clear();
      res.created.forEach((c) => { state.results.set(picked[c.line].key, { tasks: c.tasks }); state.created.push(...c.tasks); picked[c.line].open_wt = flt(picked[c.line].open_wt) + c.tasks.length; state.sel.delete(picked[c.line].key); });
      res.errors.forEach((e) => state.results.set(picked[e.line].key, { error: e.error }));
      draw();
      const n = res.created.reduce((a, c) => a + c.tasks.length, 0);
      status(res.errors.length ? __("{0} task(s) created, {1} row(s) refused: {2}", [n, res.errors.length, res.errors[0].error]) : __("{0} task(s) created", [n]), res.errors.length ? "err" : "ok");
    }

    $root.on("change", ".wb-pick", (e) => { const k = $(e.currentTarget).closest("tr").data("k"); e.currentTarget.checked ? state.sel.add(k) : state.sel.delete(k); $(e.currentTarget).closest("tr").toggleClass("sel", e.currentTarget.checked); });
    $root.on("change", ".wb-table input[data-f]", (e) => { const $i = $(e.currentTarget), r = rowOf($i.closest("tr").data("k")); r[$i.data("f")] = $i.is(":checkbox") ? $i.prop("checked") : $i.val(); });
    $root.on("change", ".d-in", (e) => { const r = current(); if (r) { r[$(e.currentTarget).data("f")] = e.currentTarget.value; draw(); } });
    $root.on("change", ".d-conf", (e) => { const r = current(); if (r) { r.confirm = e.currentTarget.checked; draw(); } });
    $root.on("click", ".wb-table tbody tr", (e) => { if ($(e.target).is("input")) return; state.cur = shown().findIndex((r) => r.key === $(e.currentTarget).data("k")); drawDetail(); drawPane(); });
    $root.on("click", ".wb-tab", (e) => { state.tab = $(e.currentTarget).data("tab"); drawPane(); });
    $root.on("click", ".wb-prev", () => { state.cur = Math.max(state.cur - 1, 0); drawDetail(); drawPane(); });
    $root.on("click", ".wb-next", () => { state.cur = Math.min(state.cur + 1, shown().length - 1); drawDetail(); drawPane(); });
    $root.on("click", ".wb-toggle", () => { state.detail = !state.detail; $root.find(".wb-detail").toggle(state.detail); $root.find(".wb-table").toggle(!state.detail); $root.find(".wb-toggle").text(state.detail ? __("List") : __("Detail")); });
    $root.on("click", ".wb-del", () => { state.rows = state.rows.filter((r) => !state.sel.has(r.key)); state.sel.clear(); draw(); });
    $root.on("click", ".wb-refresh", async () => { if (!state.rows.length) return; const found = await load({ names: JSON.stringify(state.rows.map((r) => r.key)) }); state.rows = found; draw(); status(__("Refreshed {0} row(s)", [found.length]), "ok"); });
    $root.on("click", ".wb-go", find);
    $root.on("keydown", ".wb-value", (e) => { if (e.key === "Enter") find(); });
    $root.on("click", ".wb-adv", advanced);
    $root.on("click", ".wb-create", () => create(false));
    $root.on("click", ".wb-create-confirm", () => create(true));
    $root.on("show.bs.dropdown", () => $root.find(".wb-hist").html(hist().map((h, i) => `<li><a href="#" data-i="${i}">${esc(h.value)} <span class="text-muted">(${esc(h.by)})</span></a></li>`).join("") || `<li class="disabled"><a>${__("No searches yet")}</a></li>`));
    $root.on("click", ".wb-hist a", (e) => { e.preventDefault(); const h = hist()[$(e.currentTarget).data("i")]; $root.find(".wb-by").val(h.by); $root.find(".wb-value").val(h.value); find(); });
    $root.on("change", ".wb-wh", () => { setTitle(); state.rows = []; state.sel.clear(); state.results.clear(); draw(); });
    $root.on("change", ".wb-show", draw);

    return frappe.call({ method: "frappe.client.get_list", args: { doctype: "WMS Warehouse", fields: ["name"], limit_page_length: 100 } }).then((r) => {
      state.warehouses = (r.message || []).map((w) => w.name);
      const mine = frappe.defaults.get_user_default("WMS Warehouse");
      $root.find(".wb-wh").html(state.warehouses.map((w) => `<option>${esc(w)}</option>`).join("")).val(state.warehouses.includes(mine) ? mine : state.warehouses[0]);
      setTitle(); draw();
      return { find: (by, value) => { $root.find(".wb-by").val(by); $root.find(".wb-value").val(value); return find(); } };
    });
  }

  const v = () => Math.floor(Date.now() / 600000);
  const load = (src) => new Promise((resolve, reject) => {
    if (document.querySelector(`script[data-wms-src^="${src}"]`)) { resolve(); return; }
    const el = document.createElement("script");
    el.src = `${src}?v=${v()}`; el.dataset.wmsSrc = el.src; el.onload = () => resolve(); el.onerror = () => reject(new Error(`could not load ${src}`));
    document.head.appendChild(el);
  });
  function boot(wrapper, mode) {
    const page = frappe.ui.make_app_page({ parent: wrapper, title: MODES[mode].title(""), single_column: true });
    return load("/assets/frappe_wms/js/wms_grid.js").then(() => load("/assets/frappe_wms/js/wms_selection.js")).then(() => mount(page, mode))
      .catch((e) => frappe.msgprint({ title: __("Create Warehouse Task"), indicator: "red", message: esc(e.message) }));
  }
  return { boot };
})();
