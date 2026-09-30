import { h } from "#wms/ui/dom.js";
import { S, nav, run, load, notify, update } from "#wms/app.js";
import { api } from "#wms/core/api.js";
import { _ } from "#wms/core/i18n.js";
import { Section, Field, Card, StatusBadge, Empty, Loading, Btn } from "#wms/ui/kit.js";
import { parseGS1, gtinVariants } from "#wms/core/gs1.js";
import { fmtQty, flt, parseNum, isNumeric, round6 } from "#wms/core/util.js";
import { feedback } from "#wms/core/feedback.js";
import { href } from "#wms/core/routes.js";
import { saveDraft, loadDraft, clearDraft, ensureKey, finishFlow, enteredFresh, sectionCrumb, sectionHash } from "#wms/screens/shared.js";
import { listScreen } from "#wms/screens/lists.js";

export const receiveList = listScreen({
  id: "receive", pattern: "receive", title: _("Receive"), section: "inbound", method: "frappe_wms.api.inbound.list_open_inbound_deliveries", empty: _("No open inbound deliveries."),
  card: (d) => Card({ title: d.inbound_delivery_number || d.name, right: StatusBadge(d.status), meta: [d.external_reference ? `${d.supplier || ""} · ${d.external_reference}` : d.supplier || "", h("br"), d.receiving_bin || ""], onClick: () => nav.go(href("receive", d.name)) }),
});

// Scan-first receiving, the SAP RF way: the operator scans what is in front of them - a product
// barcode, a GS1 pallet label, or an HU - and the screen finds the delivery line, instead of a
// form of every line to fill in. Lines are collected into one Goods Receipt and posted together.
const st = { name: null, wl: null, loading: false, active: null, entry: null, pending: [], lastHu: "", lastHuType: "", code: "", idem: "", w0: 0 };
const key = (n) => `receive:${n}`;
const persist = () => saveDraft(key(st.name), { v: 2, active: st.active, entry: st.entry, pending: st.pending, lastHu: st.lastHu, lastHuType: st.lastHuType, idem: st.idem },
  { label: `${_("Receive")} · ${st.wl ? st.wl.inbound_delivery_number || st.name : st.name}`, route: href("receive", st.name) });

const lineOf = (id) => (st.wl ? st.wl.lines.find((l) => l.inbound_delivery_item === id) : null);
const pendingQty = (id) => round6(st.pending.filter((p) => p.inbound_delivery_item === id).reduce((a, p) => a + flt(p.quantity), 0));
const openQty = (l) => round6(flt(l.remaining) - pendingQty(l.inbound_delivery_item));

function newEntry(l) {
  return { hu: st.lastHu || "", huType: st.lastHuType || (st.wl && st.wl.default_hu_type) || "", batch: "", serials: [], qty: fmtQty(openQty(l)), expiry: "", uom: l.stock_uom };
}

function select(l, gs) {
  if (st.active !== l.inbound_delivery_item || !st.entry) { st.active = l.inbound_delivery_item; st.entry = newEntry(l); }
  if (gs) {
    if (gs.batch) st.entry.batch = gs.batch;
    if (gs.expiry) st.entry.expiry = gs.expiry;
    if (gs.sscc) st.entry.hu = gs.sscc;
    if (gs.count && !l.serial_required) st.entry.qty = fmtQty(Math.min(gs.count, openQty(l)));
    if (gs.serial && l.serial_required) addSerial(l, gs.serial);
  }
  persist();
}

// The first field of the line being received that still needs a scan or an entry.
function nextField(l) {
  const e = st.entry;
  if (!e.hu) return "hu";
  if (l.batch_required && !e.batch) return "batch";
  return l.serial_required ? "serial" : "qty";
}

function matchLine(code) {
  const variants = gtinVariants(code);
  const hits = st.wl.lines.filter((l) => l.item === code || (l.barcodes || []).some((b) => variants.includes(b) || gtinVariants(b).includes(code)));
  return hits.find((l) => openQty(l) > 0) || hits[0] || null;
}

function addSerial(l, serial) {
  const e = st.entry;
  serial = String(serial).trim();
  if (!serial) return null;
  const taken = e.serials.includes(serial) || st.pending.some((p) => p.item === l.item && p.serial_no === serial);
  if (taken) return _("Serial {0} is already scanned.", [serial]);
  if (e.serials.length + 1 > openQty(l)) return _("Only {0} left to receive on this line.", [fmtQty(openQty(l))]);
  e.serials.push(serial);
  return null;
}

