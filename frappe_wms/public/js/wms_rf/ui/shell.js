import { h } from "#wms/ui/dom.js";
import { S, nav, notify, update, setRenderer } from "#wms/app.js";
import { _ } from "#wms/core/i18n.js";
import { runPrimary } from "#wms/ui/keys.js";
import { inflightCount } from "#wms/core/api.js";

// Fixed frame: header / notice / view / action bar / busy overlay. Only the pieces whose content changed are replaced,
// and focus, caret and scroll are carried across each rebuild so a re-render never steals the field being typed in.
let hdr, noticeEl, view, actionsEl, busyEl;
// Header, notice and action bar are only rebuilt when what they show changed: replacing a button between a finger's
// touchstart and touchend (any background refresh does) makes the tap silently vanish.
const sigs = { hdr: "", notice: "", actions: "" };

export function mountShell(root) {
  hdr = h("header#hdr");
  noticeEl = h("div#notice", { role: "status", "aria-live": "polite" });
  view = h("main#view", { tabindex: "-1" });
  actionsEl = h("footer#actions");
  busyEl = h("div#busy", { hidden: true, role: "alert", "aria-live": "assertive" });
  root.replaceChildren(hdr, noticeEl, view, actionsEl, busyEl);
  setRenderer(render);
  // Coming back online must not replace an error the operator is looking at (its Retry button is the way forward).
  window.addEventListener("online", () => { S.online = true; if (!S.notice) notify.info(_("Back online."), { ttl: 3000 }); update(); });
  window.addEventListener("offline", () => { S.online = false; update(); });
  window.addEventListener("beforeunload", (e) => { if (inflightCount() > 0) { e.preventDefault(); e.returnValue = ""; } });
}

function safe(fn, label) {
  try { return fn(); } catch (err) {
    console.error(`[wms-rf] ${label} failed`, err);
    return null;
  }
}

function renderHeader() {
  const screen = S.screen;
  const ctx = S.ctx();
  const isRoot = !screen || screen.root;
  const title = (screen && safe(() => screen.title(ctx), "title")) || _("WMS Scanner");
  const sub = screen && screen.crumb ? safe(() => screen.crumb(ctx), "crumb") : null;
  const chip = S.resource ? h("a.chip", { href: "#/session", title: _("Device & session") }, h("span.dot", { class: S.online ? "on" : "off" }), "\u{1F4E1} ", S.resource.name, S.resource.current_queue ? ` · ${S.resource.current_queue}` : "") : null;
  return [
    !isRoot ? h("button.hdr-btn", { type: "button", "aria-label": _("Back"), onclick: () => nav.back() }, "‹") : null,
    h("div.hdr-title", h("div.title", title), sub ? h("div.crumb", sub) : h("div.crumb", chip || "")),
    screen && screen.refresh ? h("button.hdr-btn", { type: "button", "aria-label": _("Refresh"), onclick: () => !S.busy && screen.refresh(ctx) }, "↻") : null,
    !isRoot && S.resource ? h("button.hdr-btn", { type: "button", "aria-label": _("Home"), onclick: () => nav.go("#/") }, "⌂") : null,
  ];
}

function renderNotice() {
  const n = S.notice;
  const offline = !S.online ? h("div.notice.warn", { role: "alert" }, h("span", _("You are offline. Entries are kept; sending resumes when the connection returns."))) : null;
  if (!n) return [offline];
  return [offline, h("div.notice", { class: n.kind, role: n.kind === "error" ? "alert" : "status" },
    h("span.notice-text", n.text),
    n.retry ? h("button.notice-btn", { type: "button", onclick: () => { const r = n.retry; notify.clear(); r(); } }, _("Retry")) : null,
    h("button.notice-x", { type: "button", "aria-label": _("Dismiss"), onclick: () => notify.clear() }, "✕"))];
}

function renderActions() {
  const cfg = S.actions;
  if (!cfg || (!cfg.primary && !(cfg.secondary && cfg.secondary.length))) return null;
  const btn = (a, cls) => h("button.btn", { class: cls, type: "button", disabled: !!a.disabled || S.busy > 0, onclick: () => { if (!S.busy) a.run(); } }, a.icon ? h("span.btn-icon", a.icon) : null, a.label);
  return [
    cfg.secondary && cfg.secondary.length ? h("div.actions-row", cfg.secondary.map((a) => btn(a, `btn-${a.kind || "secondary"}`))) : null,
    cfg.primary ? btn(cfg.primary, `btn-${cfg.primary.kind || "primary"} primary`) : null,
  ];
}

