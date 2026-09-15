frappe.pages["wms-monitor"].on_page_load = function (wrapper) {
  const page = frappe.ui.make_app_page({
    parent: wrapper,
    title: __("WMS Monitor"),
    single_column: true,
  });
  new WMSMonitor(page);
};

class WMSMonitor {
  constructor(page) {
    this.page = page;
    this.warehouse = null;
    this.$body = $(`
      <div class="wms-monitor">
        <div class="wms-monitor-filters form-inline" style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:16px;"></div>
        <div class="wms-monitor-summary" style="display:flex;flex-wrap:wrap;gap:12px;margin-bottom:20px;"></div>
        <div class="wms-monitor-section">
          <h5>${__("Stock Movements")}</h5>
          <div class="wms-monitor-ledger-filters form-inline" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;"></div>
          <div class="wms-monitor-ledger-table"></div>
        </div>
        <div class="wms-monitor-section" style="margin-top:24px;">
          <h5>${__("Warehouse Tasks")}</h5>
          <div class="wms-monitor-task-filters form-inline" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;"></div>
          <div class="wms-monitor-task-table"></div>
        </div>
      </div>
    `).appendTo(this.page.main);

    this.render_warehouse_filter();
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
      if (this.warehouse) this.load();
    });
    if (warehouses.length === 1) {
      $filters.find(".wms-mon-warehouse").val(warehouses[0].name).trigger("change");
    }
  }

  load() {
    this.render_ledger_filters();
    this.render_task_filters();
    this.load_summary();
    this.search_ledger();
    this.search_tasks();
  }

  async load_summary() {
    const summary = await frappe.call("frappe_wms.api.monitor.get_summary", { warehouse: this.warehouse }).then((r) => r.message);
    const $summary = this.$body.find(".wms-monitor-summary");
    $summary.empty();
    const cards = [
      { label: __("Exceptions"), value: summary.exceptions, route: ["List", "Warehouse Task", { warehouse: this.warehouse, status: "Exception" }] },
      { label: __("Pending Replenishment"), value: summary.pending_replenishment, route: ["List", "Warehouse Request", { warehouse: this.warehouse, request_type: "Replenish" }] },
      { label: __("Inbound In Progress"), value: summary.inbound_in_progress, route: ["List", "Inbound Delivery", { warehouse: this.warehouse }] },
      { label: __("Outbound In Progress"), value: summary.outbound_in_progress, route: ["List", "Outbound Delivery", { warehouse: this.warehouse }] },
      { label: __("Open Counts"), value: summary.open_counts, route: ["List", "WMS Physical Inventory Count", { warehouse: this.warehouse }] },
      { label: __("Open Inspections"), value: summary.open_inspections, route: ["List", "WMS Quality Inspection", { warehouse: this.warehouse }] },
    ];
    (summary.open_tasks_by_type || []).forEach((row) => {
      cards.unshift({ label: __("Open {0}", [row.task_type]), value: row.count, route: ["List", "Warehouse Task", { warehouse: this.warehouse, task_type: row.task_type }] });
    });
    cards.forEach((card) => {
      const $card = $(`
        <div class="wms-mon-card" style="border:1px solid var(--border-color);border-radius:8px;padding:10px 16px;min-width:140px;cursor:pointer;">
          <div style="font-size:22px;font-weight:700;">${card.value}</div>
          <div class="text-muted" style="font-size:12px;">${frappe.utils.escape_html(card.label)}</div>
        </div>
      `).appendTo($summary);
      $card.on("click", () => frappe.set_route(...card.route));
    });
  }

  render_ledger_filters() {
    const $filters = this.$body.find(".wms-monitor-ledger-filters");
    $filters.html(`
      <input class="form-control input-sm wms-mon-ledger-product" placeholder="${__("Product")}" style="width:140px;">
      <input class="form-control input-sm wms-mon-ledger-bin" placeholder="${__("Storage Bin")}" style="width:140px;">
      <input class="form-control input-sm wms-mon-ledger-hu" placeholder="${__("Handling Unit")}" style="width:140px;">
      <input class="form-control input-sm wms-mon-ledger-movement" placeholder="${__("Movement Type")}" style="width:120px;">
      <input type="date" class="form-control input-sm wms-mon-ledger-from" style="width:150px;">
      <input type="date" class="form-control input-sm wms-mon-ledger-to" style="width:150px;">
      <button class="btn btn-primary btn-sm wms-mon-ledger-search">${__("Search")}</button>
    `);
    $filters.find(".wms-mon-ledger-search").on("click", () => this.search_ledger());
  }

  render_task_filters() {
    const $filters = this.$body.find(".wms-monitor-task-filters");
    $filters.html(`
      <input class="form-control input-sm wms-mon-task-product" placeholder="${__("Product")}" style="width:140px;">
      <input class="form-control input-sm wms-mon-task-type" placeholder="${__("Task Type")}" style="width:120px;">
      <input class="form-control input-sm wms-mon-task-status" placeholder="${__("Status")}" style="width:120px;">
      <input class="form-control input-sm wms-mon-task-resource" placeholder="${__("Resource")}" style="width:140px;">
      <button class="btn btn-primary btn-sm wms-mon-task-search">${__("Search")}</button>
    `);
    $filters.find(".wms-mon-task-search").on("click", () => this.search_tasks());
  }

  async search_ledger() {
    if (!this.warehouse) return;
    const $f = this.$body.find(".wms-monitor-ledger-filters");
    const args = {
      warehouse: this.warehouse,
      product: $f.find(".wms-mon-ledger-product").val() || undefined,
      storage_bin: $f.find(".wms-mon-ledger-bin").val() || undefined,
      handling_unit: $f.find(".wms-mon-ledger-hu").val() || undefined,
      movement_type: $f.find(".wms-mon-ledger-movement").val() || undefined,
      from_date: $f.find(".wms-mon-ledger-from").val() || undefined,
      to_date: $f.find(".wms-mon-ledger-to").val() || undefined,
    };
    const rows = await frappe.call("frappe_wms.api.monitor.search_ledger", args).then((r) => r.message || []);
    const $table = this.$body.find(".wms-monitor-ledger-table");
    if (!rows.length) { $table.html(`<div class="text-muted">${__("No movements found")}</div>`); return; }
    $table.html(this.render_table(rows, [
      ["posting_datetime", __("Posted")], ["movement_type", __("Movement")], ["product", __("Product")],
      ["quantity", __("Qty")], ["storage_bin", __("Bin")], ["handling_unit", __("HU")],
      ["stock_type", __("Stock Type")], ["reference_doctype", __("Reference Type")], ["reference_name", __("Reference")],
    ], "WMS Stock Ledger Entry"));
  }

  async search_tasks() {
    if (!this.warehouse) return;
    const $f = this.$body.find(".wms-monitor-task-filters");
    const args = {
      warehouse: this.warehouse,
      product: $f.find(".wms-mon-task-product").val() || undefined,
      task_type: $f.find(".wms-mon-task-type").val() || undefined,
      status: $f.find(".wms-mon-task-status").val() || undefined,
      assigned_resource: $f.find(".wms-mon-task-resource").val() || undefined,
    };
    const rows = await frappe.call("frappe_wms.api.monitor.search_tasks", args).then((r) => r.message || []);
    const $table = this.$body.find(".wms-monitor-task-table");
    if (!rows.length) { $table.html(`<div class="text-muted">${__("No tasks found")}</div>`); return; }
    $table.html(this.render_table(rows, [
      ["name", __("Task")], ["task_type", __("Type")], ["status", __("Status")], ["product", __("Product")],
      ["planned_quantity", __("Planned")], ["confirmed_quantity", __("Confirmed")],
      ["source_bin", __("Source")], ["destination_bin", __("Destination")],
      ["priority", __("Priority")], ["assigned_resource", __("Resource")],
    ], "Warehouse Task"));
  }

  render_table(rows, columns, doctype) {
    const head = columns.map(([, label]) => `<th>${frappe.utils.escape_html(label)}</th>`).join("");
    const body = rows.map((row) => {
      const cells = columns.map(([field]) => {
        let value = row[field];
        if (field === "name" || field === "reference_name") {
          const target_doctype = field === "reference_name" ? row.reference_doctype : doctype;
          if (value && target_doctype) return `<td><a href="/app/${frappe.router.slug(target_doctype)}/${encodeURIComponent(value)}">${frappe.utils.escape_html(String(value))}</a></td>`;
        }
        return `<td>${value === null || value === undefined ? "" : frappe.utils.escape_html(String(value))}</td>`;
      }).join("");
      return `<tr>${cells}</tr>`;
    }).join("");
    return `<div class="table-responsive"><table class="table table-bordered table-sm"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }
}
