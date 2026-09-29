import { h } from "#wms/ui/dom.js";
import { S, nav, run, load, notify, update } from "#wms/app.js";
import { api } from "#wms/core/api.js";
import { _ } from "#wms/core/i18n.js";
import { Section, Field, Card, StatusBadge, Empty, Loading, Hint } from "#wms/ui/kit.js";
import { fmtQty, flt, parseNum, isNumeric, round6 } from "#wms/core/util.js";
import { feedback } from "#wms/core/feedback.js";
import { href } from "#wms/core/routes.js";
import { saveDraft, loadDraft, clearDraft, ensureKey, finishFlow, enteredFresh, sectionCrumb, sectionHash } from "#wms/screens/shared.js";
import { listScreen } from "#wms/screens/lists.js";

export const receiveList = listScreen({
  id: "receive", pattern: "receive", title: _("Receive"), section: "inbound", method: "frappe_wms.api.inbound.list_open_inbound_deliveries", empty: _("No open inbound deliveries."),
  card: (d) => Card({ title: d.inbound_delivery_number || d.name, right: StatusBadge(d.status), meta: [d.supplier || "", h("br"), d.receiving_bin || ""], onClick: () => nav.go(href("receive", d.name)) }),
});

const st = { name: null, doc: null, lines: [], huTypes: null, form: null, loading: false, w0: 0 };
const key = (n) => `receive:${n}`;
const persist = () => saveDraft(key(st.name), { lines: st.lines, idem: st.form.idem }, { label: `${_("Receive")} · ${st.doc ? st.doc.inbound_delivery_number || st.name : st.name}`, route: href("receive", st.name) });

export const receiveDetail = {
  id: "receive-detail", pattern: "receive/:name",
  title: () => _("Receive"), crumb: () => sectionCrumb("inbound"), parent: () => "#/receive",
  async enter(ctx) {
    const name = ctx.params.name;
    if (enteredFresh(ctx, "receive-detail")) st.w0 = nav.depth;
    if (st.name !== name) { st.name = name; st.doc = null; st.lines = []; st.form = { idem: "" }; }
    st.loading = !st.doc; update();
    const [doc, types] = await Promise.all([
      load(() => api("frappe.client.get", { doctype: "Inbound Delivery", name }, { read: true })),
      st.huTypes ? st.huTypes : load(() => api("frappe.client.get_list", { doctype: "Handling Unit Type", fields: ["name", "numbering_mode"], limit_page_length: 50 }, { read: true })),
    ]);
    st.loading = false;
    if (!doc) return { redirect: "#/receive" };
    st.huTypes = types || [];
    st.doc = doc;
    const saved = loadDraft(key(name));
    const fresh = (doc.items || []).map((row) => ({
      inbound_delivery_item: row.name, item: row.item, remaining: round6(flt(row.expected_quantity) - flt(row.received_quantity)),
      stock_uom: row.stock_uom, stock_type: row.expected_stock_type, handling_unit: "", hu_type: "", quantity: "", batch_no: "",
    })).filter((l) => l.remaining > 0).map((l) => ({ ...l, quantity: fmtQty(l.remaining) }));
    // A saved draft only applies to lines that still exist with the same remaining quantity.
    st.lines = fresh.map((l) => { const d = saved && saved.lines.find((x) => x.inbound_delivery_item === l.inbound_delivery_item && x.remaining === l.remaining); return d ? { ...l, handling_unit: d.handling_unit, hu_type: d.hu_type, quantity: d.quantity, batch_no: d.batch_no || "" } : l; });
    st.form.idem = (saved && saved.idem) || "";
    update();
  },
  refresh: () => nav.replace(location.hash),
  render() {
    const wrap = h("div");
    if (st.loading) return Loading();
    wrap.append(Hint(_("Scan the Handling Unit for each line. Enter moves to the next field.")));
    if (!st.lines.length) { wrap.append(Empty(_("Nothing left to receive on this delivery."), "✅")); return wrap; }
    const typeOptions = [{ value: "", label: "—" }, ...(st.huTypes || []).map((t) => ({ value: t.name, label: t.name }))];
    st.lines.forEach((l, i) => wrap.append(Section({},
      h("div.line-head", l.item), h("div.line-sub", `${_("Remaining: {0} {1}", [fmtQty(l.remaining), l.stock_uom])} · ${_(l.stock_type)}`),
      Field({ name: `hu${i}`, kind: "scan", label: _("Handling Unit"), placeholder: _("Scan HU barcode"), value: l.handling_unit, autofocus: i === 0,
        onInput: (v) => { l.handling_unit = v; persist(); }, onCommit: (v) => { l.handling_unit = v; persist(); } }),
      Field({ name: `type${i}`, kind: "select", label: _("If new HU, type"), value: l.hu_type, options: typeOptions, onInput: (v) => { l.hu_type = v; persist(); } }),
      // Not every item is batch-controlled, and this screen has no way to know which ones are
      // without an extra lookup - left blank and optional here, same as "If new HU, type" above;
      // create_and_submit_goods_receipt itself throws a clear per-row error if an item that
      // needs one was left blank, and the operator can fill this in and resubmit.
      Field({ name: `batch${i}`, kind: "scan", label: _("Batch number (if required)"), placeholder: _("Scan or type the batch"), value: l.batch_no, onInput: (v) => { l.batch_no = v; persist(); }, onCommit: (v) => { l.batch_no = v; persist(); } }),
      Field({ name: `qty${i}`, kind: "qty", label: _("Quantity"), value: l.quantity, unit: l.stock_uom, enterNext: true, onInput: (v) => { l.quantity = v; persist(); } }))));
    return wrap;
  },
  actions() {
    if (!st.lines.length) return null;
    const n = st.lines.filter((l) => l.handling_unit).length;
    return { primary: { label: n ? _("Post receipt ({0} lines)", [n]) : _("Post receipt"), icon: "✓", run: submit } };
  },
};

