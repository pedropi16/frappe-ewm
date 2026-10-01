# WMS RF App load-test & hardening — status and plan

Last updated 2026-09-30. This tracks an ongoing engagement, separate from `app_gap.md` (an
unrelated earlier SAP-EWM-parity roadmap) — this one is specifically about driving the `/wms` RF
app under realistic, concurrent load against **production** and fixing whatever breaks.

## The standing task

> I need to create load, from start to finish, using the RF UI only — users picking, doing
> inbound, creating handling units, moving products, repacking, outbound etc. The load needs to be
> realistic and click and use the inputs, and record problems, bad usability trends, and flow
> errors. Draft a plan to implement fixes. Use production for the test. Concurrent is fine. Use
> whatever data is available — create customers/suppliers/products/POs/SOs as needed. Keep all
> generated data on the site after the test. Keep fixing bugs and running tests — create more
> products, tasks, documents, and try to make a full cycle.

Full running findings/fix-plan doc (updated throughout, read this first for narrative detail and
rationale behind every fix below):
**https://claude.ai/artifact/Pk1oamYSGrRxmvkVw1H1gc**

## Environment

- **Production**: `erp.pinohomelab.duckdns.org`, docker compose stack — containers
  `frappe-backend-1`, `frappe-frontend-1`, `frappe-scheduler-1`, `frappe-queue-short-1`,
  `frappe-queue-long-1`, plus `redis-queue-1`/`redis-cache-1`/`db-1`. Image `frappe/erpnext:v16.34.2`.
  Containers pull this repo from git remote `upstream` (site name inside the containers:
  `erp.pinohomelab.duckdns.org`).
- **Dev bench**: `~/frappe-bench` (site `wms.local`), `apps/frappe_wms` symlinked to
  `~/frappe-ewm/frappe_wms` in this repo. Always run the test suite here before deploying.
- **This repo**: remote `origin` → `github.com/pedropi16/frappe-ewm.git`. Production containers'
  `upstream` remote points at the same repo.
- **Standing deploy policy**: deploy to production by default once dev tests pass, without waiting
  to be told — still pause before unusually risky/destructive actions.

### Deploy routine

```bash
# 1. Test on dev first
cd ~/frappe-bench && bench --site wms.local run-tests --app frappe_wms

# 2. Pull into every production container
for c in frappe-backend-1 frappe-frontend-1 frappe-scheduler-1 frappe-queue-short-1 frappe-queue-long-1; do
  docker exec "$c" bash -c "cd apps/frappe_wms && git pull --ff-only upstream main"
done

# 3. If the commit added/changed a migration patch:
docker exec frappe-backend-1 bash -c "bench --site erp.pinohomelab.duckdns.org migrate"
# (a patch only ever auto-runs ONCE per site — to re-run an idempotent patch after fixing a bug in
# it, invoke its execute() directly instead of through migrate, e.g.:
#   docker exec frappe-backend-1 bash -c "cd /home/frappe/frappe-bench && bench --site erp.pinohomelab.duckdns.org execute frappe_wms.patches.v0_2.<patch_name>.execute")

# 4. Otherwise just clear cache + restart
docker exec frappe-backend-1 bash -c "bench --site erp.pinohomelab.duckdns.org clear-cache"
for c in frappe-backend-1 frappe-frontend-1 frappe-scheduler-1 frappe-queue-short-1 frappe-queue-long-1; do
  docker restart "$c"
done
sleep 8 && curl -s -o /dev/null -w "%{http_code}\n" https://erp.pinohomelab.duckdns.org/api/method/ping
```

Frontend JS/CSS changes need `frappe-frontend-1` specifically pulled/restarted too (not just
backend) — and the reverse proxy in front of production caches static assets for ~30 minutes, so a
frontend change can look "not deployed" for a while after a correct deploy. Don't chase that as a
bug; wait it out or bust the cache path.

## What's been fixed and deployed this session (chronological)

1. **P0 permission cascade** blocking receive/ship for WMS-role-only users (ERPNext mapper
   functions' internal permission checks) — `4bed73c`, `c8d7ffa`.
2. **Putaway tasks never queued** — `attach_task` read `source_bin` first for every task type, but
   a Putaway's source is always the generic receiving dock, never the zone its queue is scoped to.
   Fixed to read `destination_bin` first for Putaway — `dbf52d1`.
3. Two bugs found under **real concurrent load** (parent/child HU nesting on Pick,
   `QueryDeadlockError` under real DB contention) — `d8e81b9`, corrected/finalized in `f804e66`
   (first attempt used a per-candidate savepoint, which MySQL can discard wholesale when it picks
   that transaction as the deadlock *victim* — second bug, fixed by a plain `rollback()` + return
   None instead).
4. **RF Receive** couldn't originate a brand-new batch/serial (only accept already-registered
   ones) — added `_get_or_create_batch`/`_get_or_create_serial_no` plus a `serial${i}` field —
   `3afe7b2`.
