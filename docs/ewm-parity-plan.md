# Implementation plan: closing the SAP EWM gaps

Draft 2026-10-05. Source of the gaps: [app_gap.md](../app_gap.md), section "Comparison with the SAP EWM architecture reference".
Estimates are rough working days for one developer including tests and deployment; they are for ordering the work, not a promise.

## Working rules (apply to every item)

1. **Per item:** read the SAP behaviour -> short design note in the PR/commit -> doctype/service change -> a test that drives a real flow (not just the function) -> full suite at its known baseline -> deploy.
2. **Defaults keep today's behaviour.** A new indicator, rule or flag that is not configured must change nothing. New enforcement checks only fire when the master data asks for them. (Lesson from the bin-type work: do not compare fields that existing data fills for another purpose.)
3. **Schema changes** that replace a field (e.g. `hu_managed` -> HU requirement) go with a patch in `patches/v0_2`, run on dev against a copy of production data first, and keep the old field readable for one release.
4. **Deploy order:** backup -> pull in the five containers -> `bench migrate` -> clear cache -> restart all five -> verify the new doctypes/fields exist and the scheduler is quiet.
5. **Configurator:** every new setup doctype is added to `wms_configurator/tools/extract_schema.py` (re-generate both copies) and `setup/import_profile.py`.
6. **Desktop vs RF:** planning/staging/maintenance screens are desk Pages or doctype forms; the RF app only executes tasks.
7. Known baseline: 6 failures + 1 error in the suite come from dev-DB resource pollution, not from the code (see PLAN.md); a new failure outside that list is a regression.

## Phase A - Product control indicators and determination (about 10 days) - DONE 2026-10-05

Shipped: A1, A2, A3, A4, A6 (HU requirement, HU type check switch, max quantity capacity, default putaway strategy, identification point), A7 defaults (storage type/bin, queue, priority), A5 max quantity per storage type and the two-step flag. **Not built (deferred):** velocity code, warehouse product group, nesting factor (A5); default *removal* strategy on the storage type (A6); rough bin determination and the extra determination-matrix keys document type / item type / process / control indicator (A7). Decisions taken as in SAP: the control indicator is the primary mechanism and `preferred_storage_type` stays only as the last fallback.

The core of SAP's putaway/removal search; everything else in storage determination hangs off it.

| Item | Change | Test |
| --- | --- | --- |
| A1 Control indicator masters | New `Putaway Control Indicator`, `Stock Removal Control Indicator`, `Storage Section Indicator` (code, name). Fields on `WMS Product Warehouse` (per warehouse) with a fallback on `WMS Product`. | Product with PCI resolves the indicator per warehouse. |
| A2 STSS keyed by indicator | `Storage Type Search Sequence` gets `putaway_control_indicator` (+ activity/direction). `determine_destination_bin` picks the sequence by indicator, then stock type; falls back to today's rule. | Two products with different PCI land in different storage types through one rule. |
| A3 Section indicator | `Storage Section` gets `section_indicator`; the bin search filters sections by the product's indicator. | Product with indicator X only gets bins of sections with X; no indicator = unchanged. |
| A4 SRCI on removal | `Removal Rule` and removal-side search sequence take the stock removal indicator. | Same product, two indicators, two removal storage orders. |
| A5 Product extras | Max quantity per storage type (child on product-warehouse), HU-type-check indicator, two-step-picking flag (feeds picking strategy), velocity code, warehouse product group. | Max quantity caps the putaway into that storage type. |
| A6 Storage type | Tri-state **HU requirement** (Forbidden/Optional/Mandatory) replacing `hu_managed` (patch + keep reading the old flag); capacity methods *Max Quantity* and *Bin Type Capability*; storage-type default putaway/removal strategy used when no rule matches; role *Identification Point*. | Forbidden HU rejects an HU putaway; Optional accepts both; patch maps existing `hu_managed`. |
| A7 Warehouse process type | Default source/destination storage type and bin, default queue and priority, rough-bin-determination flag. Determination rule adds document type, item type, process indicator, control indicator. | Process type picks default destination when the request has none. |

Dependencies: A6 touches `bin_rules.bin_violations` (shared by planning, confirm and moves) - do it after A2/A3 so one test file covers the combined behaviour. Decision needed: whether PCI replaces `preferred_storage_type` or sits beside it (recommendation: beside, PCI wins when set).

## Phase B - Production supply completion (about 9 days)

| Item | Change | Test |
| --- | --- | --- |
| B1 Control cycle | New `Production Supply Control Cycle` (warehouse, PSA, product -> staging storage type/bin, replenishment method, min/max). PMR item staging uses the cycle's bin; PSA gets a bins table (supply bins) instead of one `supply_bin`. | Two materials of one PMR staged to different bins of one PSA. |
| B2 Crate parts | Min/max per cycle; confirmed consumption below minimum raises a replenishment request independent of any PMR (extends `run_replenishment_check`). | Consumption below min creates a request up to max. |
| B3 Direct consumption | Consumption booked from the bin where the material is (no staging), reserved to the PMR item. Hook in `consume_from_stock_entry` when the PSA stock is short and the cycle allows it. | Consume with nothing staged posts from the source bin. |
| B4 Return unused | Desk action on the staging page: create tasks from the PSA back to storage for staged-minus-consumed of a PMR item, reduce `staged_quantity`/`tasked_quantity`; `close_pmr` offers it. | Close with leftover creates return tasks and releases the reservation. |
| B5 Deconsolidation hop | Wire the PSA's optional deconsolidation work center into staging as an intermediate step (reuse Layout Storage Control mechanics: first leg to a work-center location, second leg to the PSA). | Mixed-source staging goes via the location, then to the PSA. |
| B6 Planned/release-order-parts view | Staging page: aggregate by product across PMRs for a time window, one movement per product (today manual cross-order). | Aggregated task covers three PMR items. |

