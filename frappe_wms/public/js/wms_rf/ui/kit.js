import { h } from "#wms/ui/dom.js";
import { S, update, run } from "#wms/app.js";
import { _ } from "#wms/core/i18n.js";
import { normalizeScan } from "#wms/core/scan.js";
import { gs1Element, parseGS1 } from "#wms/core/gs1.js";
import { api } from "#wms/core/api.js";
import { feedback } from "#wms/core/feedback.js";
import { prefs } from "#wms/core/prefs.js";
import { scanWithCamera, cameraSupported } from "#wms/core/camera.js";
import { statusClass, parseNum, isNumeric } from "#wms/core/util.js";

// ---------- small presentational pieces ----------
export const Section = (opts, ...children) => {
  const o = typeof opts === "string" ? { title: opts } : opts || {};
  return h("section.section", o.title ? h("h3", o.title) : null, o.hint ? h("div.hint", o.hint) : null, ...children);
};
export const Hint = (text) => h("div.hint", text);
export const Empty = (text, icon = "\u{1F4ED}") => h("div.empty", h("div.empty-icon", icon), h("div", text));
export const Loading = (text) => h("div.empty", h("div.spinner"), h("div", text || _("Loading…")));
export const Badge = (text, cls) => h("span.badge", { class: cls || statusClass(text) }, text);
export const StatusBadge = (status) => Badge((status === "On Hold" ? "\u{1F512} " : "") + _(status), statusClass(status));

export function KV(pairs) {
  return h("div.kv", pairs.filter(Boolean).map(([k, v]) => h("div", h("div.k", k), h("div.v", v == null || v === "" ? "-" : v))));
}

export function Card({ title, right, meta, qty, onClick, dim, children, attrs }) {
  const el = h("div.card", { ...(attrs || {}), class: dim ? "dim" : "", role: onClick ? "button" : null, tabindex: onClick ? "0" : null },
    h("div.row1", h("div.card-title", title), right ? h("div.card-right", right) : null),
    meta ? h("div.meta", meta) : null,
    qty ? h("div.qty", qty) : null,
    children);
  if (onClick) {
    el.addEventListener("click", (e) => { if (!e.target.closest("button, input, select")) onClick(e); });
    el.addEventListener("keydown", (e) => { if (e.key === " " && e.target === el) { e.preventDefault(); onClick(e); } });
  }
  return el;
}

export function Btn({ label, kind = "secondary", onClick, disabled, small, icon }) {
  return h("button.btn", { class: `btn-${kind}${small ? " small" : ""}`, type: "button", disabled: !!disabled, onclick: onClick }, icon ? h("span.btn-icon", icon) : null, label);
}

// A grid of menu-button tiles - the chooser screens (menu.js's root/section grids, Picking's
// "search by kind", Count/Move/Picking's System Guided/Manual choosers) all render this same shape.
export function MenuGrid(items) {
  return h("div.menu-grid", items.map((i) => h("button.menu-btn", { type: "button", onclick: i.run }, h("span.icon", i.icon), h("span", i.label), i.badges && i.badges.length ? h("div", i.badges) : null)));
}

// Sets a field's error, gives feedback, and focuses it - the common tail of a manual/button-driven
// (non-scan) validation failure, shared by every wizard-style screen (task.js, move.js, ...).
export function fail(name, message) {
  S.fieldErrors[name] = message; feedback.error(); S.focusRequest = name; update();
}

export function Stepper(total, index, labels) {
  return h("div.stepper", h("div.step-dots", Array.from({ length: total }, (_x, i) => h("div.dot", { class: i < index ? "done" : i === index ? "active" : "" }))),
    h("div.step-label", _("Step {0} of {1}", [index + 1, total]), labels ? [" · ", labels[index]] : null));
}

