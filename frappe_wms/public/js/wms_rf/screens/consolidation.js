import { h } from "#wms/ui/dom.js";
import { S, nav, run, load, notify, update } from "#wms/app.js";
import { api } from "#wms/core/api.js";
import { _ } from "#wms/core/i18n.js";
import { Section, Field, Card, StatusBadge, Btn, Loading, KV, Hint } from "#wms/ui/kit.js";
import { fmtQty } from "#wms/core/util.js";
import { feedback } from "#wms/core/feedback.js";
import { href } from "#wms/core/routes.js";
import { sectionCrumb } from "#wms/screens/shared.js";
import { listScreen } from "#wms/screens/lists.js";

export const consolidationList = listScreen({
  id: "consolidation", pattern: "consolidation", title: _("Consolidation"), section: "internal", method: "frappe_wms.api.consolidation.list_open_consolidation_groups", empty: _("No open Consolidation Groups."),
  card: (g) => Card({ title: g.name, right: StatusBadge(g.gather_status), meta: `${g.staging_bin}${g.target_hu ? " / " + g.target_hu : ""}`, qty: _("{0} / {1} lines done", [g.done_count, g.line_count]), onClick: () => nav.go(href("consolidation", g.name)) }),
});

const st = { group: null, name: null, target: "", scan: "", candidates: [] };
const M = (m) => `frappe_wms.api.consolidation.${m}`;
async function fetchGroup() { st.group = await api(M("get_consolidation_group"), { group_name: st.name }, { read: true }); update(); }
async function act(method, args, msg, label) {
  const r = await run(async () => { const out = await api(M(method), { group_name: st.name, ...args }); await fetchGroup(); return out === undefined ? true : out; }, { label });
  if (r === undefined) return false;
  feedback.ok(); if (msg) notify.ok(typeof msg === "function" ? msg(r) : msg); return true;
}

export const consolidationDetail = {
  id: "consolidation-detail", pattern: "consolidation/:name",
  title: () => _("Consolidation"), crumb: () => sectionCrumb("internal"), parent: () => "#/consolidation",
  async enter(ctx) {
    if (st.name !== ctx.params.name) { st.name = ctx.params.name; st.group = null; st.target = ""; st.scan = ""; st.candidates = []; }
    const ok = await load(async () => { await fetchGroup(); return true; });
    if (!ok) return { redirect: "#/consolidation" };
  },
  refresh: () => load(fetchGroup),
  render() {
    const g = st.group;
    if (!g) return Loading();
    const wrap = h("div", Section({ title: `${g.name} · ${_(g.gather_status)}` }, KV([[_("Staging bin"), g.staging_bin], [_("Target HU"), g.target_hu]])));
    if (!g.target_hu) wrap.append(Section({},
      Field({ name: "target", kind: "scan", label: _("Set target Handling Unit"), placeholder: _("Scan HU barcode"), value: st.target, autofocus: true, onInput: (v) => { st.target = v; },
        onCommit: async (v) => { const ok = await act("set_consolidation_target_hu", { target_hu: v }, null, _("Saving…")); return ok ? undefined : false; } })));
    const lines = g.lines || [];
    wrap.append(Section({ title: _("Lines") }, !lines.length ? Hint(_("No lines yet - add one below.")) : null, lines.map((l) => {
      const progress = l.status === "Deconsolidated" ? l.fulfilled_quantity : l.gathered_quantity;
      return Card({ title: l.product, right: StatusBadge(l.status), meta: `${l.reference_doctype} ${l.reference_name} → ${l.final_destination_bin}`, qty: `${fmtQty(progress)} / ${fmtQty(l.quantity)} ${l.stock_uom || ""}`,
        children: l.status === "Ready" || l.status === "Pending" ? Btn({ label: _("Remove"), kind: "danger", small: true, onClick: () => act("remove_consolidation_line", { line_name: l.name }, null, _("Removing…")) }) : null });
    })));
    const add = Section({ title: `+ ${_("Add line")}` },
      Field({ name: "scan", kind: "scan", label: _("Scan Outbound Delivery, Work Order, Stock Allocation or Warehouse Request"), placeholder: _("Scan barcode"), value: st.scan, autofocus: !!g.target_hu, onInput: (v) => { st.scan = v; },
        onCommit: async (v) => {
          const c = await run(() => api(M("find_joinable_references"), { barcode: v }, { read: true }), { label: _("Searching…") });
          if (c === undefined) return false;
          if (!c.length) return _("Nothing joinable found for {0} - it may already be on a group, or not fully picked/staged yet.", [v]);
          st.candidates = c; update();
        } }));
    st.candidates.forEach((c) => add.append(Card({ title: c.product, meta: `${c.reference_doctype} ${c.reference_name}${c.source_label ? " · " + c.source_label : ""}`, qty: fmtQty(c.quantity),
      onClick: async () => { if (await act("add_consolidation_line", { reference_doctype: c.reference_doctype, reference_name: c.reference_name }, null, _("Adding…"))) { st.scan = ""; st.candidates = []; update(); } } })));
    wrap.append(add);
    return wrap;
  },
  actions() {
    const g = st.group;
    if (!g) return null;
    const secondary = [];
    if (g.gather_status === "Fully Gathered") return { primary: { label: _("Split to destinations"), run: () => act("complete_consolidation_group", {}, (c) => _("{0} split to destination task(s) created - confirm them from Putaway Tasks", [c.length]), _("Working…")) } };
    if ((g.lines || []).some((l) => l.status === "Ready") && g.target_hu) return { primary: { label: _("Gather"), run: () => act("gather_consolidation_group", {}, (c) => _("{0} gather task(s) created - confirm them from Internal Tasks", [c.length]), _("Working…")) } };
    return null;
  },
};
