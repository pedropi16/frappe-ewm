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

**Setting this up for the first time?** Start with
[`SETUP.md`](SETUP.md) instead — a checklist that walks install → core
warehouse structure → RF logon/queueing → verification, in the order you
actually need to do it, linking back into the relevant section here for
detail on each step.

## Status

SAP EWM Basic parity (P0–P3) plus 7 of 8 Advanced-tier areas (P4) are
**complete** — see [`app_gap.md`](app_gap.md) for the full gap analysis,
capability map, and a phase-by-phase log of every design call made along the
way. In short:

- **P0 — Harden**: closed 8 defects that let WMS and ERPNext drift apart or
  left configured rules unenforced (PI-to-ERPNext posting, receipt valuation,
  allocation locking, storage/bin rule enforcement, batch/serial/SLED
  controls, alternative UoMs, ERPNext stock-guard coverage, the `WMS Stock
  Type` Inventory Dimension).
- **P1 — Strategy engine**: putaway and stock removal are now decided by
  configurable rules (search sequences, Bulk/Pallet/Near-Fixed-Bin/General
  Storage putaway strategies, a full FIFO/LIFO/FEFO/Stringent
  FIFO/Partial-Qty-First/By-Quantity/Fixed-Bin removal engine, per-warehouse
  process type determination) instead of hardcoded literals.
- **P2 — Process control**: multi-step inbound/outbound actually executes
  (Storage Process chaining), pick denial, order-related and direct
  replenishment, quality inspection auto-creation, `WO Creation Rule`
  task-count capping, and production supply (Work Order staging/FG receipt).
- **P3 — Inventory & control**: tolerance-gated count posting with
  recount/supervisor-approval, scheduled cycle counting (ABC/low-stock/putaway
  PI/bin check/annual), a Difference Analyzer, server-enforced RF scan
  verification, and a KPI/Alerts dashboard on the WMS Monitor.
- **P4 — Advanced EWM** (7 of 8 areas; yard/transport units/dock appointments
  were built later, see *Yard and dock appointments*):
  wave templates with cut-off/auto-release, opportunistic cross-docking,
  BOM-driven kitting (Assemble/Disassemble), slotting & rearrangement, labor
  standards/performance, VAS depth (Packaging-Spec-driven step generation,
  duration capture), and task/activity-based 3PL billing.

