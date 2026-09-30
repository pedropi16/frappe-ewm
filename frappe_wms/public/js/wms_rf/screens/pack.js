import { h } from "#wms/ui/dom.js";
import { S, run, load, notify, update } from "#wms/app.js";
import { api } from "#wms/core/api.js";
import { _ } from "#wms/core/i18n.js";
import { Section, Field, Card, Badge, Btn, Empty, Loading, Hint } from "#wms/ui/kit.js";
import { fmtQty, parseNum, isNumeric, round6 } from "#wms/core/util.js";
import { feedback } from "#wms/core/feedback.js";
import { parseGS1, gtinVariants } from "#wms/core/gs1.js";
import { refreshSession, sectionCrumb, sectionHash, ensureKey } from "#wms/screens/shared.js";

// RF packing at a work center (SAP EWM /SCWM/PACK on a handheld): log on to the packing table,
// then scan source HU -> product -> quantity -> destination HU and the pack posts; Close HU asks
// for the weight. Same backend as the desk Packing Station (api/packing_station.py).
const API = "frappe_wms.api.packing_station";
const st = { mode: "product", f: { src: "", prod: "", qty: "", uom: "", dst: "", dlv: "", hu: "", into: "", close: "", weight: "", type: "", idem: "" },
  data: null, centers: null, orders: [], loading: false };

const wc = () => (S.resource && S.resource.current_work_center) || null;

async function refresh() {
  st.loading = true; update();
  await load(refreshSession);
  if (wc()) {
    const [data, orders] = await Promise.all([
      load(() => api(`${API}.station_overview`, { work_center: wc() }, { read: true })),
      load(() => api("frappe_wms.api.scanner.list_open_packing_orders", {}, { read: true })),
    ]);
    if (data) st.data = data;
    st.orders = (orders || []).filter((o) => data && o.work_center_bin === data.work_center.bin);
  } else if (S.resource && !st.centers) {
    st.centers = await load(() => api("frappe_wms.api.resource.list_available_work_centers", { warehouse: S.resource.warehouse }, { read: true })) || [];
  }
  st.loading = false; update();
}

const allNodes = () => { const out = []; const walk = (ns) => ns.forEach((n) => { out.push(n); walk(n.children || []); }); walk((st.data && st.data.handling_units) || []); return out; };
const node = (name) => allNodes().find((n) => n.name === name || n.hu_number === name) || null;
// Deliveries are worked out for top-level HUs; anything nested inherits its top HU's.
const topOf = (name) => ((st.data && st.data.handling_units) || []).find((t) => { const has = (n) => n.name === name || (n.children || []).some(has); return has(t); });
const deliveriesOf = (name) => { const t = topOf(name); return t ? t.deliveries || [] : []; };

async function productFrom(value, raw) {
  const gs = parseGS1(raw) || parseGS1(value);
  const src = node(st.f.src);
  const onHand = src ? src.stock || [] : (st.data ? st.data.loose_stock : []);
  const codes = gs && gs.gtin ? gtinVariants(gs.gtin) : [value];
  let hit = onHand.find((s) => codes.includes(s.product));
  if (!hit) {
    for (const code of codes) {
      let r = null; try { r = await api("frappe_wms.api.scanner.resolve_scan", { code }, { read: true, timeoutMs: 6000 }); } catch (e) { /* offline */ }
      const item = r && (r.matches || []).find((m) => m.type === "item");
      if (item) { hit = onHand.find((s) => s.product === item.name); if (hit) break; return _("{0} is not in {1}.", [item.name, st.f.src || _("the loose stock")]); }
    }
  }
  if (!hit) return _("{0} is not a product in {1}.", [value, st.f.src || _("the loose stock")]);
  st.f.prod = hit.product; st.f.uom = hit.stock_uom;
  if (!st.f.qty) st.f.qty = fmtQty(gs && gs.count ? Math.min(gs.count, hit.quantity) : hit.quantity);
}

