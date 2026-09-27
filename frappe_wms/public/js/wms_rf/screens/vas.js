import { h } from "#wms/ui/dom.js";
import { S, nav, run, load, update } from "#wms/app.js";
import { api } from "#wms/core/api.js";
import { _ } from "#wms/core/i18n.js";
import { Section, Field, Card, StatusBadge, Badge, Loading, KV } from "#wms/ui/kit.js";
import { fmtQty, uid } from "#wms/core/util.js";
import { feedback } from "#wms/core/feedback.js";
import { href } from "#wms/core/routes.js";
import { finishFlow, enteredFresh, sectionCrumb } from "#wms/screens/shared.js";
import { listScreen } from "#wms/screens/lists.js";

export const vasList = listScreen({
  id: "vas", pattern: "vas", title: _("VAS"), section: "outbound", method: "frappe_wms.api.vas.list_open_vas_orders", empty: _("No open VAS orders."),
  header: () => h("button.btn.btn-secondary", { type: "button", onclick: () => nav.go("#/vas-new") }, `+ ${_("Generate from Packaging Spec")}`),
  card: (o) => Card({ title: o.name, right: StatusBadge(o.status), meta: [o.handling_unit, o.work_center_bin ? ` · ${o.work_center_bin}` : ""], qty: _("{0} / {1} steps done", [o.completed_count, o.activity_count]), onClick: () => nav.go(href("vas", o.name)) }),
});

const nw = { hu: "", bin: "", idem: "", w0: 0 };
export const vasNew = {
  id: "vas-new", pattern: "vas-new",
  title: () => _("New VAS order"), crumb: () => sectionCrumb("outbound"), parent: () => "#/vas",
  enter(ctx) { if (enteredFresh(ctx, "vas-new")) Object.assign(nw, { hu: "", bin: (S.resource && S.resource.current_work_center_bin) || "", idem: "", w0: nav.depth }); },
  render() {
    return h("div", Section({ title: _("Generate VAS from Packaging Spec"), hint: _("Scan the Handling Unit and the work center bin. A VAS Order is built with one step per level of the item's Packaging Spec.") },
      Field({ name: "hu", kind: "scan", label: _("Handling Unit"), placeholder: _("Scan HU barcode"), value: nw.hu, autofocus: true, onInput: (v) => { nw.hu = v; }, onCommit: (v) => { nw.hu = v; } }),
      Field({ name: "bin", kind: "scan", label: _("Work center bin"), placeholder: _("Scan bin barcode"), value: nw.bin, onInput: (v) => { nw.bin = v; }, onCommit: (v) => { nw.bin = v; } })));
  },
  actions: () => ({ primary: { label: _("Generate"), run: generateVas } }),
};
async function generateVas() {
  {
    if (!nw.hu.trim()) { S.fieldErrors.hu = _("Scan the Handling Unit."); feedback.error(); S.focusRequest = "hu"; update(); return; }
    if (!nw.bin.trim()) { S.fieldErrors.bin = _("Scan the work center bin."); feedback.error(); S.focusRequest = "bin"; update(); return; }
    if (!nw.idem) nw.idem = `VAS:${uid()}`;
    const name = await run(() => api("frappe_wms.api.vas.create_vas_order_from_packaging_spec", { handling_unit: nw.hu.trim(), work_center_bin: nw.bin.trim(), idempotency_key: nw.idem }), { label: _("Generating…"), again: generateVas });
    if (!name) return;
    feedback.done(); nw.idem = "";
    nav.replace(href("vas", typeof name === "string" ? name : name.name));
  }
}

const st = { order: null, name: null, w0: 0 };
async function fetchOrder(name) { st.order = await api("frappe_wms.api.vas.get_vas_order", { vas_order_name: name }, { read: true }); update(); }
export const vasDetail = {
  id: "vas-detail", pattern: "vas/:name",
  title: () => _("VAS order"), crumb: () => sectionCrumb("outbound"), parent: () => "#/vas",
  async enter(ctx) {
    st.name = ctx.params.name;
    if (enteredFresh(ctx, "vas-detail")) st.w0 = nav.depth;
    const ok = await load(async () => { await fetchOrder(st.name); return true; });
    if (!ok) return { redirect: "#/vas" };
  },
  refresh: () => load(() => fetchOrder(st.name)),
  render() {
    const o = st.order;
    if (!o) return Loading();
    const acts = [...(o.activities || [])].sort((a, b) => (a.step_no || 0) - (b.step_no || 0));
    return h("div", Section({ title: `${o.name} · ${_(o.status)}` }, KV([[_("Handling Unit"), o.handling_unit], [_("Work center"), o.work_center_bin]])),
      Section({ title: _("Steps"), hint: _("Tap a step when it is done.") }, acts.map((row, i) => h("div.timeline-row", h("div.timeline-dot", { class: row.completed ? "done" : "active" }, row.completed ? "✓" : i + 1),
        h("div.timeline-card", Card({ title: row.activity_type, dim: !!row.completed, right: row.completed ? Badge(_("Done"), "Done") : null, meta: row.instruction || "", qty: row.quantity ? `${fmtQty(row.quantity)} ${row.stock_uom || ""}` : null, onClick: row.completed ? null : () => complete(row) }))))));
  },
};

async function complete(row) {
  const o = st.order;
  const r = await run(() => api("frappe_wms.api.vas.complete_activity", { vas_order_name: o.name, activity_row_name: row.name }), { label: _("Saving…") });
  if (!r) return;
  feedback.ok();
  if (r.status === "Completed") { feedback.done(); finishFlow(st.w0, "#/vas", _("{0} completed", [o.name])); return; }
  await load(() => fetchOrder(o.name));
}
