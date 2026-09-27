import { readFileSync } from "node:fs";
import { expect } from "@playwright/test";

export const seed = () => JSON.parse(readFileSync(new URL("./.seed.json", import.meta.url), "utf8"));

// ---- admin REST (token auth: no session/CSRF needed) ----
export function admin(request) {
  const s = seed();
  const headers = { Authorization: `token ${s.api_key}:${s.api_secret}`, "Content-Type": "application/json" };
  return {
    async post(path, data) { const r = await request.post(path, { headers, data }); if (!r.ok()) throw new Error(`${path}: ${r.status()} ${await r.text()}`); return (await r.json()); },
    async get(path) { const r = await request.get(path, { headers }); if (!r.ok()) throw new Error(`${path}: ${r.status()} ${await r.text()}`); return (await r.json()); },
    async put(path, data) { const r = await request.put(path, { headers, data }); if (!r.ok()) throw new Error(`${path}: ${r.status()} ${await r.text()}`); return (await r.json()); },
    async del(path) { await request.delete(path, { headers }); },
    async method(name, data) { return (await this.post(`/api/method/${name}`, data)).message; },
  };
}

export async function makeTask(request, overrides = {}) {
  const s = seed();
  const doc = { doctype: "Warehouse Task", task_type: "Internal Move", warehouse: s.warehouse, product: s.item, planned_quantity: 5, stock_uom: s.uom,
    source_bin: "E2E-WH-A1", destination_bin: "E2E-WH-B1", stock_type_from: "AVAILABLE", stock_type_to: "AVAILABLE", movement_type: "301",
    priority: "Normal", status: "Open", ...overrides };
  return (await admin(request).post("/api/resource/Warehouse Task", doc)).data;
}
export async function getTask(request, name) { return (await admin(request).get(`/api/resource/Warehouse Task/${encodeURIComponent(name)}`)).data; }

export async function freeResources(request) {
  const a = admin(request);
  for (const code of seed().resources) await a.put(`/api/resource/WMS Resource/${code}`, { user: null, logged_in_at: null, current_queue: null, current_work_center: null });
}

// ---- operator session in the browser ----
export async function loginOperator(page) {
  const s = seed();
  const r = await page.request.post("/api/method/login", { form: { usr: s.operator, pwd: s.operator_password } });
  expect(r.ok(), "operator login").toBeTruthy();
}

export async function openApp(page, hash = "") {
  await page.goto("/wms" + hash);
  await page.waitForFunction(() => window.WMS_BOOTED === true);
}

// Logs the operator on to a resource through the API (before the app loads) so a test can start from the menu.
export async function logon(page, code = "E2E-RF1") {
  const html = await (await page.request.get("/wms")).text();
  const csrf = html.match(/"csrf":\s*"([^"]+)"/)[1];
  const r = await page.request.post("/api/method/frappe_wms.api.resource.log_on", { headers: { "X-Frappe-CSRF-Token": csrf }, data: { resource_code: code } });
  expect(r.ok(), "log on").toBeTruthy();
}

// Types like a Bluetooth HID scanner: the whole code back to back with no delay, then Enter.
export async function scan(page, code) {
  await page.keyboard.type(code, { delay: 0 });
  await page.keyboard.press("Enter");
}
// A person's Enter: never within 350ms of a screen change (the app ignores those as accidental double-Enters).
export async function enter(page) { await page.waitForTimeout(400); await page.keyboard.press("Enter"); }
// Types like a person: 120ms between keys.
export async function typeSlow(page, text) { await page.keyboard.type(text, { delay: 120 }); }

export const notice = (page) => page.locator("#notice");
export const view = (page) => page.locator("#view");
