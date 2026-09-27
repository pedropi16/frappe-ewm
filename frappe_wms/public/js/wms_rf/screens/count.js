import { h } from "#wms/ui/dom.js";
import { S, nav, run, notify, update } from "#wms/app.js";
import { api } from "#wms/core/api.js";
import { _ } from "#wms/core/i18n.js";
import { Section, Field, Card, StatusBadge, Loading, Hint, Empty } from "#wms/ui/kit.js";
import { fmtQty, parseNum, isNumeric } from "#wms/core/util.js";
import { feedback } from "#wms/core/feedback.js";
import { href } from "#wms/core/routes.js";
import { saveDraft, loadDraft, clearDraft, finishFlow, enteredFresh, sectionCrumb } from "#wms/screens/shared.js";
import { listScreen, findRow } from "#wms/screens/lists.js";

export const countList = listScreen({
  id: "count", pattern: "count", title: _("Count"), section: "internal", method: "frappe_wms.api.inventory.list_open_counts", empty: _("No open counts."),
  card: (c) => Card({ title: c.name, right: StatusBadge(c.status), meta: [c.warehouse, c.storage_bin ? ` / ${c.storage_bin}` : "", h("br"), c.status === "Draft" ? _("Tap to start counting") : _("{0} line(s) left to count", [(c.items || []).length])], onClick: () => nav.go(href("count", c.name)) }),
});

const st = { name: null, count: null, items: [], w0: 0 };
const key = (n) => `count:${n}`;
const persist = () => saveDraft(key(st.name), { items: st.items.map((r) => ({ name: r.name, counted_quantity: r.counted_quantity })) }, { label: `${_("Count")} · ${st.name}`, route: href("count", st.name) });

export const countDetail = {
  id: "count-detail", pattern: "count/:name",
  title: () => _("Count"), crumb: () => sectionCrumb("internal"), parent: () => "#/count",
  async enter(ctx) {
    const name = ctx.params.name;
    if (enteredFresh(ctx, "count-detail")) st.w0 = nav.depth;
    let count = await findRow(countList, name, ctx);
    if (count && count.status === "Draft") {
      const ok = await run(async () => { await api("frappe_wms.api.inventory.snapshot_count", { count_name: name }); return true; }, { busy: true, label: _("Starting count…") });
      if (!ok) return { redirect: "#/count" };
      await countList.refresh(ctx);
      count = countList.st.rows.find((c) => c.name === name);
      if (!count) { notify.warn(_("Count could not be started - it may have no stock in scope.")); return { redirect: "#/count" }; }
    }
    if (!count) { notify.warn(_("That count is no longer open.")); return { redirect: "#/count" }; }
    st.name = name; st.count = count;
    const saved = loadDraft(key(name));
    st.items = (count.items || []).map((r) => { const d = saved && saved.items.find((x) => x.name === r.name); return { ...r, counted_quantity: d ? d.counted_quantity : r.counted_quantity != null ? String(r.counted_quantity) : "" }; });
    update();
  },
  render() {
    if (!st.count) return Loading();
    const wrap = h("div", Hint(_("Count what is physically there. Lines you leave blank stay open for later.")));
    st.items.forEach((r, i) => wrap.append(Section({},
      h("div.line-head", r.product), h("div.line-sub", `${r.storage_bin}${r.handling_unit ? " / " + r.handling_unit : ""} · ${_("book {0} {1}", [fmtQty(r.book_quantity), r.stock_uom])}`),
      Field({ name: `c${i}`, kind: "qty", label: _("Counted quantity"), value: r.counted_quantity, unit: r.stock_uom, autofocus: i === 0, enterNext: true, onInput: (v) => { r.counted_quantity = v; persist(); } }))));
    if (!st.items.length) wrap.append(Empty(_("Nothing left to count."), "✅"));
    return wrap;
  },
  actions() { return st.items.length ? { primary: { label: _("Save counts"), icon: "✓", run: submit } } : null; },
};

async function submit() {
  const counted = {};
  for (let i = 0; i < st.items.length; i++) {
    const r = st.items[i];
    if (String(r.counted_quantity).trim() === "") continue;
    if (!isNumeric(r.counted_quantity) || parseNum(r.counted_quantity) < 0) { S.fieldErrors[`c${i}`] = _("Enter a number, 0 or more."); feedback.error(); S.focusRequest = `c${i}`; update(); return; }
    counted[r.name] = parseNum(r.counted_quantity);
  }
  if (!Object.keys(counted).length) { notify.warn(_("Enter at least one counted quantity.")); return; }
  persist();
  // record_counts sets absolute counted quantities, so resending it is naturally safe.
  const result = await run(() => api("frappe_wms.api.inventory.record_counts", { count_name: st.name, counted_quantities: JSON.stringify(counted) }), { label: _("Saving counts…"), again: submit });
  if (!result) return;
  let message = _("Saved counts for {0}", [st.name]);
  if (result.status === "Counted") {
    try {
      const posted = await api("frappe_wms.api.inventory.post_count", { count_name: st.name });
      message = posted.status === "Posted" ? _("Count {0} fully counted and posted", [st.name]) : _("Count {0} fully counted; a variance is out of tolerance and is held for recount/supervisor approval", [st.name]);
    } catch (e) { message = _("Count {0} fully counted; ask a supervisor to post it", [st.name]); }
  }
  feedback.done(); clearDraft(key(st.name));
  const w0 = st.w0; st.name = null;
  finishFlow(w0, "#/s/internal", message);
}