// "Scan this" prompt: says exactly which code is expected, in large type.
export function Expect(label, values) {
  const list = (values || []).filter(Boolean);
  return h("div.expect", h("div.expect-label", label), h("div.expect-values", list.map((v, i) => [i ? h("span.or", _("or")) : null, h("span.code", v)])));
}

// ---------- fields ----------
// spec: { name, label, value, kind: "scan"|"text"|"qty"|"select", placeholder, hint, unit, options, autofocus, disabled,
//         onInput(value), onCommit(value, via, raw) -> undefined | "error text" | false | {focus: "field"} | Promise<...> }
// A field with onCommit is validated when the operator presses Enter / a scanner ends a scan / the camera returns a code.
export function Field(spec) {
  const kind = spec.kind || "text";
  const err = S.fieldErrors[spec.name];
  const tok = S.routeToken; // fields belong to the route that rendered them; a stale field must never touch the next screen's state
  let input;
  if (kind === "select") {
    input = h("select", { name: spec.name, "data-fk": spec.name, disabled: !!spec.disabled, onchange: (e) => spec.onInput && spec.onInput(e.target.value) },
      (spec.options || []).map((o) => h("option", { value: o.value, selected: String(o.value) === String(spec.value == null ? "" : spec.value) }, o.label)));
  } else {
    const isScan = kind === "scan";
    input = h("input", {
      type: "text", name: spec.name, "data-fk": spec.name, "data-kind": kind, value: spec.value == null ? "" : String(spec.value),
      placeholder: spec.placeholder || "", disabled: !!spec.disabled,
      inputmode: kind === "qty" ? "decimal" : isScan && !prefs().keyboard ? "none" : "text",
      enterkeyhint: "go", autocomplete: "off", autocapitalize: "off", autocorrect: "off", spellcheck: "false",
      "data-autofocus": spec.autofocus ? "1" : null,
      "aria-invalid": err ? "true" : null, "aria-describedby": err ? `${spec.name}-err` : null,
      oninput: (e) => { if (tok !== S.routeToken) return; if (S.fieldErrors[spec.name]) { delete S.fieldErrors[spec.name]; field.classList.remove("invalid"); const m = field.querySelector(".field-error"); if (m) m.remove(); } if (spec.onInput) spec.onInput(e.target.value); },
      onfocus: (e) => { setTimeout(() => { try { e.target.select(); } catch (x) { /* not selectable */ } }, 0); },
    });
  }
  const rec = { name: spec.name, kind, input, spec, tok, get value() { return input.value; } };
  S.fields.push(rec);

  let control = input;
  if (kind === "scan" && !spec.disabled) {
    const kbd = h("button.icon-btn", { type: "button", "aria-label": _("Toggle keyboard"), title: _("Type instead of scanning"), onclick: () => { const on = input.getAttribute("inputmode") === "none"; input.setAttribute("inputmode", on ? "text" : "none"); input.blur(); input.focus(); } }, "⌨");
    const cam = cameraSupported() ? h("button.icon-btn.cam", { type: "button", "aria-label": _("Scan with camera"), onclick: async () => {
      if (S.busy) return;
      const code = await scanWithCamera({ hint: spec.label });
      if (code) { S.focusRequest = null; await commitField(rec, code, "camera"); }
    } }, "\u{1F4F7}") : null;
    control = h("div.scanrow", input, cam, kbd);
  } else if (kind === "qty" && spec.unit) {
    control = h("div.scanrow", input, h("span.unit", spec.unit));
  }
  const field = h("div.field", { class: err ? "invalid" : "" },
    spec.label ? h("label", { for: null }, spec.label) : null,
    control,
    err ? h("div.field-error", { id: `${spec.name}-err`, role: "alert" }, err) : null,
    spec.hint ? h("div.hint", spec.hint) : null);
  return field;
}

