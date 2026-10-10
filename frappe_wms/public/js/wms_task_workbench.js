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
      cols: [["product", __("Product")], ["batch_no", __("Batch")], ["stock_type", __("Stock Type")], ["handling_unit", __("Source HU")], ["storage_type", __("Source Storage Type")], ["storage_section", __("SrcStorSec")], ["source_bin", __("Source Bin")], ["available", __("Available")]],
      tabs: [["created", __("Created Product WTs")], ["content", __("Stock in Source Bin")]],
      detail: [[["product", __("Product")], ["batch_no", __("Batch")], ["stock_type", __("Stock Type")], ["handling_unit", __("Source HU")], ["source_bin", __("Source Bin")]], []],
    },
  };
  MODES.posting = {
    title: (wh) => __("Posting Change in Warehouse Number {0}", [wh]), view: "stock", finds: MODES.stock.finds, doc: true,
    cols: [["product", __("Product")], ["batch_no", __("Batch")], ["stock_type", __("Stock Type")], ["handling_unit", __("Source HU")], ["storage_type", __("Source Storage Type")], ["source_bin", __("Source Bin")], ["available", __("Available")]],
    edit: [["quantity", __("Quantity"), 70], ["to_stock_type", __("New Stock Type"), 140], ["to_stock_owner", __("New Owner"), 110], ["to_entitled_party", __("New Party"), 110], ["to_country_of_origin", __("New Country"), 80], ["to_batch_no", __("New Batch"), 100], ["reason", __("Reason"), 170]],
    mass: [["to_stock_type", __("New Stock Type"), "Link", "WMS Stock Type"], ["to_stock_owner", __("New Owner"), "Link", "WMS Stock Owner"], ["to_entitled_party", __("New Party Entitled to Dispose"), "Link", "WMS Entitled Party"],
           ["to_country_of_origin", __("New Country of Origin"), "Link", "Country"], ["to_batch_no", __("New Batch"), "Link", "Batch"], ["reason", __("Reason"), "Small Text"]],
    tabs: [["created", __("Posted Changes")], ["content", __("Stock in Source Bin")]],
  };
  MODES.hu.mass = MODES.stock.mass = [["process_type", __("Whse Proc. Type"), "Link", "Warehouse Process Type"], ["destination_bin", __("Destination Bin"), "Link", "Storage Bin"], ["destination_hu", __("Destination HU"), "Link", "Handling Unit"],
    ["priority", __("Priority"), "Select", "Low\nNormal\nHigh\nUrgent"], ["reason", __("Reason"), "Small Text"], ["confirm", __("Confirmation"), "Check"]];
  MODES.hu.mass = [...MODES.hu.mass, ["unpack", __("No HU WT"), "Check"]];
  [MODES.hu.mass, MODES.stock.mass].forEach((m) => m.splice(1, 0, ["destination_storage_type", __("Destination Storage Type"), "Link", "Storage Type"], ["destination_section", __("Destination Storage Section"), "Link", "Storage Section"]));
  MODES.hu.edit = [["process_type", __("Whse Proc. Type"), 90], ["destination_hu", __("Destination HU"), 120], ["destination_storage_type", __("Dest. Storage Type"), 100], ["destination_section", __("DstStorSec"), 80], ["destination_bin", __("Destination Bin"), 130]];
  MODES.stock.edit = [["quantity", __("Quantity"), 70], ...MODES.hu.edit];
  MODES.scrap = {
    ...MODES.posting, title: (wh) => __("Scrapping in Warehouse Number {0}", [wh]), verb: __("Scrap"), api: "stock_adjustment.process_scrap_lines", doctype: "WMS Stock Adjustment", posted: __("{0} scrapping document(s) posted"),
    edit: [["quantity", __("Quantity"), 70], ["reason", __("Reason"), 220]], mass: [["reason", __("Reason"), "Small Text"]], tabs: [["created", __("Scrapped")], ["content", __("Stock in Source Bin")]],
  };
  // The posting change can also relocate the stock (SAP: it then creates a warehouse task): the same destination fields as the ad hoc tasks, checked with Enter.
  const MOVE = ["process_type", "destination_storage_type", "destination_section", "destination_bin", "confirm"];
  Object.assign(MODES.posting, { verb: __("Post"), api: "posting_change.process_lines", checkApi: "posting_change.check_lines", doctype: "WMS Posting Change", posted: __("{0} posting change(s) posted"),
    edit: [...MODES.posting.edit, ["process_type", __("Whse Proc. Type"), 90], ["destination_storage_type", __("Dest. Storage Type"), 100], ["destination_section", __("DstStorSec"), 80], ["destination_bin", __("Destination Bin"), 130]],
    mass: [...MODES.posting.mass, ...MODES.stock.mass.filter((m) => MOVE.includes(m[0]))] });
  const CSS = `.wms-wb .wb-bar select.form-control,.wms-wb .wb-bar input.wb-value{width:auto;display:inline-block}.wms-wb .wb-title{margin:4px 0 10px}.wms-wb .wb-bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:8px}.wms-wb .wb-bar label{margin:0;color:var(--text-muted);font-size:12px}
    .wms-wb .wb-strip{border-top:1px solid var(--border-color);border-bottom:1px solid var(--border-color);padding:6px 0}.wms-wb .wb-table{margin:8px 0}.wms-wb .wb-table .wms-grid-scroll{max-height:340px}
    .wms-wb table{margin:0;font-size:12px}.wms-wb th{background:var(--control-bg);white-space:nowrap}.wms-wb tr.sel td{background:var(--highlight-color,#eef)}.wms-wb input.wb-cell{width:110px;height:24px;font-size:12px}
    .wms-wb .wb-detail{display:none;border:1px solid var(--border-color);padding:12px;margin:8px 0}.wms-wb .wb-detail .col{display:inline-block;vertical-align:top;width:48%}.wms-wb .wb-detail .f{margin-bottom:6px}.wms-wb .wb-detail .f span.l{display:inline-block;width:170px;color:var(--text-muted)}
    .wms-wb .wb-tabs{display:flex;gap:2px;border-bottom:1px solid var(--border-color)}.wms-wb .wb-tab{padding:6px 14px;cursor:pointer}.wms-wb .wb-tab.active{border-bottom:2px solid var(--primary);font-weight:600}
    .wms-wb .wb-pane{padding:8px 0;min-height:90px}.wms-wb .wb-status{margin-top:8px;padding:6px 10px;background:var(--control-bg);font-size:12px;border-left:4px solid var(--gray-400)}
    .wms-wb .wb-status.ok{border-color:var(--green-500)}.wms-wb .wb-status.warn{border-color:var(--orange-500)}.wms-wb .wb-status.err{border-color:var(--red-500)}`;

  function mount(page, mode) {
    const M = MODES[mode];
    if (!document.getElementById("wms-wb-css")) $(`<style id="wms-wb-css">${CSS}</style>`).appendTo(document.head);
    const state = { rows: [], picked: [], cur: 0, warehouses: [], created: [], selection: null, detail: false, tab: M.tabs[0][0] };
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
        ${M.doc && !M.checkApi ? "" : `<button class="btn btn-default btn-xs wb-check" title="${__("Determine process type and destination for the marked rows (all rows if none is marked) without creating anything")}">&#10003; ${__("Check (Enter)")}</button>`}
        <button class="btn btn-default btn-xs wb-mass" title="${__("Enter values once and put them into all marked rows")}">&#9998; ${__("Mass Change")}</button>
        <button class="btn btn-primary btn-xs wb-create">${M.doc ? M.verb : __("Create")}</button>
        ${M.doc ? "" : `<button class="btn btn-default btn-xs wb-create-confirm">${__("Create + Confirm")}</button>`}
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

    // Mass Change: the values entered here go into every marked row, so nothing has to be typed row by row.
    function massChange() {
      const rows = state.picked.length ? state.picked : (state.detail && current() ? [current()] : []);
      if (!rows.length) { status(__("Mark the rows to change first (click their row numbers)."), "warn"); return; }
      const d = new frappe.ui.Dialog({
        title: __("Mass Change for {0} marked row(s)", [rows.length]),
        fields: M.mass.map(([fieldname, label, fieldtype, options]) => ({ fieldname, label, fieldtype, options,
          get_query: fieldname === "destination_bin" ? () => ({ filters: { warehouse: wh() } }) : fieldname === "process_type" ? () => ({ filters: { activity: ["in", ["Internal Move", "Putaway"]], active: 1 } }) : undefined })),
        primary_action_label: __("Apply to marked rows"),
        primary_action: (values) => {
          Object.entries(values).forEach(([f, v]) => { if (!v) return; rows.forEach((r) => { r[f] = v === 1 ? true : v; $root.find(`.wb-in[data-k="${window.CSS.escape(r.key)}"][data-f="${f}"]`).each((_, el) => { el.type === "checkbox" ? (el.checked = true) : (el.value = v); }); }); });
          d.hide(); drawDetail(); status(__("Values placed into {0} row(s)", [rows.length]), "ok");
        },
      });
      d.show();
    }

    // The list is the Monitor's grid: cells and rows can be marked and copied like in Excel; the cells that need input are real input fields.
    const inp = (f, w) => (r) => `<input class="form-control wb-cell wb-in" data-k="${esc(r.key)}" data-f="${f}" value="${esc(r[f] ?? "")}" style="width:${w}px">`;
    function draw() {
      const rows = shown();
      const cols = [...M.cols, ...M.edit.map(([f, l, w]) => [f, l, inp(f, w)]),
        ...(M.doc && !M.checkApi ? [] : [["confirm", __("Task Confirmation"), (r) => `<input type="checkbox" class="wb-in" data-k="${esc(r.key)}" data-f="confirm" ${r.confirm ? "checked" : ""}>`]]),
        ["result", __("Result"), (r) => `<span class="wb-res ${(r.result || "").startsWith("\u2714") ? "text-success" : "text-danger"}" data-k="${esc(r.key)}" title="${esc(r.result || "")}">${esc((r.result || "").slice(0, 90))}</span>`]];
      state.picked = [];
      const grid = new window.wms_grid.DataGrid(rows, cols, null, { numeric: ["quantity", "available", "open_wt"],
        onSelect: (sel) => { state.picked = sel; if (sel.length && state.cur !== rows.indexOf(sel[0])) { state.cur = rows.indexOf(sel[0]); drawDetail(); drawPane(); } } });
      $root.find(".wb-table").empty().append(grid.$el);
      if (state.cur >= rows.length) state.cur = Math.max(rows.length - 1, 0);
      drawDetail(); drawPane();
    }
    const rowOf = (k) => state.rows.find((r) => r.key === k);
    const current = () => shown()[state.cur];
    const ptName = {};

    function drawDetail() {
      const r = current(), n = shown().length;
      if (!r) { $root.find(".wb-detail").html(`<div class="text-muted">${__("No rows")}</div>`); return; }
      const f = ([k, l]) => `<div class="f"><span class="l">${esc(l)}</span><span class="v">${esc(r[k] ?? "")}</span></div>`;
      const inpf = (k, l, w = 200) => `<div class="f"><span class="l">${esc(l)}</span><input class="form-control input-sm d-in" data-f="${k}" value="${esc(r[k] || "")}" style="width:${w}px;display:inline-block"></div>`;
      const chk = (k, l, on, ro) => `<div class="f"><span class="l">${esc(l)}</span><input type="checkbox" class="${ro ? "" : "d-chk"}" data-f="${k}" ${on ? "checked" : ""} ${ro ? "disabled" : ""}></div>`;
      if (M.doc) {
        $root.find(".wb-detail").html(`<div class="col">${[["product", __("Product")], ["batch_no", __("Batch")], ["stock_type", __("Stock Type")], ["handling_unit", __("Source HU")], ["source_bin", __("Source Bin")], ["available", __("Available")]].map(f).join("")}${inpf("quantity", __("Quantity"), 90)}</div>
          <div class="col">${M.edit.slice(1).map(([k, l]) => inpf(k, l, k === "reason" ? 260 : 200)).join("")}${M.checkApi ? chk("confirm", __("Confirmation"), r.confirm) : ""}<div class="f"><span class="l">${__("Result")}</span><span class="${(r.result || "").startsWith("\u2714") ? "text-success" : "text-danger"}">${esc(r.result || "")}</span></div><div class="text-muted" style="text-align:right">${state.cur + 1} / ${n}</div></div>`);
        return;
      }
      $root.find(".wb-detail").html(`<div class="col">${M.detail[0].map(f).join("")}${f(["storage_type", __("Source Storage Type")])}${f(["storage_section", __("Source Storage Section")])}
          ${mode === "stock" ? inpf("quantity", __("Quantity"), 90) : ""}${inpf("destination_hu", __("Destination HU"))}${inpf("destination_storage_type", __("Destination Storage Type"), 120)}${inpf("destination_section", __("Destination Storage Section"), 120)}${inpf("destination_bin", __("Destination Storage Bin"))}
          <div class="f"><span class="l">${__("Result")}</span><span class="${(r.result || "").startsWith("\u2714") ? "text-success" : "text-danger"}">${esc(r.result || "")}</span></div></div>
        <div class="col">${chk("open", __("Open HU WT"), flt(r.open_wt) > 0, true)}${chk("step", __("HU Step Completed"), r.step_done, true)}${chk("confirm", __("Confirmation"), r.confirm)}
          ${mode === "hu" ? chk("unpack", __("No HU WT"), r.unpack) : ""}${chk("added", __("Add.WTs Creatd"), flt(r.created_n) > 0, true)}
          <div class="f"><span class="l">${__("Whse Proc. Type")}</span><input class="form-control input-sm d-in" data-f="process_type" value="${esc(r.process_type || "")}" style="width:90px;display:inline-block"> <span class="text-muted pt-name">${esc(ptName[r.process_type] || "")}</span></div>
          ${inpf("priority", __("Priority"), 120)}${inpf("reason", __("Reason"), 260)}
          <div class="text-muted" style="text-align:right">${state.cur + 1} / ${n}</div></div>`);
      if (r.process_type && ptName[r.process_type] === undefined) frappe.db.get_value("Warehouse Process Type", r.process_type, "process_type_name").then((x) => { ptName[r.process_type] = (x.message && x.message.process_type_name) || ""; $root.find(".pt-name").text(ptName[r.process_type]); });
    }

    async function drawPane() {
      const $p = $root.find(".wb-pane"), r = current();
      $root.find(".wb-tab").removeClass("active").filter(`[data-tab="${state.tab}"]`).addClass("active");
      const table = (cols, rows) => `<table class="table table-bordered table-sm"><thead><tr>${cols.map(([, l]) => `<th>${esc(l)}</th>`).join("")}</tr></thead><tbody>${rows.map((x) => `<tr>${cols.map(([k]) => `<td>${esc(x[k] ?? "")}</td>`).join("")}</tr>`).join("") || `<tr><td class="text-muted">${__("Nothing to show")}</td></tr>`}</tbody></table>`;
      if (state.tab === "created") {
        const rows = state.created.length ? (M.doc ? await frappe.call({ method: "frappe.client.get_list", args: { doctype: M.doctype, filters: [["name", "in", state.created]], fields: M.doctype === "WMS Posting Change" ? ["name", "status", "product", "quantity", "from_stock_type", "to_stock_type", "storage_bin", "reason"] : ["name", "status", "product", "quantity", "stock_type", "storage_bin", "reason"], limit_page_length: 200 } }).then((r) => r.message || [])
          : await call("adhoc.task_status", { names: JSON.stringify(state.created) })) : [];
        if (M.doc) { $p.html(table(M.doctype === "WMS Posting Change" ? [["name", __("Document")], ["status", __("Status")], ["product", __("Product")], ["quantity", __("Quantity")], ["from_stock_type", __("From")], ["to_stock_type", __("To")], ["storage_bin", __("Bin")], ["reason", __("Reason")]] : [["name", __("Document")], ["status", __("Status")], ["product", __("Product")], ["quantity", __("Quantity")], ["stock_type", __("Stock Type")], ["storage_bin", __("Bin")], ["reason", __("Reason")]], rows)); return; }
        $p.html(table([["name", __("Task")], ["task_type", __("Type")], ["status", __("Status")], ["product", __("Product")], ["planned_quantity", __("Quantity")], ["source_bin", __("Source Bin")], ["destination_bin", __("Destination Bin")], ["source_hu", __("Source HU")], ["destination_hu", __("Destination HU")], ["warehouse_order", __("Warehouse Order")], ["reason", __("Reason")]], rows));
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
          makeGrid: (rows) => { Promise.resolve(load({ names: JSON.stringify(rows.map((r) => r.name)) })).then((found) => { state.rows = found; state.cur = 0; draw(); status(__("Selection resulted in {0} hit(s)", [found.length]), found.length ? "ok" : "warn"); }); return $("<div></div>"); },
        }).init();
      }
      state.selection.openDialog();
    }

    const lineOf = (r, confirmNow) => M.doc ? { name: r.name, quantity: flt(r.quantity), ...Object.fromEntries(M.edit.slice(1).map(([k]) => [k, r[k]])), confirm: confirmNow || r.confirm ? 1 : 0 }
      : { ...(mode === "hu" ? { handling_unit: r.handling_unit, unpack: r.unpack ? 1 : 0 } : { name: r.name, quantity: flt(r.quantity) }), destination_bin: r.destination_bin, destination_hu: r.destination_hu,
          destination_storage_type: r.destination_storage_type, destination_section: r.destination_section, process_type: r.process_type, priority: r.priority, reason: r.reason, confirm: confirmNow || r.confirm ? 1 : 0 };
    // what a result fills into a row: the process type and the destination bin with its storage type and section (Enter in SAP)
    function fill(r, res) {
      ["process_type", "destination_bin", "destination_storage_type", "destination_section"].forEach((f) => {
        if (res[f] === undefined) return;
        r[f] = res[f] || "";
        $root.find(`.wb-in[data-k="${window.CSS.escape(r.key)}"][data-f="${f}"]`).val(r[f]);
      });
    }
    const showResult = (r, text) => { r.result = text; $root.find(`.wb-res[data-k="${window.CSS.escape(r.key)}"]`).text(text.slice(0, 90)).attr("title", text).attr("class", `wb-res ${text.startsWith("\u2714") ? "text-success" : "text-danger"}`); };
    const where = (x) => `${x.destination_bin} (${x.destination_storage_type || "-"} / ${x.destination_section || "-"})`;
    // Enter: resolve the marked rows (or the one being edited) without creating anything - the destination the process type would give, or why it is refused
    async function check(rows) {
      if ((M.doc && !M.checkApi) || !rows.length) return;
      const res = await call(M.checkApi || "adhoc.check_lines", { lines: JSON.stringify(rows.map((r) => lineOf(r))) });
      let bad = 0;
      res.forEach((x) => { const r = rows[x.line]; if (x.ok) { fill(r, x); showResult(r, `\u2714 ${x.summary ? x.summary + (x.destination_bin ? " \u00b7 " : "") : ""}${x.destination_bin ? `${x.process_type_name || x.process_type || ""} \u2192 ${where(x)}${x.destinations > 1 ? " ..." : ""}` : ""}`); } else { bad++; showResult(r, `\u2718 ${x.error}`); } });
      status(bad ? __("{0} row(s) cannot be created: {1}", [bad, res.find((x) => !x.ok).error]) : __("{0} row(s) checked: destinations determined", [rows.length]), bad ? "err" : "ok");
      drawDetail();
    }

    // What was posted or moved changes the stock the rows show: read them again, and add the stock a posting change left behind (its new stock type / owner ...)
    async function reread(added, done, arrived) {
      const old = new Map(state.rows.map((r) => [r.key, r]));
      const keys = [...old.keys(), ...added.map(([k]) => k).filter((k) => k && !old.has(k))];
      const found = await load({ names: JSON.stringify(keys) });
      found.forEach((r) => {
        const was = old.get(r.key) || (added.find(([k]) => k === r.key) || [])[1];
        if (was) Object.assign(r, { result: was.result, created_n: was.created_n, step_done: was.step_done });
        if (old.has(r.key) && !done.has(r.key)) M.edit.forEach(([f]) => { r[f] = old.get(r.key)[f]; });  // a refused row keeps what was typed, so it can be corrected and tried again
      });
      // product tasks that were confirmed: the stock now sits in the destination bin - show it too
      if (M.view !== "hu") {
        const have = new Set(found.map((r) => r.key));
        for (const bin of new Set(arrived || [])) (await call("adhoc.find_rows", { warehouse: wh(), mode: "stock", by: "storage_bin", value: bin })).forEach((r) => { if (!have.has(r.key)) { have.add(r.key); found.push(r); } });
      }
      state.rows = found;
    }

    async function create(confirmNow) {
      const rows = shown();
      const picked = state.picked.length ? state.picked : (state.detail && current() ? [current()] : []);
      if (!picked.length) { status(__("Select at least one row (click its row number)."), "warn"); return; }
      const lines = picked.map((r) => lineOf(r, confirmNow));
      const res = await call(M.doc ? M.api : "adhoc.process_lines", { lines: JSON.stringify(lines), defaults: M.doc ? undefined : "{}" }, { freeze: true });
      res.created.forEach((c) => { const r = picked[c.line], made = M.doc ? [...c.documents, ...(c.tasks || [])] : c.tasks; state.created.push(...(M.doc ? c.documents : made)); r.open_wt = flt(r.open_wt) + made.length; r.created_n = flt(r.created_n) + made.length; r.step_done = confirmNow || r.confirm; r.result = "\u2714 " + made.join(", ") + (c.destination_bin ? " \u2192 " + where(c) : ""); if (c.destination_bin) fill(r, c); });
      res.errors.forEach((e) => { picked[e.line].result = "\u2718 " + e.error; });
      await reread(res.created.map((c) => [c.balance, picked[c.line]]), new Set(res.created.map((c) => picked[c.line].key)),
        M.doc ? [] : res.created.filter((c) => c.destination_bin && (confirmNow || picked[c.line].confirm)).map((c) => c.destination_bin));
      draw();
      const n = res.created.reduce((a, c) => a + (M.doc ? c.documents : c.tasks).length, 0);
      window.wms_grid.report(n ? (M.doc ? __(M.posted, [n]) : __("{0} task(s) created", [n])) : "", res.errors.map((e) => [picked[e.line].handling_unit || `${picked[e.line].product || ""} ${picked[e.line].source_bin || ""}`.trim(), e.error]));
      status(res.errors.length ? (M.doc ? __(M.posted + ", {1} row(s) refused: {2}", [n, res.errors.length, res.errors[0].error]) : __("{0} task(s) created, {1} row(s) refused: {2}", [n, res.errors.length, res.errors[0].error])) : (M.doc ? __(M.posted, [n]) : __("{0} task(s) created", [n])), res.errors.length ? "err" : "ok");
    }

    const CHECKED = ["destination_bin", "destination_hu", "destination_storage_type", "destination_section", "process_type", ...(M.checkApi ? ["quantity", "to_stock_type", "to_stock_owner", "to_entitled_party", "to_country_of_origin", "to_batch_no"] : [])];
    $root.on("change", ".wb-in", (e) => { const $i = $(e.currentTarget), r = rowOf($i.data("k")); if (!r) return; r[$i.data("f")] = $i.is(":checkbox") ? $i.prop("checked") : $i.val(); if (CHECKED.includes($i.data("f")) && $i.val()) check([r]); });
    $root.on("keydown", ".wb-in", (e) => { if (e.key !== "Enter") return; const r = rowOf($(e.currentTarget).data("k")); if (r) { r[$(e.currentTarget).data("f")] = e.currentTarget.value; check([r]); } });
    $root.on("click", ".wb-check", () => check(state.picked.length ? state.picked : shown()));
    $root.on("change", ".d-in", (e) => { const r = current(); if (!r) return; r[$(e.currentTarget).data("f")] = e.currentTarget.value; if (CHECKED.includes($(e.currentTarget).data("f")) && e.currentTarget.value) check([r]); else if ($(e.currentTarget).data("f") === "process_type") drawDetail(); });
    $root.on("keydown", ".d-in", (e) => { if (e.key === "Enter") { const r = current(); if (r) { r[$(e.currentTarget).data("f")] = e.currentTarget.value; check([r]); } } });
    $root.on("change", ".d-chk", (e) => { const r = current(); if (r) r[$(e.currentTarget).data("f")] = e.currentTarget.checked; });
    $root.on("click", ".wb-tab", (e) => { state.tab = $(e.currentTarget).data("tab"); drawPane(); });
    $root.on("click", ".wb-prev", () => { state.cur = Math.max(state.cur - 1, 0); drawDetail(); drawPane(); });
    $root.on("click", ".wb-next", () => { state.cur = Math.min(state.cur + 1, shown().length - 1); drawDetail(); drawPane(); });
    // the marked row opens as a form (SAP: the list turns into the two-column detail), the same button goes back to the list
    $root.on("click", ".wb-toggle", () => { if (!state.detail && state.picked.length) state.cur = Math.max(shown().indexOf(state.picked[0]), 0); state.detail = !state.detail; if (!state.detail) draw(); else drawDetail(); $root.find(".wb-detail").toggle(state.detail); $root.find(".wb-table").toggle(!state.detail); $root.find(".wb-toggle").text(state.detail ? __("List") : __("Detail")); });
    $root.on("click", ".wb-del", () => { const gone = new Set(state.picked.map((r) => r.key)); state.rows = state.rows.filter((r) => !gone.has(r.key)); draw(); });
    $root.on("click", ".wb-refresh", async () => { if (!state.rows.length) return; const found = await load({ names: JSON.stringify(state.rows.map((r) => r.key)) }); state.rows = found; draw(); status(__("Refreshed {0} row(s)", [found.length]), "ok"); });
    $root.on("click", ".wb-go", find);
    $root.on("keydown", ".wb-value", (e) => { if (e.key === "Enter") find(); });
    $root.on("click", ".wb-adv", advanced);
    $root.on("click", ".wb-create", () => create(false));
    $root.on("click", ".wb-mass", massChange);
    $root.on("click", ".wb-create-confirm", () => create(true));
    $root.on("show.bs.dropdown", () => $root.find(".wb-hist").html(hist().map((h, i) => `<li><a href="#" data-i="${i}">${esc(h.value)} <span class="text-muted">(${esc(h.by)})</span></a></li>`).join("") || `<li class="disabled"><a>${__("No searches yet")}</a></li>`));
    $root.on("click", ".wb-hist a", (e) => { e.preventDefault(); const h = hist()[$(e.currentTarget).data("i")]; $root.find(".wb-by").val(h.by); $root.find(".wb-value").val(h.value); find(); });
    $root.on("change", ".wb-wh", () => { setTitle(); state.rows = []; draw(); });
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
