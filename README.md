# Frappe WMS

An MVP warehouse management and execution system built as a Frappe app, running
alongside ERPNext (Frappe Framework v16). It replaces direct stock movement on
the warehouses it manages with its own ledger, tasks, and scanner-driven
workflows, then mirrors every posting back into ERPNext so ERPNext's own
reports and valuation stay correct.

This document is the map: how the pieces fit together, what each config
doctype actually controls, and where to go to change behavior. For the full
feature list see the bottom of this file; this document is written to be read
top to bottom by someone configuring the app for the first time.

## Contents
- [Mental model](#mental-model)
- [Data model](#data-model)
- [The rule engine (how config drives behavior)](#the-rule-engine-how-config-drives-behavior)
- [Core flows](#core-flows)
- [Modules](#modules)
- [Configuration reference](#configuration-reference)
- [Roles & permissions](#roles--permissions)
- [ERPNext integration](#erpnext-integration)
- [Background jobs](#background-jobs)
- [RF / scanner app](#rf--scanner-app)
- [Desk surfaces](#desk-surfaces)
- [Install](#install)
- [Production warning](#production-warning)

## Mental model

Everything in this app reduces to three layers, always in this order:

1. **Structure** — you configure warehouses, storage types, and bins once
   (rarely changes).
2. **Rules** — you configure how the app decides *where stock goes* and *what
   process to run* (changes as operations evolve).
3. **Documents** — day-to-day transactions (Inbound/Outbound Delivery, Goods
   Receipt/Issue, Warehouse Task, ...) that consume the rules and structure to
   move stock. These are what operators touch, mostly through the RF app.

Every stock movement, no matter which document triggers it, ends up as one or
more **WMS Stock Ledger Entry** rows (immutable, append-only) and updates the
matching **WMS Stock Balance** row (a rebuildable projection of the ledger —
see `services/stock.py`). If you ever doubt current quantities, `WMS Stock
Balance` can be rebuilt from `WMS Stock Ledger Entry` with
`frappe_wms.services.stock.rebuild_balances()`; the ledger is the only source
of truth.

## Data model

```
Company
  └─ WMS Warehouse ───── linked 1:1 ──── ERPNext Warehouse
       └─ Storage Type (e.g. RECEIVING, BULK, PICK, STAGING, SHIP)
            └─ Storage Bin (aisle/rack/level/position, capacity limits)
                 └─ Handling Unit (HU) ── nested via parent_hu/top_hu
                      └─ stock sits "in" an HU, in a bin, in a stock type
```

- **WMS Warehouse** wraps an ERPNext Warehouse and adds WMS-only settings:
  default receiving/shipping/difference bins, default stock type, and whether
  negative stock is allowed there.
- **Storage Type** groups bins by role (`storage_role`) and behavior — whether
  it mixes products/stock types/batches in one bin, whether it's HU-managed,
  and how it checks capacity (`capacity_check_method`).
- **Storage Bin** is the physical slot: capacity limits (max HUs / weight /
  volume), a putaway/removal/inventory block flag each (for taking a bin out
  of service without deleting it), and optional whitelists of which stock
  types / HU types it accepts (`Storage Bin Allowed Stock Type` / `... HU
  Type` child tables).
- **Handling Unit (HU)** is a physical container (pallet, tote, carton) that
  can nest inside another HU (`parent_hu`) up to an arbitrary depth
  (`hierarchy_level`, `top_hu` cached for fast lookups). Stock quantities are
  usually tracked per HU, not per bin, when the storage type is HU-managed.
- **WMS Stock Type** is *not* a physical location — it's a status dimension
  layered on top of the physical location (e.g. `AVAILABLE`, `QUALITY`,
  `DAMAGED`, `SCRAP`, `WAREHOUSE_BLOCKED`). Each stock type independently
  controls whether it's `available_for_allocation`, `picking_allowed`,
  `shipping_allowed`, `movement_allowed`, and whether it `requires_release`
  or `requires_reason` to move out of. Quality Inspection and stock-type-change
  moves change *this* dimension without necessarily moving the physical bin/HU.
- **WMS Stock Ledger Entry** is one row per (warehouse, product, batch, serial,
  HU, bin, stock type) delta, always created in balanced pairs for a transfer
  (source negative, destination positive) via `services/stock.transfer_stock`.
  Idempotency keys prevent double-posting on retries.
- **WMS Stock Balance** is keyed by the same dimension tuple (hashed into its
  `name`) and holds the running `quantity`, `allocated_quantity`, and derived
  `available_quantity`. It is written *only* by `services/stock.py` — direct
  edits are blocked by `permissions.balance_has_permission`.

## The rule engine (how config drives behavior)

This is the part that makes the app "configurable" rather than hard-coded, and
the part worth understanding before touching anything else. Two independent
rule tables, both evaluated by `services/determination.py`, both matched by
**priority ascending, first full match wins**, where an empty field on a rule
means "matches anything":

### 1. Process Determination Rule → *which process runs*
Given a warehouse + document type (Goods Receipt / Goods Issue / etc.) + item
context, picks a **Storage Process** (an ordered list of **Storage Process
Step**s, each pointing at a **Warehouse Process Type**) or a **Route**.
This is how "receiving product X always gets quality-inspected first" or
"receiving into warehouse B always requests before putaway" gets expressed
without touching code.

### 2. Bin Determination Rule → *which bin a movement lands in*
Given a warehouse + activity (Putaway/Pick/Stage/...) + item/stock-type/HU
context, either returns a `fixed_destination_bin`, requires manual selection,
or applies a **strategy** over the bins of a `destination_storage_type`:

| Strategy | Behavior |
|---|---|
| Least Utilized Bin | Lowest `current_hu_count` first |
| Bin Sequence | Lowest `sequence` first (fixed pick-path order) |
| First Empty Bin | Prefers bins with zero HUs, falls back to sequence |
| Addition to Existing Stock | Prefers a bin that already holds this product |
| Manual Selection | Forces the operator to pick (no auto-determination) |

Both rule tables are scoped to a warehouse and can be filtered on any
combination of item / item group / stock type / HU type / source or
destination storage type — narrower, higher-priority rules should sit above
broader fallback rules.

### Supporting config that feeds the rules
- **Warehouse Process Type** — defines what a step of activity actually
  requires (source/destination/HU/stock required?), its `confirmation_mode`,
  and which **WMS Movement Type** it posts.
- **WMS Movement Type** — the ledger-level classification (Goods Receipt,
  Putaway, Picking, Goods Issue, Inventory Gain/Loss, Pack/Unpack, ...), each
  tagged with its `inventory_effect` (Increase/Decrease/Transfer) and an
  optional `reversal_movement_type` for cancellations.
- **Replenishment Rule** — per (warehouse, product, pick bin), a
  minimum/target quantity sourced from a bulk storage type; the hourly job
  raises a Warehouse Request when the pick bin falls to or below minimum.
- **Allowed Task Type** — restricts which task types a given context may
  generate.

`after_install` seeds a sensible starting set of Stock Types, Movement Types,
and Warehouse Process Types (see `install.py`) so a fresh site isn't empty,
but Process/Bin Determination Rules and Replenishment Rules are
warehouse-specific and must be configured per site.

## Core flows

**Inbound:** `Inbound Delivery` (expected receipt, e.g. against a Purchase
Order) → operator scans HUs against it in the RF app → `Goods Receipt` is
posted → ledger entries increase stock in the receiving bin → Process
Determination Rule decides if putaway is required → `Warehouse Request` →
`Warehouse Task` (Putaway) → operator confirms in RF → stock moves from
receiving bin to its determined destination bin.

**Outbound:** `Outbound Delivery` (against a Sales Order, or standalone) is
allocated (`services/allocation.py`) — reserves `WMS Stock Balance` rows,
optionally grouped into a `WMS Wave` for combined release — → pick tasks are
generated per allocation (or clustered across a wave's deliveries onto shared
bins/products) → operator picks in RF → stock stages → `Packing Order`
(optional) groups HUs for shipment → `Goods Issue` posted → ledger decreases
stock, ERPNext Delivery Note (or generic Stock Entry) mirrored.

**Physical count:** `WMS Physical Inventory Count` snapshots `WMS Stock
Balance` for a warehouse (optionally scoped to bin/storage type/product) →
operator records counted quantities in RF → posting the count writes the
variance as an Inventory Gain/Loss movement against the ledger.

**Quality inspection:** `WMS Quality Inspection` splits a quantity out of an
inspection stock type into a passed-to / failed-to stock type, reusing the
same stock-type-change movement mechanics as any other posting change — no
physical relocation required.

**Ad-hoc move:** the RF "Move" action skips the request/task-planning step
entirely and calls `services/task.create_and_confirm_move` directly — for
on-the-spot corrections where a plan isn't needed.

## Modules

| Module | Contains |
|---|---|
| `wms_core` | Warehouse/Storage Type/Storage Bin structure, WMS Settings, the WMS Monitor page, the WMS workspace |
| `wms_setup` | Everything in [the rule engine](#the-rule-engine-how-config-drives-behavior): determination rules, process types, movement types, replenishment rules, plus child tables (delivery line items, HU/stock-type bin whitelists, packing source/destination HUs, shipment lines) |
| `wms_inbound` | Inbound Delivery, Goods Receipt |
| `wms_outbound` | Outbound Delivery, Goods Issue, Stock Allocation, Packing Order, WMS Wave |
| `wms_inventory` | WMS Product, WMS Stock Type, WMS Stock Balance, WMS Stock Ledger Entry, Physical Inventory Count, Quality Inspection |
| `wms_handling_units` | Handling Unit, HU Type, HU Event (audit trail), Packaging Material |
| `wms_execution` | Warehouse Request, Warehouse Task, Task Allocation, Warehouse Queue, WMS Resource, WMS Exception Code |
| `wms_shipping` | WMS Route, WMS Shipment |

`services/*.py` holds the transactional logic each doctype's controller calls
into (allocation, determination, receipt, issue, picking, packing,
replenishment, quality, inventory_count, procurement/sales — the PO/SO
integration, erpnext_sync). `events/*.py` wires those services (and guard
logic) to `hooks.py`'s `doc_events`. `api/*.py` is the whitelisted surface the
RF frontend and scanner hardware call.

## Configuration reference

Everything an implementer needs to touch, roughly in the order you'd touch it
on a new site:

1. **WMS Settings** (`/app/wms-settings`, singleton) — the one app-wide
   switch panel:
   - `enforce_wms_only_stock_movements` (default **on**) — see
     [ERPNext integration](#erpnext-integration). Turn off only as a temporary
     escape hatch.
   - `default_handling_unit_type` — used by the RF app to silently
     auto-register a scanned HU barcode that doesn't exist yet.
2. **Company → WMS Warehouse** — one per ERPNext Warehouse you want WMS to
   manage. Set default receiving/shipping/difference bins and default stock
   type once bins exist below.
3. **Storage Type** per warehouse (e.g. RECEIVING, BULK, PICK, STAGING, SHIP,
   QUALITY, DAMAGE) — decide mixing rules and whether it's HU-managed.
4. **Storage Bin** per storage type — physical layout, capacity limits,
   optional stock-type/HU-type whitelists.
5. **Warehouse Process Type** — the seeded set (`GR_UNLOAD`, `GR_PUTAWAY`,
   `OB_PICK`, `OB_STAGE`, `OB_LOAD`, `INTERNAL_MOVE`, `PACK_REPACK`,
   `STOCK_TYPE_CHANGE`, `REPLENISH`) usually covers an MVP; add more only if
   you need a new activity/movement-type combination.
5. **Bin Determination Rule** — at least one fallback rule per
   (warehouse, activity) with no item/stock-type filters, so determination
   never dead-ends; add narrower higher-priority rules on top for exceptions.
6. **Process Determination Rule** — at least one fallback per (warehouse,
   document type) pointing at a Storage Process (or Route).
7. **Storage Process** / **Storage Process Step** — only needed where a
   document type must run more than one step (e.g. unload, then putaway).
8. **Replenishment Rule** — one per (warehouse, product, pick bin) you want
   auto-replenished; the hourly job does the rest.
9. **Roles** — assign the roles below to users; optionally add **User
   Permission** rows restricting a user to specific `WMS Warehouse` values
   (see [Roles & permissions](#roles--permissions)).

## Roles & permissions

Roles are seeded by `setup/roles.py` on install:

`WMS Operator`, `WMS Receiver`, `WMS Picker`, `WMS Packer`, `WMS Loader`,
`WMS Inventory Controller`, `WMS Supervisor`, `WMS Process Engineer`,
`WMS Master Data`, `WMS Administrator`, `WMS Integration User`, `WMS Auditor`.

The RF app checks specific roles per action (Operator/Receiver/Loader/
Supervisor depending on the action; System Manager always allowed — see
`utils.require_role` call sites). Desk doctype-level permissions follow
Frappe's normal role-permission matrix (Role Permission Manager) — nothing
in this app overrides those except two custom, warehouse-scoped checks:

- **WMS Stock Ledger Entry** and **WMS Stock Balance** are read-only from the
  desk (write/create/delete/submit/cancel always denied — they're written
  exclusively by `services/stock.py` with `flags.ignore_permissions`, so a
  desk edit would desync the balance from the ledger).
- If a user has a **User Permission** row restricting them to specific `WMS
  Warehouse` values, that also filters their visibility of `WMS Stock Ledger
  Entry` and `Warehouse Task` rows (`permissions.py`) — no User Permission
  rows means no extra restriction.

## ERPNext integration

- Each `WMS Warehouse` auto-links to a matching ERPNext `Warehouse`.
- Posting a `Goods Receipt` / `Goods Issue` mirrors a `Stock Entry` (Material
  Receipt/Issue) into ERPNext, keeping ERPNext's own Bin quantities and
  valuation reconciled with the WMS ledger.
- If the Goods Receipt/Issue originates from a Purchase Order or Sales Order
  (via the order's "Create > Inbound/Outbound Delivery" button, or
  `services/procurement.py` / `services/sales.py`), it posts a Purchase
  Receipt / Delivery Note against that order instead of a generic Stock
  Entry, so the order's received/delivered percentage updates correctly.
  Cancelling the Goods Receipt/Issue cancels the matching ERPNext document.
  **A Goods Receipt/Issue must be either fully order-linked or fully
  standalone** — mixed lines are rejected rather than mis-posted.
- **Stock enforcement**: once a warehouse is WMS-linked, `events/
  erpnext_stock_guard.py` blocks direct Stock Entry / Delivery Note /
  Purchase Receipt / Stock Reconciliation postings against it from ERPNext's
  own desk, pointing the user at the matching WMS document instead. Warehouses
  *not* linked to a WMS Warehouse are completely unaffected — other ERPNext
  flows (manufacturing, subcontracting, etc.) keep working normally elsewhere.
  Toggle off via `WMS Settings.enforce_wms_only_stock_movements`.
  **Known gap**: this doesn't cover every ERPNext path that can move stock
  (Purchase/Sales Invoice with "Update Stock", Subcontracting, Asset
  scrapping, Job Card) — only the four primary stock documents.
- A daily job (`verify_erpnext_stock_reconciliation`) flags any drift between
  WMS and ERPNext quantities per warehouse/product via `frappe.log_error`.

## Background jobs

Registered in `hooks.py` under `scheduler_events`:

| Frequency | Job | Purpose |
|---|---|---|
| Hourly | `recalculate_stale_bin_capacity` | Recomputes `current_hu_count`/`current_weight` per active bin from live HU data |
| Hourly | `run_replenishment_check` | Evaluates every active Replenishment Rule, raises a Warehouse Request+Task for pick bins at/below minimum (skips if one's already pending) |
| Daily | `verify_stock_balance_integrity` | Logs any negative `WMS Stock Balance` rows as an error for review |
| Daily | `verify_erpnext_stock_reconciliation` | Logs WMS vs ERPNext quantity drift per warehouse/product |

None of these post anything automatically except replenishment task creation
— the integrity/reconciliation checks are report-only (`frappe.log_error`),
by design, so they never silently correct the ledger.

## RF / scanner app

`/wms` is a chrome-free, scanner-oriented page (not a desk form) — the RF
frontend at `frappe_wms/www/wms/`, styled after SAP EWM's RF UI. A home menu
leads to:

| Action | What it does |
|---|---|
| Tasks | Confirm any planned putaway/pick/move/stage/load task, or report an exception |
| Receive | Pick an open Inbound Delivery, scan an HU per line (unknown barcodes auto-register using `default_handling_unit_type`), post the Goods Receipt — which immediately raises putaway tasks |
| Ship | Pick a delivery that's fully picked but not issued, confirm/adjust the suggested staged HU per line, post the Goods Issue |
| Pack | Complete an open Packing Order in one tap |
| Move | Ad-hoc bin-to-bin/HU-to-HU transfer with no planning step |
| Count | Record physical inventory quantities, auto-posts once every line is counted |
| Quality | Complete an inspection's pass/fail split |
| Lookup | HU/bin contents by barcode |

`api/scanner.py` and the other `api/*.py` modules are the whitelisted
endpoints this frontend (and real barcode hardware) call.

## Desk surfaces

- **WMS workspace** (`/app/wms`) — every WMS doctype grouped by module, plus
  shortcuts to the WMS Monitor, RF Scanner, WMS Settings, and the ERPNext
  masters a warehouse operation regularly needs (Stock Settings, Warehouse,
  Item, Purchase Order, Sales Order). Visibility follows normal Frappe
  per-doctype/per-role permissions — this workspace doesn't add or remove
  access, only groups links.
- **WMS Monitor** (`/app/wms-monitor`) — pick a warehouse to see summary
  counts (open tasks by type, exceptions, pending replenishment, deliveries
  in progress, open counts/inspections, open waves, active resources — each
  linking to its filtered list view), plus searchable sections for ledger
  movements, tasks, HUs, deliveries, waves (with one-click Release per draft
  wave), resource workload, and warehouse queues. Roles: WMS Supervisor /
  Administrator / Inventory Controller / Auditor, System Manager.

## Install

```bash
cd frappe-bench
bench get-app /path/to/frappe_wms_full_app
bench --site your-site install-app frappe_wms
bench --site your-site migrate
bench build --app frappe_wms
```

Requires ERPNext master doctypes (`Item`, `UOM`, `Batch`, `Serial No`,
`Company`, `Supplier`, `Customer`) — install ERPNext first. `after_install`
then seeds default Stock Types, Movement Types, Warehouse Process Types, and
the roles listed above; everything else in
[Configuration reference](#configuration-reference) is left for you to set up
per site.

## Production warning

This is a complete MVP scaffold, not a certified SAP EWM replacement.
Validate accounting integration, concurrency, permissions, barcode hardware,
reversal rules, and migration data in a non-production site before go-live.
