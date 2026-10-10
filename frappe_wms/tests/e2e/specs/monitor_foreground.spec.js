import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { seed } from "../helpers.js";

// Confirmation in the foreground on the desk: the dialog asks for the serial numbers the system cannot guess, and refuses a wrong one.
const bench = (fn) => execFileSync("bench", ["--site", process.env.SITE || "wms.local", "execute", `frappe_wms.tests.e2e.seed.${fn}`],
  { cwd: process.env.BENCH_DIR || `${process.env.HOME}/frappe-bench`, encoding: "utf8" });

test.beforeAll(() => { bench("cleanup"); bench("serial_stock"); });
test.afterAll(() => bench("cleanup"));

test("a task that needs serial numbers is confirmed in the foreground dialog", async ({ page }) => {
  const s = seed();
  const name = bench("serial_task").split("\n").find((l) => l.startsWith("E2E_TASK ")).slice(9);
  expect((await page.request.post("/api/method/login", { form: { usr: s.admin, pwd: s.admin_password } })).ok()).toBeTruthy();
  await page.goto(`/app/warehouse-task/${name}`);
  await page.getByRole("button", { name: /Actions/ }).click();
  await page.locator("a:visible, .dropdown-item:visible", { hasText: "Confirm Task" }).first().click();
  const d = page.locator(".modal.show", { hasText: "foreground" });
  await expect(d).toContainText("Serial Numbers");
  await d.locator("[data-fieldname='scanned_source'] input").fill("E2E-WH-A1");
  await d.locator("[data-fieldname='scanned_destination'] input").fill("E2E-WH-A2");
  await d.locator("[data-fieldname='serial_text'] textarea").fill("NOT-A-SERIAL\nE2ESNM1");
  await d.locator(".btn-primary").click();
  const refusal = page.locator(".modal.show", { hasText: /not in/ });
  await expect(refusal).toBeVisible();   // a wrong serial is refused
  await refusal.locator(".btn-modal-close").click();
  await d.locator("[data-fieldname='serial_text'] textarea").fill("E2ESNM1\nE2ESNM2");
  await d.locator(".btn-primary").click();
  await expect(page.locator(".alert-container, .desk-alert").filter({ hasText: "confirmed" }).first()).toBeVisible();
  await expect.poll(async () => (await page.request.get(`/api/resource/Warehouse Task/${name}`)).json().then((j) => j.data.status)).toBe("Confirmed");
});
