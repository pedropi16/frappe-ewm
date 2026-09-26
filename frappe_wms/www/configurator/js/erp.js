import { getConnection, authHeader } from "./connect.js";

/**
 * Live link to an ERPNext / frappe_wms site, used to fill Link fields from real data.
 *
 * Two ways in, tried in this order:
 *  - "session": the configurator is being served by the site itself (/configurator) and the
 *    person is signed in - nothing to set up, no API key, no CORS. Read-only GETs only.
 *  - "token":   an API key/secret entered in the Connect dialog (needed when the configurator
 *    is hosted somewhere else; the site must allow CORS).
 */
const state = { mode: "none", base: "", user: "", label: "", error: "" };
const listeners = new Set();
const cache = new Map();

export const status = () => ({ ...state });
export const isConnected = () => state.mode !== "none";
export function subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); }
const notify = () => listeners.forEach((fn) => fn(status()));

async function request(path, params) {
  const qs = params ? "?" + new URLSearchParams(params).toString() : "";
  if (state.mode === "session") {
    return fetch(`${path}${qs}`, { credentials: "same-origin", headers: { Accept: "application/json" } });
  }
  const conn = getConnection();
  return fetch(`${conn.url.replace(/\/+$/, "")}${path}${qs}`, { headers: { Accept: "application/json", ...authHeader(conn) } });
}

async function whoAmI() {
  const res = await request("/api/method/frappe.auth.get_logged_user");
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  const user = (await res.json()).message;
  if (!user || user === "Guest") throw new Error("not signed in");
  return user;
}

/** Called once at startup: picks up the signed-in session when hosted by the site. */
export async function init() {
  state.mode = "session";
  try {
    state.user = await whoAmI();
    state.label = location.host;
    state.base = "";
    state.error = "";
  } catch {
    state.mode = "none";
    await useToken();
    return;
  }
  cache.clear();
  notify();
}

/** (Re)connect with whatever is saved in the Connect dialog. Returns the resulting status. */
export async function useToken() {
  const conn = getConnection();
  cache.clear();
  if (!conn.url || !conn.key || !conn.secret) {
    if (state.mode !== "session") { state.mode = "none"; state.user = ""; state.label = ""; state.error = ""; }
    notify();
    return status();
  }
  const prev = { ...state };
  state.mode = "token";
  try {
    state.user = await whoAmI();
    state.label = conn.url.replace(/^https?:\/\//, "").replace(/\/+$/, "");
    state.error = "";
  } catch (e) {
    Object.assign(state, prev, { mode: prev.mode === "session" ? "session" : "none", error: `Could not connect: ${e.message}. Check the URL, key/secret and that the site allows CORS.` });
  }
  notify();
  return status();
}

/** Link search, same endpoint the desk's own Link fields use. Results: [{value, description}]. */
export async function searchLink(doctype, txt = "", pageLength = 15) {
  if (!isConnected()) return [];
  const key = `${state.mode}|${doctype}|${txt}`;
  if (cache.has(key)) return cache.get(key);
  const promise = (async () => {
    try {
      const res = await request("/api/method/frappe.desk.search.search_link", { doctype, txt, page_length: String(pageLength) });
      if (!res.ok) return [];
      const body = await res.json();
      const rows = body.results || body.message || [];
      return rows.map((r) => ({ value: r.value, description: (r.description || "").replace(/^\s*,\s*/, "") }));
    } catch {
      return [];
    }
  })();
  cache.set(key, promise);
  return promise;
}

/** Does this exact value exist on the connected site? null when we can't tell (offline). */
export async function exists(doctype, value) {
  if (!isConnected() || !value) return null;
  const rows = await searchLink(doctype, value, 20);
  return rows.some((r) => r.value === value);
}
