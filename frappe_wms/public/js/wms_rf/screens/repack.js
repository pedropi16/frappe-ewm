import { h } from "#wms/ui/dom.js";
import { S, nav, run, load, notify, update } from "#wms/app.js";
import { api } from "#wms/core/api.js";
import { _ } from "#wms/core/i18n.js";
import { Section, Field, Card, Badge, Btn, Loading } from "#wms/ui/kit.js";
import { fmtQty, parseNum, isNumeric, flt, uid } from "#wms/core/util.js";
import { feedback } from "#wms/core/feedback.js";
import { href } from "#wms/core/routes.js";
import { saveDraft, loadDraft, clearDraft, ensureKey, finishFlow, enteredFresh, sectionCrumb } from "#wms/screens/shared.js";
import { huTypes } from "#wms/screens/hu.js";

const st = { code: "" };
export const repackScan = {
  id: "repack", pattern: "repack",
  title: () => _("Repack"), crumb: () => sectionCrumb("internal"), parent: () => "#/s/internal",
  enter() { st.code = ""; },
  render() {
    return h("div", Section({}, Field({ name: "hu", kind: "scan", label: _("Scan the Handling Unit to repack"), placeholder: _("Scan HU barcode"), value: st.code, autofocus: true, onInput: (v) => { st.code = v; },
      onCommit: async (v) => {
        let r; try { r = await api("frappe_wms.api.scanner.resolve_scan", { code: v }, { read: true }); } catch (e) { return e.message; }
        const hu = (r.matches || []).find((m) => m.type === "hu");
        if (!hu) return _("{0} is not a Handling Unit.", [v]);
        nav.go(href("repack", hu.name));
      } })));
  },
};

const d = { hu: null, detail: null, lines: [], dest: "", newType: "", types: [], form: { idem: "" }, w0: 0 };
const key = (n) => `repack:${n}`;
const persist = () => saveDraft(key(d.hu), { lines: d.lines, dest: d.dest, idem: d.form.idem }, { label: `${_("Repack")} · ${d.hu}`, route: href("repack", d.hu) });

export const repackDetail = {
  id: "repack-detail", pattern: "repack/:hu",
  title: () => _("Repack"), crumb: () => sectionCrumb("internal"), parent: () => "#/repack",
  async enter(ctx) {
    const hu = ctx.params.hu;
    if (enteredFresh(ctx, "repack-detail")) d.w0 = nav.depth;
    d.types = await huTypes();
    const detail = await run(() => api("frappe_wms.api.scanner.hu_overview", { hu_number: hu }, { read: true }), { busy: false, exclusive: false });
    if (!detail) return { redirect: "#/repack" };
    d.hu = hu; d.detail = detail;
    const fresh = [
      { kind: "hu", label: _("This entire Handling Unit"), handling_unit: detail.handling_unit.name, selected: false },
      ...detail.children.map((c) => ({ kind: "hu", label: _("Nested HU: {0} ({1})", [c.name, c.hu_type]), handling_unit: c.name, selected: false })),
      ...detail.stock.map((s) => ({ kind: "item", label: s.product, product: s.product, batch_no: s.batch_no || "", serial_no: s.serial_no || "", stock_type: s.stock_type, stock_uom: s.stock_uom, available: s.quantity, quantity: "0" })),
    ];
    const saved = loadDraft(key(hu));
    d.lines = saved && saved.lines.length === fresh.length ? saved.lines : fresh;
    d.dest = saved ? saved.dest : ""; d.form.idem = (saved && saved.idem) || "";
    update();
  },
  render() {
    if (!d.detail) return Loading();
    const wrap = h("div", Section({ title: `${d.detail.handling_unit.hu_number} · ${d.detail.handling_unit.current_bin || "-"}`, hint: _("Pick whole Handling Units to move and/or enter quantities of individual items. Item repack needs the destination HU to already be in this same bin.") }));
    const hus = d.lines.map((l, i) => ({ l, i })).filter(({ l }) => l.kind === "hu");
    if (hus.length) wrap.append(Section({ title: _("Handling Units") }, hus.map(({ l, i }) => Card({ title: l.label, dim: !l.selected, right: l.selected ? Badge(_("Selected"), "Selected") : null, onClick: () => { l.selected = !l.selected; persist(); update(); } }))));
    const items = d.lines.map((l, i) => ({ l, i })).filter(({ l }) => l.kind === "item");
    if (items.length) wrap.append(Section({ title: _("Items") }, items.map(({ l, i }) => h("div.line-row",
      h("div.line-head", l.product), h("div.line-sub", `${l.batch_no || ""} ${_(l.stock_type)} · ${_("available {0} {1}", [fmtQty(l.available), l.stock_uom || ""])}`),
      Field({ name: `rq${i}`, kind: "qty", label: _("Quantity to repack"), value: l.quantity, unit: l.stock_uom, enterNext: true, onInput: (v) => { l.quantity = v; persist(); } })))));
    wrap.append(Section({ title: _("Destination") },
      Field({ name: "dest", kind: "scan", label: _("Destination Handling Unit"), placeholder: _("Scan an existing HU barcode"), value: d.dest, onInput: (v) => { d.dest = v; persist(); }, onCommit: (v) => { d.dest = v; persist(); } }),
      Field({ name: "newtype", kind: "select", label: _("...or create a new one"), value: d.newType, options: [{ value: "", label: _("Select HU type") }, ...d.types.map((t) => ({ value: t.name, label: t.name }))], onInput: (v) => { d.newType = v; } }),
      Btn({ label: _("Create as destination"), onClick: createDest })));
    return wrap;
  },
  actions: () => (d.detail ? { primary: { label: _("Repack"), icon: "✓", run: submit } } : null),
};