Dependencies: B1 before B2/B3. Decision needed: one PSA = several bins (SAP: storage type + section + bin) or several PSAs per area (recommendation: bins table).

## Phase C - Execution control and exceptions (about 10 days)

| Item | Change | Test |
| --- | --- | --- |
| C1 Exception actions | `WMS Exception Code` gains business context (RF task, desktop task, packing, count) and a configured system action: change bin, split task, skip, post difference. RF/desk execute the action instead of a hard-coded list. | Each action applied to a task (bin changed, task split into confirmed + open, difference posted). |
| C2 Block reason codes | `Storage Bin`/HU block takes a reason code master; shown in the Monitor. | Block without a code refused when the setting is on. |
| C3 Automatic replenishment | A confirmed removal that drops the pick bin below min raises the request immediately (not only the hourly scan). | Confirm a pick below min -> request exists in the same transaction. |
| C4 Wave control | Template fields: release threshold, lock time; wave split action; collective retrieval as one combined task for two-step picking. | Split a wave; threshold blocks an early release. |
| C5 WOCR | Grouping by activity area/consolidation group, item filters (hazard/weight), sort by the area's bin sequence, pick-HU calculation from the packaging spec. | Tasks of two areas end in two orders, ordered by bin sort. |
| C6 Queue by door/staging | Queue determination also looks at door/staging area. | Outbound staging tasks queue by door. |
| C7 Pick-time zero stock check | Emptying a bin on a pick raises a zero-stock count. | Short pick creates the count. |
| C8 Planned cross-docking | Cross dock request created from an expected inbound delivery against open outbound demand before receipt. | Planned demand reserved, receipt routes to staging. |

## Phase D - Topography, labor and handling units (about 8 days)

| Item | Change | Test |
| --- | --- | --- |
| D1 Bin coordinates | X/Y/Z, access type, fire containment section on `Storage Bin`; bulk-storage stack height and lane depth. | Bulk bin refuses a stack above the limit. |
| D2 Bin sorting per activity | `Activity Area` child table (activity -> bin sort sequence); task `sequence` comes from the area/activity, not the single bin sequence. | Pick and putaway order the same bins differently. |
| D3 Travel distance | Manhattan distance from coordinates (network distance later); used by sort and by Labor. | Distance of a task chain. |
| D4 Labor formula | Labor Standard gains base allowance, travel factor, handling factor (weight/volume), PF&D; planned vs actual uses it. | Planned seconds of a known order. |
| D5 HU extras | Outer dimensions and max payload on the HU, HU type group, Planned/In-Transit statuses. | Payload check on packing. |
| D6 Packspec condition technique | Packaging Spec determination by product + customer/vendor. | Customer-specific spec wins over the default. |

## Phase E - Decision-gated work (not scheduled)

Large or business-dependent; decide before any work starts.

| Item | Why gated | Effort if approved |
| --- | --- | --- |
| E1 Owner / Party Entitled to Dispose on stock | New dimension on `WMS Stock Balance` and ledger (migration of every balance key, locking, billing by owner). Needed only for 3PL / vendor-managed stock. | 10-15 days |
| E2 Document model (request -> order -> final) and configurable document types | Touches every inbound/outbound flow and the ERPNext replication; only worth it if you need status profiles/field control per document type. | 15+ days |
| E3 General Post Processing Framework | A condition/action engine over events; today print + ERP sync cover the real cases. | 8 days |
| E4 Transportation Unit entity with yard tasks | Appointments/shipments already cover the check-in/door flow. | 6 days |
| E5 Quality sample management and usage-decision tasks | Needs the inspection process agreed. | 6 days |
| E6 Strategy-override hooks (putaway, WOCR, queue) | Extension points for other apps; build when a second app needs one. | 3 days |
| E7 Cube/consumption analytics layer | Monitor KPIs cover the daily need. | 5 days |
| Out of scope | MFS/PLC, RS232 scale integration (revisit with hardware). | - |

## Sequence and milestones

1. **Phase A** (indicators, storage type, WPT defaults) - unlocks accurate putaway/removal; ship A1-A3 first, A6 last.
2. **Phase B** (production supply) in parallel with A once A1 exists, because B1 reuses the indicator masters' patterns; B4 and B5 are the most visible to production users.
3. **Phase C** items are independent; order by what hurts on the floor: C1, C3, C7, then the wave/WOCR items.
4. **Phase D** after C5 (sort sequence feeds WOCR).
5. **Phase E** only on a business decision; E1 and E2 are the two that change the data model.

Total scheduled work (A-D): about 37 working days. Each phase ends with a deployment and a short production check list (the new doctypes exist, the scheduler is quiet, one real scenario through the changed area).

## Open decisions

1. PCI beside or instead of `preferred_storage_type` (A).
2. One PSA with several bins, or several PSAs (B).
3. Is 3PL / multi-owner on the roadmap (E1)? It decides whether the owner dimension is built early, because adding it later migrates every balance.
4. Do you need configurable document types and the request/order/final split (E2), or is the replicated delivery enough?