// The product's counting units (stock unit first, then the Item's UOM conversions): packing a case
// of 12 is typed as 1 case and posted as 12.
const unitsFor = (product) => ((st.data && st.data.units) || {})[product] || [];
const unitFor = (f) => unitsFor(f.prod).find((u) => u.uom === f.uom) || unitsFor(f.prod)[0] || { uom: "", factor: 1 };
function unitFields(f) {
  const units = unitsFor(f.prod), unit = unitFor(f);
  return [
    units.length > 1 ? Field({ name: "uom", kind: "select", label: _("Counting unit"), value: unit.uom,
      options: units.map((u) => ({ value: u.uom, label: u.factor === 1 ? u.uom : `${u.uom} (${fmtQty(u.factor)} ${units[0].uom})` })),
      onInput: (v) => { const now = (units.find((u) => u.uom === v) || units[0]).factor;
        if (isNumeric(f.qty)) f.qty = fmtQty(round6(parseNum(f.qty) * unit.factor / now)); f.uom = v; update(); } }) : null,
    Field({ name: "qty", kind: "qty", label: _("Quantity"), value: f.qty, unit: unit.uom || undefined, enterNext: true, onInput: (v) => { f.qty = v; if (unit.factor !== 1) update(); },
      hint: unit.factor !== 1 && isNumeric(f.qty) ? _("= {0} {1}", [fmtQty(round6(parseNum(f.qty) * unit.factor)), units[0].uom]) : null }),
  ];
}

function scanHu(key, mustExist = true) {
  return (value) => {
    const n = node(value);
    if (mustExist && !n) return _("{0} is not on this packing table.", [value]);
    st.f[key] = n ? n.name : value;
  };
}