function renderBusy() {
  if (S.busy > 0) {
    busyEl.hidden = false;
    busyEl.replaceChildren(h("div.busy-box", h("div.spinner"), h("div", S.busyLabel || _("Working…")), S.busySlow ? h("div.hint", _("Slow connection - still trying. Please wait.")) : null));
  } else { busyEl.hidden = true; busyEl.replaceChildren(); }
}

function restoreFocus(prev, forced) {
  const name = forced || (prev && prev.fk);
  let target = name ? view.querySelector(`[data-fk="${CSS.escape(name)}"]`) : null;
  // Entry autofocus applies once the screen actually has its field (data may load after the route change), and never
  // steals focus from a field the operator already put the cursor in.
  const auto = !target && S.autofocusPending && !(prev && prev.fk) ? view.querySelector("[data-autofocus]") : null;
  if (auto) target = auto;
  if (!target || S.busy > 0) return !!auto;
  try {
    target.focus({ preventScroll: false });
    if (prev && prev.fk === target.dataset.fk && !forced && typeof prev.start === "number" && target.setSelectionRange && target.type === "text") target.setSelectionRange(prev.start, prev.end);
  } catch (e) { /* element not focusable right now */ }
  return !!auto;
}

function render() {
  if (!hdr) return;
  const a = document.activeElement;
  const inView = a && view.contains(a);
  const prev = inView && a.dataset ? { fk: a.dataset.fk, start: a.selectionStart, end: a.selectionEnd } : null;
  const scroller = view;
  const scrollTop = scroller.scrollTop;

  S.fields = [];
  S.actions = null;
  const screen = S.screen;
  const ctx = S.ctx();

  const hdrSig = `${S.route && S.route.hash}|${S.online}|${S.resource && S.resource.name}|${S.resource && S.resource.current_queue}|${screen && safe(() => screen.title(ctx), "title")}`;
  if (hdrSig !== sigs.hdr) { sigs.hdr = hdrSig; hdr.replaceChildren(...[renderHeader()].flat().filter(Boolean)); }
  const noticeSig = `${S.online}|${S.notice ? S.notice.id : 0}`;
  if (noticeSig !== sigs.notice) { sigs.notice = noticeSig; noticeEl.replaceChildren(...renderNotice().flat().filter(Boolean)); }

  let content = null;
  if (screen) {
    try { content = screen.render(ctx); } catch (err) {
      console.error("[wms-rf] screen render failed", err);
      content = h("div.section", h("h3", _("This screen could not be displayed")), h("div.hint", String(err && err.message || err)),
        h("button.btn.btn-primary", { onclick: () => location.reload() }, _("Reload")), h("button.btn.btn-secondary", { onclick: () => nav.go("#/") }, _("Home")));
    }
    try { S.actions = screen.actions ? screen.actions(ctx) : null; } catch (err) { console.error("[wms-rf] actions failed", err); }
  }
  view.replaceChildren(...[content].flat().filter(Boolean));
  const cfg = S.actions;
  const actSig = cfg ? `${S.route && S.route.hash}|${S.busy > 0}|${JSON.stringify([cfg.primary && [cfg.primary.label, cfg.primary.disabled, cfg.primary.kind], (cfg.secondary || []).map((a) => [a.label, a.disabled, a.kind])])}` : "";
  if (actSig !== sigs.actions) {
    sigs.actions = actSig;
    const acts = renderActions();
    actionsEl.replaceChildren(...[acts].flat().filter(Boolean));
    actionsEl.hidden = !acts;
  }
  document.body.classList.toggle("has-actions", !!cfg && !actionsEl.hidden);

  view.scrollTop = S.scrollTop ? 0 : scrollTop;
  if (restoreFocus(prev, S.focusRequest)) S.autofocusPending = false;
  S.focusRequest = null;
  S.scrollTop = false;
  renderBusy();
  document.title = `${(screen && safe(() => screen.title(ctx), "title")) || _("WMS Scanner")} · WMS`;
}

export { runPrimary };
