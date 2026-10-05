// Production Staging (SAP EWM staging app): stage the materials of production orders (Production Material
// Requests) into a Production Supply Area. Pick the PSA, choose Single Order (stock reserved to one PMR item)
// or Cross Order (one movement for the same product across orders, pooled), choose the storage bin each line
// is taken from, and create the tasks. Server side: services/production_supply.py.
frappe.pages["wms-production-staging"].on_page_load = function (wrapper) {
  const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Production Staging"), single_column: true });
  const esc = frappe.utils.escape_html;
  const call = (method, args) => frappe.call({ method: `frappe_wms.api.production_supply.${method}`, args }).then((r) => r.message);
  const state = { rows: [], status: [] };

  const psaField = page.add_field({ fieldname: "psa", label: __("Production Supply Area"), fieldtype: "Link", options: "Production Supply Area", change: () => load() });
  const methodField = page.add_field({ fieldname: "method", label: __("Staging Method"), fieldtype: "Select", options: "Single Order\nCross Order", default: "Single Order", change: () => render() });
  page.set_primary_action(__("Create Staging Tasks"), () => create(), "add");
  page.add_inner_button(__("Auto-stage"), () => autoStage());
  page.add_inner_button(__("Refresh"), () => load());
  const $body = $(`<div class="wms-staging"></div>`).appendTo(page.main);
  $body.on("change", "select.wms-src", function () { setQty($(this).closest("tr")); });

  const psa = () => psaField.get_value();
  const srcLabel = (p) => `${p.storage_bin}${p.handling_unit ? " · " + p.handling_unit : ""}${p.batch_no ? " · " + p.batch_no : ""} (${flt(p.available_quantity)})`;
  const srcOptions = (proposals) => proposals.map((p, i) => `<option value="${i}">${esc(srcLabel(p))}</option>`).join("") || `<option value="">${__("No stock")}</option>`;

  function setQty($tr) {
    const g = state.groups[$tr.data("g")];
    const p = g.proposals[$tr.find("select.wms-src").val()];
    $tr.find("input.wms-qty").val(p ? Math.min(g.open_quantity, flt(p.available_quantity)) : 0);
  }

  // Single Order: one row per PMR item. Cross Order: one row per product, serving all its open items.
  function groups() {
    if (methodField.get_value() === "Single Order") return state.rows.map((r) => ({ ...r, items: [r.pmr_item], label: [r.pmr, r.work_order, r.planned_date || ""] }));
    const by = {};
    for (const r of state.rows) {
      const g = (by[r.product] = by[r.product] || { product: r.product, items: [], open_quantity: 0, required_quantity: 0, tasked_quantity: 0, proposals: r.proposals, label: [] });
      g.items.push(r.pmr_item); g.open_quantity += r.open_quantity; g.required_quantity += flt(r.required_quantity); g.tasked_quantity += flt(r.tasked_quantity); g.label.push(r.pmr);
    }
    return Object.values(by).map((g) => ({ ...g, label: [[...new Set(g.label)].join(", "), "", ""] }));
  }

  function render() {
    if (!psa()) { $body.html(`<p class="text-muted" style="margin:20px;">${__("Select a Production Supply Area.")}</p>`); return; }
    state.groups = groups();
    const single = methodField.get_value() === "Single Order";
    const open = state.groups.map((g, i) => `<tr data-g="${i}"><td><input type="checkbox" class="wms-pick"></td>
      <td>${esc(g.label[0])}</td><td>${esc(g.label[1])}</td><td>${esc(g.label[2])}</td><td>${esc(g.product)}</td><td>${esc(single ? g.operation || "" : "")}</td>
      <td class="text-right">${flt(g.required_quantity)}</td><td class="text-right">${flt(g.tasked_quantity)}</td><td class="text-right"><b>${flt(g.open_quantity)}</b></td>
      <td><select class="form-control input-xs wms-src">${srcOptions(g.proposals)}</select></td>
      <td><input type="number" step="any" class="form-control input-xs wms-qty" style="width:90px"></td></tr>`).join("");
    const status = state.status.map((r) => `<tr><td><a href="/app/production-material-request/${encodeURIComponent(r.pmr)}">${esc(r.pmr)}</a></td><td>${esc(r.work_order)}</td><td>${esc(__(r.status))}</td>
      <td>${esc(r.product)}</td><td class="text-right">${flt(r.required_quantity)}</td><td class="text-right">${flt(r.tasked_quantity)}</td><td class="text-right">${flt(r.staged_quantity)}</td><td class="text-right">${flt(r.consumed_quantity)}</td></tr>`).join("");
    $body.html(`<h5 style="margin-top:14px;">${__("Open for staging")}</h5>
      <div style="overflow-x:auto;"><table class="table table-bordered table-sm"><thead><tr><th></th><th>${single ? __("PMR") : __("PMRs")}</th><th>${__("Work Order")}</th><th>${__("Date")}</th><th>${__("Product")}</th><th>${__("Operation")}</th>
        <th>${__("Required")}</th><th>${__("Tasked")}</th><th>${__("Open")}</th><th>${__("Take from")}</th><th>${__("Quantity")}</th></tr></thead>
        <tbody>${open || `<tr><td colspan="11" class="text-muted">${__("Nothing open for staging.")}</td></tr>`}</tbody></table></div>
      <h5 style="margin-top:22px;">${__("Production Material Requests")}</h5>
      <div style="overflow-x:auto;"><table class="table table-bordered table-sm"><thead><tr><th>${__("PMR")}</th><th>${__("Work Order")}</th><th>${__("Status")}</th><th>${__("Product")}</th>
        <th>${__("Required")}</th><th>${__("Tasked")}</th><th>${__("Staged (reserved)")}</th><th>${__("Consumed")}</th></tr></thead><tbody>${status}</tbody></table></div>`);
    $body.find("tbody tr[data-g]").each(function () { setQty($(this)); });
  }

  async function load() {
    if (!psa()) { render(); return; }
    [state.rows, state.status] = await Promise.all([call("staging_overview", { psa: psa() }), call("pmr_overview", { psa: psa() })]);
    render();
  }

  async function create() {
    const single = methodField.get_value() === "Single Order";
    const lines = [];
    $body.find("tbody tr[data-g]").each(function () {
      const $tr = $(this);
      if (!$tr.find("input.wms-pick").is(":checked")) return;
      const g = state.groups[$tr.data("g")], p = g.proposals[$tr.find("select.wms-src").val()], quantity = flt($tr.find("input.wms-qty").val());
      if (!p || !(quantity > 0)) return;
      const line = { source_bin: p.storage_bin, source_hu: p.handling_unit, batch_no: p.batch_no, serial_no: p.serial_no, quantity };
      lines.push(single ? { ...line, pmr_item: g.items[0] } : { ...line, product: g.product, pmr_items: g.items });
    });
    if (!lines.length) { frappe.show_alert({ message: __("Select the lines to stage."), indicator: "orange" }); return; }
    const out = await call("stage_items", { psa: psa(), method: methodField.get_value(), lines: JSON.stringify(lines) });
    frappe.show_alert({ message: __("{0} staging task(s) created", [out.length]), indicator: "green" });
    load();
  }

  async function autoStage() {
    if (!psa()) return;
    const out = await call("auto_stage", { psa: psa() });
    frappe.show_alert({ message: __("{0} staging task(s) created", [out.length]), indicator: "green" });
    load();
  }

  render();
};
