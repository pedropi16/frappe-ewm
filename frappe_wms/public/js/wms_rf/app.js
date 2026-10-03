import { createNavigator } from "#wms/core/navigator.js";
import { resolveRoute } from "#wms/core/routes.js";
import { createDrafts } from "#wms/core/drafts.js";
import { AppError, errorText } from "#wms/core/errors.js";
import { feedback } from "#wms/core/feedback.js";
import { _ } from "#wms/core/i18n.js";

// The one shared runtime: session state, navigation, notices, and run() - the wrapper every server call goes through.
export const S = {
  booted: false,
  resource: null,
  settings: { require_scan_verification: 0, require_bin_check_digits: 0 },
  tasks: [],
  route: null,
  prevRoute: null,
  navAt: 0,
  screen: null,
  routeToken: 0,
  busy: 0,
  busyLabel: "",
  busySlow: false,
  notice: null,
  online: typeof navigator !== "undefined" ? navigator.onLine !== false : true,
  fields: [],
  fieldErrors: {},
  focusRequest: null,
  autofocusPending: false,
  scrollTop: false,
  afterLogon: null,
  actions: null,
};

let store = null;
try { store = window.sessionStorage; store.setItem("wms.rf.probe", "1"); store.removeItem("wms.rf.probe"); } catch (e) { store = null; }
const memory = new Map();
const fallback = { getItem: (k) => (memory.has(k) ? memory.get(k) : null), setItem: (k, v) => memory.set(k, v), removeItem: (k) => memory.delete(k), key: (i) => [...memory.keys()][i], get length() { return memory.size; } };
export const drafts = createDrafts(store || fallback);

// ---- rendering ----
let renderer = () => {};
let scheduled = false;
export function setRenderer(fn) { renderer = fn; }
export function update() {
  if (scheduled) return;
  scheduled = true;
  const go = () => { scheduled = false; renderer(); };
  if (typeof requestAnimationFrame === "function" && document.visibilityState === "visible") requestAnimationFrame(go); else setTimeout(go, 16);
}

// ---- screens & routes ----
const screens = new Map();
const routes = [];
export function registerScreen(screen) {
  screens.set(screen.id, screen);
  routes.push({ id: screen.id, pattern: screen.pattern });
}
export function screenById(id) { return screens.get(id); }

// ---- navigation ----
let navigator_ = null;
export const nav = {
  go: (hash) => navigator_.go(hash),
  replace: (hash) => navigator_.replace(hash),
  unwind: (steps, hash) => navigator_.unwind(steps, hash),
  get depth() { return navigator_ ? navigator_.depth : 0; },
  back() {
    const fallbackHash = S.screen && S.screen.parent ? S.screen.parent(S.ctx()) : "#/";
    navigator_.back(fallbackHash);
  },
  home() { navigator_.go("#/"); },
};
S.ctx = () => ({ params: (S.route && S.route.params) || {}, query: (S.route && S.route.query) || {}, hash: (S.route && S.route.hash) || "" });

const LAST_KEY = "wms.rf.last";
export function lastRoute() {
  try { const v = JSON.parse(store.getItem(LAST_KEY) || "null"); return v && Date.now() - v.t < 12 * 3600 * 1000 ? v.hash : null; } catch (e) { return null; }
}
function rememberRoute(hash) { try { store.setItem(LAST_KEY, JSON.stringify({ hash, t: Date.now() })); } catch (e) { /* storage unavailable */ } }

async function onNavigate(hash, meta) {
  const token = ++S.routeToken;
  const route = resolveRoute(routes, hash) || resolveRoute(routes, "#/");
  const screen = screens.get(route.id);
  if (!S.resource && screen.needsResource !== false) { S.afterLogon = route.hash; navigator_.replace("#/logon"); return; }
  S.navAt = typeof performance !== "undefined" ? performance.now() : 0;
  S.prevRoute = S.route;
  S.route = route;
  S.screen = screen;
  if (S.notice && S.notice.keepOnce) S.notice.keepOnce = false; else S.notice = null;
  S.fieldErrors = {};
  S.focusRequest = null;
  S.autofocusPending = true;
  S.scrollTop = !(meta && meta.same);
  rememberRoute(route.hash);
  update();
  try {
    const result = screen.enter ? await screen.enter({ ...S.ctx(), meta: meta || {}, token }) : null;
    if (token !== S.routeToken) return;
    if (result && result.redirect) { navigator_.replace(result.redirect); return; }
  } catch (e) {
    if (token === S.routeToken) notify.error(errorText(e));
  }
  update();
}

export function startNavigation(win, defaultHash) {
  navigator_ = createNavigator(win, { onNavigate });
  navigator_.start(defaultHash);
}
export function stillHere(token) { return token === S.routeToken; }

// ---- notices ----
let noticeTimer = 0;
let noticeSeq = 0;
function setNotice(n) {
  clearTimeout(noticeTimer);
  S.notice = n ? { ...n, id: ++noticeSeq } : null;
  n = S.notice;
  if (n && n.kind !== "error" && n.ttl !== 0) noticeTimer = setTimeout(() => { if (S.notice === n) { S.notice = null; update(); } }, n.ttl || 6000);
  update();
}
export const notify = {
  ok(text, opts = {}) { setNotice({ kind: "ok", text, keepOnce: true, ...opts }); },
  info(text, opts = {}) { setNotice({ kind: "info", text, keepOnce: true, ...opts }); },
  warn(text, opts = {}) { feedback.warn(); setNotice({ kind: "warn", text, ...opts }); },
  error(text, opts = {}) { feedback.error(); setNotice({ kind: "error", text, ...opts }); },
  clear() { setNotice(null); },
};

// ---- run(): the wrapper for every server call ----
// opts.again: the user action to re-run on Retry (default: just fn). fn returns a truthy value on success (or the API result). On failure run() shows the error (with a Retry button when a
// resend is safe), and resolves undefined. While a mutating call is in flight, a second run() is ignored - a double tap
// or a repeated scanner Enter can never submit twice.
export async function run(fn, opts = {}) {
  const { busy = true, exclusive = busy, label = "", onError, again } = opts;
  if (exclusive && S.busy > 0) return undefined;
  let slowTimer = 0;
  if (busy) {
    S.busy++; S.busyLabel = label; S.busySlow = false;
    slowTimer = setTimeout(() => { S.busySlow = true; update(); }, 6000);
    update();
  }
  try {
    return await fn();
  } catch (e) {
    if (!(e instanceof AppError && e.kind === "session")) {
      const retryable = e instanceof AppError && e.retryable;
      // Retry re-runs the whole user action (`again`) - not just the request - so the follow-up (clear draft, navigate, tone) happens too.
      notify.error(errorText(e), retryable ? { retry: again || (() => run(fn, opts)) } : {});
    }
    if (onError) onError(e);
    return undefined;
  } finally {
    if (busy) { S.busy--; clearTimeout(slowTimer); S.busySlow = false; update(); }
  }
}

// Non-blocking load for list/detail data: no overlay, no lock, errors still surface (with Retry).
export function load(fn, opts = {}) { return run(fn, { busy: false, exclusive: false, ...opts }); }

export { _ };
