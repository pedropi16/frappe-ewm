import { h } from "#wms/ui/dom.js";
import { S, nav, run, notify, update } from "#wms/app.js";
import { api } from "#wms/core/api.js";
import { _ } from "#wms/core/i18n.js";
import { Section, Field, Btn, Loading, Hint } from "#wms/ui/kit.js";
import { fmtQty, parseNum, isNumeric } from "#wms/core/util.js";
import { feedback } from "#wms/core/feedback.js";
import { href } from "#wms/core/routes.js";
import { saveDraft, loadDraft, clearDraft, ensureKey, finishFlow, enteredFresh, sectionCrumb } from "#wms/screens/shared.js";

// Break a Handling Unit down: scan it, split its stock across destinations.
const st = { code: "" };
export const deconScan = {
  id: "decon", pattern: "decon",
  title: () => _("Deconsolidate"), crumb: () => sectionCrumb("inbound"), parent: () => "#/s/inbound",
  enter() { st.code = ""; },
  render() {
    return h("div", Section({ hint: _("Scan the Handling Unit to break down.") },
      Field({ name: "hu", kind: "scan", label: _("Handling Unit"), placeholder: _("Scan HU barcode"), value: st.code, autofocus: true, onInput: (v) => { st.code = v; },
        onCommit: async (v) => {
          let r;
          try { r = await api("frappe_wms.api.scanner.resolve_scan", { code: v }, { read: true }); } catch (e) { return e.message; }
          const hu = (r.matches || []).find((m) => m.type === "hu");
          if (!hu) return _("{0} is not a Handling Unit.", [v]);
          nav.go(href("decon", hu.name));
        } })));
  },
  actions: () => ({ primary: { label: _("Load Handling Unit"), run: () => { const rec = S.fields[0]; if (rec && rec.input.value.trim()) rec.spec.onCommit(rec.input.value.trim()).then((e) => { if (e) { S.fieldErrors.hu = e; feedback.error(); update(); } }); } } }),
};

const d = { hu: null, detail: null, lines: [], form: { idem: "" }, w0: 0 };
const key = (n) => `decon:${n}`;
const persist = () => saveDraft(key(d.hu), { lines: d.lines, idem: d.form.idem }, { label: `${_("Deconsolidate")} · ${d.hu}`, route: href("decon", d.hu) });

export const deconDetail = {
  id: "decon-detail", pattern: "decon/:hu",
  title: () => _("Deconsolidate"), crumb: () => sectionCrumb("inbound"), parent: () => "#/decon",
  async enter(ctx) {
    const hu = ctx.params.hu;
    if (enteredFresh(ctx, "decon-detail")) d.w0 = nav.depth;
    const detail = await run(() => api("frappe_wms.api.scanner.hu_overview", { hu_number: hu }, { read: true }), { busy: false, exclusive: false });
    if (!detail) return { redirect: "#/decon" };
    if (!detail.stock || !detail.stock.length) { notify.warn(_("This Handling Unit has no stock to deconsolidate.")); return { redirect: "#/decon" }; }
    d.hu = hu; d.detail = detail;
    const saved = loadDraft(key(hu));
    d.lines = saved ? saved.lines : detail.stock.map((r) => ({ product: r.product, batch_no: r.batch_no || "", serial_no: r.serial_no || "", stock_type: r.stock_type, stock_uom: r.stock_uom, available: r.quantity, quantity: fmtQty(r.quantity), destination_bin: "", destination_hu: "" }));
    d.form.idem = (saved && saved.idem) || "";
    update();
  },
  render() {
    if (!d.detail) return Loading();
    const wrap = h("div", Section({ title: `${d.detail.handling_unit.hu_number} · ${d.detail.handling_unit.current_bin || "-"}`, hint: _("Split each line across one or more destinations. Use Split to divide one product across several.") }));
    d.lines.forEach((l, i) => wrap.append(Section({},
      h("div.line-head", l.product), h("div.line-sub", `${l.batch_no || ""} ${_(l.stock_type)} · ${_("available on HU: {0} {1}", [fmtQty(l.available), l.stock_uom || ""])}`),
      Field({ name: `q${i}`, kind: "qty", label: _("Quantity"), value: l.quantity, unit: l.stock_uom, autofocus: i === 0, enterNext: true, onInput: (v) => { l.quantity = v; persist(); } }),
      Field({ name: `b${i}`, kind: "scan", label: _("Destination bin"), placeholder: _("Scan bin barcode"), value: l.destination_bin, onInput: (v) => { l.destination_bin = v; persist(); }, onCommit: (v) => { l.destination_bin = v; persist(); } }),
      Field({ name: `h${i}`, kind: "scan", label: _("Destination HU (optional)"), placeholder: _("Scan HU barcode"), value: l.destination_hu, onInput: (v) => { l.destination_hu = v; persist(); }, onCommit: (v) => { l.destination_hu = v; persist(); } }),
      h("div.actions-row", Btn({ label: `+ ${_("Split")}`, small: true, onClick: () => { d.lines.splice(i + 1, 0, { ...l, quantity: "0", destination_bin: "", destination_hu: "" }); persist(); update(); } }),
        d.lines.length > 1 ? Btn({ label: _("Remove"), kind: "danger", small: true, onClick: () => { d.lines.splice(i, 1); persist(); update(); } }) : null))));
    return wrap;
  },
  actions: () => (d.detail ? { primary: { label: _("Create tasks"), icon: "✓", run: submit } } : null),
};

async function submit() {
  for (let i = 0; i < d.lines.length; i++) {
    const l = d.lines[i];
    if (String(l.quantity).trim() !== "" && !isNumeric(l.quantity)) { S.fieldErrors[`q${i}`] = _("Enter a number."); feedback.error(); S.focusRequest = `q${i}`; update(); return; }
  }
  const lines = d.lines.filter((l) => parseNum(l.quantity) > 0);
  if (!lines.length) { notify.warn(_("Enter a quantity for at least one line.")); return; }
  const bad = d.lines.findIndex((l) => parseNum(l.quantity) > 0 && !l.destination_bin.trim() && !l.destination_hu.trim());
  if (bad >= 0) { S.fieldErrors[`b${bad}`] = _("Every line needs a destination bin or Handling Unit."); feedback.error(); S.focusRequest = `b${bad}`; update(); return; }
  const payload = lines.map((l) => ({ product: l.product, batch_no: l.batch_no || undefined, serial_no: l.serial_no || undefined, stock_type: l.stock_type, quantity: parseNum(l.quantity), destination_bin: l.destination_bin.trim() || undefined, destination_hu: l.destination_hu.trim() || undefined }));
  const idem = ensureKey(d.form, "DC"); persist();
  const created = await run(() => api("frappe_wms.api.deconsolidation.create_deconsolidation_tasks", { source_hu: d.detail.handling_unit.name, lines: JSON.stringify(payload), idempotency_key: idem }), { label: _("Creating tasks…"), again: submit });
  if (!created) return;
  feedback.done(); clearDraft(key(d.hu));
  const w0 = d.w0; d.detail = null;
  finishFlow(w0, "#/tasks/inbound", _("{0} deconsolidation task(s) created", [created.length]));
}
