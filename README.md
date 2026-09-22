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
- **P4 — Advanced EWM** (7 of 8 areas — yard/transport
  units/dock-appointment-scheduling deliberately out of scope for this pass):
  wave templates with cut-off/auto-release, opportunistic cross-docking,
  BOM-driven kitting (Assemble/Disassemble), slotting & rearrangement, labor
  standards/performance, VAS depth (Packaging-Spec-driven step generation,
  duration capture), and task/activity-based 3PL billing.

This is a complete, tested MVP (243 automated tests), not a certified SAP EWM
replacement — see [Production warning](#production-warning).

## Contents
- [Status](#status)
- [Mental model](#mental-model)
- [Data model](#data-model)
- [The rule engine (how config drives behavior)](#the-rule-engine-how-config-drives-behavior)
- [Numbering (WMS Number Range)](#numbering-wms-number-range)
- [Printing (spool)](#printing-spool)
- [Core flows](#core-flows)
- [Advanced EWM (P4)](#advanced-ewm-p4)
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
  [Shipping/loading](#core-flows) and configuration step 11 below.

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

## Printing (spool)

Mirrors SAP EWM's spool control: printing is entirely config-driven and
opt-in, with the same two-layer split as everything else in this app —
*structure* (what output devices exist) and *rules* (when they print).

- **Output Device = WMS Resource with `resource_type = Printer`.** No
  separate doctype — a printer is just another physical device, like a
  Forklift or a Scanner (see [Resources, Resource
  Groups](#core-flows) above), identified by its `device_id`.
- **WMS Print Determination Rule** (`services/printing._matching_rule`,
  same "priority ascending, first match wins" pattern as [the rule
  engine](#the-rule-engine-how-config-drives-behavior)) maps a
  (`warehouse`, `event`) pair to an `output_device` (validated on save to
  actually be a Printer in that same warehouse) and an optional `print_format`.
  Four events are wired in today: `HU Created`, `Putaway Confirmed`, `Goods
  Issue Posted`, `Shipment Loaded`.
- **`services/printing.create_print_spool`** is called from each of those
  four trigger points (`Handling Unit.after_insert`, `services/task.confirm_task`
  for a fully-confirmed Putaway task, `services/issue.post_goods_issue`,
  `services/shipping.confirm_hu_loaded` once a shipment is fully loaded) and
  looks up a matching rule for that warehouse+event. **No matching rule means
  nothing happens** — exactly like a doctype with no `WMS Number Range`
  configured, this is opt-in per warehouse and per event, not a hard
  dependency the rest of the app breaks without.
- A match creates a **WMS Print Spool** row (`Queued`, referencing whatever
  document raised the event — the Handling Unit, Warehouse Task, Goods
  Issue, or WMS Shipment) instead of talking to a printer directly. This is
  a spool queue, not real printer output: something (a Print Station report,
  a kiosk screen, a background poller) is expected to list `Queued` rows for
  its device (`api/printing.list_queued_spools`) and, once actually printed,
  call `api/printing.mark_printed` (or `mark_failed` with a reason on error)
  — the app ships the queue and its API, not printer hardware integration.

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
rather than looping forever. `approve_variance` (gated by `WMS Supervisor`)
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
into a **WMS Resource Group**
(per warehouse), and a **Warehouse Queue** points at a Resource Group via
its `resource_group` field, rather than at individual resources — that's
what makes the pool swappable without reconfiguring every queue. If a
Warehouse Queue is configured for a warehouse/activity(/storage type), every
`Warehouse Task` created for that activity is attached
(`services/warehouse_order.attach_task`) to a **Warehouse Order** — a batch
of tasks sharing the same `batch_key` (e.g. one putaway request, one
pick-task group) — which is auto-assigned to whichever active `WMS Resource`
on that queue currently has the fewest open Warehouse Orders. A resource
without a standing assignment joins a queue (`join_queue`) and pulls the
next one itself (`pull_next_warehouse_order`, oldest-priority-first). This
is optional infrastructure: a task type with no matching Warehouse Queue is
simply never routed through a Warehouse Order and
behaves as before (assigned/worked directly).

## Advanced EWM (P4)

Seven of SAP EWM's eight Advanced-tier capability areas, each independent and
each opt-in the same way as everything above — configure it and it engages,
leave it unconfigured and the rest of the app behaves exactly as before.
Yard management / transport units / dock appointment scheduling is the one
area **not** built in this pass (`WMS Route Stop.stop_type = "Yard"` stays a
purely descriptive label with no behavior behind it).

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
- **Kitting**: a **Kitting Order** (kit item, a submitted **BOM**, warehouse,
  work center bin, quantity, direction) explodes the BOM's components scaled
  to the order quantity. Completing it (Assemble: consume components →
  produce the kit item; Disassemble: the reverse) posts against the real
  `handling_unit` actually holding each component's stock — not loose — since
  `WMS Stock Balance` is HU-dimensioned, and mirrors to ERPNext as a
  **Repack**-purpose Stock Entry (not "Manufacture", which hard-requires
  backflush rows this flow doesn't produce). Created from the WMS Monitor's
  **Kitting** tab; completed from either that same tab or the RF app's
  **Kitting** action (under Internal), which lists open orders scoped to the
  logged-on operator's own warehouse.
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

## Modules

| Module | Contains |
|---|---|
| `wms_core` | Warehouse/Storage Type/Storage Bin structure (Storage Section, Bin Type), WMS Settings, the WMS Monitor page, the WMS workspace |
| `wms_setup` | Everything in [the rule engine](#the-rule-engine-how-config-drives-behavior): Process/Bin/Warehouse-Process-Type-Determination Rule, Removal Rule, Storage Type Search Sequence, WO Creation Rule, Inspection Rule, process types, movement types, replenishment rules, WMS Number Range, WMS HU Number Pool, WMS Print Determination Rule, plus [P4](#advanced-ewm-p4)'s Wave Template, Labor Standard, and Billing Rate, plus child tables (delivery line items, HU/stock-type bin whitelists, packing source/destination HUs, shipment lines) |
| `wms_inbound` | Inbound Delivery, Goods Receipt |
| `wms_outbound` | Outbound Delivery, Goods Issue, Stock Allocation, Packing Order, WMS Wave, VAS Order (+ VAS Order Activity) |
| `wms_inventory` | WMS Product (+ per-warehouse `WMS Product Warehouse` overrides), WMS Stock Type, WMS Stock Balance, WMS Stock Ledger Entry, Physical Inventory Count (+ Count Tolerance Group, Cycle Count Rule), Quality Inspection |
| `wms_handling_units` | Handling Unit, HU Type, HU Event (audit trail - its `handling_unit`/bin/parent-HU fields are plain Data, not Links, so it never blocks deleting/recycling the HU or bin it once pointed at), Packaging Material, Packaging Spec (+ Packaging Spec Level) |
| `wms_execution` | Warehouse Request, Warehouse Task, Task Allocation, Warehouse Order (queue-assigned batch of tasks), Warehouse Queue, WMS Resource, WMS Resource Group, WMS Print Spool, WMS Exception Code, [P4](#advanced-ewm-p4)'s Kitting Order (+ Kitting Order Component) |
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
   **Include at least one with `storage_role = Door`** — step 11 below can't
   configure a Route's door without a Door-role bin to point it at, and
   Goods Issue can't post without one either.
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
17. **WMS Resource** (`resource_type = Printer`) / **WMS Print Determination
    Rule** — entirely optional: skip both and nothing prints, exactly like
    skipping step 15. Add one Printer Resource per physical device, then a
    rule per (warehouse, event) pointing at it, where you actually want `HU
    Created` / `Putaway Confirmed` / `Goods Issue Posted` / `Shipment
    Loaded` to queue something (see [Printing](#printing-spool)).
18. **[P4](#advanced-ewm-p4) config, all optional**: **Wave Template**
    (warehouse/route, cut-off time, auto-release) for scheduled wave
    generation; **Labor Standard** (warehouse/task type/item group, standard
    seconds per unit) for the KPI dashboard's efficiency numbers; **Billing
    Rate** (warehouse/customer, activity, rate, billing item) before using
    the Monitor's Billing tab. Skip any of these and that specific P4 feature
    simply has nothing to compute from — nothing else in the app depends on
    them.
19. **Roles** — assign the roles below to users; optionally add **User
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
| Hourly | `generate_scheduled_waves` | [P4] Sweeps matching submitted Outbound Deliveries into a new Draft `WMS Wave` per active Wave Template |
| Hourly | `release_due_waves` | [P4] Releases any template-generated Draft wave whose cut-off time has passed |
| Daily | `verify_stock_balance_integrity` | Logs any negative `WMS Stock Balance` rows as an error for review |
| Daily | `verify_erpnext_stock_reconciliation` | Logs WMS vs ERPNext quantity drift per warehouse/product |
| Daily | `generate_scheduled_counts` | [P3] Generates `WMS Physical Inventory Count` documents per active Cycle Count Rule (ABC/Low Stock/Zero Stock/Putaway PI/Bin Check/Annual) |

None of these post anything automatically except replenishment/wave/count
generation — the integrity/reconciliation checks are report-only
(`frappe.log_error`), by design, so they never silently correct the ledger.

## RF / scanner app

`/wms` is a chrome-free, scanner-oriented page (not a desk form) — the RF
frontend at `frappe_wms/www/wms/`, styled after SAP EWM's RF UI. Opening it
first shows a **Log On** screen: if the signed-in user isn't currently logged
on to any WMS Resource, it lists free ones to claim (`api/resource.log_on`)
rather than blocking on an admin having pre-assigned one; once claimed, a Log
Off button (next to the queue controls) releases it again. Only then does
the home menu appear, leading to:

| Section | Action | What it does |
|---|---|---|
| Inbound | Receive | Pick an open Inbound Delivery, scan an HU per line (unknown barcodes auto-register using `default_handling_unit_type`), post the Goods Receipt — which immediately raises Putaway (and, where matched, [P4] Cross Dock) tasks |
| Inbound | Putaway Tasks | Confirm any open Putaway, Unload, Deconsolidation, or [P4] Cross Dock task, or report an exception — every task type shares the same generic confirm wizard (source scan → quantity → destination scan → HU), so nothing here is task-type-specific |
| Inbound | Deconsolidate | Split a received HU's contents across multiple destination bins in one flow |
| Inbound | Quality | Complete an inspection's pass/fail split |
| Internal | Move | Ad-hoc bin-to-bin/HU-to-HU transfer with no planning step |
| Internal | Internal Tasks | Confirm any open Internal Move (including [P4] slotting rearrangement tasks), Posting Change, or Inventory Count task |
| Internal | Close Movement | Advance an HU one hop along its Route's multi-stop journey |
| Internal | Repack | Move whole HUs and/or partial item quantities into a destination HU |
| Internal | Count | Record physical inventory quantities; auto-posts once every line is counted (or holds for recount/approval — see [Core flows](#core-flows)) |
| Internal | Handling Units | Look up, create (scan a barcode, or leave it blank for an Internal HU Type), nest/unnest, block/unblock, or recycle an empty, reusable HU (frees its number for reuse) |
| Internal | Kitting [P4] | List open Kitting Orders for the logged-on operator's warehouse; tap one to complete it (Assemble/Disassemble) |
| Outbound | Picking | Enter a delivery/wave reference to jump straight into picking its tasks |
| Outbound | Pick Tasks | Confirm any open Pick, Stage, or Load task |
| Outbound | Ship | Pick a delivery that's fully picked but not issued, confirm/adjust the suggested loaded HU per line, post the Goods Issue manually — a fallback for whatever the automatic post-on-load (see [Shipping/loading](#core-flows)) hasn't already handled |
| Outbound | Pack | Complete an open Packing Order in one tap |
| Outbound | VAS | Complete open VAS activity steps, or tap "+ Generate from Packaging Spec" [P4] to build a new VAS Order's steps from a scanned HU's item's Packaging Spec instead of typing them in by hand |
| Outbound | Load | Pick a `Ready to Load`/`Loading` Shipment, scan each HU to walk it through the Route's Stops (if any) to the door and mark it loaded, then depart the Shipment once full |
| — | Lookup | HU/bin contents by barcode |

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
  Departed); ERPNext's own **Purchase Order** / **Sales Order** get "Create
  Inbound/Outbound Delivery"; ERPNext's **Work Order** gets "FG Receipt
  (WMS)" [P2] — posts a Goods Receipt sourced from the Work Order and drives
  the normal putaway flow from it (`create_fg_receipt_from_work_order`).
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
    Stock / Create Pick Tasks actions, plus a manual Post Goods Issue action
    once the delivery is Loaded (normally unnecessary - loading already
    auto-posts it, see [Shipping/loading](#core-flows)) — everything needed
    to drive and watch the pick-to-ship lifecycle without leaving the
    Monitor or reaching for the RF app.
  - **Stock Overview** — current `WMS Stock Balance` positions (quantity/
    allocated/available per product/bin/HU/stock type), with a per-stock-type
    summary strip — the current-state counterpart to Stock Movements' history.
  - **Warehouse Tasks**, **Handling Units** — searchable, each its own node.
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
  - **Kitting** [P4] — create a Kitting Order (kit item, BOM, work center
    bin, quantity, direction) and complete open ones.
  - **Billing** [P4] — preview `generate_billing_for_period` for a
    warehouse/customer/date range, then create the Draft Sales Invoice.
  - **Alerts** — counts awaiting supervisor approval (with a bulk "Approve
    Selected" action), aged exceptions, and stalled Warehouse Orders.

  Roles: WMS Supervisor / Administrator / Inventory Controller / Auditor,
  System Manager.

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
