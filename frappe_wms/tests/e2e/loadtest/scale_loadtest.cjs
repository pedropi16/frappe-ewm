// Scaled-up multi-operator load test against PRODUCTION.
// Tier 1: 20 concurrent genuine RF browser sessions (real UI clicks/scans) - this is what
// actually surfaces usability problems, so it stays real Playwright, not simulated.
// Tier 2: 30 concurrent lightweight "desk" actors (plain authenticated fetch, no browser) doing
// work that has no RF screen anyway (POs, releasing deliveries, approving counts) - reaching
// real 50-concurrent-actor backend load without 50 browser processes on a 6.9GB host.
const path = require("path");
const dns = require("dns");
const { chromium } = require("playwright-core");
const fs = require("fs");

const BASE = process.env.LOADTEST_BASE_URL || "https://erp.pinohomelab.duckdns.org";
const PASSWORD = process.env.LOADTEST_PASSWORD;
if (!PASSWORD) { console.error("Set LOADTEST_PASSWORD"); process.exit(1); }
const SHOT_DIR = process.env.LOADTEST_SHOT_DIR || path.join(__dirname, "shots-scale");
fs.mkdirSync(SHOT_DIR, { recursive: true });

const TARGET_HOST = new URL(BASE).hostname;
// Confirmed live: when this script and the production stack run on the SAME host, resolving the
// public domain hairpins out through the home router's NAT and back in - fine for one request at
// a time, but under real concurrent connections this degraded every "open delivery" page load to
// 15-30+ seconds, even though gunicorn/nginx/the app itself all answered a direct loopback hit in
// single-digit milliseconds throughout. Set LOADTEST_SKIP_HAIRPIN_BYPASS=1 to disable this (e.g.
// running the script from a genuinely different machine, where the public DNS answer is correct).
if (!process.env.LOADTEST_SKIP_HAIRPIN_BYPASS) {
  const bypassIP = process.env.LOADTEST_BYPASS_IP || "127.0.0.1";
  const originalLookup = dns.lookup;
  dns.lookup = (hostname, options, callback) => {
    if (typeof options === "function") { callback = options; options = {}; }
    if (hostname === TARGET_HOST) {
      if (options && options.all) return callback(null, [{ address: bypassIP, family: 4 }]);
      return callback(null, bypassIP, 4);
    }
    return originalLookup(hostname, options, callback);
  };
  console.log(`[hairpin bypass] ${TARGET_HOST} -> ${bypassIP} for this process's own DNS lookups (Node fetch/http); Chromium gets its own --host-resolver-rules below.`);
}

const RF_NAMES = ["Ana","Bruno","Carla","Diego","Elena","Felipe","Gabriela","Hugo","Irene","Javier",
  "Karina","Luis","Marta","Nico","Olivia","Pablo","Quinn","Rosa","Santiago","Teresa"];
const RF_PERSONAS = RF_NAMES.map((name, i) => ({
  email: `loadtest.${name.toLowerCase()}@pinohomelab.test`, resource: `LOADTEST-RF${i + 1}`, name,
}));
const DESK_ACTORS = Array.from({ length: 30 }, (_, i) => ({
  email: `loadtest.desk${String(i + 1).padStart(2, "0")}@pinohomelab.test`, name: `Desk${String(i + 1).padStart(2, "0")}`,
}));

const findings = [];
function note(kind, persona, msg, extra) {
  const entry = { t: new Date().toISOString(), kind, persona, msg, ...extra };
  findings.push(entry);
  console.log(`[${kind}] (${persona}) ${msg}${extra ? " " + JSON.stringify(extra) : ""}`);
}
function dump(tag) { try { fs.writeFileSync(`${SHOT_DIR}/findings-${tag}.json`, JSON.stringify(findings, null, 2)); } catch (e) {} }

async function timed(persona, label, fn) {
  const t0 = Date.now();
  try { const r = await fn(); const ms = Date.now() - t0; if (ms > 3000) note("PERF", persona, `${label} took ${ms}ms (slow)`, { ms }); return r; }
  catch (e) { note("BUG", persona, `${label} threw: ${e.message}`, {}); throw e; }
}
async function shot(page, name) { try { await page.screenshot({ path: `${SHOT_DIR}/${name}.png` }); } catch (e) {} }

function launchOptsFor() {
  const launchOpts = { args: ["--no-sandbox"] };
  if (!process.env.LOADTEST_SKIP_HAIRPIN_BYPASS) {
    const bypassIP = process.env.LOADTEST_BYPASS_IP || "127.0.0.1";
    launchOpts.args.push(`--host-resolver-rules=MAP ${TARGET_HOST} ${bypassIP}`);
  }
  if (process.env.CHROMIUM_EXECUTABLE_PATH) launchOpts.executablePath = process.env.CHROMIUM_EXECUTABLE_PATH;
  return launchOpts;
}