export default {
  id: "pack", pattern: "pack",
  title: () => _("Pack"), crumb: () => sectionCrumb("outbound"), parent: () => sectionHash("outbound"),
  enter: () => refresh(),
  refresh: () => refresh(),
  render() {
    const wrap = h("div");
    if (st.loading && !st.data && !st.centers) return Loading();
    if (!S.resource) { wrap.append(Empty(_("Your user has no active WMS Resource."))); return wrap; }
    if (!wc()) {
      wrap.append(Section({ title: _("Log on to a packing station"), hint: _("Packing happens at a work center - pick the table you are standing at.") },
        (st.centers || []).length ? st.centers.map((c) => Btn({ label: `${c.work_center_name || c.work_center_code} · ${c.bin}`, onClick: () => logOn(c.name) }))
          : Hint(_("No work centers are set up for this warehouse."))));
      return wrap;
    }
    const d = st.data;
    if (!d) return Loading();
    wrap.append(h("div.hint", `\u{1F3ED} ${d.work_center.work_center_name || d.work_center.name} · ${d.work_center.bin}`,
      h("button.linkbtn", { type: "button", style: { marginLeft: "10px" }, onclick: logOff }, _("Change"))));
    const cfg = d.work_center;
    const modes = [["product", _("Pack product"), "allow_pack_product"], ["hu", _("Pack HU"), "allow_pack_hu"],
      ["new", _("New HU"), "allow_create_hu"], ["close", _("Close HU"), "allow_close_hu"]].filter((m) => cfg[m[2]]);
    if (!modes.length) { wrap.append(Hint(_("No packing functions are enabled for this work center."))); return wrap; }
    if (!modes.some((m) => m[0] === st.mode)) st.mode = modes[0][0];
    if (st.mode === "new" && !st.f.type && cfg.default_hu_type) st.f.type = cfg.default_hu_type;
    wrap.append(h("div.seg", modes.map(([k, l]) => h("button", { type: "button", class: st.mode === k ? "on" : "", onclick: () => { st.mode = k; update(); } }, l))));
    const f = st.f;
    const box = Section({});
    if (st.mode === "product") {
      const dl = deliveriesOf(f.src);
      box.append(...[
        Field({ name: "src", kind: "scan", label: _("Source HU (skip = loose on table)"), placeholder: _("Scan HU"), value: f.src, autofocus: !f.src,
          onInput: (v) => { f.src = v; }, onCommit: (v) => (v ? scanHu("src")(v) : undefined) }),
        Field({ name: "prod", kind: "scan", label: cfg.quantity_proposal === "One Unit per Scan" ? _("Product (each scan packs one)") : _("Product"),
          placeholder: _("Scan product or GS1 label"), value: f.prod, onInput: (v) => { f.prod = v; },
          onCommit: async (v, via, raw) => {
            const err = await productFrom(v, raw);
            if (err || cfg.quantity_proposal !== "One Unit per Scan") return err;
            f.qty = "1";
            if (!f.dst) return { focus: "dst" };
            return (await packProduct()) === false ? false : { focus: "prod" };
          } }),
        ...unitFields(f),
        Field({ name: "dst", kind: "scan", label: _("Destination HU"), placeholder: _("Scan carton / pallet"), value: f.dst, onInput: (v) => { f.dst = v; },
          onCommit: async (v) => { const e = scanHu("dst")(v); if (e) return e; return (await packProduct()) === false ? false : { focus: "prod" }; } }),
        dl.length > 1 ? Field({ name: "dlv", kind: "select", label: _("For delivery"), value: f.dlv, onInput: (v) => { f.dlv = v; },
          options: [{ value: "", label: _("Choose…") }, ...dl.map((x) => ({ value: x, label: (d.deliveries[x] || {}).outbound_delivery_number || x }))] }) : null].filter(Boolean));
    } else if (st.mode === "hu") {
      box.append(
        Field({ name: "hu", kind: "scan", label: _("HU to pack"), placeholder: _("Scan HU"), value: f.hu, autofocus: true, onInput: (v) => { f.hu = v; }, onCommit: scanHu("hu") }),
        Field({ name: "into", kind: "scan", label: _("Into HU"), placeholder: _("Scan pallet / carton"), value: f.into, onInput: (v) => { f.into = v; },
          onCommit: async (v) => { const e = scanHu("into")(v); if (e) return e; return (await packHu()) === false ? false : { focus: "hu" }; } }));
    } else if (st.mode === "new") {
      box.append(
        Field({ name: "type", kind: "select", label: _("HU type"), value: f.type, onInput: (v) => { f.type = v; update(); },
          options: [{ value: "", label: _("Choose…") }, ...(d.hu_types || []).map((t) => ({ value: t.name, label: t.hu_type_name || t.name }))] }),
        Field({ name: "newnum", kind: "scan", label: _("HU label"), placeholder: ((d.hu_types || []).find((t) => t.name === f.type) || {}).numbering_mode === "Internal" ? _("Leave blank - numbered automatically") : _("Scan the empty label"),
          value: "", submitOnEmpty: true, onCommit: (v) => createHu(v) }));
    } else {
      box.append(
        Field({ name: "close", kind: "scan", label: _("HU to close"), placeholder: _("Scan HU"), value: f.close, autofocus: true, onInput: (v) => { f.close = v; }, onCommit: scanHu("close") }),
        Field({ name: "weight", kind: "qty", label: cfg.weigh_on_close === "Required" ? _("Gross weight (scale, required)") : _("Gross weight (scale)"), value: f.weight, unit: "kg", onInput: (v) => { f.weight = v; }, onCommit: () => closeHu() }));
    }
    wrap.append(box);
    const tops = d.handling_units;
    wrap.append(Section({ title: _("On the table ({0})", [tops.length]) },
      tops.length ? tops.map((n) => Card({ title: n.hu_number || n.name, right: n.closed ? Badge(_("Closed"), "Confirmed") : Badge(n.hu_type || "", "Open"),
        meta: [(n.deliveries || []).map((x) => (d.deliveries[x] || {}).outbound_delivery_number || x).join(", "), (n.children || []).length ? ` · ${_("{0} HU inside", [(n.children || []).length])}` : ""],
        qty: (n.stock || []).map((s) => `${s.product} ${fmtQty(s.quantity)}`).join(" · ") || _("empty"),
        onClick: () => { if (st.mode === "close") f.close = n.name; else if (st.mode === "hu") { if (!f.hu) f.hu = n.name; else f.into = n.name; } else if (!f.src) f.src = n.name; else f.dst = n.name; update(); } }))
        : Hint(_("Nothing on this table yet."))));
    if (st.orders.length) {
      wrap.append(Section({ title: _("Packing orders") }, st.orders.map((o) => Card({ title: o.name, meta: `${(o.source_hus || []).join(", ")} → ${(o.destination_hus || []).join(", ")}`,
        children: Btn({ label: _("Complete packing"), small: true, onClick: () => completeOrder(o) }) }))));
    }
    return wrap;
  },
  actions() {
    if (!wc() || !st.data) return null;
    const primary = { product: { label: _("Pack"), icon: "\u{1F4E6}", run: packProduct }, hu: { label: _("Pack HU"), run: packHu },
      new: { label: _("Create HU"), run: () => createHu("") }, close: { label: _("Close HU"), icon: "\u{1F512}", run: closeHu } }[st.mode];
    return { primary };
  },
};

async function station(method, args, okMsg) {
  const r = await run(() => api(`${API}.${method}`, { work_center: wc(), ...args }), { label: _("Posting…") });
  if (r === undefined) return undefined;
  feedback.done(); notify.ok(okMsg(r), { ttl: 2500 });
  await refresh();
  return r;
}

