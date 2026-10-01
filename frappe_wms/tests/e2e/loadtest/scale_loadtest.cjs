// Scaled-up multi-operator load test against PRODUCTION.
// Tier 1: 20 concurrent genuine RF browser sessions (real UI clicks/scans) - this is what
// actually surfaces usability problems, so it stays real Playwright, not simulated.
// Tier 2: 30 concurrent lightweight "desk" actors (plain authenticated fetch, no browser) doing
// work that has no RF screen anyway (POs, releasing deliveries, approving counts) - reaching
// real 50-concurrent-actor backend load without 50 browser processes on a 6.9GB host.
const path = require("path");
const { chromium } = require("playwright-core");
const fs = require("fs");

const BASE = process.env.LOADTEST_BASE_URL || "https://erp.pinohomelab.duckdns.org";
const PASSWORD = process.env.LOADTEST_PASSWORD;
if (!PASSWORD) { console.error("Set LOADTEST_PASSWORD"); process.exit(1); }
const SHOT_DIR = process.env.LOADTEST_SHOT_DIR || path.join(__dirname, "shots-scale");
fs.mkdirSync(SHOT_DIR, { recursive: true });

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
async function noticeText(page) { return (await page.locator("#notice, .notice").first().textContent().catch(() => "")) || ""; }

// Loading() and Empty() (ui/kit.js) both render a ".empty" div, so waiting for ".line-head, .empty"
// alone can resolve the instant a spinner appears - indistinguishable from the screen actually
// having settled. Poll until there are real lines, or an empty-state whose text isn't the loading
// spinner's own placeholder text.
async function waitForSettled(page, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const lineCount = await page.locator(".line-head").count().catch(() => 0);
    if (lineCount > 0) return "lines";
    const emptyText = await page.locator(".empty").first().textContent().catch(() => null);
    if (emptyText && !/loading/i.test(emptyText)) return "empty";
    await page.waitForTimeout(250);
  }
  return "timeout";
}

