import test from "node:test";
import assert from "node:assert/strict";

import { normalizeScan, createBurstTracker } from "../../public/js/wms_rf/core/scan.js";
import { flt, fmtQty, matchExpected } from "../../public/js/wms_rf/core/util.js";
import { htmlToText, messageFromBody, AppError } from "../../public/js/wms_rf/core/errors.js";
import { createDrafts } from "../../public/js/wms_rf/core/drafts.js";
import { parseHash, resolveRoute, href } from "../../public/js/wms_rf/core/routes.js";
import { createNavigator } from "../../public/js/wms_rf/core/navigator.js";
import { api, configureApi } from "../../public/js/wms_rf/core/api.js";
import { setMessages, _ } from "../../public/js/wms_rf/core/i18n.js";

test("normalizeScan strips control chars, whitespace and AIM symbology ids", () => {
  assert.equal(normalizeScan("  BIN-A1\r\n"), "BIN-A1");
  assert.equal(normalizeScan("]C1BIN-A1"), "BIN-A1");
  assert.equal(normalizeScan("\u0002HU-0001\u0003"), "HU-0001");
  assert.equal(normalizeScan("a\u001db"), "ab");
  assert.equal(normalizeScan(null), "");
  assert.equal(normalizeScan("]E05901234123457"), "5901234123457");
  assert.equal(normalizeScan("]not-aim"), "]not-aim", "only ]<letter><digit> is a symbology id");
});

test("burst tracker: fast keystrokes are a scan, human typing is not", () => {
  let t = 0;
  const b = createBurstTracker(() => t);
  for (const ch of "BIN-A1") { t += 5; b.key(ch); }
  assert.equal(b.finish(), "BIN-A1");
  for (const ch of "BIN-A1") { t += 180; b.key(ch); }
  assert.equal(b.finish(), null);
  // too short to be a scan even if fast
  for (const ch of "ab") { t += 5; b.key(ch); }
  assert.equal(b.finish(), null);
  // a long pause starts a new burst
  for (const ch of "xx") { t += 5; b.key(ch); }
  t += 500;
  for (const ch of "HU-77") { t += 5; b.key(ch); }
  assert.equal(b.finish(), "HU-77");
});

test("matchExpected returns the canonical spelling, ignoring case and spaces", () => {
  assert.equal(matchExpected(" bin-a1 ", ["BIN-A1", "HU-1"]), "BIN-A1");
  assert.equal(matchExpected("hu-1", [null, "HU-1"]), "HU-1");
  assert.equal(matchExpected("nope", ["BIN-A1"]), null);
  assert.equal(matchExpected("", ["BIN-A1"]), null);
});

test("quantities format without float noise", () => {
  assert.equal(fmtQty(0.1 + 0.2), "0.3");
  assert.equal(fmtQty(5), "5");
  assert.equal(fmtQty("2.50"), "2.5");
  assert.equal(flt("abc"), 0);
});

test("htmlToText and messageFromBody hide markup and tracebacks", () => {
  assert.equal(htmlToText("<b>Item</b> &amp; bin<br>not found"), "Item & bin not found");
  const body = { _server_messages: JSON.stringify([JSON.stringify({ message: "<div>Scanned source does not match the task</div>" })]) };
  assert.equal(messageFromBody(body), "Scanned source does not match the task");
  const tb = { exception: "frappe.exceptions.ValidationError: Invalid confirmed quantity" };
  assert.equal(messageFromBody(tb), "Invalid confirmed quantity");
  assert.equal(messageFromBody({ exception: "Traceback (most recent call last):\n  File \"x\", line 1" }), "");
  assert.equal(messageFromBody(null), "");
});

test("drafts: save/load/clear, TTL expiry, corrupt data", () => {
  const store = new Map();
  const storage = { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, v), removeItem: (k) => store.delete(k), key: (i) => [...store.keys()][i], get length() { return store.size; } };
  let now = 1000;
  const d = createDrafts(storage, { now: () => now, ttl: 5000 });
  assert.equal(d.load("move"), null);
  d.save("move", { qty: "3" });
  assert.deepEqual(d.load("move"), { qty: "3" });
  assert.deepEqual(d.keys(), ["move"]);
  now += 6000;
  assert.equal(d.load("move"), null, "expired");
  store.set("wms.rf.draft.bad", "{not json");
  assert.equal(d.load("bad"), null);
  d.save("a", 1); d.save("b", 2); d.clearAll();
  assert.deepEqual(d.keys(), []);
  const broken = createDrafts({ getItem() { throw new Error("denied"); }, setItem() { throw new Error("denied"); }, removeItem() { throw new Error("x"); } });
  assert.equal(broken.save("k", 1), false);
  assert.equal(broken.load("k"), null);
});

test("routes: parse, resolve, encode round trip", () => {
  const routes = [
    { id: "menu", pattern: "" },
    { id: "task", pattern: "task/:name/:step?" },
    { id: "move", pattern: "move/:step?" },
    { id: "tasks", pattern: "tasks/:group" },
  ];
  assert.equal(resolveRoute(routes, "#/").id, "menu");
  assert.equal(resolveRoute(routes, "").id, "menu");
  const r = resolveRoute(routes, "#/task/WT-0001/quantity?x=1");
  assert.deepEqual(r.params, { name: "WT-0001", step: "quantity" });
  assert.equal(r.query.x, "1");
  assert.equal(resolveRoute(routes, "#/task/WT-0001").params.step, undefined);
  assert.equal(resolveRoute(routes, "#/move").id, "move");
  assert.equal(resolveRoute(routes, "#/nope/xx"), null);
  assert.equal(resolveRoute(routes, "#/task"), null);
  const odd = "A/B #1";
  assert.equal(href("task", odd, "review"), "#/task/A%2FB%20%231/review");
  assert.equal(resolveRoute(routes, href("task", odd, "review")).params.name, odd);
  assert.deepEqual(parseHash("#/a/b?k=v%20w").query, { k: "v w" });
});