async function createDest() {
  if (!d.newType) { S.fieldErrors.newtype = _("Pick a Handling Unit type."); feedback.error(); S.focusRequest = "newtype"; update(); return; }
  const r = await run(() => api("frappe_wms.api.handling_unit.create_handling_unit", { hu_type: d.newType, storage_bin: d.detail.handling_unit.current_bin }), { label: _("Creating…") });
  if (!r) return;
  d.dest = r.name; persist(); notify.ok(_("Created {0} as the destination", [r.name])); update();
}

async function submit() {
  const hu = d.detail.handling_unit;
  const dest = d.dest.trim();
  const huLines = d.lines.filter((l) => l.kind === "hu" && l.selected);
  for (let i = 0; i < d.lines.length; i++) { const l = d.lines[i]; if (l.kind === "item" && String(l.quantity).trim() !== "" && !isNumeric(l.quantity)) { S.fieldErrors[`rq${i}`] = _("Enter a number."); feedback.error(); S.focusRequest = `rq${i}`; update(); return; } }
  const itemLines = d.lines.filter((l) => l.kind === "item" && parseNum(l.quantity) > 0);
  if (!huLines.length && !itemLines.length) { notify.warn(_("Select at least one Handling Unit or enter a quantity to repack.")); return; }
  const over = d.lines.findIndex((l) => l.kind === "item" && parseNum(l.quantity) > flt(l.available));
  if (over >= 0) { S.fieldErrors[`rq${over}`] = _("Only {0} available.", [fmtQty(d.lines[over].available)]); feedback.error(); S.focusRequest = `rq${over}`; update(); return; }
  if (!dest) { S.fieldErrors.dest = _("Scan the destination Handling Unit (or create a new one below)."); feedback.error(); S.focusRequest = "dest"; update(); return; }
  const idem = ensureKey(d.form, "RF-REPACK"); persist();
  const ok = await run(async () => {
    for (const line of huLines) {
      const child = line.handling_unit === hu.name ? hu : await api("frappe_wms.api.handling_unit.handling_unit_detail", { hu_name: line.handling_unit }, { read: true });
      if (child.parent_hu) await api("frappe_wms.api.handling_unit.unnest_handling_unit", { hu_name: line.handling_unit });
      await api("frappe_wms.api.handling_unit.nest_handling_unit", { hu_name: line.handling_unit, parent_hu: dest });
    }
    if (itemLines.length) {
      const items = itemLines.map((l) => ({ item: l.product, batch_no: l.batch_no || undefined, serial_no: l.serial_no || undefined, stock_type: l.stock_type, stock_uom: l.stock_uom, quantity: parseNum(l.quantity) }));
      await api("frappe_wms.api.scanner.repack", { source_hu: hu.name, destination_hu: dest, items: JSON.stringify(items), idempotency_key: idem });
    }
    return true;
  }, { label: _("Repacking…"), again: submit });
  if (!ok) return;
  feedback.done(); clearDraft(key(d.hu));
  const w0 = d.w0; d.detail = null;
  finishFlow(w0, "#/hu", _("Repacked into {0}", [dest]));
}
