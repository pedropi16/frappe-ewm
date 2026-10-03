// Focused, single-persona probe of the Load -> Ship (Goods Issue) RF screens, driven for real
// through the browser - the part of the outbound pipeline this engagement had never actually
// exercised (every delivery used to dead-end at "Picked" with no reachable path to Goods Issue;
// see PLAN.md). Not part of scale_loadtest.cjs's concurrent races - this just needs ONE session
// walking #/load then #/ship for shipments a one-off setup script already staged, to find real
// screen-level bugs in a part of the app nothing had ever clicked through before.
const path = require("path");
const dns = require("dns");
const { chromium } = require("playwright-core");
const fs = require("fs");

const BASE = process.env.LOADTEST_BASE_URL || "https://erp.pinohomelab.duckdns.org";
const PASSWORD = process.env.LOADTEST_PASSWORD;
if (!PASSWORD) { console.error("Set LOADTEST_PASSWORD"); process.exit(1); }
const EMAIL = process.env.LOADTEST_EMAIL || "loadtest.ana@pinohomelab.test";
const RESOURCE = process.env.LOADTEST_RESOURCE || "LOADTEST-RF1";
const SHOT_DIR = path.join(__dirname, "shots-scale");
fs.mkdirSync(SHOT_DIR, { recursive: true });

const TARGET_HOST = new URL(BASE).hostname;
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
}

const findings = [];
function note(kind, msg, extra) {
  const entry = { t: new Date().toISOString(), kind, msg, ...extra };
  findings.push(entry);
  console.log(`[${kind}] ${msg}${extra ? " " + JSON.stringify(extra) : ""}`);
}
async function shot(page, name) { try { await page.screenshot({ path: `${SHOT_DIR}/${name}.png` }); } catch (e) {} }

async function scan(page, code) { await page.keyboard.type(String(code), { delay: 0 }); await page.keyboard.press("Enter"); await page.waitForTimeout(250); }
async function focusField(page, name) { const el = page.locator(`[data-fk="${name}"]`).first(); await el.waitFor({ timeout: 8000 }); await el.click(); return el; }
async function fillScanField(page, name, value) { const el = await focusField(page, name); await el.fill(""); await scan(page, value); }
async function tapPrimary(page, label) { const btn = page.locator("#actionbar button, .actionbar button, button", { hasText: label }).first(); await btn.waitFor({ timeout: 8000 }); await btn.click(); await page.waitForTimeout(400); }
async function noticeText(page) { const el = page.locator("#notice, .notice").first(); if (!(await el.count().catch(() => 0))) return ""; return (await el.textContent().catch(() => "")) || ""; }

