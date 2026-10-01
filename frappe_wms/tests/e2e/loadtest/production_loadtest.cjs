// Realistic multi-operator RF-app load test against PRODUCTION (default: erp.pinohomelab.duckdns.org).
// Drives the actual UI (clicks, scans, form fills) for 5 concurrent personas across a full
// receive -> putaway -> move -> repack -> pick -> pack -> load -> ship day, with two deliberate
// concurrency windows (putaway race, pick race) to stress pull_next_warehouse_order's atomic claim.
//
// Run from this directory: LOADTEST_PASSWORD=<password> node production_loadtest.cjs
// See ../../../../PLAN.md for full context, current findings, and what to run next.
//
// Env vars:
//   LOADTEST_PASSWORD      required - shared password for the 5 loadtest.* RF accounts
//   LOADTEST_BASE_URL      optional - defaults to production below
//   LOADTEST_SHOT_DIR      optional - defaults to ./shots next to this file
//   CHROMIUM_EXECUTABLE_PATH  optional - only needed if Playwright's own bundled Chromium can't
//                             launch in this environment (e.g. missing system libs); this repo's
//                             dev sandbox needed both an explicit executablePath AND
//                             LD_LIBRARY_PATH set on the `node` invocation itself - if you hit
//                             "error while loading shared libraries: libasound.so.2" or similar,
//                             that's this same class of problem, environment-specific to fix.
const path = require("path");
const { chromium } = require("playwright-core");
const fs = require("fs");

const BASE = process.env.LOADTEST_BASE_URL || "https://erp.pinohomelab.duckdns.org";
const PASSWORD = process.env.LOADTEST_PASSWORD;
if (!PASSWORD) { console.error("Set LOADTEST_PASSWORD (shared password for the 5 loadtest.* RF accounts) before running."); process.exit(1); }
const SHOT_DIR = process.env.LOADTEST_SHOT_DIR || path.join(__dirname, "shots");
fs.mkdirSync(SHOT_DIR, { recursive: true });

const PERSONAS = {
  ana: { email: "loadtest.ana@pinohomelab.test", resource: "LOADTEST-RF1", name: "Ana" },
  bruno: { email: "loadtest.bruno@pinohomelab.test", resource: "LOADTEST-RF2", name: "Bruno" },
  carla: { email: "loadtest.carla@pinohomelab.test", resource: "LOADTEST-RF3", name: "Carla" },
  diego: { email: "loadtest.diego@pinohomelab.test", resource: "LOADTEST-RF4", name: "Diego" },
  elena: { email: "loadtest.elena@pinohomelab.test", resource: "LOADTEST-RF5", name: "Elena" },
};

// Real, pre-registered Batch records (a supervisor/master-data step, done once via the API setup
// script - the RF Receive screen's batch field has no "register a new one here" path, see the
// findings doc) so these specific batch-controlled items can actually be received this run.
const KNOWN_BATCHES = {
  "LT-COLA-ENCUAD": "LOT-ENCUAD-0926",
  "LT-FILTRO-ACEITE": "LOT-FILTRO-0926",
  "LT-ACEITE-10W40": "LOT-ACEITE-0926",
};

let pppObdName = null; // the Outbound Delivery created in Stage 4 from the fresh Pick-Pack-Pass SO, used by stage5b to build a Packing Order once it's picked

const findings = [];
function note(kind, persona, msg, extra) {
  const entry = { t: new Date().toISOString(), kind, persona, msg, ...extra };
  findings.push(entry);
  console.log(`[${kind}] (${persona}) ${msg}${extra ? " " + JSON.stringify(extra) : ""}`);
}

async function timed(persona, label, fn) {
  const t0 = Date.now();
  try {
    const r = await fn();
    const ms = Date.now() - t0;
    if (ms > 2500) note("PERF", persona, `${label} took ${ms}ms (slow)`, { ms });
    return r;
  } catch (e) {
    note("BUG", persona, `${label} threw: ${e.message}`, {});
    throw e;
  }
}

async function shot(page, name) {
  try { await page.screenshot({ path: `${SHOT_DIR}/${name}.png` }); } catch (e) { /* ignore */ }
}

