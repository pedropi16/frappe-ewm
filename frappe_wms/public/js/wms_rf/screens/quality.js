import { h } from "#wms/ui/dom.js";
import { S, nav, run, notify, update } from "#wms/app.js";
import { api } from "#wms/core/api.js";
import { _ } from "#wms/core/i18n.js";
import { Section, Field, Card, Badge, Loading, KV } from "#wms/ui/kit.js";
import { fmtQty, parseNum, isNumeric, flt } from "#wms/core/util.js";
import { feedback } from "#wms/core/feedback.js";
import { href } from "#wms/core/routes.js";
import { finishFlow, enteredFresh, sectionCrumb } from "#wms/screens/shared.js";
import { listScreen, findRow } from "#wms/screens/lists.js";

export const qualityList = listScreen({
  id: "quality", pattern: "quality", title: _("Quality"), section: "inbound", method: "frappe_wms.api.inventory.list_open_inspections", empty: _("No open inspections."),
  card: (i) => Card({ title: i.product, right: Badge(_(i.from_stock_type), "Normal"), meta: `${i.storage_bin}${i.handling_unit ? " / " + i.handling_unit : ""}`, qty: `${fmtQty(i.quantity)} ${i.stock_uom}`, onClick: () => nav.go(href("quality", i.name)) }),
});

const st = { insp: null, passed: "", failed: "0", w0: 0 };

export const qualityDetail = {
  id: "quality-detail", pattern: "quality/:name",
  title: () => _("Inspection"), crumb: () => sectionCrumb("inbound"), parent: () => "#/quality",
  async enter(ctx) {
    if (enteredFresh(ctx, "quality-detail")) st.w0 = nav.depth;
    const insp = await findRow(qualityList, ctx.params.name, ctx);
    if (!insp) { notify.warn(_("That inspection is no longer open.")); return { redirect: "#/quality" }; }
    if (!st.insp || st.insp.name !== insp.name) { st.insp = insp; st.passed = fmtQty(insp.quantity); st.failed = "0"; }
    update();
  },
  render() {
    const i = st.insp;
    if (!i) return Loading();
    const total = flt(i.quantity);
    return h("div",
      Section({ title: i.product }, KV([[_("Quantity"), `${fmtQty(i.quantity)} ${i.stock_uom}`], [_("From"), _(i.from_stock_type)], [_("Pass →"), _(i.passed_to_stock_type)], [_("Fail →"), _(i.failed_to_stock_type)]])),
      Section({ hint: _("Passed and failed must add up to {0}. Changing one updates the other.", [fmtQty(total)]) },
        Field({ name: "passed", kind: "qty", label: _("Passed quantity"), value: st.passed, unit: i.stock_uom, autofocus: true, enterNext: true,
          onInput: (v) => { st.passed = v; if (isNumeric(v)) { st.failed = fmtQty(Math.max(0, total - parseNum(v))); const el = document.querySelector('[data-fk="failed"]'); if (el) el.value = st.failed; } } }),
        Field({ name: "failed", kind: "qty", label: _("Failed quantity"), value: st.failed, unit: i.stock_uom,
          onInput: (v) => { st.failed = v; if (isNumeric(v)) { st.passed = fmtQty(Math.max(0, total - parseNum(v))); const el = document.querySelector('[data-fk="passed"]'); if (el) el.value = st.passed; } } })));
  },
  actions() { return st.insp ? { primary: { label: _("Complete inspection"), icon: "✓", run: submit } } : null; },
};

async function submit() {
  const i = st.insp;
  if (!isNumeric(st.passed) || !isNumeric(st.failed)) { S.fieldErrors.passed = _("Enter numbers."); feedback.error(); S.focusRequest = "passed"; update(); return; }
  const passed = parseNum(st.passed), failed = parseNum(st.failed);
  if (passed < 0 || failed < 0 || Math.abs(passed + failed - flt(i.quantity)) > 0.0001) { S.fieldErrors.passed = _("Passed + failed must equal {0}.", [fmtQty(i.quantity)]); feedback.error(); S.focusRequest = "passed"; update(); return; }
  // complete_inspection posts with fixed per-inspection ledger keys, so a resend is answered rather than double-posted.
  const ok = await run(() => api("frappe_wms.api.inventory.complete_inspection", { inspection_name: i.name, passed_quantity: passed, failed_quantity: failed }), { label: _("Completing…"), again: submit });
  if (ok === undefined) return;
  feedback.done();
  const w0 = st.w0; const name = i.name; st.insp = null;
  finishFlow(w0, "#/quality", _("Inspection {0} completed", [name]));
}