// Runs a field's validation for a completed entry, then gives feedback and moves on. `via` is scan | enter | camera.
let committing = false;
export async function commitField(rec, raw, via) {
  // One entry is validated at a time: a scanner that double-fires (bounced trigger, same label read twice) or a scan racing an
  // Enter would otherwise run two overlapping commits over the same form state.
  if (S.busy || committing) return false;
  committing = true;
  try { return await commitFieldInner(rec, raw, via); } finally { committing = false; }
}
async function commitFieldInner(rec, raw, via) {
  const token = S.routeToken;
  if (rec.tok !== token) return false; // rendered for a previous screen
  let value = rec.kind === "scan" ? normalizeScan(raw) : String(raw == null ? "" : raw).trim();
  // A GS1 label scanned into any scan field: the field gets its own element (spec.gs1 names it -
  // "sscc", "gtin", "batch", "serial" - or the most identifying one); handlers still get `raw`.
  if (rec.kind === "scan" && rec.spec.gs1 !== false) {
    const want = rec.spec.gs1 || null;
    const element = gs1Element(raw, want) || gs1Element(value, want);
    if (element) {
      value = element;
      // An SSCC stands for a Handling Unit: hand the screen the HU it belongs to.
      const gs = parseGS1(raw) || parseGS1(normalizeScan(raw));
      if (gs && gs.sscc === element) {
        try {
          const r = await api("frappe_wms.api.scanner.resolve_scan", { code: element }, { read: true, timeoutMs: 6000 });
          const hu = (r.matches || []).find((m) => m.type === "hu");
          if (hu) value = hu.name;
        } catch (e) { /* offline: keep the SSCC, the screen reports it if it cannot use it */ }
      }
    }
  }
  if (rec.input.isConnected) rec.input.value = value;
  if (rec.spec.onInput) rec.spec.onInput(value);
  let error;
  if (rec.spec.onCommit) {
    if (rec.kind === "qty" && value && !isNumeric(value)) error = _("Enter a number.");
    else error = await rec.spec.onCommit(value, via, raw); // raw keeps GS1 separators (core/gs1.js)
  }
  if (token !== S.routeToken) return !error; // the commit itself navigated (e.g. advanced a wizard step)
  if (error && typeof error === "object" && error.focus) { // accepted, and the handler knows which field comes next
    delete S.fieldErrors[rec.name];
    if (rec.kind === "scan" || via === "scan" || via === "camera") feedback.ok();
    S.focusRequest = error.focus; update();
    return true;
  }
  if (error === false) { S.focusRequest = rec.name; update(); return false; } // failed and already reported (e.g. by run())
  if (error) {
    S.fieldErrors[rec.name] = error;
    feedback.error();
    S.focusRequest = rec.name;
    update();
    return false;
  }
  delete S.fieldErrors[rec.name];
  if (rec.kind === "scan" || via === "scan" || via === "camera") feedback.ok();
  focusNextField(rec);
  return true;
}

// After a good entry, move to the next field on the screen that still needs one; otherwise stay put.
export function focusNextField(rec) {
  const i = S.fields.indexOf(rec);
  const rest = S.fields.slice(i + 1).filter((f) => !f.spec.disabled);
  const next = rest.find((f) => f.kind === "scan" && !f.input.value) || rest.find((f) => f.kind !== "select" && !f.input.value) || rest.find((f) => f.kind !== "select");
  S.focusRequest = next ? next.name : null;
  // Move focus now, not on the next frame: a second scan can arrive before the re-render and must land in the next field.
  if (next && next.input.isConnected && S.busy === 0) { try { next.input.focus(); } catch (e) { /* not focusable */ } }
  update();
}

// Quantity helpers screens use with Field({kind:"qty"}).
export { parseNum, isNumeric };

// ---------- lists & submits ----------
// Standard "search-or-scan then open" list header used by several screens.
export function ListHeader({ title, action }) {
  return h("div.list-head", title ? h("h3", title) : null, action || null);
}

// Runs a button-triggered submit with the shared lock/error handling; a thin alias so screens read naturally.
export const submit = (fn, opts) => run(fn, opts);
