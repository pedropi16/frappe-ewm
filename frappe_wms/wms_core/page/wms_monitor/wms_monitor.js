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
        const btn = $(`<button class="btn btn-xs btn-success" style="margin-left:6px;">${__("Post Goods Issue")}</button>`);
        btn.on("click", () => frappe.call("frappe_wms.api.outbound.post_goods_issue_for_delivery", { delivery_name })
          .then(() => { frappe.show_alert({ message: __("Goods Issue posted"), indicator: "green" }); this.load_delivery_detail(delivery_name); this.search_outbound_deliveries(); })
          .catch(() => {}));
        $actions.append(btn);
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
      ["resource_code", __("Resource")], ["user", __("User")], ["resource_type", __("Type")],
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

function flt(v) { const n = parseFloat(v); return isNaN(n) ? 0 : n; }
