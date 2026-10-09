// Ad hoc processing of many records at once (SAP EWM /SCWM/ADHU, /SCWM/ADPROD, posting change, scrapping):
// the Monitor's advanced selection finds the lines, the action bar processes every marked one.
frappe.pages["wms-adhoc"].on_page_load = function (wrapper) {
  const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Ad Hoc Processing"), single_column: true });
  const esc = frappe.utils.escape_html;
  const v = () => Math.floor(Date.now() / 600000);
  const load = (src) => new Promise((resolve, reject) => {
    if (document.querySelector(`script[data-wms-src^="${src}"]`)) { resolve(); return; }
    const el = document.createElement("script");
    el.src = `${src}?v=${v()}`; el.dataset.wmsSrc = el.src; el.onload = () => resolve(); el.onerror = () => reject(new Error(`could not load ${src}`));
    document.head.appendChild(el);
  });
  const free = (l) => !flt(l.allocated_quantity);
  const names = (lines) => lines.map((l) => ({ name: l.name }));
  const call = (method, args) => frappe.call({ method: `frappe_wms.api.${method}`, args, freeze: true }).then((r) => r.message);
  const done = (msg) => frappe.show_alert({ message: msg, indicator: "green" });

  // transaction -> the monitor view that finds its lines, and what the action bar does with the marked ones
  const TX = {
    adhu: { label: __("Ad Hoc Task: Handling Units"), view: "hu", actions: (wh) => [{ label: __("Create Tasks…"), kind: "primary", run: (rows) => taskDialog(wh(), rows.map((r) => ({ handling_unit: r.name }))) }] },
    adprod: { label: __("Ad Hoc Task: Stock"), view: "stock", actions: (wh) => [{ label: __("Create Tasks…"), kind: "primary", appliesTo: free, run: (rows) => taskDialog(wh(), names(rows)) }] },
    posting: { label: __("Posting Change"), view: "stock", actions: () => [{ label: __("Posting Change…"), kind: "primary", appliesTo: free, run: (rows) => postingDialog(rows) }] },
    scrap: { label: __("Scrapping"), view: "stock", actions: () => [{ label: __("Scrap…"), kind: "danger", appliesTo: free, run: (rows) => scrapDialog(rows) }] },
  };

  function taskDialog(warehouse, lines) {
    const d = new frappe.ui.Dialog({
      title: __("Create Tasks for {0} line(s)", [lines.length]),
      fields: [
        { fieldname: "destination_bin", label: __("Destination Bin"), fieldtype: "Link", options: "Storage Bin", reqd: 1, get_query: () => ({ filters: { warehouse } }) },
        { fieldname: "priority", label: __("Priority"), fieldtype: "Select", options: "Low\nNormal\nHigh\nUrgent", default: "Normal" },
      ],
      primary_action_label: __("Create Tasks"),
      primary_action: async (values) => {
        const tasks = await call("adhoc.create_adhoc_tasks", { lines: JSON.stringify(lines), destination_bin: values.destination_bin, priority: values.priority });
        d.hide(); done(__("{0} task(s) created", [tasks.length]));
        frappe.set_route("List", "Warehouse Task", { name: ["in", tasks] });
      },
    });
    d.show();
  }

  function postingDialog(rows) {
    const d = new frappe.ui.Dialog({
      title: __("Posting Change for {0} line(s)", [rows.length]),
      fields: [
        { fieldname: "to_stock_type", label: __("New Stock Type"), fieldtype: "Link", options: "WMS Stock Type" },
        { fieldname: "to_stock_owner", label: __("New Owner"), fieldtype: "Link", options: "WMS Stock Owner" },
        { fieldname: "to_entitled_party", label: __("New Party Entitled to Dispose"), fieldtype: "Link", options: "WMS Entitled Party" },
        { fieldname: "to_country_of_origin", label: __("New Country of Origin"), fieldtype: "Link", options: "Country" },
        { fieldname: "to_batch_no", label: __("New Batch"), fieldtype: "Link", options: "Batch" },
        { fieldname: "reason", label: __("Reason"), fieldtype: "Small Text", reqd: 1 },
      ],
      primary_action_label: __("Post"),
      primary_action: async (values) => {
        const { reason, to_stock_type, ...changes } = values;
        Object.keys(changes).forEach((k) => { if (!changes[k]) delete changes[k]; });
        const out = await call("stock_adjustment.change_stock", { lines: JSON.stringify(names(rows)), reason, to_stock_type, changes: JSON.stringify(changes) });
        d.hide(); done(__("{0} posting change(s) posted", [out.length])); state.sel && state.sel.execute();
      },
    });
    d.show();
  }

  function scrapDialog(rows) {
    frappe.prompt([{ fieldname: "reason", label: __("Reason"), fieldtype: "Small Text", reqd: 1 }], async ({ reason }) => {
      const out = await call("stock_adjustment.scrap_stock", { lines: JSON.stringify(names(rows)), reason });
      done(__("{0} scrapping document(s) posted", [out.length])); state.sel && state.sel.execute();
    }, __("Scrap {0} line(s)", [rows.length]), __("Scrap"));
  }

  const state = { sel: null, warehouses: [] };
  const $root = $(`<div class="wms-adhoc">
    <div class="form-inline" style="gap:8px;margin-bottom:10px">
      <label>${__("Transaction")}</label><select class="form-control input-sm wms-ah-tx">${Object.entries(TX).map(([k, t]) => `<option value="${k}">${esc(t.label)}</option>`).join("")}</select>
      <label>${__("Warehouse")}</label><select class="form-control input-sm wms-ah-wh"></select>
      <button type="button" class="btn btn-default btn-sm wms-ah-search">${__("Advanced Search")}</button>
    </div>
    <div class="wms-ah-sel"></div><div class="wms-ah-res"></div></div>`).appendTo(page.main);
  const wh = () => $root.find(".wms-ah-wh").val();

  async function open() {
    const tx = TX[$root.find(".wms-ah-tx").val()];
    if (!wh()) { frappe.show_alert({ message: __("Choose a warehouse"), indicator: "orange" }); return; }
    $root.find(".wms-ah-sel,.wms-ah-res").empty();
    state.sel = await new wms_selection.SelectionScreen({
      view: tx.view, $mount: $root.find(".wms-ah-sel"), $results: $root.find(".wms-ah-res"), autoOpen: () => true,
      decorate: { noDetails: true },
      getWarehouse: wh,
      makeGrid: (rows, columns, opts, doctype) => new window.wms_grid.DataGrid(rows, columns, doctype, { ...opts, actions: tx.actions(wh) }).$el,
    }).init();
  }
  $root.find(".wms-ah-search").on("click", open);
  $root.find(".wms-ah-tx").on("change", () => { state.sel = null; $root.find(".wms-ah-sel,.wms-ah-res").empty(); });

  load("/assets/frappe_wms/js/wms_grid.js").then(() => load("/assets/frappe_wms/js/wms_selection.js")).then(async () => {
    const r = await frappe.call({ method: "frappe.client.get_list", args: { doctype: "WMS Warehouse", fields: ["name"], limit_page_length: 100 } });
    state.warehouses = (r.message || []).map((w) => w.name);
    const mine = frappe.defaults.get_user_default("WMS Warehouse");
    $root.find(".wms-ah-wh").html(state.warehouses.map((w) => `<option>${esc(w)}</option>`).join("")).val(state.warehouses.includes(mine) ? mine : state.warehouses[0]);
    const tx = frappe.get_route()[1]; if (TX[tx]) $root.find(".wms-ah-tx").val(tx);
    open();
  });
};