// The one scan field: product / GS1 label / HU.
async function onScan(value, via, raw) {
  const gs = parseGS1(raw) || parseGS1(value);
  st.code = "";
  if (gs && (gs.gtin || gs.content_gtin)) {
    const l = matchLine(gs.gtin || gs.content_gtin);
    if (!l) return _("GTIN {0} is not on this delivery.", [gs.gtin || gs.content_gtin]);
    select(l, gs); update(); return { focus: nextField(l) };
  }
  if (gs && gs.sscc) {
    if (!st.entry) return _("Scan the product first, then the pallet label.");
    st.entry.hu = gs.sscc; persist(); update(); return { focus: nextField(lineOf(st.active)) };
  }
  const l = matchLine(value);
  if (l) {
    if (openQty(l) <= 0) return _("{0} is already fully collected in this receipt.", [l.item]);
    select(l); update(); return { focus: nextField(l) };
  }
  let matches = [];
  try { matches = (await api("frappe_wms.api.scanner.resolve_scan", { code: value }, { read: true, timeoutMs: 6000 })).matches || []; } catch (e) { /* offline */ }
  const item = matches.find((m) => m.type === "item");
  if (item) { const l2 = matchLine(item.name); if (l2) { select(l2); update(); return { focus: nextField(l2) }; } return _("{0} is not on this delivery.", [item.name]); }
  if (matches.some((m) => m.type === "bin")) return _("{0} is a storage bin. Scan a product or an HU label.", [value]);
  // An HU label (known or brand new): it belongs to the line being received.
  if (!st.entry) return _("{0} is not a product on this delivery. Scan the product first.", [value]);
  st.entry.hu = value; persist(); update();
  return { focus: nextField(lineOf(st.active)) };
}

export const receiveDetail = {
  id: "receive-detail", pattern: "receive/:name",
  title: () => _("Receive"), crumb: () => sectionCrumb("inbound"), parent: () => "#/receive",
  async enter(ctx) {
    const name = ctx.params.name;
    if (enteredFresh(ctx, "receive-detail")) st.w0 = nav.depth;
    if (st.name !== name) Object.assign(st, { name, wl: null, active: null, entry: null, pending: [], lastHu: "", lastHuType: "", code: "", idem: "" });
    st.loading = !st.wl; update();
    const wl = await load(() => api("frappe_wms.api.inbound.receiving_worklist", { inbound_delivery: name }, { read: true }));
    st.loading = false;
    if (!wl) { if (!st.wl) return { redirect: "#/receive" }; update(); return; }
    st.wl = wl;
    const saved = loadDraft(key(name));
    if (saved && saved.v === 2) {
      // A draft survives only for lines that are still open, and never beyond what is still open on them.
      st.pending = (saved.pending || []).filter((p) => lineOf(p.inbound_delivery_item));
      for (const l of wl.lines) { while (pendingQty(l.inbound_delivery_item) - flt(l.remaining) > 1e-6) st.pending.splice(st.pending.findLastIndex((p) => p.inbound_delivery_item === l.inbound_delivery_item), 1); }
      st.active = lineOf(saved.active) ? saved.active : null;
      st.entry = st.active ? saved.entry : null;
      Object.assign(st, { lastHu: saved.lastHu || "", lastHuType: saved.lastHuType || "", idem: saved.idem || "" });
    }
    update();
  },
  refresh: () => nav.replace(location.hash),
  render() {
    if (st.loading && !st.wl) return Loading();
    const wl = st.wl;
    const wrap = h("div");
    const open = wl.lines.filter((l) => openQty(l) > 0);
    wrap.append(Section({ title: `${wl.inbound_delivery_number || wl.name} · ${wl.supplier || ""}`, hint: [wl.external_reference, wl.receiving_bin].filter(Boolean).join(" · ") },
      Field({ name: "code", kind: "scan", label: _("Scan product, GS1 label or HU"), placeholder: _("Scan barcode"), value: st.code, autofocus: !st.entry,
        onInput: (v) => { st.code = v; }, onCommit: onScan })));
    const l = st.active && lineOf(st.active);
    if (l && st.entry) wrap.append(entryView(l));
    if (st.pending.length) wrap.append(pendingView());
    if (!wl.lines.length) { wrap.append(Empty(_("Nothing left to receive on this delivery."), "✅")); return wrap; }
    wrap.append(Section({ title: _("Lines to receive ({0})", [open.length]), hint: _("Tap a line if its product has no barcode.") },
      wl.lines.map((x) => Card({ title: x.item, dim: openQty(x) <= 0 || x.inbound_delivery_item === st.active,
        meta: [x.item_name && x.item_name !== x.item ? x.item_name : "", x.batch_required ? ` · ${_("batch")}` : "", x.serial_required ? ` · ${_("serials")}` : ""],
        qty: `${fmtQty(openQty(x))} / ${fmtQty(x.remaining)} ${x.stock_uom || ""}`,
        onClick: openQty(x) > 0 ? () => { select(x); S.focusRequest = "hu"; update(); } : null }))));
    return wrap;
  },
  actions() {
    if (!st.wl) return null;
    if (st.entry) return { primary: { label: _("Add to receipt"), icon: "+", run: addEntry }, secondary: st.pending.length ? [{ label: _("Post ({0})", [st.pending.length]), run: submit }] : [] };
    if (st.pending.length) return { primary: { label: _("Post receipt ({0} rows)", [st.pending.length]), icon: "✓", run: submit } };
    return null;
  },
};