async function submit() {
  const lines = st.lines.map((l, i) => ({ l, i })).filter(({ l }) => l.handling_unit.trim());
  if (!lines.length) { notify.warn(_("Scan at least one Handling Unit.")); S.focusRequest = "hu0"; update(); return; }
  for (const { l, i } of lines) {
    if (!isNumeric(l.quantity) || parseNum(l.quantity) <= 0) { S.fieldErrors[`qty${i}`] = _("Enter a quantity greater than zero."); feedback.error(); S.focusRequest = `qty${i}`; update(); return; }
    if (round6(parseNum(l.quantity)) > l.remaining) { S.fieldErrors[`qty${i}`] = _("Only {0} {1} remaining on this line.", [fmtQty(l.remaining), l.stock_uom]); feedback.error(); S.focusRequest = `qty${i}`; update(); return; }
  }
  const items = lines.map(({ l }) => ({ inbound_delivery_item: l.inbound_delivery_item, item: l.item, quantity: parseNum(l.quantity), stock_uom: l.stock_uom, handling_unit: l.handling_unit.trim(), stock_type: l.stock_type, hu_type: l.hu_type || undefined, batch_no: (l.batch_no || "").trim() || undefined }));
  const idem = ensureKey(st.form, "GR");
  persist();
  const result = await run(() => api("frappe_wms.api.inbound.create_and_submit_goods_receipt", { inbound_delivery: st.name, items: JSON.stringify(items), idempotency_key: idem }), { label: _("Posting receipt…"), again: submit });
  if (!result) return;
  feedback.done();
  clearDraft(key(st.name));
  const w0 = st.w0; st.name = null;
  finishFlow(w0, "#/tasks/inbound", _("Goods Receipt {0} posted, {1} putaway task(s) created", [result.goods_receipt, (result.warehouse_tasks || []).length]));
}
