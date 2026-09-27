import { h } from "#wms/ui/dom.js";
import { S, nav, run, load, notify, update } from "#wms/app.js";
import { api } from "#wms/core/api.js";
import { _ } from "#wms/core/i18n.js";
import { Section, Field, Card, Badge, Btn, KV, Loading, Empty } from "#wms/ui/kit.js";
import { fmtQty } from "#wms/core/util.js";
import { feedback } from "#wms/core/feedback.js";
import { href } from "#wms/core/routes.js";
import { saveDraft, loadDraft, clearDraft, finishFlow, enteredFresh, sectionCrumb } from "#wms/screens/shared.js";
import { closeMovement } from "#wms/screens/closemove.js";
import { uid } from "#wms/core/util.js";

const st = { rows: [], search: "", loading: false, loaded: false, types: null };

async function ensureTypes() {
  if (st.types) return;
  const rows = await load(() => api("frappe.client.get_list", { doctype: "Handling Unit Type", fields: ["name", "numbering_mode"], limit_page_length: 50 }, { read: true }));
  st.types = rows || [];
}
export const huTypes = async () => { await ensureTypes(); return st.types; };

async function refresh() {
  st.loading = true; update();
  const rows = await load(() => api("frappe_wms.api.handling_unit.list_handling_units", { search: st.search || undefined }, { read: true }));
  st.loading = false; st.loaded = true; if (rows) st.rows = rows; update();
}

export const huList = {
  id: "hu", pattern: "hu",
  title: () => _("Handling Units"), crumb: () => sectionCrumb("internal"), parent: () => "#/s/internal",
  async enter() { await ensureTypes(); await refresh(); },
  refresh,
  render() {
    const wrap = h("div", Section({}, Field({ name: "search", kind: "scan", label: _("Search by barcode"), placeholder: _("Scan or type an HU barcode"), value: st.search, autofocus: true, onInput: (v) => { st.search = v; }, onCommit: async (v) => { st.search = v; await refresh(); } })));
    if (st.loading && !st.loaded) { wrap.append(Loading()); return wrap; }
    if (!st.rows.length) { wrap.append(Empty(_("No handling units found."))); return wrap; }
    st.rows.forEach((u) => wrap.append(Card({ title: u.hu_number, right: Badge(_(u.status), u.status === "Blocked" ? "High" : "Normal"), meta: [`${u.hu_type} · ${u.current_bin || "-"}`, u.parent_hu ? ` · ${_("in {0}", [u.parent_hu])}` : ""], qty: _(u.stock_status), onClick: () => nav.go(href("hu", u.name)) })));
    return wrap;
  },
  actions: () => ({ primary: { label: _("Create Handling Unit"), icon: "+", run: () => nav.go("#/hu-new") } }),
};

const nw = { hu_number: "", hu_type: "", storage_bin: "", parent_hu: "", idem: "", w0: 0 };
export const huNew = {
  id: "hu-new", pattern: "hu-new",
  title: () => _("New Handling Unit"), crumb: () => sectionCrumb("internal"), parent: () => "#/hu",
  async enter(ctx) { await ensureTypes(); if (enteredFresh(ctx, "hu-new")) { Object.assign(nw, { hu_number: "", hu_type: "", storage_bin: "", parent_hu: "", idem: "", w0: nav.depth }); } update(); },
  render() {
    const type = (st.types || []).find((t) => t.name === nw.hu_type);
    const internal = type && type.numbering_mode === "Internal";
    return h("div", Section({},
      Field({ name: "type", kind: "select", label: _("Handling Unit type"), value: nw.hu_type, options: [{ value: "", label: _("Select type") }, ...(st.types || []).map((t) => ({ value: t.name, label: t.name }))], onInput: (v) => { nw.hu_type = v; update(); } }),
      Field({ name: "number", kind: "scan", label: _("Barcode"), placeholder: internal ? _("Leave blank - number is auto-assigned") : _("Scan the blank HU barcode"), value: nw.hu_number, disabled: !!internal, autofocus: true, onInput: (v) => { nw.hu_number = v; }, onCommit: (v) => { nw.hu_number = v; } }),
      Field({ name: "bin", kind: "scan", label: _("Storage bin"), placeholder: _("Scan a bin (or leave blank if nesting)"), value: nw.storage_bin, onInput: (v) => { nw.storage_bin = v; }, onCommit: (v) => { nw.storage_bin = v; } }),
      Field({ name: "parent", kind: "scan", label: _("Parent Handling Unit"), placeholder: _("Scan a parent HU to nest into (optional)"), value: nw.parent_hu, onInput: (v) => { nw.parent_hu = v; }, onCommit: (v) => { nw.parent_hu = v; } })));
  },
  actions: () => ({ primary: { label: _("Create"), icon: "✓", run: createHu } }),
};
async function createHu() {
  {
    const type = (st.types || []).find((t) => t.name === nw.hu_type);
    const internal = type && type.numbering_mode === "Internal";
    if (!nw.hu_type) { S.fieldErrors.type = _("Pick a Handling Unit type."); feedback.error(); S.focusRequest = "type"; update(); return; }
    if (!internal && !nw.hu_number.trim()) { S.fieldErrors.number = _("Scan the barcode of the blank Handling Unit."); feedback.error(); S.focusRequest = "number"; update(); return; }
    if (!nw.storage_bin.trim() && !nw.parent_hu.trim()) { S.fieldErrors.bin = _("Scan a bin or a parent Handling Unit."); feedback.error(); S.focusRequest = "bin"; update(); return; }
    const r = await run(() => api("frappe_wms.api.handling_unit.create_handling_unit", { hu_number: nw.hu_number.trim() || undefined, hu_type: nw.hu_type, storage_bin: nw.storage_bin.trim() || undefined, parent_hu: nw.parent_hu.trim() || undefined }), { label: _("Creating…"), again: createHu });
    if (!r) return;
    feedback.done(); finishFlow(nw.w0, "#/hu", _("Handling Unit {0} created", [r.name]));
  }
}

