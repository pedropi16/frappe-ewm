import { _ } from "#wms/core/i18n.js";
import { AppError, friendlyStatus, messageFromBody } from "#wms/core/errors.js";

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

// api("frappe_wms.api.scanner.my_tasks", {..}, {read: true})
//   read: true marks a side-effect-free call; it is retried on network failure / timeout. Mutations are never retried
//   here - the caller resends them with the same idempotency key, which is what makes the resend safe.
export async function api(method, args, { read = false, timeoutMs, signal } = {}) {
  const attempts = read ? 3 : 1;
  let lastErr;
  for (let attempt = 0; attempt < attempts; attempt++) {
    try {
      return await once(method, args, { timeoutMs: timeoutMs || config.timeoutMs, signal });
    } catch (e) {
      lastErr = e;
      const transient = e instanceof AppError && (e.kind === "network" || e.kind === "timeout");
      if (!read || !transient || attempt === attempts - 1) throw e;
      await new Promise((r) => setTimeout(r, 400 * (attempt + 1)));
    }
  }
  throw lastErr;
}

async function once(method, args, { timeoutMs, signal }) {
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

  // Frappe answers a logged-out user with 403 + a message ("Login to access..."), so any auth-ish status re-checks the session.
  if ([400, 401, 403].includes(res.status) && await sessionIsDead()) {
    config.onSessionExpired();
    throw new AppError(_("Your session expired. Log in again - your work here is saved."), { kind: "session", status: res.status });
  }
  const msg = messageFromBody(data) || friendlyStatus(res.status);
  const kind = res.status === 403 ? "permission" : res.status >= 500 ? "server" : "validation";
  throw new AppError(msg, { kind, status: res.status, retryable: res.status >= 500, raw: data });
}