function entryView(l) {
  const e = st.entry;
  const types = [{ value: "", label: "—" }, ...(st.wl.hu_types || []).map((t) => ({ value: t.name, label: t.name }))];
  const box = Section({ title: `${l.item}${l.item_name && l.item_name !== l.item ? " · " + l.item_name : ""}`, hint: _("Open {0} {1} · {2}", [fmtQty(openQty(l)), l.stock_uom, _(l.stock_type)]) },
    Field({ name: "hu", kind: "scan", label: _("Handling Unit"), placeholder: _("Scan HU / pallet label"), value: e.hu,
      hint: e.hu && e.hu === st.lastHu ? _("Same HU as the previous line - scan another to change it.") : null,
      onInput: (v) => { e.hu = v; persist(); }, onCommit: (v) => { e.hu = v; persist(); } }),  // a pallet label arrives here as its SSCC (ui/kit.js)
    Field({ name: "hutype", kind: "select", label: _("If new HU, type"), value: e.huType, options: types, onInput: (v) => { e.huType = v; persist(); } }));
  if (l.batch_required) {
    box.append(Field({ name: "batch", kind: "scan", gs1: "batch", label: _("Batch"), placeholder: _("Scan the batch"), value: e.batch,
      hint: e.expiry ? _("Expiry {0} (from the label)", [e.expiry]) : null,
      onInput: (v) => { e.batch = v; persist(); }, onCommit: (v, via, raw) => { const gs = parseGS1(raw); if (gs && gs.batch) { e.batch = gs.batch; if (gs.expiry) e.expiry = gs.expiry; } else e.batch = v; persist(); } }));
  }
  if (l.serial_required) {
    box.append(Field({ name: "serial", kind: "scan", gs1: "serial", label: _("Serial numbers ({0} of {1})", [e.serials.length, fmtQty(openQty(l))]), placeholder: _("Scan each serial"), value: "",
      onCommit: (v, via, raw) => {
        const gs = parseGS1(raw);
        const err = addSerial(l, gs && gs.serial ? gs.serial : v);
        if (err) return err;
        feedback.ok(); persist(); S.focusRequest = "serial"; update(); return false;
      } }),
      e.serials.length ? h("div.chips", e.serials.map((sn, i) => h("button.chip", { type: "button", title: _("Remove"), onclick: () => { e.serials.splice(i, 1); persist(); update(); } }, `${sn} ✕`))) : null);
  } else {
    const uoms = l.uoms || [{ uom: l.stock_uom, factor: 1 }];
    const unit = uoms.find((u) => u.uom === (e.uom || l.stock_uom)) || uoms[0];
    if (uoms.length > 1) {
      box.append(Field({ name: "uom", kind: "select", label: _("Counting unit"), value: unit.uom,
        options: uoms.map((u) => ({ value: u.uom, label: u.factor === 1 ? u.uom : `${u.uom} (${fmtQty(u.factor)} ${l.stock_uom})` })),
        onInput: (v) => { const was = (uoms.find((u) => u.uom === e.uom) || uoms[0]).factor; const now = (uoms.find((u) => u.uom === v) || uoms[0]).factor;
          if (isNumeric(e.qty)) e.qty = fmtQty(round6(parseNum(e.qty) * was / now)); e.uom = v; persist(); update(); } }));
    }
    box.append(Field({ name: "qty", kind: "qty", label: _("Quantity"), value: e.qty, unit: unit.uom, onInput: (v) => { e.qty = v; persist(); update(); }, onCommit: () => addEntry(),
      hint: unit.factor !== 1 && isNumeric(e.qty) ? _("= {0} {1}", [fmtQty(round6(parseNum(e.qty) * unit.factor)), l.stock_uom]) : null }));
  }
  box.append(Btn({ label: _("Cancel this line"), small: true, onClick: () => { st.active = null; st.entry = null; persist(); S.focusRequest = "code"; update(); } }));
  return box;
}

