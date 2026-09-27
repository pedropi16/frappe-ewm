import { h } from "#wms/ui/dom.js";
import { S, nav, run, notify, update } from "#wms/app.js";
import { api } from "#wms/core/api.js";
import { _ } from "#wms/core/i18n.js";
import { Section, Field, Card, StatusBadge, Empty, Loading, KV, Hint } from "#wms/ui/kit.js";
import { fmtQty, parseNum, isNumeric, round6 } from "#wms/core/util.js";
import { feedback } from "#wms/core/feedback.js";
import { href } from "#wms/core/routes.js";
import { saveDraft, loadDraft, clearDraft, ensureKey, finishFlow, enteredFresh, sectionCrumb } from "#wms/screens/shared.js";
import { listScreen, findRow } from "#wms/screens/lists.js";

export const shipList = listScreen({
  id: "ship", pattern: "ship", title: _("Ship"), section: "outbound", method: "frappe_wms.api.outbound.list_ready_to_ship", empty: _("Nothing ready to ship."),
  card: (d) => Card({ title: d.outbound_delivery_number, right: StatusBadge(d.status), meta: [d.customer || "", h("br"), d.staging_bin || "", d.door ? ` · ${_("Door")} ${d.door}` : "", d.route ? ` · ${d.route}` : ""], onClick: () => nav.go(href("ship", d.name)) }),
});

const st = { name: null, delivery: null, lines: [], form: { idem: "" }, w0: 0 };
const key = (n) => `ship:${n}`;
const persist = () => saveDraft(key(st.name), { lines: st.lines, idem: st.form.idem }, { label: `${_("Ship")} · ${st.delivery ? st.delivery.outbound_delivery_number : st.name}`, route: href("ship", st.name) });

export const shipDetail = {
  id: "ship-detail", pattern: "ship/:name",
  title: () => _("Ship"), crumb: () => sectionCrumb("outbound"), parent: () => "#/ship",
  async enter(ctx) {
    const name = ctx.params.name;
    if (enteredFresh(ctx, "ship-detail")) st.w0 = nav.depth;
    const d = await findRow(shipList, name, ctx);
    if (!d) { notify.warn(_("That delivery is no longer ready to ship.")); return { redirect: "#/ship" }; }
    st.name = name; st.delivery = d;
    const saved = loadDraft(key(name));
    const fresh = d.items.map((row) => ({ outbound_delivery_item: row.name, item: row.item, remaining: row.remaining_quantity, stock_uom: row.stock_uom, stock_type: row.required_stock_type, handling_unit: row.suggested_handling_unit || "", quantity: fmtQty(row.remaining_quantity) }));
    st.lines = fresh.map((l) => { const x = saved && saved.lines.find((y) => y.outbound_delivery_item === l.outbound_delivery_item && y.remaining === l.remaining); return x ? { ...l, handling_unit: x.handling_unit, quantity: x.quantity } : l; });
    st.form.idem = (saved && saved.idem) || "";
    update();
  },
  render() {
    const d = st.delivery;
    if (!d) return Loading();
    const wrap = h("div");
    if (d.route || d.door) wrap.append(Section({}, KV([[_("Route"), d.route], [_("Door"), d.door]])));
    wrap.append(Hint(_("Scan the staged Handling Unit for each line.")));
    st.lines.forEach((l, i) => wrap.append(Section({},
      h("div.line-head", l.item), h("div.line-sub", `${_("Remaining: {0} {1}", [fmtQty(l.remaining), l.stock_uom])} · ${_(l.stock_type)}`),
      Field({ name: `hu${i}`, kind: "scan", label: _("Handling Unit"), placeholder: _("Scan staged HU"), value: l.handling_unit, autofocus: i === 0, onInput: (v) => { l.handling_unit = v; persist(); }, onCommit: (v) => { l.handling_unit = v; persist(); } }),
      Field({ name: `qty${i}`, kind: "qty", label: _("Quantity"), value: l.quantity, unit: l.stock_uom, enterNext: true, onInput: (v) => { l.quantity = v; persist(); } }))));
    if (!st.lines.length) wrap.append(Empty(_("Nothing left to ship on this delivery."), "✅"));
    return wrap;
  },
  actions() { return st.lines.length ? { primary: { label: _("Post goods issue"), icon: "✓", run: submit } } : null; },
};

async function submit() {
  const lines = st.lines.map((l, i) => ({ l, i })).filter(({ l }) => l.handling_unit.trim());
  if (!lines.length) { notify.warn(_("Scan the Handling Unit being shipped for at least one line.")); S.focusRequest = "hu0"; update(); return; }
  for (const { l, i } of lines) {
    if (!isNumeric(l.quantity) || parseNum(l.quantity) <= 0 || round6(parseNum(l.quantity)) > round6(l.remaining)) { S.fieldErrors[`qty${i}`] = _("Enter a quantity between 0 and {0}.", [fmtQty(l.remaining)]); feedback.error(); S.focusRequest = `qty${i}`; update(); return; }
  }
  const items = lines.map(({ l }) => ({ outbound_delivery_item: l.outbound_delivery_item, item: l.item, quantity: parseNum(l.quantity), stock_uom: l.stock_uom, handling_unit: l.handling_unit.trim(), stock_type: l.stock_type }));
  const idem = ensureKey(st.form, "GI"); persist();
  const result = await run(() => api("frappe_wms.api.outbound.create_and_submit_goods_issue", { outbound_delivery: st.name, items: JSON.stringify(items), idempotency_key: idem }), { label: _("Posting goods issue…"), again: submit });
  if (!result) return;
  feedback.done(); clearDraft(key(st.name));
  const w0 = st.w0; st.name = null;
  finishFlow(w0, "#/s/outbound", _("Goods Issue {0} posted", [result.goods_issue]));
}
