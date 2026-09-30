import { S, drafts, update, nav, notify } from "#wms/app.js";
import { api } from "#wms/core/api.js";
import { _ } from "#wms/core/i18n.js";
import { uid, debounce, matchExpected } from "#wms/core/util.js";

// Cross-screen data and helpers.
// Task types are grouped by which section of the app an operator naturally works them from -
// this mapping decides which #/tasks/:group a pulled or listed task shows up under.
export const TASK_TYPE_GROUPS = {
  // Cross Dock tasks are created at the same point as Putaway and worked by the same receiving operator right after, so they
  // group with inbound (as do Unload/Deconsolidation).
  inbound: ["Unload", "Putaway", "Deconsolidation", "Cross Dock"],
  internal: ["Internal Move", "Posting Change", "Inventory Count", "Consolidation"],
  // Sort is Two-Step Picking's follow-up hop (picked to a shared staging area, then sorted on to
  // the actual delivery) - same picker, same part of the flow as Pick itself.
  outbound: ["Pick", "Sort", "Stage", "Load"],
};

export const SECTIONS = {
  inbound: { label: "Inbound", icon: "\u{1F4E5}", items: [
    { icon: "\u{1F4E5}", label: "Receive", href: "#/receive" },
    { icon: "\u{1F4CB}", label: "Putaway Tasks", href: "#/tasks/inbound", taskGroup: "inbound" },
    { icon: "\u{1F4E6}", label: "Deconsolidate", href: "#/decon" },
    { icon: "✅", label: "Quality", href: "#/quality" },
  ] },
  internal: { label: "Internal", icon: "↔️", items: [
    { icon: "↔️", label: "Move", href: "#/move" },
    { icon: "\u{1F4CB}", label: "Internal Tasks", href: "#/tasks/internal", taskGroup: "internal" },
    { icon: "➡️", label: "Close Movement", href: "#/close-movement" },
    { icon: "\u{1F501}", label: "Repack", href: "#/repack" },
    { icon: "\u{1F522}", label: "Count", href: "#/count" },
    { icon: "\u{1F3F7}️", label: "Handling Units", href: "#/hu" },
    { icon: "\u{1F9E9}", label: "Kitting", href: "#/kitting" },
    { icon: "\u{1F9FA}", label: "Consolidation", href: "#/consolidation" },
  ] },
  outbound: { label: "Outbound", icon: "\u{1F4E4}", items: [
    { icon: "\u{1F3AF}", label: "Picking", href: "#/picking" },
    { icon: "\u{1F4CB}", label: "Pick Tasks", href: "#/tasks/outbound", taskGroup: "outbound" },
    { icon: "\u{1F4E4}", label: "Ship", href: "#/ship" },
    { icon: "\u{1F4E6}", label: "Pack", href: "#/pack" },
    { icon: "\u{1F9F0}", label: "VAS", href: "#/vas" },
    { icon: "\u{1F69B}", label: "Load", href: "#/load" },
  ] },
};

export const sectionCrumb = (key) => { const s = SECTIONS[key]; return s ? `${s.icon} ${_(s.label)}` : null; };
export const sectionHash = (key) => `#/s/${key}`;

// Reloads the session-level data every screen depends on: my Resource, open tasks, and app settings.
export async function refreshSession() {
  const res = await api("frappe_wms.api.scanner.my_tasks", {}, { read: true });
  S.resource = res.resource || null;
  S.tasks = res.tasks || [];
  if (res.settings) S.settings = res.settings;
  update();
  return res;
}

// ---- drafts: form state that survives reload / Back / tab kill ----
const pending = new Map();
const flushSoon = debounce(flushDrafts, 120);
export function flushDrafts() { for (const [key, entry] of pending) drafts.save(key, entry); pending.clear(); }
export function saveDraft(key, form, meta) { pending.set(key, { form, meta }); flushSoon(); }
export function loadDraft(key) { const d = drafts.load(key); return d ? d.form : null; }
export function clearDraft(key) { pending.delete(key); drafts.clear(key); }
export function draftMeta() {
  return drafts.keys().map((k) => { const d = drafts.load(k); return d && d.meta ? { key: k, ...d.meta } : null; }).filter(Boolean);
}
export function installDraftFlush() {
  window.addEventListener("pagehide", flushDrafts);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") flushDrafts(); });
}

// One idempotency key per user action, generated once and stored in the draft, so a resend after a lost response or a
// reload carries the same key and the server answers with the first result instead of posting twice.
export function ensureKey(form, prefix) {
  if (!form.idem) form.idem = `${prefix}:${uid()}`;
  return form.idem;
}

export function taskLocation(t, side) {
  const bin = side === "src" ? t.source_bin : t.destination_bin;
  const hu = side === "src" ? t.source_hu : t.destination_hu;
  return bin || hu || "-";
}

// Leaves a finished flow: drops its history entries so Back cannot re-enter it, and shows `target` where the flow was launched.
// w0 is the history depth where the flow's first screen sat (see enterFlow).
export function finishFlow(w0, target, message) {
  const steps = Math.max(0, nav.depth - (Number.isInteger(w0) ? w0 : nav.depth - 1)) + 1;
  if (message) notify.ok(message);
  nav.unwind(steps, target);
}

// True when this screen was entered from outside itself (list tap, menu, reload) - the moment a new flow's history base is set.
export function enteredFresh(ctx, screenId) {
  const from = S.prevRoute && S.prevRoute.id === screenId;
  return !from && !ctx.meta.popped;
}

// Which of the codes a screen expects this scan stands for: the code itself, or - through the
// server - the HU whose SSCC / HU number was scanned, the item whose barcode or GTIN it is.
export async function matchScan(value, expected) {
  const local = matchExpected(value, expected);
  if (local) return local;
  try {
    const r = await api("frappe_wms.api.scanner.resolve_scan", { code: value }, { read: true, timeoutMs: 6000 });
    for (const m of r.matches || []) { const hit = matchExpected(m.name, expected); if (hit) return hit; }
  } catch (e) { /* offline: the caller reports the mismatch */ }
  return null;
}
