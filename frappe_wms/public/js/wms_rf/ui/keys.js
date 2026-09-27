import { S, nav, notify } from "#wms/app.js";
import { commitField, focusNextField } from "#wms/ui/kit.js";
import { createBurstTracker } from "#wms/core/scan.js";
import { unlockAudio } from "#wms/core/feedback.js";
import { _ } from "#wms/core/i18n.js";

// Global keyboard handling for scanner "keyboards" (Bluetooth HID, e.g. Netum), desktop keyboards and RF F-keys.
//
//  * A scan = a fast burst of characters ending in Enter. It goes to the field the screen is waiting on - the focused
//    scan field, else the first empty scan field - whatever has focus, including nothing (iOS often has nothing focused).
//  * A scan that lands in a quantity box is pulled back out of it and rerouted, instead of corrupting the quantity.
//  * Enter typed by a person commits the focused field; in a quantity/text box it runs the screen's primary action.
//  * Enter is ignored while a submit is in flight or right after a screen change, so a doubled scanner Enter cannot
//    confirm the next screen by accident.
const tracker = createBurstTracker();
let valueBeforeBurst = null;
let burstTarget = null;
let lastKeyAt = 0;

function editable(el) { return el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT"); }

function fieldFor(el) { return S.fields.find((f) => f.input === el && f.tok === S.routeToken) || null; }

function pickScanTarget() {
  const scans = S.fields.filter((f) => f.kind === "scan" && !f.spec.disabled && f.input.isConnected && f.tok === S.routeToken);
  const focused = fieldFor(document.activeElement);
  if (focused && focused.kind === "scan") return focused;
  return scans.find((f) => !f.input.value) || scans[0] || null;
}

export function runPrimary() {
  if (S.busy > 0) return;
  const a = S.actions && S.actions.primary;
  if (a && !a.disabled && a.run) a.run();
}

// A scan that arrives in the moment between a screen change and its re-render finds no fields for the new screen yet - wait a
// few frames for them instead of scanning into the old screen or reporting "nothing to scan".
function deliverScan(code, attempt) {
  const rec = fieldFor(document.activeElement);
  const target = rec && rec.kind === "scan" ? rec : pickScanTarget();
  if (target) { commitField(target, code, "scan"); return; }
  if (attempt < 8) { setTimeout(() => deliverScan(code, attempt + 1), 40); return; }
  notify.warn(_("Nothing to scan on this screen."));
}

export function installKeys() {
  const warm = () => unlockAudio();
  window.addEventListener("touchstart", warm, { passive: true, once: true });
  window.addEventListener("click", warm, { once: true });

  document.addEventListener("keydown", (e) => {
    warm();
    if (e.ctrlKey || e.metaKey || e.altKey) return;

    // RF hardware keys
    if (/^F(3|5|7|8)$/.test(e.key)) {
      e.preventDefault();
      if (S.busy) return;
      if (e.key === "F3") nav.back();
      else if (e.key === "F5") { if (S.screen && S.screen.refresh) S.screen.refresh(S.ctx()); }
      else if (e.key === "F7") { const sec = S.actions && S.actions.secondary && S.actions.secondary.find((a) => a.key === "F7"); if (sec) sec.run(); }
      else runPrimary();
      return;
    }

    if (e.key.length === 1) {
      const active = document.activeElement;
      const t = performance.now();
      if (t - lastKeyAt > 120) { valueBeforeBurst = editable(active) ? active.value : null; burstTarget = editable(active) ? active : null; }
      lastKeyAt = t;
      tracker.key(e.key);
      return; // let the browser type it into whatever is focused
    }

    if (e.key !== "Enter") return;
    if (e.target && e.target.tagName === "TEXTAREA") return;

    const scanned = tracker.finish();
    lastKeyAt = 0; // Enter always ends a burst, so the next scan starts fresh even if it follows immediately
    // Enter on a focused button presses it natively - but a scan's Enter must never re-press whatever button was tapped last.
    if (!scanned && e.target && e.target.tagName === "BUTTON") return;

    e.preventDefault();
    const active = document.activeElement;
    const rec = fieldFor(active);

    if (S.busy > 0) return;
    if (performance.now() - S.navAt < 350 && !scanned) return; // debounce a doubled Enter across a screen change

    if (scanned) {
      // Remove the characters the browser typed into a non-scan field, then route the scan where it belongs.
      if (rec && rec.kind !== "scan" && burstTarget === active && valueBeforeBurst !== null) { active.value = valueBeforeBurst; if (rec.spec.onInput) rec.spec.onInput(valueBeforeBurst); }
      if (active && active.tagName === "BUTTON") active.blur();
      deliverScan(scanned, 0);
      return;
    }

    if (rec && rec.spec.enterNext && !(rec.kind === "scan" && rec.spec.onCommit)) { focusNextField(rec); return; }
    if (rec && rec.kind === "scan" && rec.spec.submitOnEmpty && !active.value.trim()) { runPrimary(); return; }
    if (rec && rec.spec.onCommit && rec.kind !== "qty") { commitField(rec, active.value, "enter"); return; }
    runPrimary();
  }, true);
}
