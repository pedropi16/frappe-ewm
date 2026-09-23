# Setup Checklist

A step-by-step path from a bare Frappe/ERPNext v16 bench to a working
`frappe_wms` installation you can actually run stock through. Work through
the phases in order — each one depends on the last. Every item links back
into [`README.md`](README.md) for the full explanation; this file is
intentionally just the checklist.

If you're picking this up mid-way (app already installed, some config
already done), skip to whichever phase you haven't finished.

## Phase 0 — Prerequisites

- [ ] A Frappe v16 bench is running, with **ERPNext v16 already installed**
      on the target site — `frappe_wms` depends on ERPNext's own master
      doctypes (`Item`, `UOM`, `Batch`, `Serial No`, `Company`, `Supplier`,
      `Customer`) and will not install without it.
- [ ] At least one ERPNext **Company** exists.
- [ ] At least one ERPNext **Warehouse** exists for each physical warehouse
      you're bringing under WMS (you'll link a `WMS Warehouse` to it in
      Phase 2).
- [ ] You have Administrator/System Manager access to the site.

## Phase 1 — Install the app

- [ ] `bench get-app https://github.com/pedropi16/frappe-ewm.git --branch main`
- [ ] `bench --site your-site install-app frappe_wms`
- [ ] `bench build --app frappe_wms`
- [ ] Verify: `bench --site your-site list-apps` shows `frappe_wms`.
- [ ] Verify: the **WMS** workspace and desktop icon appear on the desk for
      an Administrator (hard-refresh the browser if not).
- [ ] Verify: `/wms` loads (chrome-free RF scanner page) without a 404 —
      confirms the app's `www` page is wired up correctly.

Full detail: [README → Install](README.md#install). Testing on a container
you don't control the image lifecycle for (e.g. installed ad-hoc inside a
`frappe_docker` container rather than baked into a custom image)? See the
note at the bottom of that section on persistence.

## Phase 2 — Core warehouse structure (nothing moves without this)

Do these **in order** — each depends on the one before it.

- [ ] **WMS Settings** (`/app/wms-settings`) — review
      `enforce_wms_only_stock_movements` (leave on unless you have a reason
      to allow direct ERPNext stock entries against a WMS-managed
      warehouse) and set `default_handling_unit_type`.
- [ ] **WMS Warehouse** — one per ERPNext Warehouse you're managing with
      WMS. Company → WMS Warehouse.
- [ ] **Storage Type** — at least a receiving zone, a pick/bulk zone, and a
      ship zone per warehouse. **At least one Storage Type must have
      `storage_role = Door`** — nothing can ship without it.
- [ ] **Storage Bin** — physical bins under each Storage Type, including at
      least one Door-role bin.
- [ ] **Bin Determination Rule** — at least one blank-fallback rule per
      (warehouse, activity) so bin determination never dead-ends.
- [ ] **Process Determination Rule** — at least one blank-fallback rule per
      (warehouse, document type) pointing at a Storage Process or Route.
- [ ] **WMS Route** + **Route Stop** — at least one active Route per
      warehouse, with a `default_staging_bin` and a `default_door` pointing
      at a Door-role bin. **Not optional** — without this, Handling Units
      can never reach `Loaded` status, so Goods Issue can never post.

