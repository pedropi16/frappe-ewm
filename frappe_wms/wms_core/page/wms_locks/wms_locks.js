// Lock entries (SAP SM12): what is open for change right now, and who holds it. A lock also ends by itself 5 minutes after its screen went away.
frappe.pages["wms-locks"].on_page_load = function (wrapper) {
  const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Lock Entries"), single_column: true });
  const esc = frappe.utils.escape_html;
  const $body = $(`<div class="wms-locks"></div>`).appendTo(page.main);
  async function refresh() {
    const rows = await frappe.call({ method: "frappe_wms.api.locks.list_locks" }).then((r) => r.message || []);
    $body.html(rows.length ? `<table class="table table-bordered table-sm"><thead><tr><th>${__("Object")}</th><th>${__("Name")}</th><th>${__("User")}</th><th>${__("Since")}</th><th>${__("Purpose")}</th><th></th></tr></thead><tbody>${rows.map((l, i) =>
      `<tr><td>${esc(l.object_type)}</td><td>${esc(l.object_name)}</td><td>${esc(l.user)}</td><td>${esc(String(l.since).slice(0, 16))}</td><td>${esc(l.purpose || "")}</td><td><button class="btn btn-xs btn-danger" data-i="${i}">${__("Delete Lock")}</button></td></tr>`).join("")}</tbody></table>`
      : `<div class="text-muted">${__("Nothing is locked.")}</div>`);
    $body.find("button").on("click", (e) => {
      const l = rows[e.currentTarget.dataset.i];
      frappe.confirm(__("Delete the lock on {0} {1}? {2} may lose what they are doing.", [l.object_type, l.object_name, l.user]), () =>
        frappe.call({ method: "frappe_wms.api.locks.delete_lock", args: { object_type: l.object_type, object_name: l.object_name } }).then(refresh));
    });
  }
  page.set_primary_action(__("Refresh"), refresh);
  refresh();
};
