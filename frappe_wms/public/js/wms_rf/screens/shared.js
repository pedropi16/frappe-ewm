import { S, drafts, update, nav, notify, run, load } from "#wms/app.js";
import { api } from "#wms/core/api.js";
import { _ } from "#wms/core/i18n.js";
import { href } from "#wms/core/routes.js";
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
    { icon: "\u{1F4CB}", label: "Putaway", href: "#/putaway", taskGroup: "inbound" },
    { icon: "\u{1F4E6}", label: "Deconsolidate", href: "#/decon" },
    { icon: "✅", label: "Quality", href: "#/quality" },
    { icon: "\u{1F69A}", label: "Yard", href: "#/yard" },
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
    { icon: "\u{1F69A}", label: "Yard", href: "#/yard" },
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

// System Guided "get next work": claims a Warehouse Order off the resource's queue and opens its
// task. Shared by tasks.js (already on a task list, so "assigned, no task in it yet" is just a
// toast), picking.js and move.js (on the chooser menu, so that case instead falls back to
// `fallbackHash`, the group's own task list).
export async function pullWorkAndOpen(fallbackHash) {
  // run() returns undefined for two different reasons: its own exclusive-busy guard never called
  // fn() at all (no notice shown), or fn() ran and genuinely resolved to undefined - which is
  // exactly what happens here, because pull_next_warehouse_order() returning Python's None comes
  // back as a response body with no "message" key at all (confirmed live: a raw request to it
  // returns literally "{}"), and api()'s once() just returns data.message, i.e. undefined. Treating
  // every undefined as "didn't run" meant the ordinary, extremely common "nothing to pull right
  // now" case showed no notice, no navigation - nothing. An operator tapping "Get next work" with
  // an empty queue saw the button do nothing and had no way to tell that from it being broken.
  const wo = await run(() => api("frappe_wms.api.warehouse_order.pull_next_warehouse_order", {}), { label: _("Finding work…") });
  if (!wo) { notify.info(_("No work waiting right now.")); return; }
  await refreshSession();
  let task = S.tasks.find((t) => t.warehouse_order === wo);
  if (!task) {
    // "my tasks" is a capped, warehouse-wide view (list_my_tasks), not a per-Warehouse-Order one -
    // a resource that has personally accumulated a large backlog of its own earlier open/on-hold
    // work can rank that ahead of a task from a Warehouse Order genuinely just assigned to it this
    // instant, so it's a real possibility this WO's own task isn't in that capped list at all yet.
    // The WO itself was just confirmed assigned, though, so look at ITS tasks directly instead of
    // leaving the operator with a toast confirming the assignment and no way to act on it.
    const detail = await load(() => api("frappe_wms.api.warehouse_order.warehouse_order_detail", { wo_name: wo }, { read: true }));
    task = detail && detail.tasks && detail.tasks.find((t) => t.status === "Open" || t.status === "Assigned");
  }
  if (task) nav.go(href("task", task.name));
  else if (fallbackHash) nav.go(fallbackHash);
  else notify.ok(_("Assigned {0}", [wo]));
}

// Check digits replace a bin scan only when there's no competing HU to also verify on that side -
// same condition services/task.py's confirm_task applies server-side; checked here purely to
// decide what the field asks for, never to compute or compare the actual secret value client-side.
export function needsCheckDigits(bin, hu) {
  return !!(bin && !hu && S.settings.require_bin_check_digits);
}

export async function verifyCheckDigits(binName, value, onOk) {
  let ok = false;
  try { ok = await api("frappe_wms.api.scanner.verify_check_digits", { bin_name: binName, value }, { read: true, timeoutMs: 6000 }); }
  catch (e) { return _("Could not verify - check your connection and try again."); }
  if (!ok) return _("Those check digits don't match. Make sure you're at the right bin.");
  return onOk();
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
