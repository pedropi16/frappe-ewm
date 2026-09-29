import { test, expect } from "@playwright/test";
import { loginOperator, logon, openApp, freeResources, scan, enter, notice, view, seed, admin, makeTask } from "../helpers.js";

test.beforeEach(async ({ page, request }) => {
  await freeResources(request);
  await loginOperator(page);
  await logon(page);
});

async function makeDelivery(request, qty = 6) {
  const s = seed();
  const number = `E2E-IN-${Date.now().toString().slice(-8)}`;
  const doc = { doctype: "Inbound Delivery", inbound_delivery_number: number, warehouse: s.warehouse, receiving_bin: "E2E-WH-RECV",
    supplier: (await admin(request).get("/api/resource/Supplier?limit_page_length=1")).data[0].name,
    items: [{ line_number: 1, item: s.item, expected_quantity: qty, stock_uom: s.uom, expected_stock_type: "AVAILABLE" }] };
  const r = await admin(request).post("/api/resource/Inbound Delivery", doc);
  return { name: r.data.name, number };
}

test("Receive: open a delivery, scan a new HU, post - putaway task appears; nothing left afterwards", async ({ page, request }) => {
  const d = await makeDelivery(request, 6);
  await openApp(page, "#/receive");
  await view(page).getByText(d.number).click();
  await expect(page).toHaveURL(new RegExp(`#/receive/${d.name}$`));
  const hu = `E2EGR${Date.now().toString().slice(-7)}`;
  await scan(page, hu);                                            // HU barcode -> lands in the first HU field
  await page.locator('[data-fk="type0"]').selectOption("E2E-PALLET");
  await expect(page.locator('[data-fk="qty0"]')).toHaveValue("6");
  await page.getByRole("button", { name: /Post receipt/ }).click();
  await expect(notice(page)).toContainText(/Goods Receipt .* posted/);
  await expect(page).toHaveURL(/#\/tasks\/inbound$/);
  await expect(view(page).getByText(/Putaway/).first()).toBeVisible();
});

test("Receive: quantity above what is expected is rejected inline before anything is sent", async ({ page, request }) => {
  const d = await makeDelivery(request, 3);
  await openApp(page, `#/receive/${d.name}`);
  await scan(page, `E2EGX${Date.now().toString().slice(-7)}`);
  await page.locator('[data-fk="qty0"]').fill("9");
  await page.getByRole("button", { name: /Post receipt/ }).click();
  await expect(view(page).locator(".field-error")).toContainText(/Only 3/);
  await expect(page).toHaveURL(new RegExp(`#/receive/${d.name}$`));
});

// A fixed, idempotently-created item (same idiom as seed.py's own fixtures) rather than a fresh
// one per run: this item's item_code shows up in other test files' own unordered "pick any stock
// item" setUpClass queries (frappe.get_all("Item", filters={"is_stock_item": 1}, limit=1, ...)),
// so a new one on every run would keep outranking the real baseline item there and eventually
// break unrelated tests across the whole suite - one stable, unchanging item never does.
async function makeBatchItem(request) {
  const code = "E2E-BATCH-FIXED";
  const a = admin(request);
  if (!(await a.get(`/api/resource/Item/${code}`).catch(() => null))) {
    const itemGroup = (await a.get("/api/resource/Item Group?limit_page_length=1")).data[0].name;
    await a.post("/api/resource/Item", { doctype: "Item", item_code: code, item_name: code, item_group: itemGroup, stock_uom: "Nos", is_stock_item: 1, has_batch_no: 1 });
    await a.post("/api/resource/WMS Product", { doctype: "WMS Product", item: code, stock_uom: "Nos", warehouse_managed: 1, active: 1, batch_control: 1 });
  }
  const batchId = `${code}-B1`;
  if (!(await a.get(`/api/resource/Batch/${encodeURIComponent(batchId)}`).catch(() => null))) {
    await a.post("/api/resource/Batch", { doctype: "Batch", item: code, batch_id: batchId });
  }
  return { item: code, batchId };
}

// Same fixed-item idiom as makeBatchItem above - see its own comment for why.
async function makeSerialItem(request) {
  const code = "E2E-SERIAL-FIXED";
  const a = admin(request);
  if (!(await a.get(`/api/resource/Item/${code}`).catch(() => null))) {
    const itemGroup = (await a.get("/api/resource/Item Group?limit_page_length=1")).data[0].name;
    await a.post("/api/resource/Item", { doctype: "Item", item_code: code, item_name: code, item_group: itemGroup, stock_uom: "Nos", is_stock_item: 1, has_serial_no: 1 });
    await a.post("/api/resource/WMS Product", { doctype: "WMS Product", item: code, stock_uom: "Nos", warehouse_managed: 1, active: 1, serial_control: "Required at Receipt" });
  }
  return code;
}

test("Receive: a serial-controlled item is rejected without a serial, and succeeds once one is entered", async ({ page, request }) => {
  const s = seed();
  const item = await makeSerialItem(request);
  const number = `E2E-INS-${Date.now().toString().slice(-8)}`;
  const doc = { doctype: "Inbound Delivery", inbound_delivery_number: number, warehouse: s.warehouse, receiving_bin: "E2E-WH-RECV",
    supplier: (await admin(request).get("/api/resource/Supplier?limit_page_length=1")).data[0].name,
    items: [{ line_number: 1, item, expected_quantity: 1, stock_uom: "Nos", expected_stock_type: "AVAILABLE" }] };
  const r = await admin(request).post("/api/resource/Inbound Delivery", doc);
  await openApp(page, `#/receive/${r.data.name}`);
  const hu = `E2ESN${Date.now().toString().slice(-7)}`;
  await scan(page, hu);
  await page.locator('[data-fk="type0"]').selectOption("E2E-PALLET");
  await page.getByRole("button", { name: /Post receipt/ }).click();
  await expect(notice(page)).toContainText(/requires a serial number/i);
  await page.locator('[data-fk="serial0"]').fill(`SN-${Date.now().toString().slice(-8)}`);
  await page.getByRole("button", { name: /Post receipt/ }).click();
  await expect(notice(page)).toContainText(/Goods Receipt .* posted/);
});

test("Receive: a batch-controlled item is rejected without a batch, and succeeds once one is entered", async ({ page, request }) => {
  const s = seed();
  const { item, batchId } = await makeBatchItem(request);
  const number = `E2E-INB-${Date.now().toString().slice(-8)}`;
  const doc = { doctype: "Inbound Delivery", inbound_delivery_number: number, warehouse: s.warehouse, receiving_bin: "E2E-WH-RECV",
    supplier: (await admin(request).get("/api/resource/Supplier?limit_page_length=1")).data[0].name,
    items: [{ line_number: 1, item, expected_quantity: 4, stock_uom: "Nos", expected_stock_type: "AVAILABLE" }] };
  const r = await admin(request).post("/api/resource/Inbound Delivery", doc);
  await openApp(page, `#/receive/${r.data.name}`);
  const hu = `E2EBT${Date.now().toString().slice(-7)}`;
  await scan(page, hu);
  await page.locator('[data-fk="type0"]').selectOption("E2E-PALLET");
  await page.getByRole("button", { name: /Post receipt/ }).click();
  await expect(notice(page)).toContainText(/requires a batch number/i);
  await page.locator('[data-fk="batch0"]').fill(batchId);
  await page.getByRole("button", { name: /Post receipt/ }).click();
  await expect(notice(page)).toContainText(/Goods Receipt .* posted/);
});

test("Receive: entries survive a reload (draft), and Post is refused with no HU", async ({ page, request }) => {
  const d = await makeDelivery(request, 4);
  await openApp(page, `#/receive/${d.name}`);
  await page.getByRole("button", { name: /Post receipt/ }).click();
  await expect(notice(page)).toContainText(/Scan at least one Handling Unit/);
  const hu = `E2EGD${Date.now().toString().slice(-7)}`;
  await scan(page, hu);
  await page.locator('[data-fk="qty0"]').fill("2");
  await page.waitForTimeout(400);
  await page.reload(); await page.waitForFunction(() => window.WMS_BOOTED === true);
  await expect(page.locator('[data-fk="hu0"]')).toHaveValue(hu);
  await expect(page.locator('[data-fk="qty0"]')).toHaveValue("2");
});

test("Count: start a count, enter quantities, Save posts it", async ({ page, request }) => {
  const s = seed();
  // put stock into B1 with the app's own move logic (negative stock is allowed in the E2E warehouse)
  const t = await makeTask(request, { planned_quantity: 5 });
  await admin(request).method("frappe_wms.api.scanner.confirm_task", { task_name: t.name, confirmed_quantity: 5 });
  const c = await admin(request).post("/api/resource/WMS Physical Inventory Count", { doctype: "WMS Physical Inventory Count", warehouse: s.warehouse, storage_bin: "E2E-WH-B1", count_date: new Date().toISOString().slice(0, 10) });
  await openApp(page, "#/count");
  await view(page).getByText(c.data.name).click();
  await expect(page).toHaveURL(new RegExp(`#/count/${c.data.name}$`));
  const first = page.locator('[data-fk="c0"]');
  await expect(first).toBeVisible();
  await first.fill("abc");
  await page.getByRole("button", { name: /Save counts/ }).click();
  await expect(view(page).locator(".field-error")).toContainText(/number/i);
  await first.fill("5");
  await page.getByRole("button", { name: /Save counts/ }).click();
  await expect(notice(page)).toContainText(/count|Count/i);
  await expect(page).toHaveURL(/#\/s\/internal$/);
});