function fakeWindow(startHash = "") {
  const entries = [{ url: startHash, state: null }];
  let pos = 0;
  const listeners = [];
  const win = {
    location: { get hash() { return entries[pos].url; } },
    history: {
      get state() { return entries[pos].state; },
      pushState(state, _t, url) { entries.splice(pos + 1); entries.push({ url, state }); pos++; },
      replaceState(state, _t, url) { entries[pos] = { url, state }; },
      back() { this.go(-1); },
      go(n) { const to = pos + n; if (to < 0 || to >= entries.length) return; pos = to; queueMicrotask(() => listeners.forEach((f) => f({ state: entries[pos].state }))); },
    },
    addEventListener(type, fn) { if (type === "popstate") listeners.push(fn); },
  };
  return { win, entries, get pos() { return pos; } };
}

test("navigator: push/back, fallback at depth 0, and reload keeps depth", async () => {
  const seen = [];
  const f = fakeWindow("#/tasks/inbound");
  const nav = createNavigator(f.win, { onNavigate: (h, m) => seen.push([h, Object.keys(m)[0]]) });
  nav.start("#/");
  assert.deepEqual(seen.at(-1), ["#/tasks/inbound", "initial"]);
  nav.back("#/s/inbound"); // depth 0 -> falls back instead of leaving the app
  assert.deepEqual(seen.at(-1), ["#/s/inbound", "replace"]);
  nav.go("#/task/WT-1/source");
  nav.go("#/task/WT-1/quantity");
  assert.equal(nav.depth, 2);
  nav.back();
  await Promise.resolve();
  assert.deepEqual(seen.at(-1), ["#/task/WT-1/source", "popped"]);
  assert.equal(nav.depth, 1);
  // "reload": new navigator over the same window keeps idx from history.state
  const seen2 = [];
  const nav2 = createNavigator(f.win, { onNavigate: (h) => seen2.push(h) });
  nav2.start("#/");
  assert.equal(nav2.depth, 1);
  assert.equal(seen2.at(-1), "#/task/WT-1/source");
});

test("navigator.unwind removes wizard steps from the Back path", async () => {
  const f = fakeWindow("#/tasks/outbound");
  const seen = [];
  const nav = createNavigator(f.win, { onNavigate: (h, m) => seen.push([h, Object.keys(m)[0]]) });
  nav.start("#/");
  nav.go("#/task/WT-1/source");
  nav.go("#/task/WT-1/quantity");
  nav.go("#/task/WT-1/review");
  nav.unwind(3, "#/tasks/outbound");
  await Promise.resolve();
  assert.deepEqual(seen.at(-1), ["#/tasks/outbound", "replace"]);
  assert.equal(nav.depth, 0);
  assert.equal(f.win.location.hash, "#/tasks/outbound");
});

test("api: server message extraction, network error, timeout, session expiry, read retry", async () => {
  setMessages({});
  let calls = 0;
  const jsonRes = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body });

  configureApi({ csrf: "tok", timeoutMs: 50, fetch: async () => jsonRes(417, { _server_messages: JSON.stringify([JSON.stringify({ message: "<b>Bad</b> scan" })]) }) });
  await assert.rejects(api("m", {}), (e) => e instanceof AppError && e.kind === "validation" && e.message === "Bad scan");

  configureApi({ fetch: async () => { throw new TypeError("Failed to fetch"); } });
  await assert.rejects(api("m", {}), (e) => e.kind === "network" && e.retryable);

  configureApi({ fetch: (_u, o) => new Promise((_res, rej) => o.signal.addEventListener("abort", () => rej(Object.assign(new Error("a"), { name: "AbortError" })))) });
  await assert.rejects(api("m", {}), (e) => e.kind === "timeout" && e.retryable);

  let expired = 0;
  configureApi({ onSessionExpired: () => expired++, fetch: async (url) => (String(url).includes("get_logged_user") ? jsonRes(401, {}) : jsonRes(401, {})) });
  await assert.rejects(api("m", {}), (e) => e.kind === "session");
  assert.equal(expired, 1);

  // Frappe answers a logged-out user with 403 + a readable message; that must still count as an expired session.
  expired = 0;
  configureApi({ onSessionExpired: () => expired++, fetch: async (url) => (String(url).includes("get_logged_user") ? jsonRes(200, { message: "Guest" }) : jsonRes(403, { _server_messages: JSON.stringify([JSON.stringify({ message: "Login to access" })]) })) });
  await assert.rejects(api("m", {}), (e) => e.kind === "session");
  assert.equal(expired, 1);

  configureApi({ fetch: async () => { calls++; if (calls < 3) throw new TypeError("x"); return jsonRes(200, { message: { ok: 1 } }); } });
  assert.deepEqual(await api("m", {}, { read: true }), { ok: 1 });
  assert.equal(calls, 3);
  calls = 0;
  configureApi({ fetch: async () => { calls++; throw new TypeError("x"); } });
  await assert.rejects(api("m", {}), (e) => e.kind === "network");
  assert.equal(calls, 1, "mutations are never auto-retried");
});

test("i18n fills placeholders and falls back to English", () => {
  setMessages({ Hello: "Hola {0}" });
  assert.equal(_("Hello", ["Ana"]), "Hola Ana");
  assert.equal(_("Untranslated {0}/{1}", [1, 2]), "Untranslated 1/2");
});