- **Production round**: ERPNext-as-ECC document replication with change and
  cancel propagation and complete-short ([ERPNext integration](#erpnext-integration)),
  a queued ERPNext posting mode with retry, a fully customizable Repack Center
  work center, printing to network printers and a print agent, GS1 scanning
  everywhere, one authorization matrix with warehouse scoping, kit-to-stock
  with staging, reverse-stop loading, volume capacity, counting in cases and
  pallets at receipt, customer minimum shelf life, and an hourly alert digest.

Tested with 389 Python tests on a fresh v16 site, 20 JS unit tests and 39 RF
browser tests, plus a 27-user concurrent shift simulation with ledger
invariants. It is not a certified SAP EWM replacement — see [Production
warning](#production-warning).

## Contents
- [Status](#status)
- [Mental model](#mental-model)
- [Data model](#data-model)
- [The rule engine (how config drives behavior)](#the-rule-engine-how-config-drives-behavior)
- [Numbering (WMS Number Range)](#numbering-wms-number-range)
- [Printing (spool)](#printing-spool)
- [Core flows](#core-flows)
- [Advanced EWM (P4)](#advanced-ewm-p4)
- [Consolidation Group](#consolidation-group)
- [Modules](#modules)
- [Configuration reference](#configuration-reference)
- [Roles & permissions](#roles--permissions)
- [ERPNext integration](#erpnext-integration)
- [Background jobs](#background-jobs)
- [RF / scanner app](#rf--scanner-app)
- [Desk surfaces](#desk-surfaces)
- [Install](#install)
- [Uninstall](#uninstall)
- [Internationalization](#internationalization)
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
  see [Numbering](#numbering-wms-number-range). An HU Type
  marked `reusable` can be recycled once empty, returning its number to the
  pool for reissue. **Packaging Material** attaches physical dimensions
  (length/width/height/tare weight/maximum weight) to an HU Type; picking one
  when creating an HU fills in HU Type and Tare Weight for you.
- **WMS Product** (per warehouse-agnostic Item) carries `gross_weight_per_unit`
  and `volume_per_unit`, used to keep an HU's `gross_weight`/`net_weight`/
  `volume` live as stock moves in and out of it - which in turn feeds bin
  capacity checks during putaway (see
  [Bin Determination Rule](#3-bin-determination-rule--which-bin-a-movement-lands-in)).
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
- A **Door** is not a separate doctype — it's a **Storage Bin** whose
  **Storage Type** has `storage_role = Door`. This is enforced, not just a
  label: `WMS Route.default_door`, `WMS Shipment.door`, and `Outbound
  Delivery.door` are all validated to point at a Door-role bin (whether the
  value came from the Route's default or was set directly), and **Goods
  Issue refuses to post unless the Handling Unit's current bin is one**
  (`services/issue.post_goods_issue`) — see
  [Shipping/loading](#core-flows) and configuration step 16 below.

## The rule engine (how config drives behavior)

This is the part that makes the app "configurable" rather than hard-coded, and
the part worth understanding before touching anything else. Every rule table
below is evaluated in `services/determination.py` (or its own dedicated
service for Removal Rule / WO Creation Rule), all matched the same way —
**priority ascending, first full match wins**, where an empty field on a rule
means "matches anything" — so narrower, higher-priority rules sit above
broader fallback rules, and a warehouse with zero configured rules for a
given table just falls back to whatever hardcoded default that call site
always used (nothing breaks by not configuring one, same as the numbering
and printing config below).

### 1. Process Determination Rule → *which process runs*
Given a warehouse + document type (Goods Receipt / Goods Issue / etc.) + item
context, picks a **Storage Process** (an ordered list of **Storage Process
Step**s, each pointing at a **Warehouse Process Type**) or a **Route**.
This is how "receiving product X always gets quality-inspected first" or
"receiving into warehouse B always requests before putaway" gets expressed
without touching code. **A matched Storage Process actually executes, step by
step**: `create_task_automatically`/`next_step_on_confirmation`
(`services/storage_process.py`) advances to the next configured step the
moment the current one's task confirms, gated by a `predecessor_task` hold on
the successor task (a second, WO-independent gate — the ordinary intra-WO
sequence gate only spans one Warehouse Order/queue, which a chain spanning
several steps naturally leaves) so an RF operator can never work a later step
before its predecessor is actually done. Layout-oriented forced waypoints
(e.g. an I-point or lift a HU must physically pass through) are modeled as
ordinary extra Storage Process Steps rather than a second parallel mechanism.

### 2. Warehouse Process Type Determination Rule → *which process type a movement uses*
Given a warehouse + activity (Putaway/Pick/Internal Move/Replenish/
Deconsolidation) + item/item-group/stock-type/priority-level/the product's
own `process_type_determination_indicator`, picks which **Warehouse Process
Type** a movement actually uses instead of every call site hardcoding one
literal. Unconfigured means "keep using the literal that call site always
used" — this table is a layer *on top of* the seeded Warehouse Process Types
from step 5 below, not a replacement for seeding them.

### 3. Bin Determination Rule → *which bin a movement lands in*
Given a warehouse + activity (Putaway/Pick/Internal Move/...) +
item/item-group/stock-type/HU-type/source-storage-type context, either
returns a `fixed_destination_bin`, requires manual selection, or applies a
**strategy** over the bins of a `destination_storage_type` — or, when a rule
leaves `destination_storage_type` blank, over a **Storage Type Search
Sequence** (an ordered list of storage types to try in turn, moving to the
next only once the current one has zero usable bins) if `search_sequence` is
set, falling back further to the product's own `WMS Product`/`WMS Product
Warehouse` `preferred_storage_type` if neither is set:

| Strategy | Behavior |
|---|---|
| Least Utilized Bin | Lowest `current_hu_count` first |
| Bin Sequence / General Storage | Lowest `sequence` first (fixed pick-path order) |
| First Empty Bin / Pallet | Prefers bins with zero HUs, falls back to sequence (Pallet bins are one-HU-per-bin by convention) |
| Addition to Existing Stock | Prefers a bin that already holds this product |
| Bulk | Prefers a bin already holding this product, then ranks by *most* remaining HU-count capacity (best fit for a large incoming quantity) |
| Near Fixed Bin | Ranks bins near the product's `WMS Product Warehouse.fixed_bin` |
| Manual Selection | Forces the operator to pick (no auto-determination) |

Rule tables are scoped to a warehouse and can be filtered on any combination
of their supported match fields — narrower, higher-priority rules should sit
above broader fallback rules.

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

### 4. Removal Rule → *which quant a pick/removal takes first*
Given a warehouse + product/item-group/stock-type, picks a **strategy** —
FIFO, LIFO, FEFO (soonest shelf-life-expiry-date first, ignoring receipt
date), Stringent FIFO (same as FIFO, but rejects a custom sort-field
override — sort-only today, not yet a hard cross-allocation block), Partial
Quantity First (smallest quantity first), By Quantity (largest quantity
first), or Fixed Bin (restricts removal to one configured bin) — or a custom
list of sort fields overriding the named strategy's own default ordering. No
matching rule falls back to FEFO-then-FIFO. Strategies are registered Python
functions, not hardcoded `if` branches: `hooks.py`'s `wms_removal_strategies`
dict maps a strategy name to a `services/removal_rules.py` function, and
**other installed apps can add their own entries to this same hook** —
`frappe.get_hooks()` merges every app's dict together, so a custom strategy
never requires forking this app.

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
  Two other flows raise a Replenishment Request too, sharing the same
  `_create_replenishment_request` helper: **order-related** (a Pick task's
  exception follow-up action can raise one for the exact shortfall) and
  **direct** (an operator-triggered top-up with no threshold check).
- **Allowed Task Type** — restricts which task types a given context may
  generate.
- **WO Creation Rule** — per (warehouse, activity, item group, stock type),
  caps how many tasks one Warehouse Order can hold; once the cap is hit,
  further tasks in the same batch spill into a new Warehouse Order under a
  derived batch key instead of growing the first one indefinitely. No
  matching rule means a batch's tasks all share one Warehouse Order, as
  before.
- **Inspection Rule** — per (warehouse, item, item group), auto-routes a
  matching Goods Receipt row into the `QUALITY` stock type and creates a
  `WMS Quality Inspection` automatically at receipt, instead of requiring a
  manual trigger. Completing it also creates a linked ERPNext Quality
  Inspection.

`after_install` seeds a sensible starting set of Stock Types, Movement Types,
Warehouse Process Types, Exception Codes, Number Ranges, and roles (see
`install.py`) so a fresh site isn't empty, but every rule table above
(Process/Bin/Warehouse-Process-Type-Determination, Removal, WO Creation,
Inspection) and Replenishment Rules are warehouse-specific and must be
configured per site.

## Numbering (WMS Number Range)

**WMS Number Range** isn't limited to Handling Units and Shipments — it
covers every major transactional doctype: Warehouse Order, Warehouse Task,
Warehouse Request, Inbound/Outbound Delivery, Goods Receipt/Issue, Packing
Order, VAS Order, WMS Wave, Physical Inventory Count, and Quality Inspection,
alongside Handling Unit and WMS Shipment. One generic hook
(`services/numbering.autoname_from_range`, wired per doctype in `hooks.py`'s
`doc_events["autoname"]`) checks for an active range matching that doctype
(optionally scoped to a warehouse — see `_warehouse_of`, which special-cases
Packing Order since it has no warehouse field of its own, deriving it from
the delivery being packed) and uses it if found; **it is entirely opt-in per
doctype and per warehouse** — a doctype or warehouse with no range configured
keeps using its ordinary naming-series `autoname` (`WO-.##########`,
`OBD-.########`, etc.) exactly as before. Nothing breaks by not configuring
one; you only add a range where you actually want a specific prefix/window
(e.g. one number range per warehouse for Outbound Delivery numbers a 3PL
customer expects in their own format).

**Handling Unit** numbering is more involved and pre-dates the generic hook
above — it lives directly in the doctype controller, not the shared
autoname hook. Creating one - from the Desk "New" form, the RF app, or Goods
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

## Printing

Printing is config-driven and opt-in, as in SAP EWM's output determination:
*structure* (which printers exist) and *rules* (when they print).

- **A printer is a WMS Resource with `resource_type = Printer`.** Its
  connection says how jobs reach it:
  - **Network (Raw TCP)**: the server sends the job straight to
    `printer_host:printer_port` (9100) after the transaction commits. A
    printer that is off is retried every 10 minutes, up to 5 times.
  - **Print Agent**: `frappe_wms/print_agent/wms_print_agent.py` (standard
    library only) runs next to printers the server cannot reach. It claims
    rendered jobs (`api/printing.agent_claim`), prints them locally
    (`agent_printer_name`) and reports back. A job claimed but never
    confirmed goes back into the queue after 10 minutes.
  - `printer_language`: **ZPL** labels (HU labels with SSCC, GS1-128) or
    **PDF** from a Print Format.
- **WMS Print Determination Rule** maps (`warehouse`, `event`) to a printer
  and an optional Print Format. Events: `HU Created`, `HU Closed`, `Putaway
  Confirmed`, `Goods Issue Posted`, `Shipment Loaded`, and `Manual` (reprints
  and the Repack Center's "Print label" button). No matching rule, no print.
- Every job is a **WMS Print Spool** row: `Queued` → `Printing` → `Printed`
  or `Failed` (with the error and attempt count), and can be requeued.
- Standard formats: **WMS HU Label** (Handling Unit), **WMS Packing List**
  (Outbound Delivery: each shipping HU with its nested contents) and **WMS
  Shipment Manifest** (WMS Shipment). A Print Format with raw printing
  enabled is rendered as ZPL for label printers.

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
→ stock stages → `Packing Order` (optional) groups HUs for shipment → HUs are
loaded onto a `WMS Shipment` → **Goods Issue posts automatically** the moment
every HU on the shipment for that delivery is loaded (see
[Shipping/loading](#core-flows) below) → ledger decreases stock, ERPNext
Delivery Note (or generic Stock Entry) mirrored. Allocation + pick-task
creation is release: `services/picking.release_delivery_for_picking` does
both for one delivery in a single call — the same
allocate-then-create-pick-tasks sequence a Wave's release runs across a
batch of deliveries, just for one. This is deliberately **desk-only**, not
exposed on the RF scanner (which stays focused on physical execution — pick,
repack, confirm tasks, load, etc.): it's what the Outbound Delivery desk
form's "Allocate Stock" / "Create Pick Tasks" buttons call as two separate
steps, and what the WMS Monitor's Outbound Monitor drill-down (below) offers
in one tap, alongside a manual "Post Goods Issue" fallback for whenever
auto-posting hasn't (or can't yet) run.

**Partial quantities out of a Handling Unit:** when a task moves only part of
an HU's stock (a partial pick off a pallet, a split putaway, a replenishment
into a pick face) and no destination HU is scanned, the stock can't simply
travel "inside" the source HU - that would leave one HU with stock in two
bins. `services/task.confirm_task` therefore moves the whole HU when it is
being emptied, puts the quantity loose when the destination bin's Storage
Type isn't HU-managed, and otherwise asks the operator to scan the tote,
carton or new pallet it goes into ("Destination Handling Unit required"). A
WO Creation Rule with a `pick_hu_type` supplies that pick HU automatically.

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
an intermediate stop, `Loaded` for the final hop into the door — which must
be a Door-role bin, see [Data model](#data-model)), using the movement type
of the matching Warehouse Process Type (`OB_STAGE` / `OB_LOAD`) rather than a
hardcoded code. Once every HU on the shipment is loaded the shipment becomes
`Loaded`, every Outbound Delivery riding on it becomes `Loaded` too, and
**Goods Issue is posted automatically for each of those deliveries** right
there (`services/shipping._auto_post_goods_issue`) — no separate tap needed.
A delivery isn't necessarily fully postable just because *this* shipment
finished (e.g. its lines could be split across two shipments); that "nothing
to post yet" case is silently retried the next time a HU for it is loaded,
and any genuine posting failure is logged (`frappe.log_error`) rather than
blocking the loading confirmation that triggered it — the HU is physically
loaded either way. `depart_shipment` moves the shipment to `Departed`;
`complete_shipment` (a logistics-only milestone, e.g. proof of delivery
received) closes it out as `Completed` and marks its HUs `Shipped` (Goods
Issue already marked them `Shipped` when it posted, so this is normally a
no-op by the time it runs).

**Physical count:** `WMS Physical Inventory Count` snapshots `WMS Stock
Balance` for a warehouse (optionally scoped to bin/storage type/product) →
operator records counted quantities in RF → posting the count writes the
variance as an Inventory Gain/Loss movement against the ledger — **unless** a
matching **Count Tolerance Group** (per warehouse/item group, an absolute
and/or percentage threshold) says the variance is out of tolerance, in which
case the affected rows hold at `Pending Recount` instead of posting.
`request_recount` resets held rows back to `Open` for re-recording; a
still-out-of-tolerance recount escalates straight to `Pending Approval`
rather than looping forever. With *Different User to Recount* on the warehouse, a recount line must be entered by someone other than the user who counted it first (four-eyes). `approve_variance` (gated by `WMS Supervisor`)
posts held rows and closes the count. Counts can also be generated on a
schedule instead of created by hand: **Cycle Count Rule** covers six
procedure types (ABC — by `WMS Product.abc_indicator` — Low Stock, reusing
each pick bin's own `Replenishment Rule.minimum_quantity` as the threshold
rather than a second config knob; Zero Stock, Putaway PI, Bin Check, Annual)
and runs daily. A **Difference Analyzer** (WMS Monitor) aggregates posted
variance by product — total gain/loss/net-variance/over-tolerance-event
counts — across a date range.

**Quality inspection:** `WMS Quality Inspection` splits a quantity out of an
inspection stock type into a passed-to / failed-to stock type, reusing the
same stock-type-change movement mechanics as any other posting change — no
physical relocation required.

**Ad-hoc move:** the RF "Move" action skips the request/task-planning step
entirely and calls `services/task.create_and_confirm_move` directly — for
on-the-spot corrections where a plan isn't needed.

**Resources, Resource Groups & queueing (Warehouse Order):** mirrors SAP
EWM's separation of *who/what does the work* from *the pool it's drawn
from*. A **WMS Resource** models one physical device (a Forklift, an RF
Scanner, a Printer, ...) via its `resource_type` and free-text `device_id`;
`user` is whoever is currently logged on to it, not a permanent identity of
the resource. Resources are stable (an admin configures them once, wired
into their activity area via Resource Group/Queue below) — what's dynamic is
who's logged on. **Logon is self-service** (`services/resource.py`, mirroring
SAP EWM's RF logon): `log_on(resource_code)` claims a free Resource for the
calling user, throwing if someone else is already on it; logging on to a
*different* Resource transparently logs the same user off whichever one they
were on before, so a forgotten logoff never locks them out elsewhere.
`log_off()` releases it. A Supervisor can forcibly clear a stuck session with
`kick(resource_code, reason=...)` regardless of who's on it — exposed as a
"Kick" button on the WMS Resource form, and via `api/resource.py` for the RF
app's own Log On screen (`www/wms`), which lists free Resources instead of
requiring an admin to have pre-wired one to your user. Resources are pooled
into a **WMS Resource Group** (per warehouse), and a **Warehouse Queue**
points at a Resource Group via its `resource_group` field, rather than at
individual resources — **the actual source of eligibility**, not just
informational: reassigning who works a queue is a one-place edit on the
queue (or the resource's own group), never touching individual resources.
`current_queue` on a Resource stays available as an optional "focus on one
queue right now" narrowing on top of that — an operator explicitly
`join_queue`s one specific queue when they want to; without that, they're
automatically eligible for *every* active queue in their own Resource Group.
If a Warehouse Queue is configured for a warehouse/activity(/storage type/
**Activity Area** — see below), every `Warehouse Task` created for that
activity is attached (`services/warehouse_order.attach_task`) to a
**Warehouse Order** — a batch of tasks sharing the same `batch_key` (e.g. one
putaway request, one pick-task group). **A Warehouse Order never
auto-assigns a resource at creation** — matching SAP EWM (and a deliberate
correction of an earlier design here), resources only *execute* things;
assignment is never the default. It sits `Open`, scoped only to its queue,
until a resource explicitly claims it, either by pulling the next one
(`pull_next_warehouse_order`, oldest-priority-first, across every queue the
calling resource is eligible for — its joined `current_queue` if it has one,
else every active queue in its own Resource Group) or by simply confirming
the first task on it manually (`confirm_task` claims an unassigned Warehouse
Order for whoever confirms first — covers a task found via a manual search
rather than a pull). A resource with neither a joined queue nor a Resource
Group with any queues configured simply can't pull work yet
(`pull_next_warehouse_order` says so clearly); it can still *see* and
manually claim unassigned work in any queue its Resource Group covers
(`list_my_tasks`/`list_my_warehouse_orders`, both Resource-Group-aware, not
just keyed off a manual queue join). This is optional infrastructure end to
end: a task type with no matching Warehouse Queue is simply never routed
through a Warehouse Order and behaves as before (assigned/worked directly);
a Resource or Queue with no Resource Group set keeps behaving exactly as it
did before Resource Groups were wired up.

**Warehouse Order status** mirrors SAP EWM's own split of automatic vs.
deliberate blocking: `Open → Assigned → In Process → Blocked / On Hold →
Completed / Cancelled`. **Blocked** is automatic — `sync_warehouse_order`
sets it whenever the WO's lead (lowest-sequence, non-terminal) task is in
`Exception`, copying its `blocking_reason`, and clears it back to `In
Process`/`Open` once that's resolved. **On Hold** is a deliberate Supervisor
pause (`block_warehouse_order`/`resume_warehouse_order`, `require_role("WMS
Supervisor")`, exposed as Put On Hold/Resume buttons on the Warehouse Order
desk form) — `sync_warehouse_order` returns early on a WO that's already `On
Hold`, so the automatic recompute can never silently clear a deliberate
pause.

**Activity Area** (`wms_core`, mirrors Storage Type/Storage Section/Bin Type
exactly — a simple `warehouse`+`area_code`+`area_name` master record, no
child tables) is an optional, narrower-than-Storage-Type grouping of bins:
set `Storage Bin.activity_area` on the bins that need it (one per bin), then
point a `Warehouse Queue.activity_area` at the same value — `determine_queue`
matches it ahead of Storage Type, which it still falls back to, which itself
falls back to a blank-everything catch-all queue, exactly the
narrowest-match-wins idiom every other rule table in this app uses. Assigning
many bins to an Activity Area at once (SAP EWM's own mass-maintenance
transactions do this as a guided filter-then-apply flow, not one bin at a
time) is the WMS Monitor's **Bin Assignment** tab — see [Desk
surfaces](#desk-surfaces).

**Work Center** (`wms_core`, same simple shape again —
`warehouse`+`work_center_code`+`bin`) is a *second*, entirely optional RF
logon step, after Resource logon: `services/resource.py::log_on_work_center`/
`log_off_work_center` set/clear `WMS Resource.current_work_center`, mirroring
`current_queue`'s own "focus" pattern exactly. Nothing is gated by it — it
exists purely so an action that already carries a `work_center_bin` (VAS
generation today) can default to the logged-on Work Center's bin instead of
requiring a scan every time; an operator who never logs on to one keeps
scanning as before.

## Advanced EWM (P4)

Seven of SAP EWM's eight Advanced-tier capability areas, each independent and
each opt-in the same way as everything above — configure it and it engages,
leave it unconfigured and the rest of the app behaves exactly as before.
Yard management / transport units / dock appointment scheduling was not part
of this pass; it was built afterwards (see *Yard and dock appointments*).

- **Wave templates + auto-release**: a **Wave Template** (warehouse, optional
  route filter, priority, picking strategy, daily `cutoff_time`,
  `auto_release`) drives `generate_waves_from_templates` (hourly): sweeps
  matching submitted Outbound Deliveries not yet on any wave into a new Draft
  `WMS Wave`. `auto_release_due_waves` (hourly) then releases any
  template-generated Draft wave whose cut-off has passed, calling the same
  `release_wave` a manual Monitor click would.
- **Opportunistic cross-docking**: `find_cross_dock_demand` matches an
  incoming Goods Receipt row against open, not-yet-fully-allocated Outbound
  Delivery demand for the same product/stock-type in the warehouse.
  `create_putaway_requests` splits the row between a `Cross Dock` request
  (straight to the matched delivery's staging bin) and a normal Putaway
  request for any unmatched remainder. Confirming a Cross Dock task fulfills
  the delivery directly (bumps `allocated_quantity`/`picked_quantity`) —
  staged stock was never in the allocatable pool anyway (see [Core
  flows](#core-flows)'s Outbound allocation note), so it bypasses Stock
  Allocation entirely rather than routing through it. In the RF app, Cross
  Dock tasks appear alongside Putaway on the **Putaway Tasks** screen — they
  originate from the same receipt and are worked by the same operator.
- **Kitting** (kit-to-stock and reverse kitting): a **Kitting Order** (kit
  item, a submitted **BOM**, warehouse, work center bin, quantity, direction)
  explodes the BOM scaled to the order quantity. Then:
  1. **Stage**: warehouse tasks bring the inputs (components, or the kit when
     disassembling) from storage to the work center, sources in removal-rule
     order; part of an HU is unpacked, a whole HU moves as is. Automatic on
     creation or started from the RF screen (*WMS Warehouse > Kitting > Stage
     Components*).
  2. **Complete**: consumes what is actually at the work center, every
     batch/serial/HU row, soonest expiry first, and posts the output there,
     optionally onto a scanned HU (*Kitting Output HU Required*). Unstarted
     staging tasks are cancelled. ERPNext gets a **Repack** Stock Entry with
     the consumed batches.
  3. **Put away** (*Put Away Kitting Output*, per order): putaway tasks from
     the work center. If no destination can be determined yet, the request
     waits in the Monitor's *Warehouse Requests Without Tasks* alert.

  **Cancel** cancels open staging; staged stock stays at the work center.
  The RF **Kitting** screen shows each input as required / at the work
  center / on its way, with Stage, Complete and Cancel.
- **Slotting & rearrangement**: `analyze_slotting` flags a product with
  genuine recent pick activity (`min_picks` threshold against Pick-movement
  ledger entries) sitting in a `WMS Stock Balance` row whose bin's storage
  type doesn't match the product's own configured `preferred_storage_type` —
  deliberately reusing existing P1 configuration as the target rather than a
  new scoring model. `generate_rearrangement_tasks` builds a real Internal
  Move `Warehouse Task` per flagged row via the same `determine_destination_bin`
  every other putaway decision uses (a warehouse just needs one generic
  "Internal Move" Bin Determination Rule with no fixed destination storage
  type for the fallback-to-`preferred_storage_type` behavior to kick in — see
  [the rule engine](#3-bin-determination-rule--which-bin-a-movement-lands-in)).
  Both surfaced on the WMS Monitor's **Slotting** tab, with a bulk "Generate
  Rearrangement Tasks" action.
- **Labor management**: a **Labor Standard** (priority, optional warehouse,
  task type, optional item group, standard seconds per unit) feeds
  `resource_performance` — per-resource task count, average cycle time, and
  an efficiency percentage (planned vs. actual seconds against matching
  confirmed tasks) — shown on the WMS Monitor's **KPIs** tab below the
  warehouse-level cards.
- **VAS depth**: `VAS Order Activity` now records `started_at`/
  `duration_seconds` per step. `create_vas_order_from_packaging_spec`
  (RF app, VAS screen → "+ Generate from Packaging Spec") builds one VAS
  activity per level of a scanned HU's item's Packaging Spec automatically,
  instead of requiring every step to be typed in by hand.
- **3PL billing** (task/activity charges only — storage/per-diem billing is
  out of scope, since it needs a per-customer owner dimension on `WMS Stock
  Balance` that doesn't exist): a **Billing Rate** (priority, optional
  warehouse/customer, activity, `Per Task`/`Per Unit` basis, rate, billing
  item) drives `generate_billing_for_period`, which attributes confirmed
  tasks to a customer through their Stock Allocation's or Cross Dock
  request's Outbound Delivery — tasks with no traceable delivery are
  excluded rather than guessed at. `create_billing_sales_invoice` builds one
  Draft (never auto-submitted — a human reviews and submits it) ERPNext
  Sales Invoice per matched rate. Both reachable from the WMS Monitor's
  **Billing** tab (preview, then create).

## Consolidation Group

Not part of P4 above — a later addition, the mirror of Deconsolidation:
combining demand from several Outbound Deliveries *and* Work Orders onto one
physical Handling Unit at a shared staging point (a Production Supply Area
bin, or a distinct rack), then splitting it apart later once everything's
gathered.

**Gather, then split** — deliberately not "redirect a Pick or PSA-
replenishment task's destination at creation time." Picking and Work Order
material staging run to completion exactly as everywhere else in this app,
each landing wherever it always has; a **Consolidation Group** (`warehouse`,
`staging_bin`, `target_hu`, `lines`) then tracks a plan of which already-
confirmed demand (a `Stock Allocation` for a delivery, a `Warehouse Request`
for a Work Order's production supply) to move onto one shared target HU. A
**Consolidation** task (`services/consolidation.py::create_consolidation_tasks`
— structurally `create_deconsolidation_tasks` run in reverse: several varying
sources onto one fixed destination instead of one fixed source onto several)
does the actual gathering; once every line is on the target HU, the existing,
unmodified `create_deconsolidation_tasks` splits it back out to each line's
own real destination. Both legs confirm through the RF app's ordinary Task
wizard — nothing new needed there.

Reachable from the RF app's **Consolidation** action (under Internal): scan
an Outbound Delivery, Work Order, Stock Allocation, or Warehouse Request
barcode to find joinable lines, add them, set a target HU, Gather, then Split
to Destinations once fully gathered. Creating a new Consolidation Group
itself (choosing the warehouse and staging bin) is a desk/API action, not RF
— the same "supervisor plans, operator executes" split as Kitting Order.

## Production Supply Area (PSA) and Production Material Request (PMR)

Production supply the SAP EWM way: one warehouse, Production Supply Areas inside it, and a **Production Material Request** per production
order listing the materials it needs (`services/production_supply.py`).

* **PSA** - `warehouse`, a default `supply_bin` plus further `bins`, the `workstations` it serves, an optional `deconsolidation_work_center`
  and a staging mode (Manual / Automatic). A warehouse with an active PSA works with PMRs; one without keeps the direct Work Order staging.
* **Control cycle** (`Production Supply Control Cycle`, PSA + material) - how a material is staged to its PSA and into which PSA bin:
  *Pick Parts* (per order, reserved to it; the default), *Release Order Parts* (the demand of several orders staged as one pooled movement),
  *Crate Parts* (kept between a minimum and maximum in the PSA bin, independent of orders; checked hourly and after each consumption),
  *Direct Consumption* (not staged; consumption is taken from the storage bins where the material is).
* **PMR** - created when the ERPNext Work Order is submitted: one item per required WMS-managed material, with the PSA of its operation's
  workstation (the warehouse's only PSA when there is just one). Items track `required`, `tasked`, `staged` (reserved) and `consumed`
  quantities; the PMR status follows them.
* **Staging** (desk page *Production Staging*; API `frappe_wms.api.production_supply`) - the open PMR items of a PSA with source proposals
  (oldest stock first, less what open tasks already take); choose the storage bin each line is taken from and create the tasks. Single Order
  reserves the staged quantity to the PMR item; Cross Order serves several items with one movement into the unreserved pool. An Automatic PSA
  stages by control cycle when the PMR is created and hourly (`auto_stage`).
* **Deconsolidation hop** - with a deconsolidation work center on the PSA, an order's first leg goes to its own free location there (stored on
  the PMR); the leg on to the PSA is created when it is confirmed (the layout storage control mechanism). The quantity counts as staged only when
  it reaches the PSA.
* **Consumption** - backflushed from ERPNext: submitting a Manufacture / Material Consumption for Manufacture Stock Entry for the Work Order
  books the same quantity out of the PSA's bins (reserved quantity first, then the pool) and refuses more than was staged; Direct Consumption
  items book out of storage. Cancelling the entry puts the material back.
* **Return unused** (button on the staging page, `return_unused`) - tasks from the PSA bins back to storage for staged-minus-consumed of a PMR
  item (destination by the usual putaway determination); the reservation is released as they are confirmed. `close_pmr` (Supervisor) releases
  what is left of the reservation.
* The reservation is bookkeeping on the PMR item, not a stock dimension: a PSA bin can hold several orders' stock.

Not built yet: SAP's PSA as storage type + section (here: a set of bins), a manual page for aggregated release-order-parts over a time window,
returns of pooled (unreserved) stock.

## Bin types, storage groups, layout storage control, handling indicators

* **Bin Type** - the size class of a location (inner length/width/height, maximum weight, volume and number of HUs: a
  pallet bin, a shelf for boxes). A Handling Unit Type with a size (length/width/height) is only put into bins whose
  Bin Type fits it - the footprint may turn 90 degrees, the height may not - so no list of allowed HU types per bin is
  needed (the explicit list on a bin still works as an extra restriction). A bin's own capacity fields win; a blank one
  comes from its Bin Type. Where either side has no size nothing is checked.
* **Storage Group** - belongs to a Storage Type and groups its bins (an aisle, a production supply area, a cold room);
  set on the Storage Bin. A Bin Determination Rule can target a group (`destination_storage_group`).
* **Layout Storage Control** (SAP layout-oriented storage control) - a rule per warehouse: for goods going from a source
  storage type/group to a destination storage type/group, an intermediate storage type (+ group/section, or a fixed
  bin) is used. A request's task is created to the intermediate bin with the real destination in `final_destination_bin`;
  when it is fully confirmed the second leg to the destination is created from what actually arrived. The request is
  complete only when the last leg is.
* **Handling Indicator** - configurable handling rules on a product (WMS Product > Handling Indicators):
  *Required Storage Group* (temperature sensitive goods only go into the cold group - enforced wherever a destination bin
  is chosen or checked) and *Do Not Unpack* (no loose/partial moves out of the HU, no repacking, no deconsolidation).
  A product cannot carry indicators that require different storage groups.

## Control indicators and storage determination (Phase A)

SAP's product-driven storage search: indicators on the product pick the storage types searched, and the storage type carries defaults.

* **Putaway Control Indicator / Stock Removal Control Indicator / Storage Section Indicator** - small masters, set on the product's
  warehouse row (WMS Product Warehouse; WMS Product is the fallback).
* **Storage Type Search Sequence** takes an indicator (direction Putaway: putaway control indicator; direction Removal: stock removal
  control indicator). Putaway search order of storage types: the Bin Determination Rule's own storage type or sequence, the process type's
  default destination storage type, the product's putaway control indicator sequence, then the product's preferred storage type.
  If no rule yields a bin, the indicator's storage types are tried with each type's **Default Putaway Strategy**.
* **Removal**: a product with a stock removal control indicator is picked from the sequence's storage types in that order (the removal
  strategy orders within a type); stock in types outside the sequence is not allocated. No indicator: unchanged.
* **Storage Section Indicator**: a section carrying an indicator only takes products with it; a product with the indicator prefers those
  sections, then sections without one.
* **Storage Type Limits** on the product warehouse row: maximum quantity of the product per storage type (stops putaway into a full type).
* **Storage Type**: *HU Requirement* Optional / Mandatory / Forbidden (Forbidden: HUs are unpacked on putaway and the stock goes in
  loose; supersedes the old HU managed flag, migrated by patch), *HU Type Check* switch for the bin-type size check, *Default Putaway
  Strategy*, capacity method *Max Quantity* (bin or bin type `maximum_quantity`), role *Identification Point*.
* **Warehouse Process Type defaults**: source/destination storage type and bin, queue, priority - used when the request does not say.
* **Two-Step Picking** flag on the product warehouse row forces picks through the shared picking staging bin.

## Execution control (Phase C)

* **Exception actions** - a WMS Exception Code has a *Business Context* (where it is offered: RF task, desktop task, packing, physical inventory) and
  a *System Action*: **Change Bin** (the destination of a putaway-type task, else the source; the new bin must accept / hold the goods; a pick for a
  delivery cannot change source), **Split Task** (a quantity becomes a new task, optionally to another bin; picks for a delivery cannot be split),
  **Skip Task** (to the back of its Warehouse Order) and **Post Difference** (close at the quantity found, book the shortfall). Actions do not block
  the task. The RF exception screen asks for the bin / quantity the action needs. Codes without an action behave as before.
* **Block reason** - `WMS Block Reason` master (applies to Bin / Handling Unit / Both), `Storage Bin.block_reason`; WMS Settings *Require Block Reason*.
* **Replenish on Removal** (WMS Settings) - a confirmed pick that leaves the bin at or below its Replenishment Rule's minimum raises the
  replenishment immediately. **Zero Stock Check on Pick** - a pick that empties a bin creates a physical inventory count for it (one open count per bin).
* **Wave Template** - *Minimum Deliveries* (a smaller wave is not released automatically), *Maximum Deliveries* (a sweep fills waves up to this),
  *Lock Minutes* (a Draft wave cannot be split shortly before the cutoff); `split_wave` moves some deliveries of a Draft wave into a new one.
* **WO Creation Rule** - unit-weight filter (min/max gross weight per unit, so heavy items get their own rule), *Group by Activity Area* and *Group by
  Consolidation Group* (such tasks never share a Warehouse Order).
* **Warehouse Queue** - a *Door / Staging Bin*: only tasks for that door (a pick's delivery door or staging bin, a task to a door/staging bin) queue there.
* **Planned cross-docking** - `plan_cross_dock(inbound_delivery)` matches the expected quantity against open outbound demand and reserves it before
  the goods arrive (table *Planned Cross-Docking* on the Inbound Delivery); the goods receipt sends exactly that stock to the delivery's staging bin and
  the remainder is put away or matched opportunistically. Completing the inbound delivery short, or removing the outbound one, gives the demand back.

## Topography, labor and handling units (Phase D)

* **Storage Bin** - X/Y/Z coordinates, access type, fire containment section; bulk storage *stack height* x *lane depth* is the capacity when
  Maximum HUs is blank. A bin whose coordinates are all zero counts as having none.
* **Bin sort per activity** - `Activity Area > Bin Sort` (activity, bin, sort sequence) is the walk-path order of the area's bins for Pick / Putaway /
  Internal Move / Inventory Count / Replenish; **Generate Walk Path** (button on the Activity Area form, `services/travel.generate_walk_path`) fills it
  from the coordinates as a serpentine through the aisles. Pick tasks take their sequence from it (`sort_sequence`), a bin without an entry keeps its
  own sequence.
* **Travel distance** - Manhattan distance between bins (`services/travel.py`); a Warehouse Order's `travel_distance` is the walk over its tasks'
  source bins in sequence and on to the last destination (kept up to date as tasks are added).
* **Labor Standard** formula (`services/labor.py`): planned seconds = (base allowance + seconds per unit x quantity + travel seconds per distance unit x the
  task's leg + handling seconds per kg / per volume unit) x (1 + PF&D %); used by the resource efficiency KPI.
* **Handling Unit** - outer length/width/height and *maximum payload* default from the HU type (new `maximum_payload`, `hu_type_group` on the type);
  a posting that would carry more than the HU's payload is refused. Statuses *Planned* and *In Transit* exist (set by integrations or by hand, no
  automatic transitions) and `current_resource` records a carrying resource.
* **Packaging Spec condition technique** - a spec can be for a customer or a supplier (named `item-customer`); `determine_packaging_spec` takes the
  customer's, then the supplier's, then the generic spec (named by the item as before). Packing by instruction and VAS use it with the delivery's customer.

## Extension, actions, quality decisions, yard units and reports (Phase E, part 1)

* **Extension points** (hooks.py of any app): `wms_putaway_strategies` (a Bin Determination Rule with strategy *Custom*: `(bins, context) -> bins`, best first),
  `wms_removal_strategies` (a Removal Rule with strategy *Custom*), `wms_wocr_group_key` (`(task) -> str`: tasks with different values never share a
  Warehouse Order), `wms_queue_override` (`(task, queue) -> queue`), `wms_ppf_actions` (see below).
* **Post Processing Framework** - `PPF Action Profile` per document type with actions: *when* (After Insert / On Update / On Submit / On Cancel / Status
  Change / Scheduled), a *condition* (Python over `doc`), and *what* (Create Tasks for a Warehouse Request, Print, Notify, Call Method registered in
  `wms_ppf_actions`, Set Field, Create ToDo; JSON parameters). Every run is logged in `PPF Action Log`; a failing action is logged and never undoes the
  document; *Execute once* skips documents an action already succeeded for; scheduled actions run every 10 minutes over the documents their filters select.
* **Quality inspection samples and usage decisions** - an Inspection Rule's *sampling percentage* makes the inspection sample: of the HUs nested in the
  inspected HU (evenly spread, at least one), else a quantity of units; `record_sample_result` marks each sample Pass/Fail; more failed samples than
  *acceptable failures* rejects the whole quantity. A `WMS Usage Decision` (Accept / Reject / Other, target stock type, optional follow-up *Move to Bin*)
  splits the inspected quantity over decisions: each moves its stock to its stock type, raises its follow-up task and is mirrored to ERPNext.
* **Transportation Units and yard tasks** - `WMS Transportation Unit` (vehicle / container with status Planned, In Yard, At Door, Loading, Unloading,
  Departed): the dock appointment's check-in creates it on its yard spot, `WMS Yard Task` moves it between yard spots and doors (confirming a door move
  books the door on the appointment), departing it checks the appointment out.
* **Reports** - *WMS Task Analysis* (by task type / resource / day / product), *WMS Bin Utilization*, *WMS Resource Activity* (efficiency against the
  labor standards), *WMS Outbound Status*; linked from the WMS workspace.

## Owner and party entitled to dispose, document types, delivery requests (Phase E, part 2)

* **Owner / Party Entitled to Dispose** - `WMS Stock Owner` master; `stock_owner` and `entitled_party` are stock dimensions on `WMS Stock Balance` and
  the ledger (part of a balance's identity only when set, so the balances of owner-less stock are exactly as before). Set on an Inbound Delivery (and,
  per row, on a Goods Receipt Item) they come with the stock at receipt, through putaway requests/tasks, moves (a transfer keeps the stock's owner) and
  allocation: an Outbound Delivery with an owner only allocates that owner's stock, one without only the warehouse's own. A decrement that names no owner
  takes the owner of the stock it is booked from (the owner-less stock first; two owners in the same place must be named explicitly). Cross-docking
  matches deliveries of the same owner; replenishment, production staging and kitting use owner-less stock only. The ERPNext mirror carries the owner both ways: the Inventory Dimensions
  `WMS Stock Owner` / `WMS Entitled Party` (`wms_stock_owner`, `wms_entitled_party`, `to_` prefix on the target) are set on the Stock Entry /
  Delivery Note / Purchase Receipt rows built from WMS stock (grouped per owner), and `wms_stock_owner` / `wms_entitled_party` on a Purchase Order,
  Sales Order, Purchase Receipt or Delivery Note header go onto the WMS delivery created from it. Opening stock loads accept an owner per row.
* **WMS Document Type** (category Inbound Delivery / Outbound Delivery / Warehouse Request / Delivery Request / Final Outbound Delivery, optional
  warehouse, default flag): *Number Range*, *Field Defaults* (new documents), *Field Control* (Required / Read Only / Hidden, enforced on save and applied to
  the form) and a *Status Profile* (allowed status changes, optionally per role). PPF profiles can be limited to a document type.
* **WMS Delivery Request** - replication keeps the unedited replica of what the ERP document asked for (SAP's inbound / outbound delivery request) next to
  each delivery order it creates; the order links it (`delivery_request`), withdrawing the order marks it Cancelled. **Final Outbound Delivery** - created
  when a goods issue is posted (items as issued), marked Reversed with it.

## Material Flow System and scales (Phase F)

* **MFS** (`services/mfs.py`) - `WMS MFS Endpoint` (a PLC connection: the WMS connects as client, or listens as server), `WMS MFS Telegram Type`
  (a telegram's fixed-position layout; inbound types are selected by an identifier at a position and can *Confirm Warehouse Task*, *Confirm Yard Task* or *Call
  Method* with a handler registered under `wms_mfs_handlers`), `WMS MFS Telegram` (the log: Queued / Sent / Acknowledged / Received / Processed / Failed).
  Outbound telegrams are built from values, sent over TCP (optionally waiting for an acknowledgement) and retried by the scheduler up to five times; a PPF
  action *Send MFS Telegram* starts one from any document event. A server-mode endpoint is served by
  `bench --site <site> execute frappe_wms.services.mfs.listen --kwargs "{'endpoint': 'PLC1'}"` (run it supervised). Tested against loopback sockets - a real
  PLC needs its telegram layouts configured.
* **Scale** - on a Work Center, *Scale on the PC* with the serial settings, a reading pattern, a stable marker and the unit; the Repack Center's close tab then
  shows **Read scale**, which reads the gross weight through the browser's Web Serial API (Chrome / Edge, `public/js/wms_scale.js`). The line parser is unit
  tested; the serial part needs a scale to try.

## Modules

| Module | Contains |
|---|---|
| `wms_core` | Warehouse/Storage Type/Storage Bin structure (Storage Section, Bin Type, Activity Area, Work Center — see [Core flows](#core-flows)), WMS Settings, the WMS Monitor page, the WMS workspace |
| `wms_setup` | Everything in [the rule engine](#the-rule-engine-how-config-drives-behavior): Process/Bin/Warehouse-Process-Type-Determination Rule, Removal Rule, Storage Type Search Sequence, WO Creation Rule, Inspection Rule, process types, movement types, replenishment rules, WMS Number Range, WMS HU Number Pool, WMS Print Determination Rule, plus [P4](#advanced-ewm-p4)'s Wave Template, Labor Standard, and Billing Rate, plus child tables (delivery line items, HU/stock-type bin whitelists, packing source/destination HUs, shipment lines) |
| `wms_inbound` | Inbound Delivery, Goods Receipt |
| `wms_outbound` | Outbound Delivery, Goods Issue, Stock Allocation, Packing Order, WMS Wave, VAS Order (+ VAS Order Activity) |
| `wms_inventory` | WMS Product (+ per-warehouse `WMS Product Warehouse` overrides), WMS Stock Type, WMS Stock Balance, WMS Stock Ledger Entry, Physical Inventory Count (+ Count Tolerance Group, Cycle Count Rule), Quality Inspection |
| `wms_handling_units` | Handling Unit, HU Type, HU Event (audit trail - its `handling_unit`/bin/parent-HU fields are plain Data, not Links, so it never blocks deleting/recycling the HU or bin it once pointed at), Packaging Material, Packaging Spec (+ Packaging Spec Level) |
| `wms_execution` | Warehouse Request, Warehouse Task, Task Allocation, Warehouse Order (queue-assigned batch of tasks), Warehouse Queue, WMS Resource, WMS Resource Group, WMS Print Spool, WMS Exception Code, [P4](#advanced-ewm-p4)'s Kitting Order (+ Kitting Order Component), [Consolidation Group](#consolidation-group) (+ Consolidation Group Line) |
| `wms_shipping` | WMS Route (with ordered Route Stops for multi-hop staging), WMS Shipment |

`services/*.py` holds the transactional logic each doctype's controller calls
into (allocation, determination, receipt, issue, picking, packing,
replenishment, quality, inventory_count, printing, resource (logon/logoff/
kick), procurement/sales — the PO/SO integration, erpnext_sync). `events/*.py` wires those services (and guard
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
   **Include at least one with `storage_role = Door`** — step 16 below can't
   configure a Route's door without a Door-role bin to point it at, and
   Goods Issue can't post without one either.
4. **Storage Bin** per storage type — physical layout, capacity limits,
   optional stock-type/HU-type whitelists, and an optional **Activity Area**
   (assign many at once via the WMS Monitor's **Bin Assignment** tab rather
   than one bin at a time) — only needed where queue routing has to be
   narrower than whole Storage Types.
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
9. **Removal Rule** — optional; no matching rule falls back to
   FEFO-then-FIFO. Add one per (warehouse, product/item group, stock type)
   where you need a different strategy (LIFO, Fixed Bin, ...).
10. **Warehouse Process Type Determination Rule** — optional; no matching
    rule keeps the hardcoded literal each call site (Putaway/Pick/Internal
    Move/Replenish/Deconsolidation) always used. Only needed where different
    items/priorities/warehouses must route through a different Warehouse
    Process Type for the same activity.
11. **WO Creation Rule** — optional; caps tasks per Warehouse Order by
    (warehouse, activity, item group, stock type). Skip it and a batch's
    tasks all share one Warehouse Order, as before.
12. **Inspection Rule** — optional; per (warehouse, item, item group), a
    matching Goods Receipt row auto-routes to `QUALITY` and gets a `WMS
    Quality Inspection` created automatically. Skip it and quality
    inspection stays fully manual.
13. **Replenishment Rule** — one per (warehouse, product, pick bin) you want
    auto-replenished; the hourly job does the rest.
14. **Count Tolerance Group** / **Cycle Count Rule** — both optional. A
    Tolerance Group (per warehouse/item group, absolute/percentage
    thresholds) gates whether a count variance posts immediately or holds
    for recount/approval; skip it and every variance posts immediately, as
    before. A Cycle Count Rule schedules count generation (ABC/Low Stock/
    Zero Stock/Putaway PI/Bin Check/Annual) — skip it and counts stay fully
    manual.
15. **WMS Number Range** — the seeded global fallback (`HU-########`,
    `SHIP-########`) works out of the box; every other doctype it now covers
    (Warehouse Order/Task, deliveries, receipts/issues, packing, waves,
    counts, inspections) is untouched until you add one — configure a
    narrower range per warehouse, HU Type, or doctype only where you need a
    different prefix/window (see [Numbering](#numbering-wms-number-range)).
16. **WMS Route** / **Route Stop** — at least one active Route per warehouse
    (with a `default_staging_bin` and a `default_door` **that must be a
    Door-role bin from step 3** — Route save is rejected otherwise) so
    `create_shipment` can determine one; add ordered **Stops** only where
    HUs must physically pass through intermediate bins (marshalling, yard
    checkpoint) before the door. Skip this and `create_shipment` has nothing
    to determine, which means HUs can never reach `Loaded` status, which
    means Goods Issue can never post for anything shipped through this
    warehouse — this step is not optional despite being listed near the end.
17. **WMS Resource / WMS Resource Group / Warehouse Queue / Work Center** —
    optional, but this is what makes Warehouse Orders and the RF app's
    Auto-pull ("Get Work") actually work: without at least one active
    **Warehouse Queue** per (warehouse, activity), tasks for that activity
    are simply never routed through a Warehouse Order at all and stay
    assigned/worked directly, one at a time — no Blocked/On Hold lifecycle,
    no Auto-pull. Create one **WMS Resource** per physical device/operator
    login slot, optionally pool them into a **WMS Resource Group** (per
    warehouse) so a **Warehouse Queue** can point at the group rather than
    individual resources — the group is the actual source of eligibility, so
    reassigning who works a queue later is a one-place edit. **Work Center**
    is a further-optional second RF logon step, only useful if you want VAS
    actions to default a bin from it (see [Core flows](#core-flows)).
18. **WMS Resource** (`resource_type = Printer`) / **WMS Print Determination
    Rule** — entirely optional: skip both and nothing prints, exactly like
    skipping step 15. Add one Printer Resource per physical device, then a
    rule per (warehouse, event) pointing at it, where you actually want `HU
    Created` / `Putaway Confirmed` / `Goods Issue Posted` / `Shipment
    Loaded` to queue something (see [Printing](#printing-spool)).
19. **[P4](#advanced-ewm-p4) config, all optional**: **Wave Template**
    (warehouse/route, cut-off time, auto-release) for scheduled wave
    generation; **Labor Standard** (warehouse/task type/item group, standard
    seconds per unit) for the KPI dashboard's efficiency numbers; **Billing
    Rate** (warehouse/customer, activity, rate, billing item) before using
    the Monitor's Billing tab. Skip any of these and that specific P4 feature
    simply has nothing to compute from — nothing else in the app depends on
    them.
20. **Roles** — assign the roles below to users; optionally add **User
    Permission** rows restricting a user to specific `WMS Warehouse` values
    (see [Roles & permissions](#roles--permissions)).

## Roles & permissions

Roles are seeded by `setup/roles.py` on install:

`WMS Operator`, `WMS Receiver`, `WMS Picker`, `WMS Packer`, `WMS Loader`,
`WMS Inventory Controller`, `WMS Supervisor`, `WMS Process Engineer`,
`WMS Master Data`, `WMS Administrator`, `WMS Integration User`, `WMS Auditor`.

Warehouse state changes only through the WMS services, which check roles
themselves (`utils.require_role`; System Manager always allowed). DocType
permissions govern what people can *see* and which configuration they can
*maintain*, from one matrix in `setup/role_permissions.py`:

| Who | Can |
|---|---|
| Every WMS role | Read the warehouse structure, customizing and every execution document |
| WMS Process Engineer | Maintain the customizing (structure, rules, process setup, printers, settings) |
| WMS Master Data | Maintain products, bins, sections, activity areas, packaging |
| WMS Supervisor | Keeps its write access (waves, orders, deliveries); reads settings and the ERPNext posting queue |

The matrix is in the DocType definitions for new installs, and is added after
every migrate to sites whose permissions were customised (never removing
what an administrator granted). On top of it:

- **WMS Stock Ledger Entry** and **WMS Stock Balance** are read-only from the
  desk (write/create/delete/submit/cancel always denied — they're written
  exclusively by `services/stock.py` with `flags.ignore_permissions`, so a
  desk edit would desync the balance from the ledger).
- A **User Permission** on `WMS Warehouse` limits a user to those
  warehouses in every WMS doctype that links to one (deliveries, HUs, tasks,
  counts, orders, balances, the ledger) and in the RF app's lists; no User
  Permission means no extra restriction.

## ERPNext integration

### Document flow: ERPNext as ECC, the WMS as EWM

As in SAP, the business document is created in ERPNext and replicated to the
warehouse, which executes it and reports back. Each **WMS Warehouse** has an
*ERP Integration* section:

| Setting | Options | What it does |
|---|---|---|
| Inbound Replication | Manual / Purchase Order Submitted / Purchase Receipt Draft | Which ERPNext document creates the Inbound Delivery. With *Purchase Receipt Draft* (the ASN pattern) the warehouse submits that same draft at goods receipt |
| Outbound Replication | Manual / Sales Order Submitted / Delivery Note Draft | Which ERPNext document creates the Outbound Delivery. With *Delivery Note Draft* (the ECC outbound delivery pattern) the warehouse submits that draft at goods issue |
| Release Replicated Deliveries | on / off | Submit replicated deliveries straight away |
| Outbound Follow-Up | None / Allocate / Allocate and Create Pick Tasks | What happens to a released outbound delivery next |
| ERP Change Policy | Adapt Until Execution Starts / Block Changes After Replication | Order edits (Update Items, removed lines, cancellation) reach deliveries that have not started; once started they are refused |
| Close Short Orders | on / off | A delivery completed short (`Complete short`, supervisors) closes the rest of the Sales/Purchase Order |
| ERPNext Posting Mode | Synchronous / Queued with Retry | See below |

Only order lines whose ERPNext warehouse maps to a WMS Warehouse are
replicated; one order can feed several WMS warehouses. SO/PO/DN/PR forms
show the WMS status and link to the delivery, and ERPNext refuses to cancel
a document the warehouse posted (reverse it in the WMS instead).

### Posting mode

*Synchronous* posts the ERPNext document inside the warehouse transaction,
so an ERPNext error (closed period, missing account) stops the warehouse
posting too. *Queued with Retry* posts in the warehouse first and has
ERPNext follow from **WMS ERP Sync Log**, retried with backoff every 10
minutes, like SAP's qRFC queue. A reversal never overtakes its posting, and
one whose posting never reached ERPNext cancels it instead. The Monitor's
Alerts view lists what has not reached ERPNext, with *Retry now*.

### Postings

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
  erpnext_stock_guard.py` refuses every ERPNext document that would move its
  stock outside the WMS: Stock Entry, Delivery Note, Purchase Receipt, Stock
  Reconciliation, Sales / Purchase / POS Invoice with *Update Stock*,
  Subcontracting Order and Receipt, Asset Capitalization and Asset Repair,
  including product-bundle components (`packed_items`). Work Orders, Job
  Cards and Subcontracting Inward Orders move stock only through Stock
  Entries, which are refused the same way. The warehouse's own postings and
  its replicated draft Delivery Notes / Purchase Receipts pass. Warehouses
  *not* linked to a WMS Warehouse are unaffected. Toggle off via
  `WMS Settings.enforce_wms_only_stock_movements`.
- A daily job (`verify_erpnext_stock_reconciliation`) flags any drift between
  WMS and ERPNext quantities per warehouse/product via `frappe.log_error`. It
  compares against ERPNext's stock *ledger*, not its `Bin` cache: ERPNext core
  can leave `Bin.actual_qty` stale on its own under concurrency
  (`bin.update_qty` rewrites it from a non-locking read, e.g. when a Sales
  Order reserves stock while a Purchase Receipt posts the same item). When the
  ledgers agree and only the Bin is off, the job repairs the Bin with ERPNext's
  own `update_bin_qty`.

## Background jobs

Registered in `hooks.py` under `scheduler_events`:

| Frequency | Job | Purpose |
|---|---|---|
| Every 10 min | `erp_sync_queue.retry_due` | Retries queued/failed ERPNext postings (Queued with Retry warehouses) |
| Every 10 min | `printing.retry_print_jobs` | Resends failed network print jobs; requeues agent jobs never confirmed |
| Hourly | `yard.mark_no_shows` | Planned dock appointments not checked in in time become No Show and free their door |
| Hourly | `alerts.send_alert_digest` | Desk notification (and e-mail) to supervisors when a warehouse's alerts change — see WMS Settings > Alerts |
| Hourly | `recalculate_stale_bin_capacity` | Recomputes `current_hu_count`/`current_weight` per active bin from live HU data |
| Hourly | `run_replenishment_check` | Evaluates every active Replenishment Rule, raises a Warehouse Request+Task for pick bins at/below minimum (skips if one's already pending) |
| Hourly | `generate_scheduled_waves` | [P4] Sweeps matching submitted Outbound Deliveries into a new Draft `WMS Wave` per active Wave Template |
| Hourly | `release_due_waves` | [P4] Releases any template-generated Draft wave whose cut-off time has passed |
| Monthly | `archiving.monthly_archive` | Archives stock ledger entries older than *WMS Settings > Keep Stock Ledger Entries (Months)* — see [Ledger archiving](#ledger-archiving) |
| Daily | `verify_stock_balance_integrity` | Logs any negative `WMS Stock Balance` rows as an error for review |
| Daily | `verify_erpnext_stock_reconciliation` | Logs WMS vs ERPNext quantity drift per warehouse/product |
| Daily | `generate_scheduled_counts` | [P3] Generates `WMS Physical Inventory Count` documents per active Cycle Count Rule (ABC/Low Stock/Zero Stock/Putaway PI/Bin Check/Annual) |

None of these post anything automatically except replenishment/wave/count
generation — the integrity/reconciliation checks are report-only
(`frappe.log_error`), by design, so they never silently correct the ledger.

## Yard and dock appointments

SAP EWM's dock appointment scheduling plus the core of yard management.
Doors are bins of a storage type with the **Door** role, yard parking spots
bins with the **Yard** role. A **WMS Dock Appointment** books a door for a
time slot (inbound, optionally for an Inbound Delivery; outbound, optionally
for a WMS Shipment) and follows the truck:

`Planned → Checked In (gate, yard spot) → At Door → Completed → Checked Out`,
or `No Show` / `Cancelled`.

- Two active appointments never overlap at a door (plus *Door Changeover*);
  without a door, the first free one is assigned. `free_slots` lists what is
  still open per door for a day.
- An outbound truck at a door becomes its shipment's loading door, and
  departing the shipment completes the appointment.
- At the gate, *Trucks Without Appointment* decides: Allow (checked in as a
  walk-in), Warn (confirm) or Block.
- An appointment not checked in *No Show After (Minutes)* past its start
  becomes No Show and frees its door (hourly job).
- The arrival is recorded against the plan (minutes early or late).
- **Checkpoints** (storage role *Checkpoint*): arrival and departure name the gate bin;
  *Checkpoint Required* makes it mandatory. A transportation unit's **activity** is
  Planned / Active (from arrival at the checkpoint) / Completed (departure).
- **Door Determination Rule** (warehouse, priority, direction, route -> door): the
  rules' doors are tried first, then the other doors.
- **Means of Transport** master (unit type, max payload) on the transportation unit;
- **Shipping & Receiving cockpit** (Monitor > Shipping & Receiving): live trucks with their next step (check in / to door / complete / check out), and the work still without a truck - inbound deliveries, shipments, picked outbound deliveries. *Plan Truck* creates the shipment (outbound deliveries), the dock appointment and the transportation unit in one step. A vehicle (*WMS Vehicle*, one per registration) groups its transportation units: `plan_truck` takes several inbound deliveries or shipments, makes one unit for each, and the units follow the appointment together; the gate check and yard status cover every delivery on the vehicle.
- **Delivery split**: *Split Delivery* on an Outbound Delivery moves open quantity (not yet allocated/picked/issued) to a new `-S<n>` delivery; a delivery replicated from a draft Delivery Note gets a second draft Delivery Note.
- **Cartonization**: flag Handling Unit Types *Use for Cartonization*, then *Plan Cartons* on a delivery fills its Planned Shipping HUs by weight/volume per unit (WMS Product); the packing station proposes them when it creates the HU.
- **Dangerous goods**: Hazard Class master (with incompatible classes), hazard class / UN number / packing group / points on the WMS Product, allowed classes and a points limit per bin on the Storage Type; switch on *Dangerous Goods Check* on the warehouse and bin validation (putaway, bin determination, moves) enforces permission, segregation and points.
  loading a shipment heavier than the payload is refused.
- *Receipt / Loading Needs Truck at Door* (Off / Warn / Block): a delivery or shipment
  that has a dock appointment can only be received or loaded once its truck is at a door.
- *Seal Required at Departure*: an outbound truck needs its seal number to leave.
- Inbound Delivery and WMS Shipment show a **Yard Status** (In Yard / At Door / Departed).

Settings: *WMS Warehouse > Yard and Dock Appointments*. Screens: the Monitor's
**Yard & Doors** (door board, the day's schedule with Check in / To door /
Complete / Check out / Cancel, booking and walk-in dialogs) and the RF **Yard**
screen (Inbound and Outbound menus) for the gate and dock.

## Ledger archiving

The stock ledger only grows. With *WMS Settings > Keep Stock Ledger Entries
(Months)* set (0 = forever, otherwise at least 12), a monthly **WMS Ledger
Archive Run** moves every entry older than that into a gzipped JSON-lines
file attached to the run, and posts one carry-forward entry (movement type
`999`) per stock position that still holds stock. The carry-forward is dated
at the position's first receipt, so ledger totals still equal WMS Stock
Balance, FIFO/FEFO keep their order, and `rebuild_balances` gives the same
result. A supervisor can also start a run with `api/monitor.start_ledger_archive`.
Reversing a document whose ledger lines were archived is refused; post a
correcting movement instead.

Monitor selections return one page at a time (*Max. hits* per page); when
there are more, **Load next** appends the next page in the same order.

## RF / scanner app

Device setup and kiosk lockdown (Android dedicated devices, iOS Single App
Mode / Guided Access, scanner settings): [docs/rf-devices.md](docs/rf-devices.md).
What each role does on the floor and in the desk: [docs/operator-guide.md](docs/operator-guide.md).


`/wms` is a chrome-free, scanner-oriented page (not a desk form) — the RF
frontend at `frappe_wms/www/wms/`, styled after SAP EWM's RF UI. Opening it
first shows a **Log On** screen: if the signed-in user isn't currently logged
on to any WMS Resource, it lists free ones to claim (`api/resource.log_on`)
rather than blocking on an admin having pre-assigned one; once claimed, a Log
Off button (next to the queue controls) releases it again. From here an
operator can also, entirely optionally, join a Queue and/or log on to a
**Work Center** (see [Core flows](#core-flows)) — neither is required to
proceed. Only then does the home menu appear, leading to:

| Section | Action | What it does |
|---|---|---|
| Inbound | Receive | Pick an open Inbound Delivery, then scan what is in front of you: a product barcode (EAN/GTIN or item code) or a GS1-128/DataMatrix label selects its line; the HU (unknown labels auto-register with the chosen/default HU type) carries over to the next line; batch and serial fields appear only when posting requires them — serials are scanned one after another, and a GS1 label fills GTIN, batch, expiry, serial, count and SSCC by itself. Lines collect into one Goods Receipt, posted in one go — which immediately raises Putaway (and, where matched, [P4] Cross Dock) tasks |
| Inbound | Putaway Tasks | Confirm any open Putaway, Unload, Deconsolidation, or [P4] Cross Dock task, or report an exception — every task type shares the same generic confirm wizard (source scan → quantity → destination scan → HU), so nothing here is task-type-specific |
| Inbound | Deconsolidate | Split a received HU's contents across multiple destination bins in one flow |
| Inbound | Quality | Complete an inspection's pass/fail split |
| Internal | Move | Ad-hoc bin-to-bin/HU-to-HU transfer with no planning step |
| Internal | Internal Tasks | Confirm any open Internal Move (including [P4] slotting rearrangement tasks), Posting Change, or Inventory Count task |
| Internal | Close Movement | Advance an HU one hop along its Route's multi-stop journey |
| Internal | Repack | Move whole HUs and/or partial item quantities into a destination HU |
| Internal | Count | Record physical inventory quantities; auto-posts once every line is counted (or holds for recount/approval — see [Core flows](#core-flows)) |
| Internal | Handling Units | Look up, create (scan a barcode, or leave it blank for an Internal HU Type), nest/unnest, block/unblock, or recycle an empty, reusable HU (frees its number for reuse) |
| Internal | Kitting [P4] | Open Kitting Orders of the operator's warehouse; per order what is at the work center and on its way, Stage components, Complete (optionally onto a scanned output HU), Cancel |
| Internal | Consolidation | Scan an Outbound Delivery/Work Order/Stock Allocation/Warehouse Request barcode to find and add joinable lines to a [Consolidation Group](#consolidation-group), set a target HU, Gather, then Split to Destinations |
| Outbound | Picking | Auto/Manual submenu (`renderSubmenu`, a deliberately reusable tile-list pattern meant for other RF menus too): **Auto** pulls the next task off the operator's eligible queues (same as the Queue bar's Get Work); **Manual** is one scan field for any reference — HU, Warehouse Task, Warehouse Order, Warehouse Request, Queue or Outbound Delivery, told apart by `find_pick_tasks` itself — with per-kind searches underneath |
| Outbound | Pick Tasks | Confirm any open Pick, Stage, or Load task |
| — | Counting units | Receive, every task confirmation and Pack let the operator count in any of the product's units — the stock unit, the document line's unit (a delivery ordered in cases offers cases first), and the ERPNext Item's UOM conversions — and post stock units. The desk Repack Center does the same |
| Outbound | Ship | Pick a delivery that's fully picked but not issued, confirm/adjust the suggested loaded HU per line, post the Goods Issue manually — a fallback for whatever the automatic post-on-load (see [Shipping/loading](#core-flows)) hasn't already handled |
| Outbound | Pack | Log on to a work center, then scan source HU → product → quantity → destination HU to pack; create a new carton/pallet, pack a whole HU into another, close an HU with its weight. The work center's customizing decides which of these the packer gets (see Repack Center); open Packing Orders at the table can still be completed in one tap |
| Outbound | VAS | Complete open VAS activity steps, or tap "+ Generate from Packaging Spec" [P4] to build a new VAS Order's steps from a scanned HU's item's Packaging Spec instead of typing them in by hand |
| Inbound / Outbound | Yard | The day's trucks by door, yard and expected; check a truck in (scan its yard spot), send it to a door (scan the door, or its booked one), mark it done and check it out; take in a truck without appointment |
| Outbound | Load | Pick a `Ready to Load`/`Loading` Shipment and load it last stop first (the screen names the next HU and each HU's stop); scan each HU to walk it through the Route's Stops (if any) to the door, then depart the Shipment once full. *WMS Warehouse > Loading > Load Sequence Check* makes an out-of-order HU a warning (confirm) or a block |
| — | Lookup | HU/bin contents by barcode |

`api/scanner.py` and the other `api/*.py` modules are the whitelisted
endpoints this frontend (and real barcode hardware) call.

### How the scanner app behaves

Built for a phone (iPhone Safari is the reference target), a Bluetooth HID
scanner (Netum etc.) paired to it, or the phone camera - and later a
self-scanning terminal, which is just another keyboard.

- **Scanning.** A scan is a fast burst of characters ending in Enter. It is
  routed to the field the screen is waiting on *whatever has focus* (iOS often
  has nothing focused), and a scan that lands in a quantity box is pulled back
  out of it. Codes are normalised (whitespace, control characters, AIM
  `]C1`-style prefixes) and matched case-insensitively against what the task
  expects. Item barcodes (Item Barcode / EAN) resolve to the item via
  `api.scanner.resolve_scan`, which is also how "that is a bin, not a
  product" is told apart from "unknown code". Scan fields keep the on-screen
  keyboard hidden; a camera button and a keyboard toggle sit beside each. The
  camera uses the native `BarcodeDetector` where present and the vendored
  ZXing build (`public/js/wms_rf/vendor`, Apache-2.0) on iOS.
- **Feedback.** Every outcome gives a tone + coloured flash (+ vibration where
  the browser supports it; iOS Safari does not). Errors say what was scanned
  and what was expected, stay on screen until dismissed, and keep the field
  focused. *Device & session* has sound / vibration / theme switches.
- **Back, reload and lost work.** Every screen and every wizard step is a
  real history entry and a URL (`/wms#/task/WT-0001/quantity`), so Back / the
  iOS swipe walk exactly the path taken and reload lands on the same screen.
  In-progress work (a half-done Move, scanned Receipt lines, a task wizard) is
  saved to the tab's `sessionStorage` as you go and offered under *Unfinished
  work* on the menu; it survives reload, the OS killing the tab, and the login
  round trip after a session expiry. Finished flows drop their steps from the
  Back path, so Back cannot re-enter a completed task.
- **Sending twice is safe.** Every mutating call carries an idempotency key
  created once per user action and stored with the draft. The server answers a
  repeat with the first result (`services/idempotency.run_once`; stock postings
  and `confirm_task` dedupe on the ledger key). A lost response, a dead-WiFi
  retry, or a double Enter/tap cannot post twice; failures show a Retry button
  and keep every entry.
- **Product verification.** When *WMS Settings → require scan verification* is
  on, the confirm wizard gains a product-scan step; otherwise scanning the
  product is not required.
- **Files.** `www/wms/index.{html,py}` is a thin shell (an import map with a
  content hash per module - `/assets` is cached for a year - plus a visible
  boot-failure fallback). The app is native ES modules under
  `public/js/wms_rf/` (`core/` = api, router, drafts, scan, camera, feedback;
  `ui/` = shell, kit, keys; `screens/` = one file per area) and
  `public/css/wms_rf.css`. No build step. Needs iOS 16.4+ (import maps).

### Testing the scanner app

```bash
# pure-logic unit tests (Node >= 20, no browser, no site)
node --test frappe_wms/tests/js/*.test.mjs

# backend
bench --site <site> run-tests --app frappe_wms --module frappe_wms.tests.test_scanner_api
bench --site <site> run-tests --app frappe_wms --module frappe_wms.tests.test_task_confirmation

# browser end-to-end (Playwright, phone viewport) against a dev bench site
cd frappe_wms/tests/e2e && npm install && npx playwright install chromium
BENCH_DIR=~/frappe-bench SITE=wms.local PORT=18001 npx playwright test
```

The e2e suite seeds an `E2E-WH` warehouse, bins, two Resources, an item
barcode and two users (`frappe_wms/tests/e2e/seed.py`) and never touches other
data; run it on a dev site, not production. If headless Chromium reports a
missing `libasound.so.2`, unpack the `libasound2t64` .deb into
`~/.local/pw-libs` (the config picks it up) or install it system-wide.

Playwright runs Chromium; iOS-specific behaviour needs a manual pass on a
real iPhone with the Bluetooth scanner:

1. Pair the scanner in HID/keyboard mode (Netum: scan the "iOS/HID keyboard" setup code). The on-screen keyboard should disappear while it is connected.
2. Log on, open a Pick task, scan source -> Enter on quantity -> scan destination -> Confirm; every step should beep-flash and advance without touching the screen.
3. Scan a wrong bin: red flash + message naming what you scanned and what is expected.
4. Mid-task: swipe Back (previous step), then reload the tab - you should land on the step with entries intact.
5. Put the phone in airplane mode, Confirm: "No connection" with Retry; turn it back on, Retry: one confirmation.
6. Tap the camera button beside a scan field, allow the camera, scan a Code 128 label.
7. Add to Home Screen; launch it from there and repeat 2.

### Load and concurrency testing

`frappe_wms/tests/load/` drives a realistic, concurrent warehouse shift
through the real HTTP API - the same endpoints the RF app and desk buttons
call, each actor logged in as its own user with its own RF Resource - and
then checks the resulting data for consistency. Use a dev/test site only.

```bash
# 1. a fully configured DC "MAD1": ~340 bins, rules, routes, queues, 27 RF
#    resources, 27 users across every role, 200 products (batch/serial/ABC),
#    suppliers, customers and an opening-stock cutover (idempotent)
bench --site <site> execute frappe_wms.tests.load.seed_dc.run

# 2. a shift: buyer, sales clerk, supervisors, 4 receivers, 8 pickers,
#    3 packers, 3 loaders, 3 inventory controllers and a "chaos" actor that
#    races receipts and task confirmations. Run the site with several
#    gunicorn workers (not `bench serve`), and in developer mode if you want
#    server tracebacks in the report.
python3 frappe_wms/tests/load/simulate.py --url http://<site>:8000 --minutes 10 --report shift.json

# 3. consistency checks (ledger vs balances, allocations, WMS vs ERPNext
#    ledger, receipt/outbound progress, HU locations, double postings, ...)
bench --site <site> execute frappe_wms.tests.load.invariants.run --kwargs "{'warehouse': 'MAD1'}"
```

Every non-2xx response is grouped in the report by endpoint and exception;
rejections a real operator can legitimately hit (e.g. "only N left to
receive" when two receivers race for the same line) are counted separately
as expected. Every whitelisted endpoint retries a database deadlock by
re-running the whole call (`services/concurrency.retry_on_deadlock`), so a
deadlock should never surface as an HTTP 500.

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
  Departed); ERPNext's own **Purchase Order** / **Sales Order** get "Create
  Inbound/Outbound Delivery"; ERPNext's **Work Order** gets "FG Receipt
  (WMS)" [P2] — posts a Goods Receipt sourced from the Work Order and drives
  the normal putaway flow from it (`create_fg_receipt_from_work_order`).
- **WMS Monitor** (`/app/wms-monitor`) — pick a warehouse, then a node from
  the left-hand list, SAP EWM Warehouse Management Monitor-style, instead of
  one long scrolling page. Every search node (inbound, outbound, waves, stock,
  tasks, HUs, movements) has an **SAP selection screen**
  (`public/js/wms_selection.js`, compiled server-side by
  `services/selection.py`):
  - each field takes a single value, a `from`/`to` range, or opens
    **multiple selection** with *Include* and *Exclude* tabs and the SAP
    operators (=, ≠, >, ≥, <, ≤, between, not between, pattern, not pattern);
  - shortcut syntax in the field: `A*` pattern (`+` = one character),
    `>=10`, `<>X`, `10..20`, `a;b;c`, `!X` to exclude, `=` for blank;
  - paste a column copied from Excel into any field (or the dialog) and every
    line becomes one value — large lists run as one `IN (...)`;
  - **Fields…** adds any field of the DocType, plus related ones (product
    group, a bin's storage type, a product on a delivery line, a Sales/
    Purchase Order, the HU's work center, ...);
  - **Max. hits**, Execute on **F8** or Enter, and a clear warning when the
    hit limit cut the result;
  - **Save as Variant** (personal or, for supervisors, global; one default per
    user and view) and **layouts**: drag a column header's grip to move it,
    **Columns…** to show/hide/order any field, sort, and a Σ totals row — all
    saved in `WMS Monitor Variant`; **Export** downloads the visible grid as
    CSV.

  Criteria are compiled into SQL only from fieldnames validated against the
  DocType meta and escaped values, and appended to Frappe's own
  `DatabaseQuery`, so role and User Permissions still apply. Nodes:
  - **Overview** — summary counts (open tasks by type, exceptions, pending
    replenishment, deliveries in progress, open counts/inspections, open
    waves, active resources), each linking to its filtered list view.
  - **Inbound Monitor** — searchable Inbound Deliveries.
  - **Outbound Monitor** — searchable Outbound Deliveries plus Waves (with
    one-click Release per draft wave). Clicking a delivery expands a
    drill-down: its allocation/picking/packing/loading/goods-issue status,
    its Pick tasks and the Warehouse Order(s) they belong to, its Packing
    Orders and Goods Issues (with the `reversed` flag), and the Allocate
    Stock / Create Pick Tasks actions, plus a manual Post Goods Issue action
    once the delivery is Loaded (normally unnecessary - loading already
    auto-posts it, see [Shipping/loading](#core-flows)) — everything needed
    to drive and watch the pick-to-ship lifecycle without leaving the
    Monitor or reaching for the RF app.
  - **Stock Overview** — current `WMS Stock Balance` positions (quantity/
    allocated/available per product/bin/HU/stock type), with a per-stock-type
    summary strip — the current-state counterpart to Stock Movements' history.
  - **Warehouse Tasks**, **Handling Units** — searchable, each its own node.
  - **Repack Center** — the packing work center below, inside the Monitor.
  - **HU Workbench** — free repacking anywhere in the warehouse: a bin/HU/
    stock-line tree with drag and drop.
  - **Stock Movements** — searchable `WMS Stock Ledger Entry` history.
  - **Resources & Queues** — resource workload and warehouse queues.
  - **Difference Analyzer** [P3] — posted count variance aggregated by
    product (total gain/loss/net-variance/over-tolerance-event counts) over a
    date range.
  - **KPIs** [P3/P4] — warehouse-level cards (task throughput, average task
    and Warehouse Order cycle time, exception rate, count accuracy) plus a
    per-resource labor performance table (task count, average cycle time,
    efficiency % against a matching Labor Standard).
  - **Slotting** [P4] — misplaced-and-active-product recommendations, with a
    bulk "Generate Rearrangement Tasks" action.
  - **Bin Assignment** — filter Storage Bins (storage type/section/aisle/
    rack), select, bulk-assign one Activity Area to all of them in one call —
    the SAP-EWM-style mass-maintenance transaction for bin structure, instead
    of one desk edit per bin.
  - **Kitting** [P4] — create a Kitting Order (kit item, BOM, work center
    bin, quantity, direction) and complete open ones.
  - **Billing** [P4] — preview `generate_billing_for_period` for a
    warehouse/customer/date range, then create the Draft Sales Invoice.
  - **Alerts** — counts awaiting supervisor approval (with a bulk "Approve
    Selected" action), aged exceptions, and stalled Warehouse Orders.

  Roles: WMS Supervisor / Administrator / Inventory Controller / Auditor,
  System Manager.
- **Repack Center** (`/app/wms-packing-station`, also a Monitor node) —
  SAP EWM's packing work center (`/SCWM/PACK`). The packer logs on to a
  **Work Center** (a packing table = one Storage Bin) and gets the three
  /SCWM/PACK areas. Everything the station does is customizing on the Work
  Center, as in SAP's work center definition: type, inbound section (HUs
  arriving at the table, with *Take to table*) and outbound section, default
  HU type, quantity proposal (full quantity or one unit per scan), weighing
  on close (optional/required, tolerance %), completeness check
  (off/warn/block), close follow-up (stay, outbound section, delivery
  staging bin), label on create/close, and which functions exist at all
  (pack product, pack HU, unpack, pack by instruction, create/close/delete
  empty HU, post differences). The station has the HU tree of everything on the table, the selected HU's
  detail (contents, weights, delivery, label), and a scanner area with
  *Pack Product*, *Pack HU*, *Create HU* and *Close HU* tabs where Enter moves
  field to field and runs the action on the last one. Rules
  (`services/packing_station.py`):
  - one HU never mixes outbound deliveries; a tote picked for several
    deliveries asks which delivery a pack step is for; packing into an empty
    HU stamps it with the delivery (`Handling Unit.outbound_delivery`);
  - **Close HU** records the weighed gross weight (warns when it is below the
    products' net weight), locks the HU and everything nested in it, queues
    its label (print event *HU Closed*) and can move it straight to the
    delivery's staging bin; **Reopen** undoes it;
  - the delivery's packing status moves to *In Process*, then *Packed* once
    every HU holding its stock is closed;
  - shipping and goods issue follow the stock: a shipment loads the
    top-level HUs that still hold the delivery's stock (an emptied pick tote
    drops out, a carton packed onto a pallet ships as the pallet), and goods
    issue finds packed and nested HUs.

  Roles: WMS Packer / Operator / Supervisor / Administrator, System Manager.

## Install

Requires Frappe v16 and ERPNext v16 (ERPNext must already be installed on the
site — this app depends on ERPNext master doctypes: `Item`, `UOM`, `Batch`,
`Serial No`, `Company`, `Supplier`, `Customer`).

```bash
cd frappe-bench
bench get-app https://github.com/pedropi16/frappe-ewm.git --branch main
bench --site your-site install-app frappe_wms
bench build --app frappe_wms
```

`bench get-app` clones the repo into `apps/frappe_wms`; `install-app` runs
the full DocType sync, `after_install` (seeds Stock Types, Movement Types,
Warehouse Process Types, Exception Codes, Number Ranges, the WMS Stock Type
Inventory Dimension, and the roles listed below), and the workspace/desktop
icon setup, then `bench build` links/compiles the RF app and Monitor page's
static assets. No separate `migrate` step is needed on a fresh install —
`install-app` already runs it; run `bench --site your-site migrate` only
after later pulling app updates.

Everything above (Stock/Movement/Process Types, Exception Codes, roles) is a
sensible starting set so the site isn't empty; everything else in
[Configuration reference](#configuration-reference) — warehouses, storage
structure, and every rule table — is warehouse-specific and left for you to
configure per site.

This exact path was verified by uninstalling `frappe_wms` entirely and
reinstalling it from a freshly-cloned copy of this repo, not just written
down and assumed to work — see [Status](#status).

## Uninstall

```bash
bench --site your-site uninstall-app frappe_wms
```

Backs up the site by default before dropping every `frappe_wms` table and
DocType (pass `--no-backup` to skip, `--dry-run` to preview what would be
removed, `-y`/`--yes` to bypass the confirmation prompt). This only touches
`frappe_wms`'s own doctypes and modules — ERPNext and Frappe core, and any
ERPNext documents already mirrored (Stock Entries, Delivery Notes, Purchase
Receipts, Sales Invoices from [3PL billing](#advanced-ewm-p4)), are
untouched and stay exactly as they are.

To remove the app from the bench entirely after uninstalling it from every
site: `bench remove-app frappe_wms`.

## Internationalization

Every user-facing string in this app - `frappe.throw`/`msgprint` messages,
doctype field labels and Select options, the Monitor page, and the RF scanner
app - is translatable, and Spanish (`es`) ships out of the box
(`frappe_wms/locale/es.po`, 1000+ strings). Setting a user's or the site's
language to Spanish is enough; nothing else to configure. To add another
language: `bench create-po-file <locale> --app frappe_wms`, fill in the
generated `frappe_wms/locale/<locale>.po`, then `bench compile-po-to-mo --app
frappe_wms`. After adding new translatable strings anywhere in the app,
`bench generate-pot-file --app frappe_wms` followed by `bench
update-po-files --app frappe_wms --locale <locale>` merges the new (blank)
entries into an existing `.po` without touching what's already translated.

The RF scanner app (`www/wms/index.html`) is a fully custom page outside the
desk's normal `__()`/`frappe.boot` translation machinery, so it has its own
lightweight scheme: a small `_()` JS helper (same `"{0}"`/`"{1}"` placeholder
convention as `_()`/`__()` elsewhere) looks up from a `MESSAGES` dict that
`www/wms/index.py` injects per request via
`frappe.translate.get_translations_from_apps(frappe.local.lang,
["frappe_wms"])` - scoped to this app's own compiled catalog, so it covers
every dynamic-value lookup (statuses, priorities, task/activity types) as
well as every literal string in the page, always in sync with whatever's in
`es.po` (or any other locale you add) without needing a second translation
file to maintain.

## Production warning

This is a complete, tested MVP (243 automated tests covering P0–P4, fresh
install verified end to end — see [Status](#status)), not a certified SAP EWM
replacement. Validate accounting integration, concurrency, permissions,
barcode hardware, reversal rules, and migration data in a non-production site
before go-live.

- **Yard roles**: *WMS Yard Clerk* (gate, yard moves, doors) and *WMS Yard Planner* (appointments, truck planning).
- **Carrier Capacity** (max trucks per carrier per day, enforced at booking) and **recurring appointments** (`create_recurring_appointments`: daily/weekly series, clashing days skipped).
- **Return Item Type**: a customer return can be raised with an item type whose stock type it is expected in (default QUALITY).

- **Per-diem storage billing**: a daily snapshot (*Storage Usage Day*) of HUs / bins / units per stock owner; a *Billing Rate* with activity Storage and basis Per HU Day / Per Bin Day / Per Unit Day bills the customer that owns the stock (Stock Owner > Partner Type Customer).
- **Slotting index**: `frappe_wms.api.slotting.classify_abc` ranks products by pick share (A 80% / B 15% / C 5%) and can write `abc_indicator`.
- **Labor shifts and indirect labor**: *Labor Shift* master, `start_indirect_labor` / `stop_indirect_labor` (break, cleaning, training...), `labor_summary` (direct vs indirect hours, utilisation against the shift).

- **Stock key: country of origin and special stock**: balances, ledger entries, allocations, requests and tasks carry `country_of_origin`, `special_stock_type` (Sales Order / Project) and `special_stock_ref`. Like owner/party they are part of a balance's identity only when set (existing balances keep their identity), moves and decrements keep or resolve them from the stock, receipts take them from the goods receipt row or its inbound delivery line, and rebuild keeps them apart. Allocation never gives reserved stock to another order's delivery (an order's own reserved stock goes first), an Outbound Delivery Item can require a country of origin, and replenishment, production supply and cross-docking leave reserved stock alone. Not mirrored to ERPNext (the mirror documents stay per item/batch/owner) and not on opening-stock rows.
- **Consolidation group as a stock attribute**: when a line joins a Consolidation Group, its stock (picked for an outbound delivery, or staged for a production material request) is posted as stock of that group (`consolidation_group` on balances, ledger entries and tasks, identity only when set). Gather and split tasks carry it, so the stock keeps the group while it sits at the target HU and at its delivery / PMR bin; goods issue and consumption resolve it from the stock. Removing or cancelling a line before it is gathered hands the stock back without the group. Allocation, replenishment and production supply skip stock that carries a group. Lines staged before this existed are tagged when the group is gathered.