async function newSession(browser, persona) {
  const context = await browser.newContext({ viewport: { width: 390, height: 780 } });
  const page = await context.newPage();
  page.on("pageerror", (e) => note("BUG", persona.name, `JS error: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error" && !/favicon|socket\.io/i.test(m.text())) note("BUG", persona.name, `console.error: ${m.text().slice(0, 200)}`); });
  page.on("response", async (res) => {
    if (res.status() >= 400) {
      let body = ""; try { body = (await res.text()).slice(0, 400); } catch (e) {}
      note("BUG", persona.name, `HTTP ${res.status()} on ${res.url().replace(BASE, "")}`, { body });
    }
  });
  if (DEBUG_TIMING) {
    const apiStart = new Map();
    page.on("request", (req) => { if (req.url().includes("/api/method/")) apiStart.set(req.url() + req.postData(), Date.now()); });
    page.on("requestfinished", (req) => {
      if (!req.url().includes("/api/method/")) return;
      const k = req.url() + req.postData();
      const t0 = apiStart.get(k);
      if (t0) dbg(persona.name, `API ${req.url().replace(BASE, "")} finished after ${Date.now() - t0}ms`);
    });
    page.on("requestfailed", (req) => {
      if (!req.url().includes("/api/method/")) return;
      dbg(persona.name, `API ${req.url().replace(BASE, "")} FAILED: ${req.failure()?.errorText}`);
    });
  }
  return { context, page };
}

async function login(page, persona) {
  await timed(persona.name, "login", async () => {
    await page.goto(`${BASE}/login`);
    await page.fill("#login_email", persona.email);
    await page.fill("#login_password", PASSWORD);
    await page.click(".btn-login");
    await page.waitForLoadState("networkidle", { timeout: 20000 }).catch(() => {});
  });
}
async function openRF(page, persona) {
  await timed(persona.name, "open RF app", async () => {
    await page.goto(`${BASE}/wms`);
    await page.waitForFunction(() => window.WMS_BOOTED === true, { timeout: 20000 });
  });
}
async function pickDevice(page, persona) {
  await page.waitForTimeout(400);
  const hash = await page.evaluate(() => location.hash);
  if (!hash.startsWith("#/logon")) { note("FLOW", persona.name, "Resumed already-logged-on session"); return true; }
  const btn = page.locator("button", { hasText: persona.resource });
  const has = await btn.first().waitFor({ timeout: 15000 }).then(() => true).catch(() => false);
  if (!has) { note("BUG", persona.name, `Resource ${persona.resource} not offered on logon`); await shot(page, `${persona.name}-logon-missing`); return false; }
  await timed(persona.name, "pick device", async () => { await btn.first().click(); await page.waitForTimeout(300); });
  return true;
}

async function scan(page, code) { await page.keyboard.type(String(code), { delay: 0 }); await page.keyboard.press("Enter"); await page.waitForTimeout(200); }
async function focusField(page, name) { const el = page.locator(`[data-fk="${name}"]`).first(); await el.waitFor({ timeout: 8000 }); await el.click(); return el; }
async function fillScanField(page, name, value) { const el = await focusField(page, name); await el.fill(""); await scan(page, value); }
async function tapPrimary(page, label) { const btn = page.locator("#actionbar button, .actionbar button, button", { hasText: label }).first(); await btn.waitFor({ timeout: 8000 }); await btn.click(); await page.waitForTimeout(250); }
async function tapButton(page, label) { const btn = page.locator("button", { hasText: label }).first(); await btn.waitFor({ timeout: 8000 }); await btn.click(); await page.waitForTimeout(350); }
// textContent() is an auto-waiting ACTION, not a presence check: on a locator matching zero
// elements (the normal case - most of the time there's no notice showing) it retries for
// Playwright's default 30s before giving up, which the .catch() here silently swallowed as "no
// notice" while actually blocking the whole call for 30s. Measured live: this exact bug, not any
// server/network issue, was the entire "~30s stall under concurrent load" this file kept
// reporting - the server answered receiving_worklist in 37-57ms every time. count() first avoids
// the auto-wait entirely.
async function textOrEmpty(page, selector) {
  const el = page.locator(selector).first();
  if (!(await el.count().catch(() => 0))) return "";
  return (await el.textContent().catch(() => "")) || "";
}
async function noticeText(page) { return textOrEmpty(page, "#notice, .notice"); }

// Loading() and Empty() (ui/kit.js) both render a ".empty" div, so waiting for ".line-head, .empty"
// alone can resolve the instant a spinner appears - indistinguishable from the screen actually
// having settled. Poll until there are real lines, or an empty-state whose text isn't the loading
// spinner's own placeholder text.
const DEBUG_TIMING = !!process.env.LOADTEST_DEBUG_TIMING;
function dbg(persona, msg) { if (DEBUG_TIMING) console.log(`[TIMING ${new Date().toISOString()}] (${persona}) ${msg}`); }

// lineSelector defaults to ".line-head" (count.js, ship.js render their detail lines with that
// class directly) - but receive.js is the odd one out: its lines render through the shared Card()
// component (ui/kit.js), so each line is ".card", not ".line-head". Confirmed live by dumping the
// actual #app innerHTML mid-poll: with ".line-head" as the only selector, receive's screen was
// settling correctly (lines fully rendered within ~1-2s, real product cards with real quantities)
// every single time - this helper just never recognized it, so it ran to its own full timeoutMs on
// EVERY call for receive, indistinguishable from a genuine hang until the DOM was inspected directly.
async function waitForSettled(page, timeoutMs, persona, lineSelector = ".line-head") {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const lineCount = await page.locator(lineSelector).count().catch(() => 0);
    if (lineCount > 0) return "lines";
    const emptyText = await textOrEmpty(page, ".empty");
    if (emptyText && !/loading/i.test(emptyText)) return "empty";
    await page.waitForTimeout(250);
  }
  return "timeout";
}

// Polls for the hash to move to a DIFFERENT #/task/:name/:step than lastHash, instead of a flat
// sleep-then-read-once - the same family of bug every other "fixed sleep, then check" spot in this
// file had: under real concurrent load, advance()'s nav.go() (client-side scan match) is near-
// instant, but confirmTask()'s server round-trip on "review" is not, so a short flat wait can read
// the SAME step twice, double-submitting a scan or a stale Confirm click and burning a guard
// iteration without the wizard actually progressing.
async function waitForStepChange(page, lastHash, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const hash = await page.evaluate(() => location.hash);
    if (hash !== lastHash) return hash;
    await page.waitForTimeout(200);
  }
  return lastHash;
}

async function driveTaskWizard(page, persona) {
  let hash = await page.evaluate(() => location.hash);
  const initial = hash.match(/#\/task\/([^/]+)\//);
  const initialTask = initial && initial[1];
  for (let guard = 0; guard < 6; guard++) {
    const m = hash.match(/#\/task\/([^/]+)\/(\w+)/);
    if (!m) return hash;
    const step = m[2];
    const before = hash;
    if (step === "review") { await tapPrimary(page, "Confirm"); }
    else if (step === "quantity") { await tapPrimary(page, "Next"); }
    else {
      const codeEl = page.locator(".expect .code").first();
      if (!(await codeEl.count())) { note("BUG", persona.name, `Task wizard step "${step}" shows no expected code`); return hash; }
      const expected = (await codeEl.textContent()).trim();
      const fieldName = { source: "src", product: "prod", destination: "dst" }[step] || step;
      await fillScanField(page, fieldName, expected);
    }
    hash = await waitForStepChange(page, before, 10000);
    if (hash === before) { note("BUG", persona.name, `Task wizard stuck on step "${step}" (no change after 10s)`); return "stuck"; }
    // confirmTask() either unwinds to the tasks list (done) or, if confirming this task released
    // another in the same Warehouse Order, auto-chains straight to it (confirmTask's own
    // nav.unwind(..., href("task", nextTask.name))) - either way this ONE task is confirmed; let
    // the outer pullAndWorkLoop's next "Get next work" pick up the chained task fresh rather than
    // keep driving it here against the same 6-step guard meant for one task.
    const next = hash.match(/#\/task\/([^/]+)\//);
    if (step === "review" && (!next || next[1] !== initialTask)) return "confirmed";
  }
  note("BUG", persona.name, "Task wizard did not reach review/confirm within 6 steps");
  return "guard-exceeded";
}

// Waits for pullWork() (tasks.js) to actually resolve: either the hash moves to #/task/:name
// (work was found and assigned), or the notice changes to something NEW. A fixed sleep here has
// the exact same failure mode receive.js's screen had - every "stopped pulling" log in earlier
// runs showed the notice of the PRIOR action ("Joined DC1 Putaway Queue", from joinQueue() minutes
// earlier, sitting there on its own ~6s TTL) because the check ran before pull_next_warehouse_order
// had a chance to reply under real concurrent load, not because the queue was ever actually empty.
async function waitForPullResult(page, beforeNotice, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const hash = await page.evaluate(() => location.hash);
    if (/#\/task\//.test(hash)) return { kind: "task", hash };
    const nt = await noticeText(page);
    if (nt && nt !== beforeNotice) return { kind: "notice", notice: nt };
    await page.waitForTimeout(250);
  }
  return { kind: "timeout" };
}

async function pullAndWorkLoop(page, persona, group, maxIterations) {
  let confirmed = 0;
  for (let i = 0; i < maxIterations; i++) {
    try {
      await page.goto(`${BASE}/wms#/tasks/${group}`);
      await page.waitForTimeout(350);
      const pullBtn = page.locator("button", { hasText: "Get next work" });
      if (!(await pullBtn.count())) break;
      const beforeNotice = await noticeText(page);
      let result;
      await timed(persona.name, `pull next (${group}) #${i + 1}`, async () => {
        await pullBtn.first().click();
        result = await waitForPullResult(page, beforeNotice, 15000);
      });
      if (result.kind === "timeout") { note("BUG", persona.name, `${group}: "Get next work" neither assigned a task nor showed a new notice within 15s`); break; }
      if (result.kind === "notice") {
        if (/No work waiting/i.test(result.notice)) note("INFO", persona.name, `${group} queue empty after confirming ${confirmed} task(s)`);
        else note("INFO", persona.name, `${group}: stopped pulling (notice="${result.notice}")`);
        break;
      }
      const outcome = await driveTaskWizard(page, persona);
      if (outcome === "confirmed") confirmed++;
      else break; // "stuck" / "guard-exceeded": already logged a BUG inside driveTaskWizard
    } catch (e) {
      // A single stuck task (e.g. a scan field that never appears) must not take down this
      // persona's whole race, let alone the Promise.all every concurrent racer shares.
      note("BUG", persona.name, `${group} pull loop #${i + 1} threw, abandoning this persona's race: ${e.message}`);
      break;
    }
  }
  return confirmed;
}

