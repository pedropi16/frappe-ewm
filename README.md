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
- [Numbering (HU and shipment number ranges)](#numbering-hu-and-shipment-number-ranges)
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
  Every HU Type is either **External** (a person or a label printer already
  put a number on the physical HU - the operator scans it and the system just
  registers it) or **Internal** (the system always assigns the next number
  itself from a **WMS Number Range**, ignoring any number a caller passes) -
  see [Numbering](#numbering-hu-and-shipment-number-ranges). An HU Type
  marked `reusable` can be recycled once empty, returning its number to the
  pool for reissue. **Packaging Material** attaches physical dimensions
  (length/width/height/tare weight/maximum weight) to an HU Type; picking one
  when creating an HU fills in HU Type and Tare Weight for you.
- **WMS Product** (per warehouse-agnostic Item) carries `gross_weight_per_unit`
  and `volume_per_unit`, used to keep an HU's `gross_weight`/`net_weight`/
  `volume` live as stock moves in and out of it - which in turn feeds bin
  capacity checks during putaway (see
  [Bin Determination Rule](#2-bin-determination-rule--which-bin-a-movement-lands-in)).
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

Before any strategy runs, candidate bins are filtered to ones with room:
a bin at its `maximum_hus` is dropped, and one whose `current_weight` plus
the incoming weight (`WMS Product.gross_weight_per_unit` × quantity, when
that's configured) would exceed `maximum_weight` is dropped too
(`services/determination._bins_with_capacity`). A bin with no
`maximum_hus`/`maximum_weight` set is never excluded on that dimension. This
is also why `Handling Unit.gross_weight`/`net_weight`/`volume` are kept live
(`services/handling_unit.recompute_measurements`, called from every
`services/stock.post_entries` posting) instead of sitting at zero forever -
`Storage Bin.current_weight` and the hourly `recalculate_stale_bin_capacity`
job both depend on it, and it's what makes a Packaging Material's
weight/dimensions actually feed back into putaway decisions.

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

## Numbering (HU and shipment number ranges)

Creating a Handling Unit - from the Desk "New" form, the RF app, or Goods
Receipt auto-registration - only ever *requires* a **Storage Bin** (or a
parent HU to nest into) and an **HU Type** (directly, or implied by a
**Packaging Material**), same as SAP EWM. Everything else (Warehouse, HU
Number) is derived. All of this lives in the `Handling Unit` controller's
`before_insert`/`after_insert` (`wms_handling_units/doctype/handling_unit/handling_unit.py`)
so every creation path behaves identically instead of three services each
re-implementing the same rules:

- **Warehouse** is always derived from the Storage Bin (or the parent HU's
  warehouse when nesting) - it's never asked for.
- **HU Type** can be typed directly, or left blank if a **Packaging
  Material** is set (its `hu_type` is copied across); a Packaging Material
  also copies its `tare_weight` onto the HU.
- **HU Number** is handed out by **WMS Number Range** (`services/numbering.py`),
  the same "narrower match wins" pattern as the rule tables above: a range
  can be scoped to a specific `warehouse` + `hu_type`, just one of the two,
  or neither (a global fallback). `after_install` seeds one global fallback
  range for Handling Units (`HU-########`) and one for `WMS Shipment`
  (`SHIP-########`) so a fresh site works immediately.
  - **External** HU Types (the default) carry a number a person or a label
    printer already put on the physical HU - it's required and checked
    against existing HUs.
  - **Internal** HU Types are always system-numbered - `next_number` is
    always called and any number the caller passed is silently replaced.
    The Desk form and the RF "New Handling Unit" screen both disable/hide
    the barcode field once an Internal type is selected.
- **WMS Shipment** numbers are always internal - `create_shipment` calls
  `next_number("WMS Shipment", warehouse=...)`.
- Numbers are handed out under a row lock (`select ... for update` on the
  matching `WMS Number Range`), so concurrent RF scans or shipment creation
  can't collide. A range throws once `current_number` reaches `end_number`
  rather than wrapping around - raise `end_number` or add a new range to keep
  going.
- **Reuse**: recycling an empty HU whose HU Type is `reusable`
  (`services/handling_unit.recycle_handling_unit` - RF "Handling Units" detail
  screen, or the Desk "Recycle" button once it's Empty) deletes the HU and,
  for Internal numbering, frees its number into **WMS HU Number Pool** first.
  `next_number` always drains that pool before incrementing the range
  further, so a reusable tote's number comes back into circulation instead
  of numbers only ever climbing forward.
- **Auto-registration**: an unknown HU barcode scanned during Goods Receipt
  (`services/receipt.create_and_submit_goods_receipt`) falls back to
  `WMS Settings.default_handling_unit_type` when no HU Type is given, and
  goes through the same controller (numbered/validated, logs an HU Event)
  instead of a bare insert. Scanning a barcode against an Internal HU Type is
  rejected (an Internal type's HUs only ever get created at a packing
  station inside this app, never scanned in from outside).

## Core flows

**Inbound:** `Inbound Delivery` (expected receipt, e.g. against a Purchase
Order) → operator scans HUs against it in the RF app → `Goods Receipt` is
posted → ledger entries increase stock in the receiving bin → Process
Determination Rule decides if putaway is required → `Warehouse Request` →
`Warehouse Task` (Putaway) → operator confirms in RF → stock moves from
receiving bin to its determined destination bin.

**Outbound:** `Outbound Delivery` (against a Sales Order via the order's
"Create > Outbound Delivery" button, or standalone) **must be submitted**
before it can be allocated or picked — `services/allocation.allocate_delivery`
and `services/task.create_pick_tasks`/`create_pick_tasks_for_wave` all reject
a Draft delivery, the same `docstatus != 1` gate `services/receipt.py` already
uses for Goods Receipt. Once submitted it's allocated (`services/allocation.py`)
— reserves `WMS Stock Balance` rows, sourced only from bins in a "general
inventory" storage role (`_candidate_balances` excludes `Receiving`, `Staging`,
`Shipping`, `Door` and `Packing` roles, plus any bin with `removal_blocked`
set — stock already committed to one outbound movement, still awaiting
putaway, or manually blocked isn't up for grabs by a *different* delivery's
FIFO just because the ledger still shows it as available there) — optionally
grouped into a `WMS Wave` for combined release — → pick tasks are generated
per allocation (or clustered
across a wave's deliveries onto shared bins/products) → operator picks in RF
→ stock stages → `Packing Order` (optional) groups HUs for shipment →
`Goods Issue` posted → ledger decreases stock, ERPNext Delivery Note (or
generic Stock Entry) mirrored. The RF "Release" screen does allocation +
pick-task creation for one delivery in a single tap
(`services/picking.release_delivery_for_picking`) — the same
allocate-then-create-pick-tasks sequence a Wave's release runs across a
batch of deliveries, just for one; it's also what the Outbound Delivery desk
form's "Allocate Stock" / "Create Pick Tasks" buttons call as two separate
steps, and what the WMS Monitor's Outbound Monitor drill-down (below) offers
alongside a one-tap "Post Goods Issue".

**Cancelling an Outbound Delivery** is validated, not just a bare docstatus
flip (`events/deliveries.py`): blocked outright if a submitted `Goods Issue`
already references it (reverse that first), and blocked if any of its Pick
tasks has a `confirmed_quantity > 0` that hasn't itself been reversed via
`services/task.reverse_task` (reversing a Goods Issue alone doesn't un-confirm
the task that picked it — only `reverse_task` does that, by posting a
compensating task). Once past those checks, cancelling releases every
still-open (unconfirmed) Pick task's `Stock Allocation` reservation back onto
`WMS Stock Balance` and hard-cancels those tasks
(`services/allocation.cancel_allocations_for_delivery`) — nothing is left
dangling from a delivery that's abandoned before picking actually started.

**Shipping/loading:** once its deliveries are fully picked,
`services/shipping.create_shipment` picks up their staged HUs, determines a
**WMS Route** (`services/determination.determine_route`, matched by warehouse
+ carrier same as the other rule tables), and creates a `WMS Shipment`
(`Ready to Load`, numbered from the `WMS Shipment` number range). The RF
"Load" action then confirms each HU one at a time
(`services/shipping.confirm_hu_loaded`): if the Route has **Stops**
configured — an ordered list of intermediate bins, e.g. a marshalling bin for
cross-dock or a yard checkpoint — the HU is walked through them one hop at a
time (`_advance_hu_through_hops`) instead of teleporting straight to the
door, each hop posting a real ledger transfer and an HU Event (`Staged` for
an intermediate stop, `Loaded` for the final hop into the door), using the
movement type of the matching Warehouse Process Type (`OB_STAGE` /
`OB_LOAD`) rather than a hardcoded code. Once every HU on the shipment is
loaded the shipment becomes `Loaded`; `depart_shipment` moves it to
`Departed`; `complete_shipment` (a logistics-only milestone, e.g. proof of
delivery received) closes it out as `Completed` and marks its HUs `Shipped`.

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

**Task queueing (Warehouse Order):** if a **Warehouse Queue** is configured
for a warehouse/activity(/storage type), every `Warehouse Task` created for
that activity is attached (`services/warehouse_order.attach_task`) to a
**Warehouse Order** — a batch of tasks sharing the same `batch_key` (e.g. one
putaway request, one pick-task group) — which is auto-assigned to whichever
active `WMS Resource` on that queue currently has the fewest open Warehouse
Orders. A resource without a standing assignment joins a queue
(`join_queue`) and pulls the next one itself (`pull_next_warehouse_order`,
oldest-priority-first). This is optional infrastructure: a task type with no
matching Warehouse Queue is simply never routed through a Warehouse Order and
behaves as before (assigned/worked directly).

## Modules

| Module | Contains |
|---|---|
| `wms_core` | Warehouse/Storage Type/Storage Bin structure, WMS Settings, the WMS Monitor page, the WMS workspace |
| `wms_setup` | Everything in [the rule engine](#the-rule-engine-how-config-drives-behavior): determination rules, process types, movement types, replenishment rules, WMS Number Range, WMS HU Number Pool, plus child tables (delivery line items, HU/stock-type bin whitelists, packing source/destination HUs, shipment lines) |
| `wms_inbound` | Inbound Delivery, Goods Receipt |
| `wms_outbound` | Outbound Delivery, Goods Issue, Stock Allocation, Packing Order, WMS Wave |
| `wms_inventory` | WMS Product, WMS Stock Type, WMS Stock Balance, WMS Stock Ledger Entry, Physical Inventory Count, Quality Inspection |
| `wms_handling_units` | Handling Unit, HU Type, HU Event (audit trail - its `handling_unit`/bin/parent-HU fields are plain Data, not Links, so it never blocks deleting/recycling the HU or bin it once pointed at), Packaging Material |
| `wms_execution` | Warehouse Request, Warehouse Task, Task Allocation, Warehouse Order (queue-assigned batch of tasks), Warehouse Queue, WMS Resource, WMS Exception Code |
| `wms_shipping` | WMS Route (with ordered Route Stops for multi-hop staging), WMS Shipment |

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
6. **Bin Determination Rule** — at least one fallback rule per
   (warehouse, activity) with no item/stock-type filters, so determination
   never dead-ends; add narrower higher-priority rules on top for exceptions.
7. **Process Determination Rule** — at least one fallback per (warehouse,
   document type) pointing at a Storage Process (or Route).
8. **Storage Process** / **Storage Process Step** — only needed where a
   document type must run more than one step (e.g. unload, then putaway).
9. **Replenishment Rule** — one per (warehouse, product, pick bin) you want
   auto-replenished; the hourly job does the rest.
10. **WMS Number Range** — the seeded global fallback (`HU-########`,
    `SHIP-########`) works out of the box; add a narrower one per warehouse
    or per HU Type where you need a different prefix/window (see
    [Numbering](#numbering-hu-and-shipment-number-ranges)).
11. **WMS Route** / **Route Stop** — at least one active Route per warehouse
    (with a `default_staging_bin`/`default_door`) so `create_shipment` can
    determine one; add ordered **Stops** only where HUs must physically pass
    through intermediate bins (marshalling, yard checkpoint) before the door.
12. **Roles** — assign the roles below to users; optionally add **User
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
| Release | Pick an unallocated/unpicked Outbound Delivery, choose a picking strategy (Single Order / Cluster), allocate + raise its pick tasks in one tap — they then show up under Pick Tasks |
| Ship | Pick a delivery that's fully picked but not issued, confirm/adjust the suggested staged HU per line, post the Goods Issue |
| Pack | Complete an open Packing Order in one tap |
| Load | Pick a `Ready to Load`/`Loading` Shipment, scan each HU to walk it through the Route's Stops (if any) to the door and mark it loaded, then depart the Shipment once full |
| Handling Units | Look up, create (scan a barcode, or leave it blank for an Internal HU Type), nest/unnest, block/unblock, or recycle an empty, reusable HU (frees its number for reuse) |
| Move | Ad-hoc bin-to-bin/HU-to-HU transfer with no planning step |
| Count | Record physical inventory quantities, auto-posts once every line is counted |
| Quality | Complete an inspection's pass/fail split |
| Lookup | HU/bin contents by barcode |

`api/scanner.py` and the other `api/*.py` modules are the whitelisted
endpoints this frontend (and real barcode hardware) call.

## Desk surfaces

- **WMS workspace** (`/app/wms`) — every WMS doctype (including ones with no
  RF screen, like Warehouse Order or WMS Number Range) grouped by module,
  plus shortcuts to the WMS Monitor, RF Scanner, WMS Settings, and the
  ERPNext masters a warehouse operation regularly needs (Stock Settings,
  Warehouse, Item, Purchase Order, Sales Order). Visibility follows normal
  Frappe per-doctype/per-role permissions — this workspace doesn't add or
  remove access, only groups links. Anything that's a *service call* rather
  than plain CRUD (loading/recycling/etc.) is a custom button on the
  relevant doctype's form instead of a bare API endpoint you'd have to know
  to call: **Handling Unit** gets "Recycle" (once Empty) and "HU Overview";
  **WMS Shipment** gets "Depart" (once Loaded) and "Complete" (once
  Departed).
- **WMS Monitor** (`/app/wms-monitor`) — pick a warehouse, then a node from
  the left-hand list, SAP EWM Warehouse Management Monitor-style, instead of
  one long scrolling page:
  - **Overview** — summary counts (open tasks by type, exceptions, pending
    replenishment, deliveries in progress, open counts/inspections, open
    waves, active resources), each linking to its filtered list view.
  - **Inbound Monitor** — searchable Inbound Deliveries.
  - **Outbound Monitor** — searchable Outbound Deliveries plus Waves (with
    one-click Release per draft wave). Clicking a delivery expands a
    drill-down: its allocation/picking/packing/loading/goods-issue status,
    its Pick tasks and the Warehouse Order(s) they belong to, its Packing
    Orders and Goods Issues (with the `reversed` flag), and the Allocate
    Stock / Create Pick Tasks / Post Goods Issue actions — everything needed
    to drive and watch the pick-to-ship lifecycle without leaving the
    Monitor or reaching for the RF app.
  - **Stock Overview** — current `WMS Stock Balance` positions (quantity/
    allocated/available per product/bin/HU/stock type), with a per-stock-type
    summary strip — the current-state counterpart to Stock Movements' history.
  - **Warehouse Tasks**, **Handling Units** — searchable, each its own node.
  - **Stock Movements** — searchable `WMS Stock Ledger Entry` history.
  - **Resources & Queues** — resource workload and warehouse queues.

  Roles: WMS Supervisor / Administrator / Inventory Controller / Auditor,
  System Manager.

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
