import { test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { seed } from "../helpers.js";

// Screenshots for the usability audit (not an assertion spec): SHOT_DIR=<dir> npx playwright test specs/monitor_audit_shots.spec.js
const bench = (fn) => execFileSync("bench", ["--site", process.env.SITE || "wms.local", "execute", `frappe_wms.tests.e2e.seed.${fn}`], { cwd: process.env.BENCH_DIR || `${process.env.HOME}/frappe-bench`, encoding: "utf8" });
const dir = process.env.SHOT_DIR || "/tmp";
let demo;
test.beforeAll(() => { bench("cleanup"); bench("worklist_stock"); bench("cleanup_outbound"); demo = JSON.parse(bench("outbound_demo").split("\n").find((l) => l.startsWith("E2E_OUTBOUND ")).slice(13)); });
test.afterAll(() => { bench("cleanup"); bench("cleanup_outbound"); });

test("shots", async ({ page }) => {
  const s = seed();
  await page.request.post("/api/method/login", { form: { usr: s.admin, pwd: s.admin_password } });
  const shot = async (name) => { await page.waitForTimeout(1200); await page.screenshot({ path: `${dir}/${name}.png`, fullPage: false }); };
  for (const [route, name] of [["wms-monitor", "monitor"], ["wms-alerts", "alerts"], ["wms-locks", "locks"], ["wms-packing-center", "packing"], ["wms-yard", "yard"], ["wms-slotting", "slotting"], ["wms-billing", "billing"], ["wms-bin-assignment", "binassign"], ["wms-kitting", "kitting"]]) {
    await page.goto(`/app/${route}`); await shot(name);
  }
  await page.goto("/app/wms-adhu"); await page.locator(".wb-wh").selectOption(s.warehouse); await page.locator(".wb-by").selectOption("storage_bin"); await page.locator(".wb-value").fill(s.bins[0]); await page.locator(".wb-go").click(); await shot("adhu");
  await page.locator(".wb-toggle").click(); await shot("adhu_detail");
  await page.goto("/app/wms-posting"); await shot("posting_empty");
  await page.goto("/app/wms-outbound-delivery"); await shot("delivery_list");
  await page.goto(`/app/wms-outbound-delivery/${demo.delivery}`); await shot("delivery_doc");
  await page.goto("/app/wms-adhoc"); await shot("adhoc");
  await page.goto("/app/warehouse-task"); await shot("task_list");
  await page.goto("/app/wms"); await shot("workspace");
});