async function newSession(browser, persona) {
  const context = await browser.newContext({ viewport: { width: 390, height: 780 } });
  const page = await context.newPage();
  page.on("pageerror", (e) => note("BUG", persona.name, `JS error: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error" && !/favicon|socket\.io/i.test(m.text())) note("BUG", persona.name, `console.error: ${m.text().slice(0, 200)}`); });
  page.on("response", async (res) => {
    if (res.status() >= 400) {
      let body = "";
      try { body = (await res.text()).slice(0, 500); } catch (e) { /* ignore */ }
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
  await page.waitForTimeout(500);
  const hash = await page.evaluate(() => location.hash);
  if (!hash.startsWith("#/logon")) {
    // Resource was already bound to this user from an earlier run/session - the app itself
    // skips the logon screen (logon.js: enter() redirects away once S.resource is set). No
    // automatic timeout exists anywhere in this app: a device stays bound to whoever last
    // picked it until they explicitly log off or a supervisor kicks them - a crashed tab or a
    // forgotten logoff leaves that resource permanently unusable by anyone else. Flagging once.
    note("FLOW", persona.name, `Resumed already-logged-on session for ${persona.resource} (no session timeout exists in this app)`);
    return true;
  }
  const btn = page.locator("button", { hasText: persona.resource });
  const has = await btn.first().waitFor({ timeout: 15000 }).then(() => true).catch(() => false);
  if (!has) {
    note("BUG", persona.name, `Resource ${persona.resource} not offered on the logon screen (already in use, or not visible)`);
    await shot(page, `${persona.name}-logon-missing-resource`);
    return false;
  }
  await timed(persona.name, "pick device", async () => { await btn.first().click(); await page.waitForTimeout(400); });
  return true;
}

// Types like a Bluetooth HID scanner: whole code back to back, no delay, then Enter.
async function scan(page, code) { await page.keyboard.type(String(code), { delay: 0 }); await page.keyboard.press("Enter"); await page.waitForTimeout(250); }
async function typeOnly(page, code) { await page.keyboard.type(String(code), { delay: 0 }); await page.waitForTimeout(100); }

async function focusField(page, name) {
  const el = page.locator(`[data-fk="${name}"]`).first();
  await el.waitFor({ timeout: 8000 });
  await el.click();
  return el;
}

async function fillScanField(page, name, value) {
  const el = await focusField(page, name);
  await el.fill("");
  await scan(page, value);
}

async function tapPrimary(page, label) {
  const btn = page.locator("#actionbar button, .actionbar button, button", { hasText: label }).first();
  await btn.waitFor({ timeout: 8000 });
  await btn.click();
  await page.waitForTimeout(300);
}

async function tapButton(page, label) {
  const btn = page.locator("button", { hasText: label }).first();
  await btn.waitFor({ timeout: 8000 });
  await btn.click();
  await page.waitForTimeout(400);
}

async function noticeText(page) {
  return (await page.locator("#notice, .notice").first().textContent().catch(() => "")) || "";
}

// Drives whatever step the generic task-confirm wizard is currently on: scans the first
// "expected" code shown for a scan step, accepts the pre-filled quantity, and confirms on
// review. Returns the final hash once it's off the task screen (confirmed, or blocked/redirected).
async function driveTaskWizard(page, persona) {
  for (let guard = 0; guard < 6; guard++) {
    await page.waitForTimeout(350);
    const hash = await page.evaluate(() => location.hash);
    const m = hash.match(/#\/task\/([^/]+)\/(\w+)/);
    if (!m) return hash;
    const step = m[2];
    if (step === "review") { await tapPrimary(page, "Confirm"); await page.waitForTimeout(700); continue; }
    if (step === "quantity") { await tapPrimary(page, "Next"); continue; }
    const codeEl = page.locator(".expect .code").first();
    if (!(await codeEl.count())) { note("BUG", persona.name, `Task wizard step "${step}" (${hash}) shows no expected code to scan against`); return hash; }
    const expected = (await codeEl.textContent()).trim();
    const fieldName = { source: "src", product: "prod", destination: "dst" }[step] || step;
    await fillScanField(page, fieldName, expected);
  }
  note("BUG", persona.name, "Task wizard did not reach review/confirm within 6 steps - possible loop");
  return "guard-exceeded";
}

// Repeatedly taps "Get next work" (pull_next_warehouse_order) and drives whatever task it lands
// on, until the queue reports empty or maxIterations is hit. This is the actual concurrency
// stress point when run for two personas at once via Promise.all.
async function pullAndWorkLoop(page, persona, group, maxIterations) {
  let confirmed = 0;
  for (let i = 0; i < maxIterations; i++) {
    await page.goto(`${BASE}/wms#/tasks/${group}`);
    await page.waitForTimeout(400);
    const pullBtn = page.locator("button", { hasText: "Get next work" });
    if (!(await pullBtn.count())) { note("FLOW", persona.name, `No "Get next work" button on #/tasks/${group} (not joined to a queue?)`); break; }
    await timed(persona.name, `pull next (${group}) #${i + 1}`, async () => { await pullBtn.first().click(); await page.waitForTimeout(900); });
    const hash = await page.evaluate(() => location.hash);
    if (!/#\/task\//.test(hash)) {
      const nt = await noticeText(page);
      if (/No work waiting/i.test(nt)) { note("INFO", persona.name, `${group} queue empty after confirming ${confirmed} task(s)`); }
      else note("BUG", persona.name, `"Get next work" did not land on a task screen (hash=${hash}, notice="${nt}")`);
      break;
    }
    await driveTaskWizard(page, persona);
    confirmed++;
  }
  return confirmed;
}

async function joinQueue(page, persona, queueTextMatch) {
  await page.goto(`${BASE}/wms#/session`);
  await page.waitForTimeout(500);
  const alreadyRight = await page.locator(`text=${queueTextMatch}`).count();
  if (alreadyRight) { note("INFO", persona.name, `Already in a queue matching "${queueTextMatch}"`); return; }
  const leaveBtn = page.locator("button", { hasText: "Leave queue" });
  if (await leaveBtn.count()) {
    // A device can only be in one queue at a time (session.js: r.current_queue is a single field) -
    // switching work means an explicit Leave then Join, two taps with no direct "switch to" shortcut.
    note("FLOW", persona.name, "Switching queues requires an explicit Leave + Join - no one-tap 'switch queue' shortcut exists.");
    await leaveBtn.first().click();
    await page.waitForTimeout(500);
  }
  await tapButton(page, "Join a queue");
  await page.waitForTimeout(500);
  const q = page.locator("button", { hasText: queueTextMatch }).first();
  if (!(await q.count())) { note("BUG", persona.name, `No queue matching "${queueTextMatch}" offered on #/session`); return; }
  await q.click();
  await page.waitForTimeout(500);
}

// Read-only (or, for Stage 4, a deliberate desk/API-only write with no RF equivalent) authenticated
// call using the exact same request shape core/api.js itself uses. Used only where either (a) no
// mutating RF action is involved (pure orientation, standing in for a supervisor glancing at the
// Monitor or a bin label a headless browser has no camera to read), or (b) the RF app genuinely has
// no screen for the action at all (Stage 4 - see the finding logged there).
async function apiCall(page, method, args) {
  return await page.evaluate(async ({ method, args }) => {
    const res = await fetch(`/api/method/${method}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json", "X-Frappe-CSRF-Token": window.WMS.csrf },
      body: JSON.stringify(args || {}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(`${method} -> HTTP ${res.status}: ${(data && (data.exception || data._server_messages)) || res.statusText}`);
    return data.message;
  }, { method, args });
}

// In real life an operator reads this off a printed bin location label - a headless run has no
// camera for that, so this is the one place the script "knows" a bin code without having scanned
// it on some earlier screen. It is read-only orientation, never a stand-in for a mutating action.
async function findPutawayStock(page, persona) {
  const hus = await apiCall(page, "frappe_wms.api.handling_unit.list_handling_units", {});
  const landed = (hus || []).find((u) => u.current_bin && !/RECEIVING/i.test(u.current_bin) && u.stock_status && u.stock_status !== "Empty");
  if (!landed) return null;
  const detail = await apiCall(page, "frappe_wms.api.handling_unit.handling_unit_detail", { hu_name: landed.name });
  return detail;
}

// ---- Stage 3: Diego does an ad-hoc Move, then a whole-HU Repack, on real putaway stock ----
async function stage3(sessions) {
  console.log("\n=== STAGE 3: Diego does an ad-hoc Move and a Repack on real putaway stock ===");
  const { page, persona } = sessions.diego;
  const putaway = await findPutawayStock(page, persona);
  if (!putaway) { note("FLOW", persona.name, "No putaway stock found outside receiving bins - Stage 2 may not have confirmed any putaway tasks. Skipping Move/Repack."); return; }
  const stockLine = (putaway.stock || [])[0];
  if (!stockLine) { note("BUG", persona.name, `HU ${putaway.name} reports stock_status="${putaway.stock_status}" but has no stock lines`); return; }
  note("INFO", persona.name, `Found putaway stock: ${stockLine.product} x${stockLine.quantity} in HU ${putaway.name} @ ${putaway.current_bin}`);

  // frappe.client.get_list needs "DocType" read permission as a side effect in this Frappe
  // version, which a WMS-role floor user genuinely doesn't have - resolve_scan is the one
  // lookup call the RF app itself always has access to, so probe a short list of plausible
  // sibling bins with it instead of listing every bin in the warehouse.
  let destBin = null;
  for (const candidate of ["DC1-BULK-A02", "DC1-BULK-B01", "DC1-BULK-A03", "DC1-BULK-B02"]) {
    if (candidate === putaway.current_bin) continue;
    const resolved = await apiCall(page, "frappe_wms.api.scanner.resolve_scan", { code: candidate }).catch(() => null);
    if (resolved && (resolved.matches || []).some((m) => m.type === "bin" && m.name === candidate)) { destBin = candidate; break; }
  }
  if (!destBin) { note("FLOW", persona.name, "Could not resolve a different destination bin to move stock to (tried a short list of plausible DC1-BULK sibling bins) - skipping the ad-hoc Move, still attempting Repack."); }

  if (destBin) {
    await timed(persona.name, "ad-hoc move", async () => {
      await page.goto(`${BASE}/wms#/move`);
      await page.waitForTimeout(500);
      await fillScanField(page, "product", stockLine.product);
      await page.waitForTimeout(400);
      const moveQty = Math.max(1, Math.floor(Number(stockLine.quantity) / 2));
      const qtyEl = await focusField(page, "quantity");
      await qtyEl.fill(String(moveQty));
      await tapPrimary(page, "Next");
      await fillScanField(page, "source_bin", putaway.current_bin);
      await page.waitForTimeout(400);
      await fillScanField(page, "destination_bin", destBin);
      await page.waitForTimeout(400);
      // Unlike Receive's HU field (which has an explicit "If new HU, type" selector), Move's
      // destination_hu only accepts an *already-registered* HU - resolve_scan finds nothing for a
      // fresh barcode and the field just rejects it as "not a known code" (verified: filling it
      // here hangs the test on a confirmation that never comes). So this candidate bin's storage
      // type being HU-managed is a real dead end for an ad-hoc Move with no HU already in hand -
      // left unset on purpose; the resulting "requires a Handling Unit" failure below is itself
      // the finding, not a script bug to paper over.
      await shot(page, "Diego-03-move-review");
      await tapPrimary(page, "Move");
      await page.waitForTimeout(1000);
    });
    const mv = await noticeText(page);
    if (mv && /error|fail/i.test(mv)) note("BUG", persona.name, `Move failed: ${mv}`);
    else note("INFO", persona.name, `Move posted (${mv || "no confirmation text seen"})`);
    await shot(page, "Diego-03-move-done");
  }

  await timed(persona.name, "repack whole HU into a new TOTE", async () => {
    await page.goto(`${BASE}/wms#/repack`);
    await page.waitForTimeout(400);
    await fillScanField(page, "hu", putaway.name);
    await page.waitForTimeout(700);
    const wholeHuCard = page.locator(".card", { hasText: "This entire Handling Unit" }).first();
    if (!(await wholeHuCard.count())) { note("BUG", persona.name, `Repack screen for ${putaway.name} did not offer "This entire Handling Unit"`); return; }
    await wholeHuCard.click();
    await page.waitForTimeout(300);
    await page.locator('[data-fk="newtype"]').selectOption("TOTE").catch((e) => note("BUG", persona.name, `Repack: could not select new HU type TOTE: ${e.message}`));
    await tapButton(page, "Create as destination");
    await page.waitForTimeout(700);
    await shot(page, "Diego-03-repack-filled");
    await tapPrimary(page, "Repack");
    await page.waitForTimeout(1000);
  });
  const rp = await noticeText(page);
  if (rp && /error|fail/i.test(rp)) note("BUG", persona.name, `Repack failed: ${rp}`);
  else note("INFO", persona.name, `Repack posted (${rp || "no confirmation text seen"})`);
  await shot(page, "Diego-03-repack-done");
}

// ---- Stage 4: allocate + create pick tasks. THERE IS NO RF SCREEN FOR THIS AT ALL. ----
async function stage4(sessions) {
  console.log("\n=== STAGE 4: release deliveries for picking (desk/API-only step - no RF screen exists) ===");
  const { page, persona } = sessions.elena; // WMS Supervisor

  // Turn the fresh Pick-Pack-Pass Sales Order into an Outbound Delivery first - same no-RF-screen
  // gap as release_delivery_for_picking below (grep across public/js/wms_rf finds zero references
  // to create_outbound_delivery_from_sales_order either), so this is the desk/API step a real
  // supervisor would have no choice but to do outside the RF app entirely.
  try {
    const created = await apiCall(page, "frappe_wms.api.outbound.create_outbound_delivery_from_sales_order", { sales_order_name: "SAL-ORD-2026-00004", warehouse: "DC1" });
    note("BUG", persona.name, `Sales Order SAL-ORD-2026-00004 needed create_outbound_delivery_from_sales_order to become an Outbound Delivery, and the RF app has no screen for that either - created ${created} via direct API call, same as release_delivery_for_picking below.`, { sales_order: "SAL-ORD-2026-00004" });
    const doc = await apiCall(page, "frappe.client.get", { doctype: "Outbound Delivery", name: created });
    await apiCall(page, "frappe.client.submit", { doc: JSON.stringify(doc) });
    pppObdName = created;
  } catch (e) {
    note("BUG", persona.name, `create_outbound_delivery_from_sales_order(SAL-ORD-2026-00004) failed: ${e.message}`);
  }

  const awaiting = await apiCall(page, "frappe_wms.api.outbound.list_awaiting_release", {});
  if (!awaiting || !awaiting.length) { note("INFO", persona.name, "No deliveries awaiting allocation/pick-task creation."); return; }
  for (const d of awaiting) {
    note("BUG", persona.name,
      `Outbound Delivery ${d.name} cannot move forward - it needs allocation and pick-task creation - but the RF app has NO screen that calls frappe_wms.api.outbound.release_delivery_for_picking. ` +
      `That endpoint's own backend code comment literally says "One RF tap instead of the two-step allocate-then-create-pick-tasks desk flow", but grep across public/js/wms_rf finds zero references to it, release_wave, or list_awaiting_release anywhere in the RF app. ` +
      `A real warehouse would be stuck here until someone opens Desk (or this exact API is scripted, as this test just did) - every outbound order silently stalls after Sales Order submission with no RF-visible cause or error.`,
      { delivery: d.name });
    try {
      const created = await apiCall(page, "frappe_wms.api.outbound.release_delivery_for_picking", { delivery_name: d.name, strategy: "Single Order" });
      note("INFO", persona.name, `Released ${d.name} for picking (worked around via direct API call): ${JSON.stringify(created)}`);
    } catch (e) {
      note("BUG", persona.name, `release_delivery_for_picking(${d.name}) failed: ${e.message}`);
    }
  }
}

// ---- Stage 5: Bruno & Carla race for pick tasks CONCURRENTLY ----
async function stage5(sessions) {
  console.log("\n=== STAGE 5: Bruno & Carla race for pick tasks (concurrent) ===");
  // "any storage type" (not just "Pick") - the narrow DC1-PICK-Q (scoped to a dedicated pick-face
  // storage type nothing has ever been replenished into) and the new DC1-PICK-FALLBACK-Q both
  // match plain "Pick" text, and only the fallback actually has real work queued right now.
  await joinQueue(sessions.bruno.page, sessions.bruno.persona, "any storage type");
  await joinQueue(sessions.carla.page, sessions.carla.persona, "any storage type");
  const [b, c] = await Promise.all([
    pullAndWorkLoop(sessions.bruno.page, sessions.bruno.persona, "outbound", 15),
    pullAndWorkLoop(sessions.carla.page, sessions.carla.persona, "outbound", 15),
  ]);
  note("INFO", "orchestrator", `Pick race done: Bruno confirmed ${b}, Carla confirmed ${c}`);
  await shot(sessions.bruno.page, "Bruno-05-pick-done");
  await shot(sessions.carla.page, "Carla-05-pick-done");
}

// ---- Stage 5b: build a Packing Order for the Pick-Pack-Pass delivery Stage 4/5 just picked into
// a shared TOTE, so Stage 6's #/pack check has something real to drive "Complete packing" on -
// there is no API or RF path to CREATE one (confirmed: zero non-test references anywhere in the
// app), so this mirrors exactly what a desk user would have to do by hand via frappe.client.insert. ----
async function stage5b(sessions) {
  console.log("\n=== STAGE 5b: build a Packing Order for the Pick-Pack-Pass delivery (desk/API-only - no path to create one exists at all) ===");
  const { page, persona } = sessions.elena;
  if (!pppObdName) { note("FLOW", persona.name, "No Pick-Pack-Pass Outbound Delivery was created in Stage 4 - skipping Packing Order setup."); return; }
  const obd = await apiCall(page, "frappe.client.get", { doctype: "Outbound Delivery", name: pppObdName }).catch((e) => { note("BUG", persona.name, `Could not re-read ${pppObdName}: ${e.message}`); return null; });
  if (!obd || !obd.pick_pack_pass_hu) { note("FLOW", persona.name, `${pppObdName} has no pick_pack_pass_hu yet - the Pick task for it may not have been confirmed (queue empty, or it lost the race). Skipping Packing Order setup.`); return; }
  note("BUG", persona.name, `There is no API or RF path to create a Packing Order at all - not even a desk/API-only workaround like Stage 4's. This test creates one directly via frappe.client.insert, which a real supervisor cannot do without opening the DocType form in Desk by hand.`, { outbound_delivery: pppObdName });
  try {
    const destHu = await apiCall(page, "frappe.client.insert", { doc: JSON.stringify({
      doctype: "Handling Unit", hu_number: `SSCC0614143${String(Date.now()).slice(-6)}`, hu_type: "TOTE",
      current_bin: obd.staging_bin, warehouse: "DC1",
    }) });
    const order = await apiCall(page, "frappe.client.insert", { doc: JSON.stringify({
      doctype: "Packing Order", outbound_delivery: pppObdName, work_center_bin: obd.staging_bin,
      source_hus: [{ doctype: "Packing Source HU", handling_unit: obd.pick_pack_pass_hu }],
      destination_hus: [{ doctype: "Packing Destination HU", handling_unit: destHu.name }],
    }) });
    note("INFO", persona.name, `Created Packing Order ${order.name} for ${pppObdName} (source HU ${obd.pick_pack_pass_hu} -> destination HU ${destHu.name}).`);
  } catch (e) {
    note("BUG", persona.name, `Packing Order setup for ${pppObdName} failed: ${e.message}`);
  }
}

// ---- Stage 6: Elena drains whatever queues remain (Sort/Stage/Load), then posts Goods Issue via
// #/ship, and checks the Pack screen (RF has no "create a packing order" screen either - only
// "Complete packing" on one that already exists, so this only reports whether any showed up). ----
async function stage6(sessions) {
  console.log("\n=== STAGE 6: Elena works Stage/Load tasks, ships, and checks Pack ===");
  const { page, persona } = sessions.elena;
  const queues = (await apiCall(page, "frappe_wms.api.warehouse_order.list_queues", { warehouse: "DC1" })) || [];
  const remaining = queues.filter((q) => !/putaway|pick/i.test(q.queue_name));
  if (!remaining.length) note("INFO", persona.name, "No further queues (Sort/Stage/Load) configured beyond Putaway/Pick.");
  for (const q of remaining) {
    await joinQueue(page, persona, q.queue_name);
    const n = await pullAndWorkLoop(page, persona, "outbound", 8);
    note("INFO", persona.name, `Drained queue "${q.queue_name}": confirmed ${n} task(s)`);
  }

  await timed(persona.name, "ship", async () => {
    await page.goto(`${BASE}/wms#/ship`);
    await page.waitForTimeout(700);
  });
  const card = page.locator(".card").first();
  if (!(await card.count())) { note("FLOW", persona.name, "Nothing appeared on #/ship after working the remaining outbound tasks."); }
  else {
    await card.click();
    await page.waitForTimeout(700);
    await shot(page, "Elena-06-ship-detail");
    const lineCount = await page.locator(".line-head").count();
    if (!lineCount) note("FLOW", persona.name, "Ship detail opened but shows no lines - delivery may not have been fully picked/staged yet.");
    for (let i = 0; i < lineCount; i++) {
      const huField = page.locator(`[data-fk="hu${i}"]`);
      const val = await huField.inputValue().catch(() => "");
      if (!val) note("FLOW", persona.name, `Ship line ${i}: no suggested Handling Unit was pre-filled - the operator must already know which HU was staged, with no lookup shortcut offered on this screen.`);
    }
    await shot(page, "Elena-06-ship-filled");
    await timed(persona.name, "post goods issue", async () => { await tapPrimary(page, "Post goods issue"); await page.waitForTimeout(1200); });
    const gi = await noticeText(page);
    if (gi && /error|fail/i.test(gi)) note("BUG", persona.name, `Goods issue failed: ${gi}`);
    else note("INFO", persona.name, `Goods issue posted (${gi || "no confirmation text seen"})`);
    await shot(page, "Elena-06-ship-done");
  }

  await page.goto(`${BASE}/wms#/pack`);
  await page.waitForTimeout(700);
  await shot(page, "Elena-06-pack");
  const packCards = await page.locator(".card").count();
  if (!packCards) note("INFO", persona.name, "No open Packing Orders to exercise #/pack's Complete-packing action.");
  else note("INFO", persona.name, `${packCards} open Packing Order(s) found on #/pack.`);
}

(async () => {
  const launchOpts = { args: ["--no-sandbox"] };
  if (process.env.CHROMIUM_EXECUTABLE_PATH) launchOpts.executablePath = process.env.CHROMIUM_EXECUTABLE_PATH;
  const browser = await chromium.launch(launchOpts);
  const sessions = {};
  for (const key of Object.keys(PERSONAS)) {
    const persona = PERSONAS[key];
    const { context, page } = await newSession(browser, persona);
    sessions[key] = { context, page, persona };
  }

  // ---- Stage 0: everyone logs in and picks their device ----
  console.log("\n=== STAGE 0: login + pick device (5 personas) ===");
  for (const key of Object.keys(sessions)) {
    const { page, persona } = sessions[key];
    await login(page, persona);
    await openRF(page, persona);
    const ok = await pickDevice(page, persona);
    if (!ok) continue;
    await shot(page, `${persona.name}-00-menu`);
  }

  fs.writeFileSync(`${SHOT_DIR}/findings-stage0.json`, JSON.stringify(findings, null, 2));
  console.log("Stage 0 done. Findings so far:", findings.length);

  // ---- Stage 1: Ana receives open Inbound Deliveries, one new external-barcode HU per line ----
  // Update these names to whatever Inbound Deliveries are actually open before each run (see
  // PLAN.md "Before your next run" for how to check/create fresh ones) - a delivery whose PO is
  // already fully received will 404/no-op here, not silently succeed.
  console.log("\n=== STAGE 1: Ana receives open inbound deliveries ===");
  await receiveAll(sessions.ana.page, sessions.ana.persona, ["IBD-00000006"]);

  fs.writeFileSync(`${SHOT_DIR}/findings-stage1.json`, JSON.stringify(findings, null, 2));
  console.log("Stage 1 done. Findings so far:", findings.length);

  // ---- Stage 2: Bruno & Carla race for putaway tasks CONCURRENTLY (real HTTP concurrency,
  // not simulated) - the exact contention point pull_next_warehouse_order's atomic claim exists for. ----
  console.log("\n=== STAGE 2: Bruno & Carla race for putaway (concurrent) ===");
  await joinQueue(sessions.bruno.page, sessions.bruno.persona, "Putaway");
  await joinQueue(sessions.carla.page, sessions.carla.persona, "Putaway");
  const [brunoPutaway, carlaPutaway] = await Promise.all([
    pullAndWorkLoop(sessions.bruno.page, sessions.bruno.persona, "inbound", 15),
    pullAndWorkLoop(sessions.carla.page, sessions.carla.persona, "inbound", 15),
  ]);
  note("INFO", "orchestrator", `Putaway race done: Bruno confirmed ${brunoPutaway}, Carla confirmed ${carlaPutaway}`);
  await shot(sessions.bruno.page, "Bruno-02-putaway-done");
  await shot(sessions.carla.page, "Carla-02-putaway-done");
  fs.writeFileSync(`${SHOT_DIR}/findings-stage2.json`, JSON.stringify(findings, null, 2));
  console.log("Stage 2 done. Findings so far:", findings.length);

  const stages = [
    ["3", () => stage3(sessions)],
    ["4", () => stage4(sessions)],
    ["5", () => stage5(sessions)],
    ["5b", () => stage5b(sessions)],
    ["6", () => stage6(sessions)],
  ];
  for (const [n, fn] of stages) {
    try { await fn(); }
    catch (e) { note("BUG", "orchestrator", `Stage ${n} threw and was aborted: ${e.message}`); }
    fs.writeFileSync(`${SHOT_DIR}/findings-stage${n}.json`, JSON.stringify(findings, null, 2));
    console.log(`Stage ${n} done. Findings so far:`, findings.length);
  }

  fs.writeFileSync(`${SHOT_DIR}/findings-all.json`, JSON.stringify(findings, null, 2));
  console.log("\n=== LOAD TEST COMPLETE ===");
  console.log(`Total findings: ${findings.length}`);
  for (const kind of ["BUG", "FLOW", "PERF", "INFO"]) console.log(`  ${kind}: ${findings.filter((f) => f.kind === kind).length}`);

  await browser.close();
})().catch((e) => { console.error("FATAL:", e); process.exit(1); });

async function receiveAll(page, persona, deliveryNames) {
  for (const name of deliveryNames) {
    // A name starting "IBD-"/"INB-" targets one exact delivery directly (#/receive/<name>) rather
    // than matching a supplier's card by text - multiple deliveries can pile up per supplier
    // across repeated runs, and the oldest (often already-exhausted) one always renders first.
    const isDirectName = /^(IBD|INB)-/.test(name);
    await timed(persona.name, `open delivery for ${name}`, async () => {
      if (isDirectName) { await page.goto(`${BASE}/wms#/receive/${name}`); await page.waitForTimeout(700); return; }
      await page.goto(`${BASE}/wms#/receive`);
      await page.waitForTimeout(700);
      const card = page.locator(".card", { hasText: name }).first();
      const found = await card.waitFor({ timeout: 8000 }).then(() => true).catch(() => false);
      if (!found) { note("BUG", persona.name, `No open inbound delivery card found for ${name} on #/receive`); return; }
      await card.click();
      await page.waitForTimeout(700);
    });
    await shot(page, `${persona.name}-01-receive-${name}`);
    const lineHeads = await page.locator(".line-head").allTextContents().catch(() => []);
    if (!lineHeads.length) { note("FLOW", persona.name, `${name}: no open lines to receive (screen showed "nothing left" or failed to load)`); continue; }
    note("INFO", persona.name, `${name}: ${lineHeads.length} line(s) to receive: ${lineHeads.join(", ")}`);
    for (let i = 0; i < lineHeads.length; i++) {
      const barcode = `SSCC0614141${String(Date.now()).slice(-6)}${i}`;
      await fillScanField(page, `hu${i}`, barcode);
      // "If new HU, type" - a select, not a scan field; must be set explicitly for a genuinely new barcode.
      const typeSelect = page.locator(`[data-fk="type${i}"]`);
      await typeSelect.selectOption("INBOUND").catch(async (e) => note("BUG", persona.name, `${name} line ${i}: could not select HU type INBOUND: ${e.message}`));
      // Quantity is pre-filled to the full remaining amount by default - leaving it as-is is the
      // realistic "received exactly what was expected" case for most lines.
    }
    await shot(page, `${persona.name}-01-receive-${name}-filled`);
    // Up to 3 attempts: a batch- or serial-controlled line among these barcodes rejects the whole
    // receipt (the server posts nothing at all until every line is valid) with "requires a batch
    // number"/"requires a serial number" - the batch${i}/serial${i} fields on this screen let the
    // operator fill one in and resubmit, rather than being stuck (see receive.js).
    let noticeText = "";
    for (let attempt = 0; attempt < 3; attempt++) {
      await timed(persona.name, `post receipt ${name} (attempt ${attempt + 1})`, async () => {
        await tapPrimary(page, "Post receipt");
        await page.waitForTimeout(1200);
      });
      noticeText = (await page.locator("#notice, .notice").first().textContent().catch(() => "")) || "";
      const batchNeeded = noticeText.match(/Row (\d+):.*requires a batch number/i);
      const serialNeeded = noticeText.match(/Row (\d+):.*requires a serial number/i);
      if (batchNeeded && attempt === 0) {
        const rowIdx = parseInt(batchNeeded[1], 10) - 1;
        const itemCode = lineHeads[rowIdx];
        const knownBatch = KNOWN_BATCHES[itemCode];
        note("FLOW", persona.name, `${name} line ${rowIdx}: batch-controlled item blocked the WHOLE receipt (not just its own line) until a batch was entered - filling one in and resubmitting.`);
        const batchField = page.locator(`[data-fk="batch${rowIdx}"]`);
        if (!(await batchField.count())) { note("BUG", persona.name, `${name}: expected a batch${rowIdx} field on the Receive screen but found none`); break; }
        await batchField.fill(knownBatch || `LOT-${Date.now().toString().slice(-8)}`);
        continue;
      }
      if (serialNeeded && attempt === 0) {
        // The backend auto-registers a never-seen serial (see receipt.py's
        // _get_or_create_serial_no), so a genuinely new one just needs a value typed in.
        const rowIdx = parseInt(serialNeeded[1], 10) - 1;
        note("FLOW", persona.name, `${name} line ${rowIdx}: serial-controlled item blocked the WHOLE receipt until a serial was entered - filling one in and resubmitting.`);
        const serialField = page.locator(`[data-fk="serial${rowIdx}"]`);
        if (!(await serialField.count())) { note("BUG", persona.name, `${name}: expected a serial${rowIdx} field on the Receive screen but found none`); break; }
        await serialField.fill(`SN-${Date.now().toString().slice(-9)}${rowIdx}`);
        continue;
      }
      break;
    }
    // "✕" is just the toast's own close glyph, present on success AND failure alike - the actual
    // signal is the wording itself, which is why a plain /error|fail/ scan under-detects real
    // failures like "requires a batch number" or "does not have ... permission".
    const looksLikeFailure = /error|fail|requires|does not have|not found|could not|permission|no matching/i.test(noticeText) && !/posted/i.test(noticeText);
    if (looksLikeFailure) note("BUG", persona.name, `${name}: error after posting receipt: ${noticeText}`);
    else note("INFO", persona.name, `${name}: receipt posted (${noticeText || "no confirmation text seen"})`);
    await shot(page, `${persona.name}-01-receive-${name}-posted`);
  }
}
