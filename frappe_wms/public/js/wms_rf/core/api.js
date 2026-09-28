import { _ } from "#wms/core/i18n.js";
import { AppError, friendlyStatus, messageFromBody } from "#wms/core/errors.js";

// A deploy of this app's public/ JS/CSS is invisible to a browser until BOTH frappe-frontend-1
// and every backend-role container are updated (frontend serves /assets/ directly, bypassing the
// backend entirely) - AND a reverse proxy in front of the site may itself cache .js/.css by URL
// for up to 30 minutes independent of either, even once both containers are current. Confirmed
// live: erp.pinohomelab.duckdns.org sits behind nginx-proxy-manager with "Cache Assets" on
// (conf.d/include/assets.conf, proxy_cache_valid any 30m, keyed on the full request URI including
// this app's own content-hash query string) - a stale hash gets cached there the moment it's
// first requested and is then served back verbatim, including to a browser that only just loaded
// a supposedly-fresh page. After any RF-app deploy, verify what a real request actually returns
// (not just what both containers hold on disk) before treating the deploy as live.

let config = { csrf: "", onSessionExpired: () => {}, fetch: (...a) => fetch(...a), timeoutMs: 25000 };
export function configureApi(patch) { config = { ...config, ...patch }; }

// Tracks in-flight calls so the shell can warn before a reload/close would abandon a submit (see main.js).
let inflight = 0;
export function inflightCount() { return inflight; }

async function sessionIsDead() {
  try {
    const res = await config.fetch("/api/method/frappe.auth.get_logged_user", { headers: { Accept: "application/json" } });
    if (res.status === 401 || res.status === 403) return true;
    const data = await res.json().catch(() => ({}));
    return !data.message || data.message === "Guest";
  } catch (e) { return false; } // offline: cannot tell, so do not claim the session ended
}

// Frappe's own session cookie can rotate under the operator without any visible sign (renewal,
// a security setting, another tab logging on) - reproduced live: the CSRF token embedded at
// page boot silently stopped matching the (still perfectly valid, still logged-in) session
// within seconds of a fresh load, and every mutating call from then on failed with a raw
// "Invalid Request" that sessionIsDead() never catches (the user IS still logged in - only the
// token is stale). Re-fetching this same page's own HTML and pulling the fresh window.WMS.csrf
// out of it is the same mechanism the app used to get its first token, so it needs no new
// backend endpoint; window.WMS's own boot script always writes `window.WMS = {...};</script>`
// with every "</" inside the JSON pre-escaped to "<\/" (see www/wms/index.py's _script_json),
// so this can never truncate early on the JSON's own content.
let refreshingCsrf = null;
async function refreshCsrfToken() {
  if (!refreshingCsrf) {
    refreshingCsrf = (async () => {
      try {
        const res = await config.fetch(location.pathname, { headers: { Accept: "text/html" } });
        const html = await res.text();
        const m = html.match(/window\.WMS\s*=\s*([\s\S]*?);\s*<\/script>/);
        if (!m) return false;
        const boot = JSON.parse(m[1]);
        if (!boot || !boot.csrf) return false;
        config.csrf = boot.csrf;
        return true;
      } catch (e) {
        return false; // offline, or blocked - the caller's own error handling takes over
      }
    })();
  }
  try { return await refreshingCsrf; } finally { refreshingCsrf = null; }
}

// api("frappe_wms.api.scanner.my_tasks", {..}, {read: true})
//   read: true marks a side-effect-free call; it is retried on network failure / timeout. Mutations are never retried
//   here - the caller resends them with the same idempotency key, which is what makes the resend safe.
export async function api(method, args, { read = false, timeoutMs, signal } = {}) {
  const attempts = read ? 3 : 1;
  let lastErr;
  for (let attempt = 0; attempt < attempts; attempt++) {
    try {
      return await once(method, args, { timeoutMs: timeoutMs || config.timeoutMs, signal, csrfRetried: false });
    } catch (e) {
      lastErr = e;
      const transient = e instanceof AppError && (e.kind === "network" || e.kind === "timeout");
      if (!read || !transient || attempt === attempts - 1) throw e;
      await new Promise((r) => setTimeout(r, 400 * (attempt + 1)));
    }
  }
  throw lastErr;
}

async function once(method, args, { timeoutMs, signal, csrfRetried }) {
  const ctrl = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; ctrl.abort(); }, timeoutMs);
  if (signal) signal.addEventListener("abort", () => ctrl.abort());
  inflight++;
  let res;
  try {
    res = await config.fetch(`/api/method/${method}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json", "X-Frappe-CSRF-Token": config.csrf },
      body: JSON.stringify(args || {}),
      signal: ctrl.signal,
    });
  } catch (e) {
    if (timedOut) throw new AppError(_("The server did not answer in time. Check your connection and try again."), { kind: "timeout", retryable: true });
    if (e && e.name === "AbortError") throw new AppError(_("Cancelled."), { kind: "network" });
    throw new AppError(_("No connection. Nothing was lost - check your WiFi and try again."), { kind: "network", retryable: true });
  } finally {
    clearTimeout(timer);
    inflight--;
  }

  let data = {};
  try { data = await res.json(); } catch (e) { /* no JSON body */ }

  if (res.ok) return data.message;

  // A stale token, not a dead session - refresh it and resend this exact call once, transparently
  // (never on a resend of this same retry, so a genuinely broken token can't loop forever).
  if (data.exc_type === "CSRFTokenError" && !csrfRetried && await refreshCsrfToken()) {
    return once(method, args, { timeoutMs, signal, csrfRetried: true });
  }

  // Frappe answers a logged-out user with 403 + a message ("Login to access..."), so any auth-ish status re-checks the session.
  if ([400, 401, 403].includes(res.status) && await sessionIsDead()) {
    config.onSessionExpired();
    throw new AppError(_("Your session expired. Log in again - your work here is saved."), { kind: "session", status: res.status });
  }
  const msg = messageFromBody(data) || friendlyStatus(res.status);
  const kind = res.status === 403 ? "permission" : res.status >= 500 ? "server" : "validation";
  throw new AppError(msg, { kind, status: res.status, retryable: res.status >= 500, raw: data });
}
