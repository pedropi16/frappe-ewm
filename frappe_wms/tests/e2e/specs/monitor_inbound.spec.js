import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { seed, admin } from "../helpers.js";

// Desktop Inbound Monitor: process a delivery - receive and pack into a handling unit, direct placement, then create the tasks later.
const bench = (fn) => execFileSync("bench", ["--site", process.env.SITE || "wms.local", "execute", `frappe_wms.tests.e2e.seed.${fn}`],
  { cwd: process.env.BENCH_DIR || `${process.env.HOME}/frappe-bench`, encoding: "utf8" });

let deliveries;
test.beforeAll(() => { bench("cleanup"); bench("cleanup_adjustments"); deliveries = JSON.parse(bench("yard_demo").split("\n").find((l) => l.startsWith("E2E_YARD ")).slice(9)).inbound; });
test.afterAll(() => { bench("cleanup"); bench("cleanup_adjustments"); });

test("inbound monitor: receive & pack with direct placement, then create tasks for the rest", async ({ page, request }) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  const s = seed();
  expect((await page.request.post("/api/method/login", { form: { usr: s.admin, pwd: s.admin_password } })).ok()).toBeTruthy();
  await page.goto("/app/wms-monitor");
  await page.locator(".wms-mon-warehouse").selectOption(s.warehouse);
  await page.locator(".wms-mon-nav-item", { hasText: "Inbound Monitor" }).click();
  const popup = page.locator(".modal.show", { hasText: "Selection - " });
  await popup.getByRole("button", { name: /Execute/ }).click();
  await page.locator("tr", { hasText: deliveries[0] }).getByRole("button", { name: "Process" }).click();
  await page.getByRole("button", { name: /Receive & Pack/ }).click();
  const dialog = page.locator(".modal.show", { hasText: "Receive & Pack" });
  await dialog.locator("input.r-hu").first().fill("#1");
  await dialog.locator("input.r-bin").first().fill("E2E-WH-A2");
  await dialog.locator("input[data-fieldname='create_tasks']").uncheck();
  await dialog.getByRole("button", { name: "Receive", exact: true }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("button", { name: /Create Tasks/ })).toBeVisible();
  await expect(page.locator("h6", { hasText: "Handling Units (1)" })).toBeVisible();
  await page.getByRole("button", { name: /Create Tasks/ }).click();
  const tasks = page.locator(".modal.show", { hasText: "Create Tasks for" });
  await tasks.getByRole("button", { name: "Create Tasks", exact: true }).click();
  await expect(tasks).toBeHidden();
  await expect(page.locator("h6", { hasText: "Warehouse Tasks (1)" })).toBeVisible();
  const t = (await admin(request).get(`/api/resource/Warehouse Task?filters=${encodeURIComponent(JSON.stringify([["warehouse", "=", s.warehouse], ["task_type", "=", "Putaway"]]))}&fields=["destination_bin"]`)).data;
  expect(t.map((x) => x.destination_bin)).toEqual(["E2E-WH-A2"]);
  expect(errors).toEqual([]);
});