async function joinQueue(page, persona, queueTextMatch) {
  await page.goto(`${BASE}/wms#/session`);
  await page.waitForTimeout(400);
  if (await page.locator(`text=${queueTextMatch}`).count()) { return; }
  const leaveBtn = page.locator("button", { hasText: "Leave queue" });
  if (await leaveBtn.count()) { await leaveBtn.first().click(); await page.waitForTimeout(400); }
  await tapButton(page, "Join a queue");
  await page.waitForTimeout(400);
  const q = page.locator("button", { hasText: queueTextMatch }).first();
  if (!(await q.count())) { note("BUG", persona.name, `No queue matching "${queueTextMatch}" offered`); return; }
  await q.click();
  // act() (session.js) only calls notify.ok() AFTER run()'s finally has cleared S.busy, so waiting
  // for "Leave queue" to appear (which only renders once r.current_queue is set, i.e. act()'s own
  // api()+refreshSession() round-trip has actually completed) guarantees S.busy is clear before
  // this returns - a flat 400ms sleep does not: under real concurrent load that round-trip can
  // outlast 400ms, leaving S.busy still 1 when the very next screen's own run() call (pullWork()'s
  // "Get next work") hits its exclusive-busy guard and silently no-ops - no hash change, no notice,
  // nothing to see, which is exactly what showed up as "Get next work" timeouts downstream.
  const joined = await page.locator("button", { hasText: "Leave queue" }).first().waitFor({ timeout: 10000 }).then(() => true).catch(() => false);
  if (!joined) note("BUG", persona.name, `Joining "${queueTextMatch}" never showed "Leave queue" within 10s`);
}