async function driveTaskWizard(page, persona) {
  for (let guard = 0; guard < 6; guard++) {
    await page.waitForTimeout(300);
    const hash = await page.evaluate(() => location.hash);
    const m = hash.match(/#\/task\/([^/]+)\/(\w+)/);
    if (!m) return hash;
    const step = m[2];
    if (step === "review") { await tapPrimary(page, "Confirm"); await page.waitForTimeout(600); continue; }
    if (step === "quantity") { await tapPrimary(page, "Next"); continue; }
    const codeEl = page.locator(".expect .code").first();
    if (!(await codeEl.count())) { note("BUG", persona.name, `Task wizard step "${step}" shows no expected code`); return hash; }
    const expected = (await codeEl.textContent()).trim();
    const fieldName = { source: "src", product: "prod", destination: "dst" }[step] || step;
    await fillScanField(page, fieldName, expected);
  }
  note("BUG", persona.name, "Task wizard did not reach review/confirm within 6 steps");
  return "guard-exceeded";
}

async function pullAndWorkLoop(page, persona, group, maxIterations) {
  let confirmed = 0;
  for (let i = 0; i < maxIterations; i++) {
    try {
      await page.goto(`${BASE}/wms#/tasks/${group}`);
      await page.waitForTimeout(350);
      const pullBtn = page.locator("button", { hasText: "Get next work" });
      if (!(await pullBtn.count())) break;
      await timed(persona.name, `pull next (${group}) #${i + 1}`, async () => { await pullBtn.first().click(); await page.waitForTimeout(800); });
      const hash = await page.evaluate(() => location.hash);
      if (!/#\/task\//.test(hash)) {
        const nt = await noticeText(page);
        if (/No work waiting/i.test(nt)) note("INFO", persona.name, `${group} queue empty after confirming ${confirmed} task(s)`);
        else if (nt) note("INFO", persona.name, `${group}: stopped pulling (notice="${nt}")`);
        break;
      }
      await driveTaskWizard(page, persona);
      confirmed++;
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
  await page.waitForTimeout(400);
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

// 20 concurrent Chromium contexts measured ~7.2GB RSS on this 6.9GB host (production itself
// included) - that's what actually caused Stage 1 to hang permanently on "Loading..." the first
// time, not an app bug. Real RF concurrency now runs in waves of at most WAVE_SIZE personas, each
// wave's contexts fully closed before the next opens, keeping peak browser memory well inside
// what's left after production's own containers. Desk actors stay cheap (plain fetch, no
// browser) so all 30 still run as genuine concurrent actors, just once, after the RF waves.
const WAVE_SIZE = 6;
function chunk(arr, size) { const out = []; for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size)); return out; }
const WAVES = chunk(RF_PERSONAS, WAVE_SIZE);
const deliveryNames = (process.env.LOADTEST_IBD_NAMES || "").split(",").filter(Boolean);

async function runWave(waveIndex, personas, { doReceive, doPutaway, doMoveRepack, doPick, doCount, doShip }) {
  console.log(`\n########## WAVE ${waveIndex + 1}/${WAVES.length}: ${personas.map((p) => p.name).join(", ")} ##########`);
  const launchOpts = { args: ["--no-sandbox"] };
  if (process.env.CHROMIUM_EXECUTABLE_PATH) launchOpts.executablePath = process.env.CHROMIUM_EXECUTABLE_PATH;
  const browser = await chromium.launch(launchOpts);
  const sessions = {};
  try {
    for (const persona of personas) {
      const { context, page } = await newSession(browser, persona);
      sessions[persona.name] = { context, page, persona };
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
    await browser.close();
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
      // A fixed sleep here is exactly the kind of test-script impatience that looks like a real
      // app failure under genuine concurrent load: 8 receivers' page loads firing at once against
      // one modest backend container can legitimately take longer than a sleep tuned for 1-2
      // concurrent personas. Wait for the screen to actually settle (either real lines, or the
      // app's own definitive "nothing left" state) instead of guessing a duration.
      const settled = await waitForSettled(page, 15000);
      if (settled === "timeout") note("BUG", persona.name, `${name}: screen never settled (still showing a loading spinner after 15s) - possible backend stall under concurrent load`);
    });
    const lineHeads = await page.locator(".line-head").allTextContents().catch(() => []);
    if (!lineHeads.length) {
      const emptyText = await page.locator(".empty").first().textContent().catch(() => "");
      note("FLOW", persona.name, `${name}: no open lines to receive (screen: "${(emptyText || "").trim() || "no empty-state text found"}")`);
      continue;
    }
    note("INFO", persona.name, `${name}: ${lineHeads.length} line(s): ${lineHeads.join(", ")}`);
    for (let i = 0; i < lineHeads.length; i++) {
      const barcode = `SSCC${persona.resource.replace(/\D/g, "")}${String(Date.now()).slice(-7)}${i}`;
      await fillScanField(page, `hu${i}`, barcode);
      await page.locator(`[data-fk="type${i}"]`).selectOption("INBOUND").catch(() => {});
    }
    let noticeTxt = "";
    for (let attempt = 0; attempt < 3; attempt++) {
      await timed(persona.name, `post receipt ${name} (attempt ${attempt + 1})`, async () => { await tapPrimary(page, "Post receipt"); await page.waitForTimeout(1000); });
      noticeTxt = (await page.locator("#notice, .notice").first().textContent().catch(() => "")) || "";
      const batchNeeded = noticeTxt.match(/Row (\d+):.*requires a batch number/i);
      const serialNeeded = noticeTxt.match(/Row (\d+):.*requires a serial number/i);
      if (batchNeeded && attempt === 0) {
        const rowIdx = parseInt(batchNeeded[1], 10) - 1;
        await page.locator(`[data-fk="batch${rowIdx}"]`).fill(`LOT-${Date.now().toString().slice(-8)}`).catch(() => {});
        continue;
      }
      if (serialNeeded && attempt === 0) {
        const rowIdx = parseInt(serialNeeded[1], 10) - 1;
        await page.locator(`[data-fk="serial${rowIdx}"]`).fill(`SN-${Date.now().toString().slice(-9)}${rowIdx}`).catch(() => {});
        continue;
      }
      break;
    }
    const looksLikeFailure = /error|fail|requires|does not have|not found|could not|permission|no matching/i.test(noticeTxt) && !/posted/i.test(noticeTxt);
    if (looksLikeFailure) note("BUG", persona.name, `${name}: error after posting: ${noticeTxt}`);
    else note("INFO", persona.name, `${name}: receipt posted (${noticeTxt || "no confirmation text"})`);
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
  // snapshot_count fires on open (count.js enter()) - wait for the real settled state, not a guess.
  const settled = await waitForSettled(page, 15000);
  if (settled === "timeout") { note("BUG", persona.name, "Count screen never settled (still showing a loading spinner after 15s)"); return; }
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