function pendingView() {
  return Section({ title: _("In this receipt ({0} rows)", [st.pending.length]) },
    st.pending.map((p, i) => Card({ title: `${p.item} · ${fmtQty(p.quantity)} ${p.stock_uom || ""}`,
      meta: [`→ ${p.handling_unit}`, p.batch_no ? ` · ${_("batch")} ${p.batch_no}` : "", p.serial_no ? ` · SN ${p.serial_no}` : ""],
      right: h("button.icon-btn", { type: "button", "aria-label": _("Remove"), onclick: () => { st.pending.splice(i, 1); persist(); update(); } }, "✕") })));
}

function fail(name, message) { S.fieldErrors[name] = message; feedback.error(); S.focusRequest = name; update(); return false; }

function addEntry() {
  const l = st.active && lineOf(st.active);
  const e = st.entry;
  if (!l || !e) return;
  const hu = (e.hu || "").trim();
  if (!hu) return fail("hu", _("Scan the Handling Unit (pallet or case label)."));
  if (l.batch_required && !(e.batch || "").trim()) return fail("batch", _("This product needs a batch."));
  const base = { inbound_delivery_item: l.inbound_delivery_item, item: l.item, stock_uom: l.stock_uom, stock_type: l.stock_type, handling_unit: hu,
    hu_type: e.huType || undefined, batch_no: (e.batch || "").trim() || undefined };
  if (l.serial_required) {
    if (!e.serials.length) return fail("serial", _("Scan at least one serial number."));
    e.serials.forEach((sn) => st.pending.push({ ...base, quantity: 1, serial_no: sn }));
  } else {
    if (!isNumeric(e.qty) || parseNum(e.qty) <= 0) return fail("qty", _("Enter a quantity greater than zero."));
    const factor = ((l.uoms || []).find((u) => u.uom === e.uom) || { factor: 1 }).factor;
    const stockQty = round6(parseNum(e.qty) * factor);  // the receipt always posts in the stock UOM
    if (stockQty > openQty(l)) return fail("qty", _("Only {0} {1} left on this line.", [fmtQty(openQty(l)), l.stock_uom]));
    st.pending.push({ ...base, quantity: stockQty });
  }
  st.lastHu = hu; st.lastHuType = e.huType || st.lastHuType;
  st.active = null; st.entry = null;
  persist(); feedback.ok();
  notify.ok(_("Added {0} to the receipt", [l.item]), { ttl: 1800 });
  S.focusRequest = "code"; update();
}

async function submit() {
  if (st.entry && st.active) { const before = st.pending.length; addEntry(); if (st.pending.length === before) return; }
  if (!st.pending.length) { notify.warn(_("Scan a product and add it to the receipt first.")); return; }
  const idem = st.idem || (st.idem = ensureKey(st, "GR"));
  persist();
  const result = await run(() => api("frappe_wms.api.inbound.create_and_submit_goods_receipt", { inbound_delivery: st.name, items: JSON.stringify(st.pending), idempotency_key: idem }), { label: _("Posting receipt…"), again: submit });
  if (!result) return;
  feedback.done();
  clearDraft(key(st.name));
  const w0 = st.w0; st.name = null; st.wl = null;
  finishFlow(w0, "#/tasks/inbound", _("Goods Receipt {0} posted, {1} putaway task(s) created", [result.goods_receipt, (result.warehouse_tasks || []).length]));
}