(async () => {
  const launchOpts = { args: ["--no-sandbox"] };
  if (!process.env.LOADTEST_SKIP_HAIRPIN_BYPASS) launchOpts.args.push(`--host-resolver-rules=MAP ${TARGET_HOST} ${process.env.LOADTEST_BYPASS_IP || "127.0.0.1"}`);
  const browser = await chromium.launch(launchOpts);
  const context = await browser.newContext({ viewport: { width: 390, height: 780 } });
  const page = await context.newPage();
  page.on("pageerror", (e) => note("BUG", `JS error: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error" && !/favicon|socket\.io/i.test(m.text())) note("BUG", `console.error: ${m.text().slice(0, 300)}`); });
  page.on("response", async (res) => {
    if (res.status() >= 400) {
      let body = ""; try { body = (await res.text()).slice(0, 500); } catch (e) {}
      note("BUG", `HTTP ${res.status()} on ${new URL(res.url()).pathname.replace("/api/method/", "")}`, { body });
    }
  });

  try {
    await page.goto(`${BASE}/login`);
    await page.fill("#login_email", EMAIL);
    await page.fill("#login_password", PASSWORD);
    await page.click(".btn-login");
    await page.waitForLoadState("networkidle", { timeout: 20000 }).catch(() => {});
    await page.goto(`${BASE}/wms`);
    await page.waitForFunction(() => window.WMS_BOOTED === true, { timeout: 20000 });
    const hash = await page.evaluate(() => location.hash);
    if (hash.startsWith("#/logon")) {
      const btn = page.locator("button", { hasText: RESOURCE });
      if (await btn.first().waitFor({ timeout: 8000 }).then(() => true).catch(() => false)) await btn.first().click();
      else note("BUG", `Resource ${RESOURCE} not offered on logon`);
      await page.waitForTimeout(400);
    } else {
      note("FLOW", "Resumed already-logged-on session");
    }

    // ---- #/load: work through every loadable shipment ----
    await page.goto(`${BASE}/wms#/load`);
    await page.waitForTimeout(700);
    await shot(page, "load-list");
    let shipmentCards = await page.locator(".card").count();
    note("INFO", `${shipmentCards} loadable shipment(s) found on #/load`);
    for (let i = 0; i < shipmentCards; i++) {
      await page.goto(`${BASE}/wms#/load`);
      await page.waitForTimeout(600);
      const card = page.locator(".card").first();
      if (!(await card.count())) break;
      const title = await card.locator(".card-title, h3, strong").first().textContent().catch(() => "?");
      await card.click();
      await page.waitForTimeout(600);
      await shot(page, `load-detail-${i}`);
      // Load every pending HU on this shipment one at a time via the scan field, same as an
      // operator physically scanning each one as it goes on the truck.
      for (let guard = 0; guard < 10; guard++) {
        const pending = page.locator(".card", { hasText: "Pending" });
        const n = await pending.count();
        if (!n) break;
        const huField = page.locator('[data-fk="hu"]');
        if (!(await huField.count())) { note("BUG", `Load screen (${title}): pending HU(s) shown but no scan field present`); break; }
        const firstPendingTitle = await pending.first().locator(".card-title, strong").first().textContent().catch(() => null);
        if (!firstPendingTitle) { note("BUG", `Load screen (${title}): could not read the next pending HU's own barcode off its card`); break; }
        await fillScanField(page, "hu", firstPendingTitle.trim());
        await page.waitForTimeout(600);
        const nt = await noticeText(page);
        if (/not on this shipment|already loaded/i.test(nt)) { note("BUG", `Load screen (${title}): scanning the HU shown as "Pending" on its own card failed: "${nt}"`); break; }
      }
      await shot(page, `load-after-${i}`);
      const status = await page.locator(".card, section").first().textContent().catch(() => "");
      if (/Loaded/i.test(status)) {
        const departBtn = page.locator("button", { hasText: "Depart" });
        if (await departBtn.count()) {
          page.once("dialog", (d) => d.accept());
          await departBtn.first().click();
          await page.waitForTimeout(800);
          note("INFO", `Departed shipment (${title})`);
        } else {
          note("BUG", `Load screen (${title}): shipment shows Loaded but no Depart action offered`);
        }
      } else {
        note("BUG", `Load screen (${title}): did not reach "Loaded" status after loading every pending HU shown`);
      }
    }

    // ---- #/ship: post goods issue for everything now ready ----
    await page.goto(`${BASE}/wms#/ship`);
    await page.waitForTimeout(700);
    await shot(page, "ship-list");
    const readyCount = await page.locator(".card").count();
    note("INFO", `${readyCount} delivery(ies) ready to ship on #/ship`);
    for (let i = 0; i < readyCount; i++) {
      await page.goto(`${BASE}/wms#/ship`);
      await page.waitForTimeout(600);
      const card = page.locator(".card").first();
      if (!(await card.count())) break;
      const title = await card.locator(".card-title, h3, strong").first().textContent().catch(() => "?");
      await card.click();
      await page.waitForTimeout(600);
      await shot(page, `ship-detail-${i}`);
      const lineCount = await page.locator(".line-head").count();
      if (!lineCount) { note("BUG", `Ship screen (${title}): opened from the ready-to-ship list but shows no lines at all`); continue; }
      for (let j = 0; j < lineCount; j++) {
        const huField = page.locator(`[data-fk="hu${j}"]`);
        const prefilled = await huField.inputValue().catch(() => "");
        if (!prefilled) note("FLOW", `Ship line ${j} (${title}): no suggested Handling Unit pre-filled`);
      }
      await tapPrimary(page, "Post goods issue");
      await page.waitForTimeout(900);
      await shot(page, `ship-after-${i}`);
      const gi = await noticeText(page);
      if (gi && /error|fail|not loaded|not configured/i.test(gi)) note("BUG", `Goods issue failed (${title}): ${gi}`);
      else note("INFO", `Goods issue posted (${title}): ${gi || "no confirmation text shown"}`);
    }
  } catch (e) {
    note("BUG", `Probe threw: ${e.message}`);
  } finally {
    await browser.close().catch(() => {});
  }

  fs.writeFileSync(`${SHOT_DIR}/findings-load-ship-probe.json`, JSON.stringify(findings, null, 2));
  console.log("\n=== LOAD/SHIP PROBE COMPLETE ===");
  console.log(`BUG: ${findings.filter((f) => f.kind === "BUG").length}, FLOW: ${findings.filter((f) => f.kind === "FLOW").length}, INFO: ${findings.filter((f) => f.kind === "INFO").length}`);
})();
