import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { seed, admin } from "../helpers.js";

// The Inbound Monitor only finds and shows a delivery; its maintenance screen (the Inbound Delivery form) processes it: receive & pack with direct placement, create tasks later.
const bench = (fn) => execFileSync("bench", ["--site", process.env.SITE || "wms.local", "execute", `frappe_wms.tests.e2e.seed.${fn}`],
  { cwd: process.env.BENCH_DIR || `${process.env.HOME}/frappe-bench`, encoding: "utf8" });

let deliveries;
test.beforeAll(() => { bench("cleanup"); bench("cleanup_adjustments"); deliveries = JSON.parse(bench("yard_demo").split("\n").find((l) => l.startsWith("E2E_YARD ")).slice(9)).inbound; });
test.afterAll(() => { bench("cleanup"); bench("cleanup_adjustments"); });

test("inbound: monitor links to the delivery screen, which receives, packs and creates the tasks", async ({ page, request }) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  const s = seed();
  expect((await page.request.post("/api/method/login", { form: { usr: s.admin, pwd: s.admin_password } })).ok()).toBeTruthy();
  await page.goto("/app/wms-monitor");
  await page.locator(".wms-mon-warehouse").selectOption(s.warehouse);
  await page.locator(".wms-mon-nav-item", { hasText: "Inbound Monitor" }).click();
  await page.locator(".modal.show", { hasText: "Selection - " }).getByRole("button", { name: /Execute/ }).click();
  await expect(page.getByRole("button", { name: "Process" })).toHaveCount(0);  // the monitor displays; it has no processing buttons
  await page.locator("tr a", { hasText: deliveries[0] }).first().click();       // ... it links to the delivery's own screen

  await expect(page.getByRole("button", { name: /Receive & Pack/ })).toBeVisible();
  await page.getByRole("button", { name: /Receive & Pack/ }).click();
  const dialog = page.locator(".modal.show", { hasText: "Receive & Pack" });
  await dialog.locator("input.r-hu").first().fill("#1");
  await dialog.locator("input.r-bin").first().fill("E2E-WH-A2");
  await dialog.locator("input[data-fieldname='create_tasks']").uncheck();
  await dialog.getByRole("button", { name: "Receive", exact: true }).click();
  await expect(dialog).toBeHidden();

  await page.getByRole("tab", { name: "HU" }).click();
  await expect(page.locator("tr:visible", { hasText: "E2E-WH-RECV" }).first()).toBeVisible();
  await page.getByRole("button", { name: /Actions/ }).click();
  await page.locator("a:visible", { hasText: "Create Tasks…" }).first().click();
  const tasks = page.locator(".modal.show", { hasText: "Create Tasks for" });
  await tasks.getByRole("button", { name: "Create Tasks", exact: true }).click();
  await expect(tasks).toBeHidden();
  const t = (await admin(request).get(`/api/resource/Warehouse Task?filters=${encodeURIComponent(JSON.stringify([["warehouse", "=", s.warehouse], ["task_type", "=", "Putaway"]]))}&fields=["destination_bin"]`)).data;
  expect(t.map((x) => x.destination_bin)).toEqual(["E2E-WH-A2"]);
  expect(errors).toEqual([]);
});