5. **Orphaned Putaway tasks**: any task created before fix #2 above landed with
   `warehouse_order`/`queue` permanently blank — RF still listed them as workable "Open" tasks, but
   `pull_next_warehouse_order` could never serve them. Backfill migration patch, verified live via
   screenshot (tasks now correctly grouped under real Warehouse Orders) — `27e29e4`.
6. **The big one**: `create_and_submit_goods_receipt` never wrote `received_quantity`/`status`
   back onto the source Inbound Delivery. The Receive screen's own line filter
   (`remaining = expected_quantity - received_quantity`) kept re-offering an already-fully-received
   delivery as open work *forever*, and a repeat attempt eventually hit the underlying PO's real
   exhaustion, surfacing as a confusing, unrelated-looking "No matching Purchase Order rows found"
   — the actual root cause of that error recurring across this *entire* engagement, including
   `INB-00000001`'s broken state from day one. Fixed in `services/receipt.py`
   (`_update_inbound_delivery_receipt_progress`) — `fc0d696`, backfill patch for pre-existing
   affected deliveries — `c2cd8ce`, then a second-order fix: the first version used `doc.save()`,
   which throws `UpdateAfterSubmitError` against an already-submitted Inbound Delivery (a real,
   confirmed case — `INB-00000001` again) — switched to `frappe.db.set_value` throughout, which
   works uniformly regardless of docstatus — `5c8368a`. **Verified live: a full end-to-end run now
   produces zero occurrences of the "No matching PO rows" error.**
7. **`my_resource()`** (`services/task.py`) picked an unordered, arbitrary WMS Resource when a user
   had more than one active one bound — now deterministic (`order_by="modified desc"`) — part of
   `5c8368a`. Does **not** clean up the dev site's own accumulated test-pollution resources (see
   Known issues below).

All of the above are deployed to production and verified. Dev test suite baseline after every one
of these: **7 failures + 1 error** (pre-existing `my_resource()`-pollution artifacts on the dev
site only — see Known issues; not a regression, unchanged across all these commits).

## Current production test data (as of 2026-09-30)

- Suppliers: `Pino Motors`, `Pino Encuadernaciones`, `Pino Chemicals`.
- Customers: `Andres's Robotics`, `Vega Logistics`.
- Items include the original `LT-*` set plus `LT-GUANTE-NITRILO`, `LT-DESENGRASANTE` (batch-
  controlled), `LT-KIT-EMBALAJE`, `LT-JUEGO-LLAVES` (both in item group **Products**, routed
  through a new Pick-Pack-Pass Warehouse Process Type — see below).
- `IBD-00000006` (Pino Chemicals, `LT-GUANTE-NITRILO` + `LT-DESENGRASANTE`) was still open/
  receivable as of the last run — good starting point for the next run.
- A Pick-Pack-Pass setup exists: Warehouse Process Type `OB_PPP` + two
  `Warehouse Process Type Determination Rule` rows (priority 150, activity `Pick`, one per item —
  **must be item-scoped, not item_group-scoped**: `_create_pick_task_for_group` in
  `services/task.py` never passes `item_group` into `determine_process_type`'s context at all, so
  an item_group-scoped rule silently never matches — learned this the hard way, cost a full extra
  round-trip).
- 5 dedicated load-test RF accounts/resources already exist and don't need recreating:
  `loadtest.{ana,bruno,carla,diego,elena}@pinohomelab.test` → `LOADTEST-RF{1..5}`. Password is not
  stored in this repo — set it as `LOADTEST_PASSWORD` before running (same one used throughout
  this engagement; ask the user if it's not already in your environment/memory).

## The load test script

`frappe_wms/tests/e2e/loadtest/production_loadtest.js` — Playwright, drives the real `/wms` RF UI
for 5 concurrent personas through receive → putaway (concurrent race) → ad-hoc move/repack →
release-for-picking → pick (concurrent race) → pack setup → ship, logging every finding
(`BUG`/`FLOW`/`PERF`/`INFO`) to console and to `<shots dir>/findings-*.json`, with a screenshot at
every major step.

```bash
cd frappe_wms/tests/e2e/loadtest
LOADTEST_PASSWORD='<the password>' node production_loadtest.js
```

It needs Playwright's Chromium available; if the sandbox is missing system libs (this dev
environment needed both an explicit `CHROMIUM_EXECUTABLE_PATH` *and* `LD_LIBRARY_PATH` set on the
`node` invocation itself to get past `error while loading shared libraries: libasound.so.2` and
similar) — that's environment-specific, diagnose fresh in whatever sandbox the cloud session runs
in rather than assuming the same workaround applies.

