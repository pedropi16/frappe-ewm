frappe.pages["wms-monitor"].on_page_load = function (wrapper) {
  const page = frappe.ui.make_app_page({
    parent: wrapper,
    title: __("WMS Monitor"),
    single_column: true,
  });
  new WMSMonitor(page);
};

// SAP EWM-style Warehouse Management Monitor: one warehouse selector, a node tree of
// independent views down the left (Overview / Inbound / Outbound / Stock Overview /
// Warehouse Tasks / Handling Units / Stock Movements / Resources & Queues) instead of one
// long page you scroll through. Each view loads its own data only when selected.
const VIEWS = [
  { key: "overview", label: __("Overview") },
  { key: "inbound", label: __("Inbound Monitor") },
  { key: "outbound", label: __("Outbound Monitor") },
  { key: "stock", label: __("Stock Overview") },
  { key: "tasks", label: __("Warehouse Tasks") },
  { key: "hu", label: __("Handling Units") },
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

class WMSMonitor {
  constructor(page) {
    this.page = page;
    this.warehouse = null;
    this.view = "overview";

    this.$body = $(`
      <div class="wms-monitor">
        <div class="wms-monitor-filters form-inline" style="margin-bottom:16px;"></div>
        <div class="wms-monitor-shell" style="display:flex; gap:20px; align-items:flex-start;">
          <div class="wms-monitor-nav" style="flex:0 0 190px;">
            <div class="list-group wms-mon-nav-list"></div>
          </div>
          <div class="wms-monitor-content" style="flex:1; min-width:0;">
            ${VIEWS.map((v) => `
              <div class="wms-mon-view" data-view="${v.key}" style="display:none;">
                <h4>${frappe.utils.escape_html(v.label)}</h4>
                <div class="wms-mon-view-body" data-view-body="${v.key}"></div>
              </div>
            `).join("")}
          </div>
        </div>
      </div>
    `).appendTo(this.page.main);

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
      <div>
        <label>${__("Warehouse")}</label><br>
        <select class="form-control wms-mon-warehouse"><option value="">${__("Select a warehouse")}</option>${options}</select>
      </div>
    `);
    $filters.find(".wms-mon-warehouse").on("change", (e) => {
      this.warehouse = e.target.value || null;
      if (this.warehouse) this.load_view(this.view);
    });
    this.show_view(this.view);
    if (warehouses.length === 1) {
      $filters.find(".wms-mon-warehouse").val(warehouses[0].name).trigger("change");
    }
  }

  load_view(view) {
    const loaders = {
      overview: () => this.load_overview(),
      inbound: () => this.load_inbound(),
      outbound: () => this.load_outbound(),
      stock: () => this.load_stock_overview(),
      tasks: () => this.load_tasks(),
      hu: () => this.load_handling_units(),
      movements: () => this.load_movements(),
      resources: () => this.load_resources(),
      differences: () => this.load_differences(),
      kpis: () => this.load_kpis(),
      slotting: () => this.load_slotting(),
      bin_assignment: () => this.load_bin_assignment(),
      kitting: () => this.load_kitting(),
      billing: () => this.load_billing(),
      alerts: () => this.load_alerts(),
    };
    (loaders[view] || (() => {}))();
  }

  body_for(view) { return this.$body.find(`.wms-mon-view-body[data-view-body="${view}"]`); }

  render_cards($container, cards) {
    $container.empty();
    cards.forEach((card) => {
      const $card = $(`
        <div class="wms-mon-card" style="border:1px solid var(--border-color);border-radius:8px;padding:10px 16px;min-width:140px;cursor:pointer;display:inline-block;margin:0 12px 12px 0;">
          <div style="font-size:22px;font-weight:700;">${card.value}</div>
          <div class="text-muted" style="font-size:12px;">${frappe.utils.escape_html(card.label)}</div>
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
    if (!$wrap.find(".wms-mon-ind-filters").length) {
      $wrap.html(`
        <div class="wms-mon-ind-filters form-inline" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;">
          <input class="form-control input-sm wms-mon-ind-status" placeholder="${__("Status")}" style="width:140px;">
          <input class="form-control input-sm wms-mon-ind-supplier" placeholder="${__("Supplier")}" style="width:160px;">
          <button class="btn btn-primary btn-sm wms-mon-ind-search">${__("Search")}</button>
        </div>
        <div class="wms-mon-ind-table"></div>
      `);
      $wrap.find(".wms-mon-ind-search").on("click", () => this.search_inbound_deliveries());
    }
    this.search_inbound_deliveries();
  }

  async search_inbound_deliveries() {
    if (!this.warehouse) return;
    const $wrap = this.body_for("inbound");
    const args = {
      warehouse: this.warehouse,
      status: $wrap.find(".wms-mon-ind-status").val() || undefined,
      supplier: $wrap.find(".wms-mon-ind-supplier").val() || undefined,
    };
    const rows = await frappe.call("frappe_wms.api.monitor.search_inbound_deliveries", args).then((r) => r.message || []);
    const $table = $wrap.find(".wms-mon-ind-table");
    if (!rows.length) { $table.html(`<div class="text-muted">${__("No inbound deliveries found")}</div>`); return; }
    $table.html(this.render_table(rows, [
      ["name", __("Delivery")], ["inbound_delivery_number", __("Number")], ["supplier", __("Supplier")],
      ["receiving_bin", __("Receiving Bin")], ["status", __("Status")],
    ], "Inbound Delivery"));
  }

  // ---------- Outbound Monitor ----------
  async load_outbound() {
    const $wrap = this.body_for("outbound");
    if (!$wrap.find(".wms-mon-obd-filters").length) {
      $wrap.html(`
        <div class="wms-mon-obd-filters form-inline" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;">
          <input class="form-control input-sm wms-mon-obd-status" placeholder="${__("Status")}" style="width:140px;">
          <input class="form-control input-sm wms-mon-obd-customer" placeholder="${__("Customer")}" style="width:160px;">
          <button class="btn btn-primary btn-sm wms-mon-obd-search">${__("Search")}</button>
        </div>
        <div class="wms-mon-obd-table" style="margin-bottom:12px;"></div>
        <div class="wms-mon-obd-detail" style="margin-bottom:24px;"></div>
        <h5>${__("Waves")}</h5>
        <div class="wms-mon-wave-filters form-inline" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;">
          <input class="form-control input-sm wms-mon-wave-status" placeholder="${__("Status")}" style="width:140px;">
          <button class="btn btn-primary btn-sm wms-mon-wave-search">${__("Search")}</button>
        </div>
        <div class="wms-mon-wave-table"></div>
      `);
      $wrap.find(".wms-mon-obd-search").on("click", () => this.search_outbound_deliveries());
      $wrap.find(".wms-mon-wave-search").on("click", () => this.search_waves());
    }
    this.search_outbound_deliveries();
    this.search_waves();
  }

  async search_outbound_deliveries() {
    if (!this.warehouse) return;
    const $wrap = this.body_for("outbound");
    const args = {
      warehouse: this.warehouse,
      status: $wrap.find(".wms-mon-obd-status").val() || undefined,
      customer: $wrap.find(".wms-mon-obd-customer").val() || undefined,
    };
    const rows = await frappe.call("frappe_wms.api.monitor.search_outbound_deliveries", args).then((r) => r.message || []);
    const $table = $wrap.find(".wms-mon-obd-table");
    if (!rows.length) { $table.html(`<div class="text-muted">${__("No outbound deliveries found")}</div>`); return; }
    const head = [__("Delivery"), __("Number"), __("Customer"), __("Picking"), __("Goods Issue"), __("Status")].map((l) => `<th>${l}</th>`).join("");
    const body = rows.map((row) => `
      <tr class="wms-mon-obd-row" data-delivery="${frappe.utils.escape_html(row.name)}" style="cursor:pointer;">
        <td>${frappe.utils.escape_html(row.name)}</td>
        <td>${frappe.utils.escape_html(row.outbound_delivery_number || "")}</td>
        <td>${frappe.utils.escape_html(row.customer || "")}</td>
        <td>${frappe.utils.escape_html(row.picking_status || "")}</td>
        <td>${frappe.utils.escape_html(row.goods_issue_status || "")}</td>
        <td>${frappe.utils.escape_html(row.status || "")}</td>
      </tr>
    `).join("");
    $table.html(`<div class="table-responsive"><table class="table table-bordered table-sm"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`);
    $table.find(".wms-mon-obd-row").on("click", (e) => this.load_delivery_detail(e.currentTarget.dataset.delivery));
  }

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

    $wrap.append(`<h6>${__("Pick Tasks")}</h6>`);
    if (!status.tasks.length) {
      $wrap.append(`<div class="text-muted">${__("None yet")}</div>`);
    } else {
      $wrap.append(this.render_table(status.tasks, [
        ["name", __("Task")], ["task_type", __("Type")], ["status", __("Status")],
        ["planned_quantity", __("Planned")], ["confirmed_quantity", __("Confirmed")],
        ["source_bin", __("Source")], ["destination_bin", __("Destination")], ["assigned_resource", __("Resource")],
      ], "Warehouse Task"));
    }
    if (status.warehouse_orders.length) {
      $wrap.append(`<div><b>${__("Warehouse Orders")}:</b> ${status.warehouse_orders.map((wo) =>
        `<a href="/app/warehouse-order/${encodeURIComponent(wo)}">${frappe.utils.escape_html(wo)}</a>`).join(", ")}</div>`);
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
      const gi_head = [__("Goods Issue"), __("Status"), __("Posted"), __("Reversed")].map((l) => `<th>${l}</th>`).join("");
      const gi_body = status.goods_issues.map((g) => `
        <tr>
          <td><a href="/app/goods-issue/${encodeURIComponent(g.name)}">${frappe.utils.escape_html(g.name)}</a></td>
          <td>${frappe.utils.escape_html(g.status || "")}</td>
          <td>${frappe.utils.escape_html(g.posting_datetime || "")}</td>
          <td>${g.reversed ? __("Yes") : __("No")}</td>
        </tr>
      `).join("");
      $wrap.append(`<div class="table-responsive"><table class="table table-bordered table-sm"><thead><tr>${gi_head}</tr></thead><tbody>${gi_body}</tbody></table></div>`);
    }

    $detail.empty().append($wrap);
  }

  async search_waves() {
    if (!this.warehouse) return;
    const $wrap = this.body_for("outbound");
    const args = { warehouse: this.warehouse, status: $wrap.find(".wms-mon-wave-status").val() || undefined };
    const rows = await frappe.call("frappe_wms.api.monitor.search_waves", args).then((r) => r.message || []);
    const $table = $wrap.find(".wms-mon-wave-table");
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

  // ---------- Stock Overview ----------
  async load_stock_overview() {
    const $wrap = this.body_for("stock");
    if (!$wrap.find(".wms-mon-stock-filters").length) {
      $wrap.html(`
        <div class="wms-mon-stock-summary" style="margin-bottom:16px;"></div>
        <div class="wms-mon-stock-filters form-inline" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;">
          <input class="form-control input-sm wms-mon-stock-product" placeholder="${__("Product")}" style="width:140px;">
          <input class="form-control input-sm wms-mon-stock-bin" placeholder="${__("Storage Bin")}" style="width:140px;">
          <input class="form-control input-sm wms-mon-stock-storage-type" placeholder="${__("Storage Type")}" style="width:140px;">
          <input class="form-control input-sm wms-mon-stock-stock-type" placeholder="${__("Stock Type")}" style="width:120px;">
          <input class="form-control input-sm wms-mon-stock-hu" placeholder="${__("Handling Unit")}" style="width:140px;">
          <button class="btn btn-primary btn-sm wms-mon-stock-search">${__("Search")}</button>
        </div>
        <div class="wms-mon-stock-table"></div>
      `);
      $wrap.find(".wms-mon-stock-search").on("click", () => this.search_stock_overview());
    }
    const summary = await frappe.call("frappe_wms.api.monitor.stock_overview_summary", { warehouse: this.warehouse }).then((r) => r.message || []);
    this.render_cards($wrap.find(".wms-mon-stock-summary"), summary.map((row) => ({
      label: __("{0} ({1} rows)", [row.stock_type || __("(no stock type)"), row.balance_rows]),
      value: `${flt(row.quantity)} / ${flt(row.available_quantity)} ${__("avail")}`,
    })));
    this.search_stock_overview();
  }

  async search_stock_overview() {
    if (!this.warehouse) return;
    const $wrap = this.body_for("stock");
    const args = {
      warehouse: this.warehouse,
      product: $wrap.find(".wms-mon-stock-product").val() || undefined,
      storage_bin: $wrap.find(".wms-mon-stock-bin").val() || undefined,
      storage_type: $wrap.find(".wms-mon-stock-storage-type").val() || undefined,
      stock_type: $wrap.find(".wms-mon-stock-stock-type").val() || undefined,
      handling_unit: $wrap.find(".wms-mon-stock-hu").val() || undefined,
    };
    const rows = await frappe.call("frappe_wms.api.monitor.stock_overview", args).then((r) => r.message || []);
    const $table = $wrap.find(".wms-mon-stock-table");
    if (!rows.length) { $table.html(`<div class="text-muted">${__("No stock found")}</div>`); return; }
    $table.html(this.render_table(rows, [
      ["product", __("Product")], ["storage_bin", __("Bin")], ["handling_unit", __("HU")],
      ["stock_type", __("Stock Type")], ["quantity", __("Quantity")], ["allocated_quantity", __("Allocated")],
      ["available_quantity", __("Available")], ["stock_uom", __("UOM")],
    ]));
  }

  // ---------- Warehouse Tasks ----------
  async load_tasks() {
    const $wrap = this.body_for("tasks");
    if (!$wrap.find(".wms-mon-task-filters").length) {
      $wrap.html(`
        <div class="wms-mon-task-filters form-inline" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;">
          <input class="form-control input-sm wms-mon-task-product" placeholder="${__("Product")}" style="width:140px;">
          <input class="form-control input-sm wms-mon-task-type" placeholder="${__("Task Type")}" style="width:120px;">
          <input class="form-control input-sm wms-mon-task-status" placeholder="${__("Status")}" style="width:120px;">
          <input class="form-control input-sm wms-mon-task-resource" placeholder="${__("Resource")}" style="width:140px;">
          <button class="btn btn-primary btn-sm wms-mon-task-search">${__("Search")}</button>
        </div>
        <div class="wms-mon-task-table"></div>
      `);
      $wrap.find(".wms-mon-task-search").on("click", () => this.search_tasks());
    }
    this.search_tasks();
  }

  async search_tasks() {
    if (!this.warehouse) return;
    const $wrap = this.body_for("tasks");
    const args = {
      warehouse: this.warehouse,
      product: $wrap.find(".wms-mon-task-product").val() || undefined,
      task_type: $wrap.find(".wms-mon-task-type").val() || undefined,
      status: $wrap.find(".wms-mon-task-status").val() || undefined,
      assigned_resource: $wrap.find(".wms-mon-task-resource").val() || undefined,
    };
    const rows = await frappe.call("frappe_wms.api.monitor.search_tasks", args).then((r) => r.message || []);
    const $table = $wrap.find(".wms-mon-task-table");
    if (!rows.length) { $table.html(`<div class="text-muted">${__("No tasks found")}</div>`); return; }
    $table.html(this.render_table(rows, [
      ["name", __("Task")], ["task_type", __("Type")], ["status", __("Status")], ["product", __("Product")],
      ["planned_quantity", __("Planned")], ["confirmed_quantity", __("Confirmed")],
      ["source_bin", __("Source")], ["destination_bin", __("Destination")],
      ["priority", __("Priority")], ["assigned_resource", __("Resource")],
    ], "Warehouse Task"));
  }

  // ---------- Handling Units ----------
  async load_handling_units() {
    const $wrap = this.body_for("hu");
    if (!$wrap.find(".wms-mon-hu-filters").length) {
      $wrap.html(`
        <div class="wms-mon-hu-filters form-inline" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;">
          <input class="form-control input-sm wms-mon-hu-number" placeholder="${__("HU Number")}" style="width:140px;">
          <input class="form-control input-sm wms-mon-hu-status" placeholder="${__("Status")}" style="width:120px;">
          <input class="form-control input-sm wms-mon-hu-type" placeholder="${__("HU Type")}" style="width:140px;">
          <input class="form-control input-sm wms-mon-hu-bin" placeholder="${__("Current Bin")}" style="width:140px;">
          <button class="btn btn-primary btn-sm wms-mon-hu-search">${__("Search")}</button>
        </div>
        <div class="wms-mon-hu-table"></div>
      `);
      $wrap.find(".wms-mon-hu-search").on("click", () => this.search_handling_units());
    }
    this.search_handling_units();
  }

  async search_handling_units() {
    if (!this.warehouse) return;
    const $wrap = this.body_for("hu");
    const args = {
      warehouse: this.warehouse,
      hu_number: $wrap.find(".wms-mon-hu-number").val() || undefined,
      status: $wrap.find(".wms-mon-hu-status").val() || undefined,
      hu_type: $wrap.find(".wms-mon-hu-type").val() || undefined,
      current_bin: $wrap.find(".wms-mon-hu-bin").val() || undefined,
    };
    const rows = await frappe.call("frappe_wms.api.monitor.search_handling_units", args).then((r) => r.message || []);
    const $table = $wrap.find(".wms-mon-hu-table");
    if (!rows.length) { $table.html(`<div class="text-muted">${__("No handling units found")}</div>`); return; }
    $table.html(this.render_table(rows, [
      ["hu_number", __("HU")], ["hu_type", __("Type")], ["current_bin", __("Bin")], ["parent_hu", __("Parent HU")],
      ["status", __("Status")], ["stock_status", __("Stock Status")], ["outbound_delivery", __("Outbound Delivery")],
    ], "Handling Unit"));
  }

  // ---------- Stock Movements ----------
  async load_movements() {
    const $wrap = this.body_for("movements");
    if (!$wrap.find(".wms-mon-ledger-filters").length) {
      $wrap.html(`
        <div class="wms-mon-ledger-filters form-inline" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;">
          <input class="form-control input-sm wms-mon-ledger-product" placeholder="${__("Product")}" style="width:140px;">
          <input class="form-control input-sm wms-mon-ledger-bin" placeholder="${__("Storage Bin")}" style="width:140px;">
          <input class="form-control input-sm wms-mon-ledger-hu" placeholder="${__("Handling Unit")}" style="width:140px;">
          <input class="form-control input-sm wms-mon-ledger-movement" placeholder="${__("Movement Type")}" style="width:120px;">
          <input type="date" class="form-control input-sm wms-mon-ledger-from" style="width:150px;">
          <input type="date" class="form-control input-sm wms-mon-ledger-to" style="width:150px;">
          <button class="btn btn-primary btn-sm wms-mon-ledger-search">${__("Search")}</button>
        </div>
        <div class="wms-mon-ledger-table"></div>
      `);
      $wrap.find(".wms-mon-ledger-search").on("click", () => this.search_ledger());
    }
    this.search_ledger();
  }

  async search_ledger() {
    if (!this.warehouse) return;
    const $wrap = this.body_for("movements");
    const args = {
      warehouse: this.warehouse,
      product: $wrap.find(".wms-mon-ledger-product").val() || undefined,
      storage_bin: $wrap.find(".wms-mon-ledger-bin").val() || undefined,
      handling_unit: $wrap.find(".wms-mon-ledger-hu").val() || undefined,
      movement_type: $wrap.find(".wms-mon-ledger-movement").val() || undefined,
      from_date: $wrap.find(".wms-mon-ledger-from").val() || undefined,
      to_date: $wrap.find(".wms-mon-ledger-to").val() || undefined,
    };
    const rows = await frappe.call("frappe_wms.api.monitor.search_ledger", args).then((r) => r.message || []);
    const $table = $wrap.find(".wms-mon-ledger-table");
    if (!rows.length) { $table.html(`<div class="text-muted">${__("No movements found")}</div>`); return; }
    $table.html(this.render_table(rows, [
      ["posting_datetime", __("Posted")], ["movement_type", __("Movement")], ["product", __("Product")],
      ["quantity", __("Qty")], ["storage_bin", __("Bin")], ["handling_unit", __("HU")],
      ["stock_type", __("Stock Type")], ["reference_doctype", __("Reference Type")], ["reference_name", __("Reference")],
    ], "WMS Stock Ledger Entry"));
  }

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
    $table.html(this.render_table(rows, [
      ["resource_code", __("Resource")], ["resource_type", __("Type")], ["resource_group", __("Resource Group")], ["user", __("User")],
      ["current_queue", __("Queue")], ["current_bin", __("Bin")], ["open_tasks", __("Open Tasks")],
    ], "WMS Resource"));
  }

  async load_queues() {
    if (!this.warehouse) return;
    const rows = await frappe.call("frappe_wms.api.monitor.search_queues", { warehouse: this.warehouse }).then((r) => r.message || []);
    const $table = this.body_for("resources").find(".wms-mon-queue-table");
    if (!rows.length) { $table.html(`<div class="text-muted">${__("No queues configured")}</div>`); return; }
    $table.html(this.render_table(rows, [
      ["queue_code", __("Queue")], ["queue_name", __("Name")], ["activity", __("Activity")],
      ["storage_type", __("Storage Type")], ["resource_group", __("Resource Group")], ["active", __("Active")],
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
    $table.html(this.render_table(rows, [
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
    $resources.html(this.render_table(rows, [
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
    this._slotting_recommendations = recommendations;
    const $table = $wrap.find(".wms-mon-slot-table");
    if (!recommendations.length) { $table.html(`<div class="text-muted">${__("No rearrangement recommendations")}</div>`); return; }
    const body = recommendations.map((rec, idx) => `
      <tr>
        <td><input type="checkbox" class="wms-mon-slot-check" value="${idx}"></td>
        <td>${frappe.utils.escape_html(rec.product || "")}</td>
        <td>${frappe.utils.escape_html(rec.current_bin || "")}</td>
        <td>${frappe.utils.escape_html(rec.current_storage_type || "")}</td>
        <td>${frappe.utils.escape_html(rec.preferred_storage_type || "")}</td>
        <td>${rec.quantity}</td>
        <td>${rec.recent_picks}</td>
      </tr>
    `).join("");
    $table.html(`
      <div class="table-responsive">
        <table class="table table-bordered table-sm">
          <thead><tr><th></th><th>${__("Product")}</th><th>${__("Current Bin")}</th><th>${__("Current Storage Type")}</th>
            <th>${__("Preferred Storage Type")}</th><th>${__("Quantity")}</th><th>${__("Recent Picks")}</th></tr></thead>
          <tbody>${body}</tbody>
        </table>
      </div>
      <button class="btn btn-primary btn-sm wms-mon-slot-generate">${__("Generate Rearrangement Tasks")}</button>
    `);
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
    const head = [__(""), __("Bin"), __("Storage Type"), __("Section"), __("Activity Area"), __("Aisle"), __("Rack")].map((l) => `<th>${l}</th>`).join("");
    const body = rows.map((row) => `
      <tr>
        <td><input type="checkbox" class="wms-mon-bin-check" value="${frappe.utils.escape_html(row.name)}"></td>
        <td><a href="/app/storage-bin/${encodeURIComponent(row.name)}">${frappe.utils.escape_html(row.bin_code)}</a></td>
        <td>${frappe.utils.escape_html(row.storage_type || "")}</td>
        <td>${frappe.utils.escape_html(row.storage_section || "")}</td>
        <td>${frappe.utils.escape_html(row.activity_area || "")}</td>
        <td>${frappe.utils.escape_html(row.aisle || "")}</td>
        <td>${frappe.utils.escape_html(row.rack || "")}</td>
      </tr>
    `).join("");
    $table.html(`
      <div class="table-responsive">
        <table class="table table-bordered table-sm"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>
      </div>
      <div class="form-inline" style="display:flex;gap:8px;align-items:center;">
        <input class="form-control input-sm wms-mon-bin-assign-value" placeholder="${__("Activity Area (blank to clear)")}" style="width:220px;">
        <button class="btn btn-primary btn-sm wms-mon-bin-assign">${__("Assign Activity Area to Selected")}</button>
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
    const head = [__("Order"), __("Kit Item"), __("Direction"), __("Quantity"), __("Work Center Bin"), __("Status"), __("")].map((l) => `<th>${l}</th>`).join("");
    const body = filtered.map((row) => `
      <tr>
        <td><a href="/app/kitting-order/${encodeURIComponent(row.name)}">${frappe.utils.escape_html(row.name)}</a></td>
        <td>${frappe.utils.escape_html(row.kit_item || "")}</td>
        <td>${frappe.utils.escape_html(row.direction || "")}</td>
        <td>${row.quantity}</td>
        <td>${frappe.utils.escape_html(row.work_center_bin || "")}</td>
        <td>${frappe.utils.escape_html(row.status || "")}</td>
        <td><button class="btn btn-xs btn-primary wms-mon-kit-complete" data-order="${frappe.utils.escape_html(row.name)}">${__("Complete")}</button></td>
      </tr>
    `).join("");
    $table.html(`<div class="table-responsive"><table class="table table-bordered table-sm"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`);
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
    $table.html(this.render_table(lines, [
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
        <h6>${__("Counts Awaiting Approval")}</h6>
        <div class="wms-mon-alert-approval"></div>
      </div>
      <div style="margin-bottom:24px;">
        <h6>${__("Aged Exceptions")}</h6>
        <div class="wms-mon-alert-exceptions"></div>
      </div>
      <div>
        <h6>${__("Stalled Warehouse Orders")}</h6>
        <div class="wms-mon-alert-wos"></div>
      </div>
    `);
    this.render_pending_approval_alerts(alerts.pending_approval_counts || []);
    this.render_alert_table($wrap.find(".wms-mon-alert-exceptions"), alerts.aged_exceptions || [],
      [["name", __("Task")], ["task_type", __("Type")], ["product", __("Product")], ["exception_code", __("Exception")], ["blocking_reason", __("Reason")], ["modified", __("Since")]],
      "Warehouse Task", __("No aged exceptions"));
    this.render_alert_table($wrap.find(".wms-mon-alert-wos"), alerts.stalled_warehouse_orders || [],
      [["name", __("Warehouse Order")], ["activity", __("Activity")], ["queue", __("Queue")], ["priority", __("Priority")], ["task_count", __("Tasks")], ["creation", __("Created")]],
      "Warehouse Order", __("No stalled Warehouse Orders"));
  }

  render_alert_table($container, rows, columns, doctype, empty_message) {
    if (!rows.length) { $container.html(`<div class="text-muted">${empty_message}</div>`); return; }
    $container.html(this.render_table(rows, columns, doctype));
  }

  // A supervisor's one bulk action in this page: select several Under Review counts and
  // approve them all in one click, instead of opening each one individually.
  render_pending_approval_alerts(rows) {
    const $container = this.body_for("alerts").find(".wms-mon-alert-approval");
    if (!rows.length) { $container.html(`<div class="text-muted">${__("No counts awaiting approval")}</div>`); return; }
    const body = rows.map((row) => `
      <tr>
        <td><input type="checkbox" class="wms-mon-approve-check" value="${frappe.utils.escape_html(row.name)}"></td>
        <td><a href="/app/wms-physical-inventory-count/${encodeURIComponent(row.name)}">${frappe.utils.escape_html(row.name)}</a></td>
        <td>${frappe.utils.escape_html(row.product || "")}</td>
        <td>${frappe.utils.escape_html(row.storage_bin || row.storage_type || "")}</td>
        <td>${frappe.utils.escape_html(row.count_date || "")}</td>
      </tr>
    `).join("");
    $container.html(`
      <div class="table-responsive">
        <table class="table table-bordered table-sm">
          <thead><tr><th></th><th>${__("Count")}</th><th>${__("Product")}</th><th>${__("Scope")}</th><th>${__("Count Date")}</th></tr></thead>
          <tbody>${body}</tbody>
        </table>
      </div>
      <button class="btn btn-primary btn-sm wms-mon-approve-selected">${__("Approve Selected")}</button>
    `);
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

function flt(v) { const n = parseFloat(v); return isNaN(n) ? 0 : n; }