async function apiCall(page, method, args) {
  return await page.evaluate(async ({ method, args }) => {
    const res = await fetch(`/api/method/${method}`, { method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json", "X-Frappe-CSRF-Token": window.WMS.csrf },
      body: JSON.stringify(args || {}) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(`${method} -> HTTP ${res.status}: ${(data && (data.exception || data._server_messages)) || res.statusText}`);
    return data.message;
  }, { method, args });
}

// ---- Tier 2: lightweight desk actors, plain fetch, no browser ----
async function deskLogin(actor) {
  const loginRes = await fetch(`${BASE}/api/method/login`, {
    method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: `usr=${encodeURIComponent(actor.email)}&pwd=${encodeURIComponent(PASSWORD)}`,
    redirect: "manual",
  });
  const cookies = typeof loginRes.headers.getSetCookie === "function" ? loginRes.headers.getSetCookie() : (loginRes.headers.get("set-cookie") ? [loginRes.headers.get("set-cookie")] : []);
  const jar = cookies.map((c) => c.split(";")[0]).join("; ");
  if (!jar.includes("sid=")) { note("BUG", actor.name, `desk login failed (status ${loginRes.status})`); return null; }
  // frappe.sessions.get_csrf_token isn't whitelisted in this Frappe version - pull the token the
  // same place the RF app's own boot script embeds it, straight in the /wms page HTML.
  const pageRes = await fetch(`${BASE}/wms`, { headers: { Cookie: jar } }).catch(() => null);
  const html = pageRes && pageRes.ok ? await pageRes.text() : "";
  const m = html.match(/"csrf"\s*:\s*"([a-f0-9]+)"/i);
  const csrf = m ? m[1] : null;
  if (!csrf) note("BUG", actor.name, "desk login: could not extract CSRF token from /wms boot HTML");
  return { jar, csrf };
}
async function deskCall(session, method, args) {
  const headers = { "Content-Type": "application/json", Accept: "application/json", Cookie: session.jar };
  if (session.csrf) headers["X-Frappe-CSRF-Token"] = session.csrf;
  const res = await fetch(`${BASE}/api/method/${method}`, { method: "POST", headers, body: JSON.stringify(args || {}) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(`${method} -> HTTP ${res.status}: ${(data && (data.exception || data._server_messages)) || res.statusText}`);
  return data.message;
}

async function deskActorRun(actor, task) {
  const session = await deskLogin(actor);
  if (!session) return;
  try { await task(session, actor); }
  catch (e) { note("BUG", actor.name, `desk task failed: ${e.message}`); }
}

// 20 concurrent Chromium CONTEXTS sharing one browser process measured ~7.2GB RSS on this 6.9GB
// host - that caused Stage 1 to hang permanently on "Loading...", and a smaller-but-still-shared
// 6-context wave reproduced a milder version of the same thing: real opens taking 30-60s even
// though gunicorn/nginx/the app itself all answered the identical concurrent load in single-digit
// milliseconds when driven directly (curl, raw HTTP/2) bypassing the browser. Confirmed directly:
// giving one context per SEPARATE browser PROCESS instead (one per persona - what a real RF device
// actually is, each with its own browser) made the stall disappear entirely, settling in <100ms
// each. So each persona now gets its own `chromium.launch()`, not a shared context. Measured here:
// ~607MB RSS per browser process, so WAVE_SIZE is sized to fit comfortably in what's left after
// production's own containers, not to the 7.2GB/20-context ceiling that applied to the old
// shared-process model. Desk actors stay cheap (plain fetch, no browser) so all 30 still run as
// genuine concurrent actors, just once, after the RF waves.
const WAVE_SIZE = 4;
function chunk(arr, size) { const out = []; for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size)); return out; }
const WAVES = chunk(RF_PERSONAS, WAVE_SIZE);
const deliveryNames = (process.env.LOADTEST_IBD_NAMES || "").split(",").filter(Boolean);

async function runWave(waveIndex, personas, { doReceive, doPutaway, doMoveRepack, doPick, doCount, doShip }) {
  console.log(`\n########## WAVE ${waveIndex + 1}/${WAVES.length}: ${personas.map((p) => p.name).join(", ")} ##########`);
  const sessions = {};
  try {
    for (const persona of personas) {
      const browser = await chromium.launch(launchOptsFor());
      const { context, page } = await newSession(browser, persona);
      sessions[persona.name] = { browser, context, page, persona };
    }

    console.log(`=== Wave ${waveIndex + 1}: login + pick device (${personas.length} personas) ===`);
    for (const persona of personas) {
      const s = sessions[persona.name];
      await login(s.page, persona);
      await openRF(s.page, persona);
      await pickDevice(s.page, persona);
    }
    dump(`wave${waveIndex + 1}-login`);

    if (doReceive) {
      console.log(`=== Wave ${waveIndex + 1}: receiving, ${personas.length} concurrent receivers across ${deliveryNames.length} fresh deliveries ===`);
      const perReceiver = Math.ceil(deliveryNames.length / personas.length);
      await Promise.all(personas.map((persona, i) => {
        const names = deliveryNames.slice(i * perReceiver, (i + 1) * perReceiver);
        if (!names.length) return Promise.resolve();
        return receiveAll(sessions[persona.name].page, persona, names);
      }));
      dump(`wave${waveIndex + 1}-receive`);
    }

    if (doPutaway) {
      console.log(`=== Wave ${waveIndex + 1}: Putaway race, ${personas.length}-way concurrent ===`);
      for (const p of personas) await joinQueue(sessions[p.name].page, p, "Putaway");
      const results = await Promise.all(personas.map((p) => pullAndWorkLoop(sessions[p.name].page, p, "inbound", 25)));
      note("INFO", "orchestrator", `Wave ${waveIndex + 1} Putaway race done: ${results.reduce((a, b) => a + b, 0)} total tasks confirmed`, { perPersona: Object.fromEntries(personas.map((p, i) => [p.name, results[i]])) });
      dump(`wave${waveIndex + 1}-putaway`);
    }

    if (doMoveRepack) {
      console.log(`=== Wave ${waveIndex + 1}: ad-hoc Move/Repack, 2 personas ===`);
      await Promise.all(personas.slice(0, 2).map((p) => stageMoveRepack(sessions[p.name].page, p)));
      dump(`wave${waveIndex + 1}-moverepack`);
    }

    if (doPick) {
      console.log(`=== Wave ${waveIndex + 1}: Pick race, ${personas.length}-way concurrent ===`);
      for (const p of personas) await joinQueue(sessions[p.name].page, p, "Pick");
      const results = await Promise.all(personas.map((p) => pullAndWorkLoop(sessions[p.name].page, p, "outbound", 25)));
      note("INFO", "orchestrator", `Wave ${waveIndex + 1} Pick race done: ${results.reduce((a, b) => a + b, 0)} total tasks confirmed`, { perPersona: Object.fromEntries(personas.map((p, i) => [p.name, results[i]])) });
      dump(`wave${waveIndex + 1}-pick`);
    }

    if (doCount) {
      console.log(`=== Wave ${waveIndex + 1}: Counting, ${personas.length} personas via #/count ===`);
      await Promise.all(personas.map((p) => stageCount(sessions[p.name].page, p)));
      dump(`wave${waveIndex + 1}-count`);
    }

    if (doShip) {
      console.log(`=== Wave ${waveIndex + 1}: Ship + Pack check ===`);
      await stageShipAndPack(sessions[personas[0].name].page, personas[0]);
      dump(`wave${waveIndex + 1}-ship`);
    }
  } finally {
    await Promise.all(Object.values(sessions).map((s) => s.browser.close().catch(() => {})));
  }
  console.log(`Wave ${waveIndex + 1} done. Findings so far:`, findings.length);
}

async function runDeskActors() {
  console.log("\n=== DESK ACTORS: 30 concurrent, API-only (no RF screens for any of this) ===");
  const deskResults = { released: 0, failed: 0, posCreated: 0 };
  await Promise.all(DESK_ACTORS.map((actor, i) => deskActorRun(actor, async (session, a) => {
    if (i % 3 === 0) {
      const awaiting = await deskCall(session, "frappe_wms.api.outbound.list_awaiting_release", {});
      for (const d of (awaiting || []).slice(0, 2)) {
        try { await deskCall(session, "frappe_wms.api.outbound.release_delivery_for_picking", { delivery_name: d.name, strategy: "Single Order" }); deskResults.released++; }
        catch (e) { deskResults.failed++; note("BUG", a.name, `release_delivery_for_picking(${d.name}) failed: ${e.message}`); }
      }
    } else if (i % 3 === 1) {
      // per_delivered < 100 can under-exclude a Sales Order that's already effectively fully
      // delivered by quantity (float rounding) - every desk actor hitting the SAME stale
      // candidate first also just amplifies that into redundant noise, so fetch a wider pool and
      // pick randomly rather than always [0].
      const sos = await deskCall(session, "frappe.client.get_list", { doctype: "Sales Order", filters: { docstatus: 1, per_delivered: ["<", 100] }, fields: ["name", "company"], limit_page_length: 20 });
      const pick = sos && sos.length ? [sos[Math.floor(Math.random() * sos.length)]] : [];
      for (const so of pick) {
        try {
          const created = await deskCall(session, "frappe_wms.api.outbound.create_outbound_delivery_from_sales_order", { sales_order_name: so.name, warehouse: "DC1" });
          const doc = await deskCall(session, "frappe.client.get", { doctype: "Outbound Delivery", name: created });
          await deskCall(session, "frappe.client.submit", { doc: JSON.stringify(doc) });
          deskResults.posCreated++;
        } catch (e) { note("BUG", a.name, `create_outbound_delivery_from_sales_order(${so.name}) failed: ${e.message}`); }
      }
    } else {
      try {
        const pending = await deskCall(session, "frappe.client.get_list", { doctype: "WMS Physical Inventory Count", filters: { status: "Under Review" }, fields: ["name"], limit_page_length: 3 });
        for (const c of (pending || [])) { await deskCall(session, "frappe_wms.api.inventory.approve_variance", { count_name: c.name, remarks: "Desk actor load test approval" }); }
      } catch (e) { /* fine if none pending */ }
    }
  })));
  note("INFO", "orchestrator", `Desk actors done: ${JSON.stringify(deskResults)}`);
  dump("desk-actors");
  console.log("Desk actors stage done. Findings so far:", findings.length);
}

(async () => {
  // Wave 1: receive + putaway race + move/repack (where the real inbound-side work and
  // concurrency stress happens). Wave 2: putaway continuation + pick race. Desk actors (release
  // for picking) run once, between waves, so Wave 2's pick race has real demand to race over.
  // Wave 3 (whatever's left, <=8): pick continuation + counting + ship/pack.
  await runWave(0, WAVES[0], { doReceive: true, doPutaway: true, doMoveRepack: true });
  await runDeskActors();
  for (let i = 1; i < WAVES.length; i++) {
    const isLast = i === WAVES.length - 1;
    await runWave(i, WAVES[i], { doPutaway: true, doPick: true, doCount: isLast, doShip: isLast });
  }

  dump("all");
  console.log("\n=== LOAD TEST COMPLETE ===");
  console.log(`Total findings: ${findings.length}`);
  for (const kind of ["BUG", "FLOW", "PERF", "INFO"]) console.log(`  ${kind}: ${findings.filter((f) => f.kind === kind).length}`);
})().catch((e) => { console.error("FATAL:", e); dump("fatal"); process.exit(1); });

async function receiveAll(page, persona, deliveryNames) {
  for (const name of deliveryNames) {
    await timed(persona.name, `open delivery for ${name}`, async () => {
      await page.goto(`${BASE}/wms#/receive/${name}`);
      // receive.js renders its lines through the shared Card() component, so they're ".card", not
      // ".line-head" (see waitForSettled's own comment) - pass that explicitly here.
      const settled = await waitForSettled(page, 20000, persona.name, ".card");
      if (settled === "timeout") note("BUG", persona.name, `${name}: screen never settled (still showing a loading spinner after 20s) - possible backend stall under concurrent load`);
    });
    const lineHeads = await page.locator(".card").allTextContents().catch(() => []);
    if (!lineHeads.length) {
      const emptyText = await textOrEmpty(page, ".empty");
      note("FLOW", persona.name, `${name}: no open lines to receive (screen: "${(emptyText || "").trim() || "no empty-state text found"}")`);
      continue;
    }
    note("INFO", persona.name, `${name}: ${lineHeads.length} line(s): ${lineHeads.join(", ")}`);
    // receive.js has no per-line inline fields (no "hu0"/"type0") - it's one line at a time: tap an
    // open Card to select it (renders a single generic entryView with data-fk="hu"/"batch"/"serial"/
    // "qty" for whichever line is active), fill what that line needs, "Add to receipt", then the
    // list re-renders and the next open Card gets tapped. Only after every line wanted is queued
    // does "Post receipt (N rows)" submit them all together - confirmed against receive.js's actual
    // render()/entryView()/actions(), not guessed.
    let added = 0;
    for (let i = 0; i < lineHeads.length; i++) {
      try {
        const openCard = page.locator(".card[role=\"button\"]:not(.dim)").first();
        if (!(await openCard.count().catch(() => 0))) break;
        await openCard.click();
        const huField = page.locator('[data-fk="hu"]').first();
        if (!(await huField.waitFor({ timeout: 8000 }).then(() => true).catch(() => false))) {
          note("BUG", persona.name, `${name}: line ${i}: entry view never opened after tapping a card`);
          break;
        }
        const barcode = `SSCC${persona.resource.replace(/\D/g, "")}${String(Date.now()).slice(-7)}${i}`;
        await fillScanField(page, "hu", barcode);
        if (await page.locator('[data-fk="batch"]').count().catch(() => 0)) {
          await page.locator('[data-fk="batch"]').fill(`LOT-${Date.now().toString().slice(-8)}`).catch(() => {});
        }
        if (await page.locator('[data-fk="serial"]').count().catch(() => 0)) {
          // addEntry() only requires at least one serial scanned, not the full open quantity -
          // one is a valid (partial) receipt line, same as a real operator who scans what's in hand.
          await fillScanField(page, "serial", `SN-${Date.now().toString().slice(-9)}${i}`);
        } else if (await page.locator('[data-fk="qty"]').count().catch(() => 0)) {
          await page.locator('[data-fk="qty"]').fill("1").catch(() => {});
        }
        await tapPrimary(page, "Add to receipt");
        if (await huField.count().catch(() => 0)) {
          note("BUG", persona.name, `${name}: line ${i}: "Add to receipt" didn't clear the entry view (${await noticeText(page)})`);
          break;
        }
        added++;
      } catch (e) {
        note("BUG", persona.name, `${name}: line ${i} threw: ${e.message}`);
        break;
      }
    }
    if (!added) { note("FLOW", persona.name, `${name}: no lines could be added to the receipt`); continue; }
    // submit() (receive.js) on success calls finishFlow(..., "#/tasks/inbound", ...), which
    // auto-navigates away - on failure (a validation error) it stays on this same #/receive/:name
    // screen instead. A flat 1000ms sleep raced that under real concurrent load: the very next
    // loop iteration's goto() to the NEXT delivery could fire while the app's OWN post-submit
    // navigation to the Putaway queue was still in flight, so the next delivery's own queries
    // occasionally picked up leftover Putaway-task ".card" elements mid-transition (surfacing as
    // "Line X does not belong to Inbound Delivery Y" - a stale card, not a real app bug). Wait for
    // either the hash to actually leave this delivery, or a new notice (failure, stays put).
    const beforeNotice = await noticeText(page);
    let noticeTxt = "";
    await timed(persona.name, `post receipt ${name}`, async () => {
      await tapPrimary(page, "Post receipt");
      const deadline = Date.now() + 15000;
      while (Date.now() < deadline) {
        const hash = await page.evaluate(() => location.hash);
        if (!hash.includes(`/receive/${name}`)) break;
        const nt = await noticeText(page);
        if (nt && nt !== beforeNotice) break;
        await page.waitForTimeout(250);
      }
    });
    noticeTxt = await noticeText(page);
    const looksLikeFailure = /error|fail|requires|does not have|not found|could not|permission|no matching/i.test(noticeTxt) && !/posted/i.test(noticeTxt);
    if (looksLikeFailure) note("BUG", persona.name, `${name}: error after posting: ${noticeTxt}`);
    else note("INFO", persona.name, `${name}: receipt posted (${added} line(s), ${noticeTxt || "no confirmation text"})`);
  }
}

async function stageMoveRepack(page, persona) {
  try {
  const hus = await apiCall(page, "frappe_wms.api.handling_unit.list_handling_units", {}).catch(() => []);
  const candidates = (hus || []).filter((u) => u.current_bin && !/RECEIVING/i.test(u.current_bin) && u.stock_status && u.stock_status !== "Empty");
  if (!candidates.length) { note("FLOW", persona.name, "No putaway stock found for Move/Repack"); return; }
  // Randomized, not "always the first match": several personas running this concurrently
  // otherwise deterministically grab the exact same HU and collide on real, but uninteresting,
  // "already nested" contention - a realistic floor would have each worker pick a different pallet.
  const landed = candidates[Math.floor(Math.random() * candidates.length)];
  const detail = await apiCall(page, "frappe_wms.api.handling_unit.handling_unit_detail", { hu_name: landed.name }).catch(() => null);
  if (!detail || !(detail.stock || [])[0]) return;
  await timed(persona.name, "repack whole HU", async () => {
    await page.goto(`${BASE}/wms#/repack`);
    await page.waitForTimeout(400);
    await fillScanField(page, "hu", detail.name);
    await page.waitForTimeout(600);
    const wholeHuCard = page.locator(".card", { hasText: "This entire Handling Unit" }).first();
    if (!(await wholeHuCard.count())) { note("BUG", persona.name, `Repack: no "This entire Handling Unit" option for ${detail.name}`); return; }
    await wholeHuCard.click();
    await page.waitForTimeout(250);
    await page.locator('[data-fk="newtype"]').selectOption("TOTE").catch(() => {});
    await tapButton(page, "Create as destination");
    await page.waitForTimeout(600);
    await tapPrimary(page, "Repack");
    await page.waitForTimeout(900);
  });
  const rp = await noticeText(page);
  if (rp && /error|fail/i.test(rp)) note("BUG", persona.name, `Repack failed: ${rp}`);
  else note("INFO", persona.name, `Repack posted (${rp || "no confirmation"})`);
  } catch (e) {
    note("BUG", persona.name, `Move/Repack stage threw: ${e.message}`);
  }
}

async function stageCount(page, persona) {
  try {
  await page.goto(`${BASE}/wms#/count`);
  await page.waitForTimeout(600);
  const card = page.locator(".card").first();
  if (!(await card.count())) { note("FLOW", persona.name, "No open counts on #/count"); return; }
  await card.click();
  // snapshot_count fires on open (count.js enter()). On success it renders this screen's own
  // ".line-head" lines - but on failure (e.g. "No stock found for the given count scope", a real,
  // expected outcome whenever a bin's open counts outpace what's actually been put away yet) it
  // redirects straight back to "#/count", the LIST screen, which renders through the shared
  // listScreen() helper using ".card"/".empty", never ".line-head". Waiting on ".line-head" alone
  // can't tell that apart from a genuine hang - it just runs out its own timeout either way.
  const deadline = Date.now() + 15000;
  let outcome = "timeout";
  while (Date.now() < deadline) {
    const hash = await page.evaluate(() => location.hash);
    if (/#\/count\/[^/]+/.test(hash)) {
      if (await page.locator(".line-head").count().catch(() => 0)) { outcome = "detail"; break; }
    } else if (/#\/count\/?$/.test(hash)) { outcome = "redirected"; break; }
    await page.waitForTimeout(250);
  }
  if (outcome === "timeout") { note("BUG", persona.name, "Count screen never settled (still showing a loading spinner after 15s)"); return; }
  if (outcome === "redirected") { note("FLOW", persona.name, `Count redirected back to #/count (${(await noticeText(page)) || "snapshot likely found no stock in scope"})`); return; }
  const lineCount = await page.locator(".line-head").count();
  if (!lineCount) { note("FLOW", persona.name, "Count opened but shows no lines to count (snapshot found no stock in scope)"); return; }
  // Field names are c${i} (see count.js), not qty${i} - blind counting withholds book_quantity
  // from this screen by design, so there is no "correct" value to echo back; 1 is just a plausible
  // realistic entry that exercises the submit -> record_counts -> (possible variance) flow.
  for (let i = 0; i < lineCount; i++) {
    await page.locator(`[data-fk="c${i}"]`).fill("1").catch((e) => note("BUG", persona.name, `Count line ${i}: could not fill counted quantity: ${e.message}`));
  }
  await shot(page, `${persona.name}-count-filled`);
  await tapPrimary(page, "Save counts");
  await page.waitForTimeout(900);
  const ct = await noticeText(page);
  if (ct && /error|fail/i.test(ct)) note("BUG", persona.name, `Count submission failed: ${ct}`);
  else note("INFO", persona.name, `Count submitted (${ct || "no confirmation"})`);
  await shot(page, `${persona.name}-count-done`);
  } catch (e) {
    note("BUG", persona.name, `Counting stage threw: ${e.message}`);
  }
}

async function stageShipAndPack(page, persona) {
  try {
  const queues = (await apiCall(page, "frappe_wms.api.warehouse_order.list_queues", { warehouse: "DC1" })) || [];
  const remaining = queues.filter((q) => !/putaway|pick/i.test(q.queue_name));
  for (const q of remaining) { await joinQueue(page, persona, q.queue_name); await pullAndWorkLoop(page, persona, "outbound", 10); }

  await page.goto(`${BASE}/wms#/ship`);
  await page.waitForTimeout(600);
  let shipped = 0;
  for (let i = 0; i < 5; i++) {
    const card = page.locator(".card").first();
    if (!(await card.count())) break;
    await card.click();
    await page.waitForTimeout(600);
    const lineCount = await page.locator(".line-head").count();
    for (let j = 0; j < lineCount; j++) { const val = await page.locator(`[data-fk="hu${j}"]`).inputValue().catch(() => ""); if (!val) note("FLOW", persona.name, `Ship line ${j}: no suggested HU pre-filled`); }
    await tapPrimary(page, "Post goods issue").catch(() => {});
    await page.waitForTimeout(900);
    const gi = await noticeText(page);
    if (gi && /error|fail/i.test(gi)) note("BUG", persona.name, `Goods issue failed: ${gi}`);
    else { note("INFO", persona.name, `Goods issue posted (${gi || "no confirmation"})`); shipped++; }
    await page.goto(`${BASE}/wms#/ship`);
    await page.waitForTimeout(600);
  }
  note("INFO", persona.name, `Shipped ${shipped} delivery(ies) this stage`);

  await page.goto(`${BASE}/wms#/pack`);
  await page.waitForTimeout(600);
  const packCards = await page.locator(".card").count();
  note("INFO", persona.name, `${packCards} open Packing Order(s) found on #/pack`);
  } catch (e) {
    note("BUG", persona.name, `Ship/Pack stage threw: ${e.message}`);
  }
}
