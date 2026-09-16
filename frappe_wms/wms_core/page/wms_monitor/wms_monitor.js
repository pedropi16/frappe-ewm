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
        <div class="wms-monitor-section" style="margin-top:24px;">
          <h5>${__("Handling Units")}</h5>
          <div class="wms-monitor-hu-filters form-inline" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;"></div>
          <div class="wms-monitor-hu-table"></div>
        </div>
        <div class="wms-monitor-section" style="margin-top:24px;">
          <h5>${__("Inbound Deliveries")}</h5>
          <div class="wms-monitor-ind-filters form-inline" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;"></div>
          <div class="wms-monitor-ind-table"></div>
        </div>
        <div class="wms-monitor-section" style="margin-top:24px;">
          <h5>${__("Outbound Deliveries")}</h5>
          <div class="wms-monitor-obd-filters form-inline" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;"></div>
          <div class="wms-monitor-obd-table"></div>
        </div>
        <div class="wms-monitor-section" style="margin-top:24px;">
          <h5>${__("Waves")}</h5>
          <div class="wms-monitor-wave-filters form-inline" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;"></div>
          <div class="wms-monitor-wave-table"></div>
        </div>
        <div class="wms-monitor-section" style="margin-top:24px;">
          <h5>${__("Resources & Queues")}</h5>
          <div style="display:flex;gap:24px;flex-wrap:wrap;">
            <div style="flex:1;min-width:320px;">
              <h6>${__("Resource Workload")}</h6>
              <div class="wms-monitor-resource-table"></div>
            </div>
            <div style="flex:1;min-width:320px;">
              <h6>${__("Queues")}</h6>
              <div class="wms-monitor-queue-table"></div>
            </div>
          </div>
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
    this.render_hu_filters();
    this.render_inbound_delivery_filters();
    this.render_outbound_delivery_filters();
    this.render_wave_filters();
    this.load_summary();
    this.search_ledger();
    this.search_tasks();
    this.search_handling_units();
    this.search_inbound_deliveries();
    this.search_outbound_deliveries();
    this.search_waves();
    this.load_resource_workload();
    this.load_queues();
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
      { label: __("Open Waves"), value: summary.open_waves, route: ["List", "WMS Wave", { warehouse: this.warehouse }] },
      { label: __("Active Resources"), value: summary.active_resources, route: ["List", "WMS Resource", { warehouse: this.warehouse }] },
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

  render_hu_filters() {
    const $filters = this.$body.find(".wms-monitor-hu-filters");
    $filters.html(`
      <input class="form-control input-sm wms-mon-hu-number" placeholder="${__("HU Number")}" style="width:140px;">
      <input class="form-control input-sm wms-mon-hu-status" placeholder="${__("Status")}" style="width:120px;">
      <input class="form-control input-sm wms-mon-hu-type" placeholder="${__("HU Type")}" style="width:140px;">
      <input class="form-control input-sm wms-mon-hu-bin" placeholder="${__("Current Bin")}" style="width:140px;">
      <button class="btn btn-primary btn-sm wms-mon-hu-search">${__("Search")}</button>
    `);
    $filters.find(".wms-mon-hu-search").on("click", () => this.search_handling_units());
  }

  render_inbound_delivery_filters() {
    const $filters = this.$body.find(".wms-monitor-ind-filters");
    $filters.html(`
      <input class="form-control input-sm wms-mon-ind-status" placeholder="${__("Status")}" style="width:140px;">
      <input class="form-control input-sm wms-mon-ind-supplier" placeholder="${__("Supplier")}" style="width:160px;">
      <button class="btn btn-primary btn-sm wms-mon-ind-search">${__("Search")}</button>
    `);
    $filters.find(".wms-mon-ind-search").on("click", () => this.search_inbound_deliveries());
  }

  render_outbound_delivery_filters() {
    const $filters = this.$body.find(".wms-monitor-obd-filters");
    $filters.html(`
      <input class="form-control input-sm wms-mon-obd-status" placeholder="${__("Status")}" style="width:140px;">
      <input class="form-control input-sm wms-mon-obd-customer" placeholder="${__("Customer")}" style="width:160px;">
      <button class="btn btn-primary btn-sm wms-mon-obd-search">${__("Search")}</button>
    `);
    $filters.find(".wms-mon-obd-search").on("click", () => this.search_outbound_deliveries());
  }

  render_wave_filters() {
    const $filters = this.$body.find(".wms-monitor-wave-filters");
    $filters.html(`
      <input class="form-control input-sm wms-mon-wave-status" placeholder="${__("Status")}" style="width:140px;">
      <button class="btn btn-primary btn-sm wms-mon-wave-search">${__("Search")}</button>
    `);
    $filters.find(".wms-mon-wave-search").on("click", () => this.search_waves());
  }

  async search_handling_units() {
    if (!this.warehouse) return;
    const $f = this.$body.find(".wms-monitor-hu-filters");
    const args = {
      warehouse: this.warehouse,
      hu_number: $f.find(".wms-mon-hu-number").val() || undefined,
      status: $f.find(".wms-mon-hu-status").val() || undefined,
      hu_type: $f.find(".wms-mon-hu-type").val() || undefined,
      current_bin: $f.find(".wms-mon-hu-bin").val() || undefined,
    };
    const rows = await frappe.call("frappe_wms.api.monitor.search_handling_units", args).then((r) => r.message || []);
    const $table = this.$body.find(".wms-monitor-hu-table");
    if (!rows.length) { $table.html(`<div class="text-muted">${__("No handling units found")}</div>`); return; }
    $table.html(this.render_table(rows, [
      ["hu_number", __("HU")], ["hu_type", __("Type")], ["current_bin", __("Bin")], ["parent_hu", __("Parent HU")],
      ["status", __("Status")], ["stock_status", __("Stock Status")], ["outbound_delivery", __("Outbound Delivery")],
    ], "Handling Unit"));
  }

  async search_inbound_deliveries() {
    if (!this.warehouse) return;
    const $f = this.$body.find(".wms-monitor-ind-filters");
    const args = {
      warehouse: this.warehouse,
      status: $f.find(".wms-mon-ind-status").val() || undefined,
      supplier: $f.find(".wms-mon-ind-supplier").val() || undefined,
    };
    const rows = await frappe.call("frappe_wms.api.monitor.search_inbound_deliveries", args).then((r) => r.message || []);
    const $table = this.$body.find(".wms-monitor-ind-table");
    if (!rows.length) { $table.html(`<div class="text-muted">${__("No inbound deliveries found")}</div>`); return; }
    $table.html(this.render_table(rows, [
      ["name", __("Delivery")], ["inbound_delivery_number", __("Number")], ["supplier", __("Supplier")],
      ["receiving_bin", __("Receiving Bin")], ["status", __("Status")],
    ], "Inbound Delivery"));
  }

  async search_outbound_deliveries() {
    if (!this.warehouse) return;
    const $f = this.$body.find(".wms-monitor-obd-filters");
    const args = {
      warehouse: this.warehouse,
      status: $f.find(".wms-mon-obd-status").val() || undefined,
      customer: $f.find(".wms-mon-obd-customer").val() || undefined,
    };
    const rows = await frappe.call("frappe_wms.api.monitor.search_outbound_deliveries", args).then((r) => r.message || []);
    const $table = this.$body.find(".wms-monitor-obd-table");
    if (!rows.length) { $table.html(`<div class="text-muted">${__("No outbound deliveries found")}</div>`); return; }
    $table.html(this.render_table(rows, [
      ["name", __("Delivery")], ["outbound_delivery_number", __("Number")], ["customer", __("Customer")],
      ["picking_status", __("Picking")], ["goods_issue_status", __("Goods Issue")], ["status", __("Status")],
    ], "Outbound Delivery"));
  }

  async search_waves() {
    if (!this.warehouse) return;
    const $f = this.$body.find(".wms-monitor-wave-filters");
    const args = {
      warehouse: this.warehouse,
      status: $f.find(".wms-mon-wave-status").val() || undefined,
    };
    const rows = await frappe.call("frappe_wms.api.monitor.search_waves", args).then((r) => r.message || []);
    const $table = this.$body.find(".wms-monitor-wave-table");
    if (!rows.length) { $table.html(`<div class="text-muted">${__("No waves found")}</div>`); return; }
    const head = [__("Wave"), __("Route"), __("Ship Date"), __("Strategy"), __("Status"), __("Deliveries"), __("")].map((l) => `<th>${l}</th>`).join("");
    const body = rows.map((row) => `
      <tr>
        <td><a href="/app/wms-wave/${encodeURIComponent(row.name)}">${frappe.utils.escape_html(row.name)}</a></td>
        <td>${frappe.utils.escape_html(row.route || "")}</td>
        <td>${frappe.utils.escape_html(row.ship_date || "")}</td>
        <td>${frappe.utils.escape_html(row.picking_strategy || "")}</td>
        <td>${frappe.utils.escape_html(row.status || "")}</td>
        <td>${row.delivery_count}</td>
        <td>${row.status === "Draft" ? `<button class="btn btn-xs btn-primary wms-mon-release-wave" data-wave="${frappe.utils.escape_html(row.name)}">${__("Release")}</button>` : ""}</td>
      </tr>
    `).join("");
    $table.html(`<div class="table-responsive"><table class="table table-bordered table-sm"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`);
    $table.find(".wms-mon-release-wave").on("click", (e) => {
      const wave = e.currentTarget.dataset.wave;
      frappe.confirm(__("Release wave {0}? This allocates and creates pick tasks for every delivery in it.", [wave]), () => {
        frappe.call("frappe_wms.api.outbound.release_wave", { wave_name: wave }).then(() => {
          frappe.show_alert({ message: __("Wave released"), indicator: "green" });
          this.search_waves();
        });
      });
    });
  }

  async load_resource_workload() {
    if (!this.warehouse) return;
    const rows = await frappe.call("frappe_wms.api.monitor.resource_workload", { warehouse: this.warehouse }).then((r) => r.message || []);
    const $table = this.$body.find(".wms-monitor-resource-table");
    if (!rows.length) { $table.html(`<div class="text-muted">${__("No active resources")}</div>`); return; }
    $table.html(this.render_table(rows, [
      ["resource_code", __("Resource")], ["user", __("User")], ["resource_type", __("Type")],
      ["current_queue", __("Queue")], ["current_bin", __("Bin")], ["open_tasks", __("Open Tasks")],
    ], "WMS Resource"));
  }

  async load_queues() {
    if (!this.warehouse) return;
    const rows = await frappe.call("frappe_wms.api.monitor.search_queues", { warehouse: this.warehouse }).then((r) => r.message || []);
    const $table = this.$body.find(".wms-monitor-queue-table");
    if (!rows.length) { $table.html(`<div class="text-muted">${__("No queues configured")}</div>`); return; }
    $table.html(this.render_table(rows, [
      ["queue_code", __("Queue")], ["queue_name", __("Name")], ["activity", __("Activity")],
      ["storage_type", __("Storage Type")], ["required_resource_type", __("Resource Type")], ["active", __("Active")],
    ], "Warehouse Queue"));
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