const d = { hu: null, target: "", w0: 0 };
async function reload() { d.hu = await api("frappe_wms.api.handling_unit.handling_unit_detail", { hu_name: d.name }); update(); }
async function act(method, args, msg, label) {
  const ok = await run(async () => { await api(method, args); await reload(); return true; }, { label });
  if (ok) { feedback.ok(); notify.ok(msg); }
  return ok;
}
export const huDetail = {
  id: "hu-detail", pattern: "hu/:name",
  title: () => _("Handling Unit"), crumb: () => sectionCrumb("internal"), parent: () => "#/hu",
  async enter(ctx) {
    d.name = ctx.params.name; d.target = "";
    if (enteredFresh(ctx, "hu-detail")) d.w0 = nav.depth;
    const detail = await load(() => api("frappe_wms.api.handling_unit.handling_unit_detail", { hu_name: d.name }, { read: true }));
    if (!detail) return { redirect: "#/hu" };
    d.hu = detail; update();
  },
  refresh: async () => { await load(reload); },
  render() {
    const u = d.hu;
    if (!u) return Loading();
    const wrap = h("div", Section({ title: `${u.hu_number} · ${_(u.status)}` }, KV([[_("Type"), u.hu_type], [_("Bin"), u.current_bin], [_("Parent HU"), u.parent_hu], [_("Stock"), _(u.stock_status)]])));
    if (u.stock && u.stock.length) wrap.append(Section({ title: _("Contents") }, u.stock.map((r) => h("div.meta", `${r.product} · ${fmtQty(r.quantity)} ${r.stock_uom} · ${_(r.stock_type)}`))));
    if (u.children && u.children.length) wrap.append(Section({ title: _("Nested Handling Units") }, u.children.map((c) => Card({ title: c.hu_number, right: Badge(_(c.status), "Normal"), onClick: () => nav.go(href("hu", c.name)) }))));
    const actions = Section({ title: _("Actions") });
    if (u.parent_hu) actions.append(Btn({ label: _("Unnest from {0}", [u.parent_hu]), onClick: () => act("frappe_wms.api.handling_unit.unnest_handling_unit", { hu_name: u.name }, _("Unnested"), _("Unnesting…")) }));
    else actions.append(Field({ name: "nest", kind: "scan", label: _("Nest into parent HU"), placeholder: _("Scan parent HU barcode"), value: d.target, onInput: (v) => { d.target = v; },
      onCommit: async (v) => { const ok = await act("frappe_wms.api.handling_unit.nest_handling_unit", { hu_name: u.name, parent_hu: v }, _("Nested inside {0}", [v]), _("Nesting…")); return ok ? undefined : false; } }));
    const blocked = u.status === "Blocked";
    actions.append(Btn({ label: blocked ? _("Unblock") : _("Block"), kind: blocked ? "primary" : "danger", onClick: () => act(blocked ? "frappe_wms.api.handling_unit.unblock_handling_unit" : "frappe_wms.api.handling_unit.block_handling_unit", { hu_name: u.name }, blocked ? _("{0} unblocked", [u.name]) : _("{0} blocked", [u.name]), _("Working…")) }));
    if (u.stock_status === "Empty" && !u.parent_hu) actions.append(Btn({ label: _("Recycle"), kind: "danger", onClick: async () => {
      if (!confirm(_("Recycle {0}? This deletes it and frees its number for reuse.", [u.name]))) return;
      const ok = await run(() => api("frappe_wms.api.handling_unit.recycle_handling_unit", { hu_name: u.name }), { label: _("Recycling…") });
      if (ok !== undefined) { feedback.done(); finishFlow(d.w0, "#/hu", _("{0} recycled", [u.name])); }
    } }));
    if (u.current_bin && u.stock_status !== "Empty") actions.append(Btn({ label: _("Close Movement (advance to next bin)"), onClick: () => closeMovement(u.name, d.w0) }));
    wrap.append(actions);
    return wrap;
  },
};