Full detail: [README → Configuration reference](README.md#configuration-reference),
steps 1–7 and 16.

## Phase 3 — People & devices (RF logon + queueing)

- [ ] Assign **roles** to your users — pick from `WMS Operator`,
      `WMS Receiver`, `WMS Picker`, `WMS Packer`, `WMS Loader`,
      `WMS Inventory Controller`, `WMS Supervisor`, `WMS Process Engineer`,
      `WMS Master Data`, `WMS Administrator`, `WMS Integration User`,
      `WMS Auditor` — matching what each person actually does.
- [ ] **WMS Resource** — one per physical device or operator login slot
      (Forklift, RF Scanner, Picking Trolley, ...) that a user will log on
      to from the RF app.
- [ ] **WMS Resource Group** (recommended, optional) — pool related
      Resources per warehouse so a Warehouse Queue can point at the group
      instead of individual resources (reassigning who works a queue later
      becomes a one-place edit).
- [ ] **Warehouse Queue** — at least one per (warehouse, activity) you want
      routed through a Warehouse Order. **This is the one step people
      forget**: skip it, and tasks for that activity are simply assigned
      and worked directly, one at a time — no Warehouse Order, no
      Blocked/On Hold lifecycle, and the RF app's Picking → Auto ("Get
      Work") has nothing to pull. Point the queue at a Resource Group
      rather than individual resources.
- [ ] Optional: **Activity Area** — only if queue routing needs to be
      narrower than a whole Storage Type (assign to many bins at once via
      the WMS Monitor's Bin Assignment tab).
- [ ] Optional: **Work Center** — a second, purely optional RF logon step
      for actions that default a bin from it (VAS generation today).

Full detail: [README → Core flows](README.md#core-flows) (the "Resources,
Resource Groups & queueing" section) and
[Configuration reference](README.md#configuration-reference) step 17.

## Phase 4 — Verify end to end

Do this as a real user (not Administrator) with a WMS role, on the actual
device/browser you'll use for RF:

- [ ] Open `/wms`. If not already logged on to a Resource, the Log On
      screen lists free Resources — claim one.
- [ ] Optional: join a Queue and/or log on to a Work Center from the same
      screen.
- [ ] Run one full inbound cycle: create/submit an Inbound Delivery →
      Receive from RF (or desk) → confirm the Putaway task that gets
      raised.
- [ ] If you configured a Warehouse Queue in Phase 3: from RF, Outbound →
      Picking → **Auto** should pull a task without you having to search
      for one.
- [ ] Run one full outbound cycle at least once: allocate/release a
      delivery for picking → confirm the Pick task → Stage/Load → Goods
      Issue posts. This is the flow most steps in Phase 2 (Door bin, Route)
      exist to support — if it doesn't work, re-check Phase 2 first.
- [ ] As a Supervisor, open a Warehouse Order from the desk and confirm the
      Put On Hold / Resume buttons work.

## Phase 5 — Optional, as-needed configuration

None of these block basic operation — add them only when you need the
specific behavior:

| Doctype | Unlocks | Skip it and... |
|---|---|---|
| Storage Process / Storage Process Step | Multi-step inbound/outbound chains | Document types run their one hardcoded step, as before |
| Removal Rule | LIFO/Fixed-Bin/custom pick strategy per product | Falls back to FEFO-then-FIFO |
| Warehouse Process Type Determination Rule | Route different items/priorities through different Warehouse Process Types | Each call site keeps using its hardcoded literal |
| WO Creation Rule | Cap tasks per Warehouse Order | A batch's tasks all share one Warehouse Order |
| Inspection Rule | Auto-create a Quality Inspection on matching receipts | Quality stays fully manual |
| Replenishment Rule | Hourly auto-replenishment of pick bins | Replenishment stays fully manual/order-triggered |
| Count Tolerance Group | Gate count variances to recount/approval | Every variance posts immediately |
| Cycle Count Rule | Scheduled count generation (ABC/low-stock/etc.) | Counts stay fully manual |
| WMS Number Range | Custom numbering per warehouse/HU Type/doctype | Global fallback series (`HU-########`, etc.) is used |
| WMS Resource (`Printer`) + Print Determination Rule | Auto-queue label printing on events | Nothing prints |
| Wave Template | Scheduled wave generation | Waves are created manually |
| Labor Standard | Efficiency numbers on the KPI dashboard | Dashboard just omits efficiency |
| Billing Rate | 3PL billing via the Monitor's Billing tab | Billing tab has nothing to compute |

Full detail for every row: [README → Configuration reference](README.md#configuration-reference).

## Where to go next

- Full field-by-field configuration reference: [README.md](README.md#configuration-reference)
- Roles & permissions detail: [README.md](README.md#roles--permissions)
- RF app screen-by-screen walkthrough: [README.md](README.md#rf--scanner-app)
- What's actually implemented vs. planned: [app_gap.md](app_gap.md)
- Uninstalling: [README.md](README.md#uninstall)