async function packProduct() {
  const f = st.f;
  if (!f.prod) { S.fieldErrors.prod = _("Scan the product."); feedback.error(); S.focusRequest = "prod"; update(); return false; }
  if (!isNumeric(f.qty) || parseNum(f.qty) <= 0) { S.fieldErrors.qty = _("Enter a quantity greater than zero."); feedback.error(); S.focusRequest = "qty"; update(); return false; }
  if (!f.dst) { S.fieldErrors.dst = _("Scan the destination HU."); feedback.error(); S.focusRequest = "dst"; update(); return false; }
  const idem = ensureKey(f, "RF-PACK");
  const stockQty = round6(parseNum(f.qty) * unitFor(f).factor);  // always packed in the stock unit
  const r = await station("pack_product", { product: f.prod, quantity: stockQty, destination_hu: f.dst, source_hu: f.src || undefined,
    outbound_delivery: f.dlv || undefined, idempotency_key: idem }, () => _("Packed {0} {1} into {2}", [fmtQty(stockQty), f.prod, f.dst]));
  if (r === undefined) return false;
  // Keep source and destination: the next scan is usually the next product for the same carton.
  Object.assign(f, { prod: "", qty: "", uom: "", idem: "" });
  S.focusRequest = "prod"; update();
}

async function packHu() {
  const f = st.f;
  if (!f.hu || !f.into) { notify.warn(_("Scan both HUs.")); return false; }
  const r = await station("pack_hu", { hu_name: f.hu, destination_hu: f.into }, () => _("{0} packed into {1}", [f.hu, f.into]));
  if (r === undefined) return false;
  f.hu = ""; S.focusRequest = "hu"; update();
}

async function createHu(label) {
  const f = st.f;
  if (!f.type) { S.fieldErrors.type = _("Choose the HU type."); feedback.error(); S.focusRequest = "type"; update(); return false; }
  const r = await station("create_hu", { hu_type: f.type, hu_number: (label || "").trim() || undefined }, (x) => _("{0} created - it is the destination now", [x.name]));
  if (r === undefined) return false;
  f.dst = r.name; st.mode = "product"; S.focusRequest = f.src ? "prod" : "src"; update();
}

async function closeHu() {
  const f = st.f;
  if (!f.close) { S.fieldErrors.close = _("Scan the HU to close."); feedback.error(); S.focusRequest = "close"; update(); return false; }
  if (f.weight && !isNumeric(f.weight)) { S.fieldErrors.weight = _("Enter a number."); feedback.error(); S.focusRequest = "weight"; update(); return false; }
  const n = node(f.close);
  // Where the closed HU goes is the work center's "Close HU Follow-up" setting (server side).
  const args = { work_center: wc(), hu_name: f.close, gross_weight: f.weight ? parseNum(f.weight) : undefined };
  let r = await run(() => api(`${API}.close_hu`, args), { label: _("Closing…") });
  if (r === undefined) return false;
  if (r.needs_confirmation) {
    if (!confirm(r.message)) return false;
    r = await run(() => api(`${API}.close_hu`, { ...args, confirm_incomplete: 1 }), { label: _("Closing…") });
    if (r === undefined) return false;
  }
  feedback.done();
  notify.ok(r.moved_to ? _("{0} closed and moved to {1}", [f.close, r.moved_to]) : _("{0} closed", [f.close]), { ttl: 2500 });
  if (f.dst === (n && n.name)) f.dst = "";
  Object.assign(f, { close: "", weight: "" });
  await refresh();
  S.focusRequest = "close"; update();
}

async function completeOrder(o) {
  if (!confirm(_("Complete packing order {0}?", [o.name]))) return;
  const ok = await run(() => api("frappe_wms.api.scanner.complete_packing_order", { packing_order_name: o.name }), { label: _("Completing…") });
  if (ok === undefined) return;
  feedback.done(); notify.ok(_("Packing order {0} completed", [o.name])); await refresh();
}

async function logOn(name) {
  const ok = await run(() => api("frappe_wms.api.resource.log_on_work_center", { work_center_code: name }), { label: _("Logging on…") });
  if (ok === undefined) return;
  st.data = null; await refresh();
}

async function logOff() {
  const ok = await run(() => api("frappe_wms.api.resource.log_off_work_center", {}), { label: _("Leaving…") });
  if (ok === undefined) return;
  st.data = null; st.centers = null; await refresh();
}

