import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { seed, admin } from "../helpers.js";

// Desktop Production Staging page: pick the very stock line an order must use ("Choose stock...").
const bench = (fn) => execFileSync("bench", ["--site", process.env.SITE || "wms.local", "execute", `frappe_wms.tests.e2e.seed.${fn}`],
  { cwd: process.env.BENCH_DIR || `${process.env.HOME}/frappe-bench`, encoding: "utf8" });

let demo;
test.beforeAll(() => { bench("cleanup_staging"); bench("cleanup"); demo = JSON.parse(bench("staging_demo").split("\n").find((l) => l.startsWith("E2E_STAGING ")).slice(12)); });
test.afterAll(() => { bench("cleanup_staging"); bench("cleanup"); });

test("production staging: choose the stock line and stage it", async ({ page, request }) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  const s = seed();
  expect((await page.request.post("/api/method/login", { form: { usr: s.admin, pwd: s.admin_password } })).ok()).toBeTruthy();
  await page.goto("/app/wms-production-staging");
  await page.locator("input[data-fieldname='psa']").fill(demo.psa);
  await page.locator("input[data-fieldname='psa']").press("Tab");
  const row = page.locator("tr[data-g]", { hasText: demo.item });
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "Choose stock" }).click();
  const dialog = page.locator(".modal.show", { hasText: "Choose stock of" });
  const line = dialog.locator("tr[data-i]", { hasText: "E2E-WH-A1" });
  await expect(line).toBeVisible();
  await line.locator("input.wms-cpick").check();
  await line.locator("input.wms-cq").fill("4");
  await dialog.getByRole("button", { name: "Create Staging Tasks" }).click();
  await expect(dialog).toBeHidden();
  const tasks = (await admin(request).get(`/api/resource/Warehouse Request?filters=${encodeURIComponent(JSON.stringify([["warehouse", "=", s.warehouse], ["source_bin", "=", "E2E-WH-A1"]]))}&fields=["requested_quantity","destination_bin"]`)).data;
  expect(tasks).toEqual([{ requested_quantity: 4, destination_bin: "E2E-WH-STAGE" }]);

  // the kanban bin of another material: shown with its fill level, and an empty container can be signalled
  const kanban = page.locator("tr", { hasText: demo.kanban_item }).filter({ hasText: "E2E-WH-B1" });
  await expect(kanban).toContainText("Empty");
  await kanban.getByRole("button", { name: "Container empty" }).click();
  await expect.poll(async () => (await admin(request).get(`/api/resource/Warehouse Request?filters=${encodeURIComponent(JSON.stringify([["warehouse", "=", s.warehouse], ["destination_bin", "=", "E2E-WH-B1"]]))}&fields=["requested_quantity"]`)).data.length).toBeGreaterThan(0);
  expect(errors).toEqual([]);
});