**Before your next run**, update the delivery names in Stage 1's `receiveAll(...)` call (near the
top of the IIFE) to whatever's actually still open — check with:

```python
# bench --site erp.pinohomelab.duckdns.org console (or execute a small script)
import frappe
frappe.get_all("Inbound Delivery", filters={"status": ["in", ("Draft","Expected","Arrived","Receiving","Partially Received")]}, fields=["name","status"])
```

A delivery whose backing PO is already fully received will just no-op (0 lines), not error — but
better to point it at genuinely fresh data. Create more via a small one-off script (see the pattern
in the findings doc's "wipe and reseed" section) rather than hand-editing existing records.

## Pending / next steps

Roughly in priority order:

1. **Finish exercising Pick-Pack-Pass → Pack.** The rule fix (item-scoped, not item_group-scoped)
   landed but wasn't run through a full clean cycle yet — `LT-JUEGO-LLAVES` still needed a Putaway
   confirm as of the last run. Confirmed finding already banked either way: **there is no API or
   RF path to create a Packing Order at all** (not a missing RF *screen* like other gaps — a
   missing *capability*, full stop; even the raw `frappe.client.insert` fallback 403's for a plain
   WMS Operator/Supervisor role on the Handling Unit doctype). Recommend a dedicated
   `create_packing_order` service/API function mirroring how `release_delivery_for_picking` already
   wraps `allocate_delivery` + `create_pick_tasks` — **don't** just grant raw Handling Unit
   permissions, HUs should stay created only through validated service functions.
2. **Sort/Stage/Load task types** — never genuinely exercised (Stage 6 checks for queues beyond
   Putaway/Pick and has found none configured every run so far). Worth setting up a process type
   that actually uses them, same spirit as the Pick-Pack-Pass setup.
3. **Quality Inspection flow** — `Inspection Rule` exists and P2 shipped auto-creation at GR, but
   this load test has never triggered one (no configured rule matches the current test items/
   warehouse). Set one up, receive against it, confirm the RF app can actually process it.
4. **Multi-line Sales Order fulfillment** beyond a single item — most test SOs so far are 1-2 lines
   fully allocatable; try a larger, partially-allocatable one to exercise short-pick/backorder
   behavior.
5. **Serial-controlled items can't be received in quantity >1** (finding, not yet fixed) — RF
   Receive defaults a line's quantity to the full remaining amount but offers only one serial
   input per line, and the backend requires each serial to move exactly 1 unit. Needs either a
   repeatable per-unit serial entry UI or a "split this line into N single-unit receipts"
   affordance.
6. **Move's destination HU field can't register a new HU** (finding, not yet fixed) — unlike
   Receive's HU field (explicit "If new HU, type" selector), Move's `destination_hu` only accepts
   an already-registered HU; scanning a fresh barcode is rejected as "not a known code". Moving
   stock into an HU-managed bin with no HU already in hand is a dead end.
7. **Intermittent `CSRFTokenError`** under concurrent RF sessions — seen a handful of times across
   many runs (e.g. on `resolve_scan`, `my_tasks`), not root-caused. `core/api.js` already has a
   stale-CSRF refresh-and-retry wrapper from an earlier fix; this may be a gap in that logic under
   genuine concurrency, or environmental (Playwright cookie jar). Worth a focused investigation if
   it keeps recurring.
8. Keep the findings doc current as you go — it's the living record of this engagement, read it
   before making assumptions about what's already known.

## Known issues (not blocking, don't re-litigate)

- **Dev-site `my_resource()` pollution**: the dev bench (`wms.local`) has accumulated many WMS
  Resource records bound to `Administrator` across a very long test-writing session, from before
  test isolation was fully consistent. This causes a **stable baseline of 7 failures + 1 error**
  in `bench --site wms.local run-tests --app frappe_wms` (all `list_open_*`/`my_tasks`-style
  functions whose warehouse-scoping goes through `my_resource()`). Confirmed via `git stash`
  multiple times this session: these failures pre-exist and are unrelated to any change made.
  Don't chase them as regressions from new work — verify via `git stash` if ever in doubt whether a
  new failure is really new. A real fix would mean cleaning up the accumulated dev DB data (or
  making test fixtures more rigorously self-scoped), not touching `my_resource()`'s logic further.
- **Auto-mode / sandbox permission classifier**: in this session's environment, an automatic
  safety classifier intermittently blocked `bench execute`/`docker exec` calls against both the
  dev bench and production, sometimes even for pure reads, with inconsistent reasoning. A cloud
  session may run under entirely different tooling/permissions — don't assume the same friction
  applies, but if something structurally similar shows up, it's a session/sandbox-level policy,
  not something to route around cleverly; ask the user.
