import { h } from "#wms/ui/dom.js";
import { S, nav, run, load, notify, update } from "#wms/app.js";
import { api } from "#wms/core/api.js";
import { _ } from "#wms/core/i18n.js";
import { Section, Field, Card, StatusBadge, Badge, KV, Hint, Loading } from "#wms/ui/kit.js";
import { fmtQty } from "#wms/core/util.js";
import { feedback } from "#wms/core/feedback.js";
import { href } from "#wms/core/routes.js";
import { finishFlow, enteredFresh, sectionCrumb } from "#wms/screens/shared.js";
import { listScreen } from "#wms/screens/lists.js";

const M = (m) => `frappe_wms.api.kitting.${m}`;

const kitting = listScreen({
  id: "kitting", pattern: "kitting", title: _("Kitting"), section: "internal", method: M("list_open_kitting_orders"), empty: _("No open Kitting Orders."),
  card: (o) => Card({ title: o.kit_item, right: StatusBadge(o.status), meta: `${_(o.direction)} · ${o.work_center_bin || ""}`, qty: fmtQty(o.quantity), onClick: () => nav.go(href("kitting", o.name)) }),
});
export default kitting;

// Kit-to-stock at the work center: stage the inputs by warehouse tasks, build, then (optionally)
// put the output on a Handling Unit and away - the order of SAP EWM's kitting work center.
const st = { name: null, order: null, hu: "", w0: 0 };
const waiting = (o) => !o.ready && o.inputs.every((i) => i.at_work_center + i.in_transit + 0.000001 >= i.required);
async function fetchOrder() { st.order = await api(M("get_kitting_order"), { kitting_order_name: st.name }, { read: true }); update(); }

export const kittingDetail = {
  id: "kitting-detail", pattern: "kitting/:name",
  title: () => _("Kitting order"), crumb: () => sectionCrumb("internal"), parent: () => "#/kitting",
  async enter(ctx) {
    if (st.name !== ctx.params.name) Object.assign(st, { name: ctx.params.name, order: null, hu: "" });
    if (enteredFresh(ctx, "kitting-detail")) st.w0 = nav.depth;
    const ok = await load(async () => { await fetchOrder(); return true; });
    if (!ok) return { redirect: "#/kitting" };
  },
  refresh: () => load(fetchOrder),
  render() {
    const o = st.order;
    if (!o) return Loading();
    const wrap = h("div", Section({ title: `${o.name} · ${_(o.direction)}` }, KV([
      [_("Kit"), `${o.kit_item}${o.kit_item_name && o.kit_item_name !== o.kit_item ? " · " + o.kit_item_name : ""}`],
      [_("Quantity"), fmtQty(o.quantity)], [_("Work center"), o.work_center_bin], [_("Put away output"), o.putaway_output ? _("Yes") : _("No")]])));
    wrap.append(Section({ title: _("Needed at the work center"), hint: o.ready ? _("Everything is at the work center.") : waiting(o) ? _("Waiting for the staging tasks - confirm them from Internal Tasks.") : _("Stage what is missing - warehouse tasks bring it from storage.") },
      o.inputs.map((i) => {
        const done = i.at_work_center + 0.000001 >= i.required;
        return Card({ title: i.item, dim: done, right: done ? Badge(_("Ready"), "Done") : (i.in_transit ? Badge(_("On its way"), "Normal") : Badge(_("Missing"), "High")),
          meta: [i.item_name && i.item_name !== i.item ? i.item_name : "", i.in_transit ? ` · ${_("{0} on its way", [fmtQty(i.in_transit)])}` : ""],
          qty: `${fmtQty(i.at_work_center)} / ${fmtQty(i.required)} ${i.stock_uom || ""}` });
      })));
    wrap.append(Section({ title: _("Output") }, o.outputs.map((x) => Card({ title: x.item, qty: `${fmtQty(x.quantity)} ${x.stock_uom || ""}` }))));
    if (o.ready) wrap.append(Section({},
      Field({ name: "hu", kind: "scan", label: o.output_hu_required ? _("Output Handling Unit") : _("Output Handling Unit (optional)"), placeholder: _("Scan HU barcode"),
        value: st.hu, gs1: "sscc", autofocus: true, onInput: (v) => { st.hu = v; }, onCommit: (v) => { st.hu = v; } }),
      o.output_hu_required ? null : Hint(_("Leave empty to post the output loose in the work center bin."))));
    return wrap;
  },
  actions() {
    const o = st.order;
    if (!o) return null;
    const cancel = { label: _("Cancel order"), kind: "danger", run: cancelOrder };
    if (o.ready) return { primary: { label: _("Complete"), run: complete }, secondary: [cancel] };
    if (waiting(o)) return { primary: { label: _("Refresh"), run: () => load(fetchOrder) }, secondary: [cancel] };
    return { primary: { label: _("Stage components"), run: stage }, secondary: [cancel] };
  },
};

async function stage() {
  const r = await run(() => api(M("stage_kitting_components"), { kitting_order_name: st.name }), { label: _("Creating tasks…") });
  if (r === undefined) return;
  feedback.ok();
  if (r.short && r.short.length) notify.warn(_("Not enough stock for: {0}", [r.short.map((s) => `${s.item} (${fmtQty(s.missing)})`).join(", ")]));
  else notify.ok(r.requests.length ? _("{0} staging task(s) created - confirm them from Internal Tasks", [r.requests.length]) : _("Everything is already on its way."));
  await load(fetchOrder);
}

async function complete() {
  const o = st.order;
  if (o.output_hu_required && !st.hu.trim()) { S.fieldErrors.hu = _("Scan the output Handling Unit."); feedback.error(); S.focusRequest = "hu"; update(); return; }
  const r = await run(() => api(M("complete_kitting_order"), { kitting_order_name: st.name, destination_hu: st.hu.trim() || undefined }), { label: _("Completing…") });
  if (r === undefined) return;
  feedback.done();
  const done = (r.putaway_requests || []).length ? _("{0} completed - putaway task created", [st.name]) : _("{0} completed", [st.name]);
  st.name = null;
  finishFlow(st.w0, "#/kitting", done);
}

async function cancelOrder() {
  if (!confirm(_("Cancel kitting order {0}? Open staging tasks are cancelled.", [st.name]))) return;
  const r = await run(() => api(M("cancel_kitting_order"), { kitting_order_name: st.name }), { label: _("Cancelling…") });
  if (r === undefined) return;
  feedback.ok();
  const name = st.name; st.name = null;
  finishFlow(st.w0, "#/kitting", _("{0} cancelled", [name]));
}
