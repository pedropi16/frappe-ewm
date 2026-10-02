# WMS RF App load-test & hardening — status and plan

Last updated 2026-10-02. This tracks an ongoing engagement, separate from `app_gap.md` (an
unrelated earlier SAP-EWM-parity roadmap) — this one is specifically about driving the `/wms` RF
app under realistic, concurrent load against **production** and fixing whatever breaks.

## ⚠️ Operational hazard — `frappe_wms` is not durably installed on production

**Confirmed live 2026-10-01:** when Portainer recreated `frappe-backend-1` to apply a
`GUNICORN_WORKERS` env change, the fresh container came up with `apps/frappe_wms` completely
missing - the app was only ever `git clone`'d into the container's writable layer by hand during
earlier deploys, never baked into the image or placed on a persistent volume. The site's own DB
still listed `frappe_wms` as installed, so every WMS-touching request would have failed outright
until it was manually re-cloned (`git clone https://github.com/pedropi16/frappe-ewm.git`, placed at
`apps/frappe_wms`, remote renamed `origin`->`upstream` to match the other containers, then
`./env/bin/pip install -e apps/frappe_wms`, cache clear, restart). **Any future container
recreation - a Portainer redeploy, an image update, a host reboot that drops the container - will
silently repeat this** until the backend image/volumes are changed to make the app persistent (bake
it into a custom image, or mount `apps/frappe_wms` from a volume that survives recreation). Treat
"site responds to `/api/method/ping` with 200" as necessary but **not sufficient** evidence WMS
itself is working after any container-level change on production - verify with something that
actually imports `frappe_wms` (e.g. `bench execute` against a function in it) before trusting the
site.

## Resolved: gunicorn worker starvation

Production's backend used to run with only `GUNICORN_WORKERS=2`, `GUNICORN_THREADS=4` (8 concurrent
request slots total). A 2026-10-01 50-actor run (20 real RF browser sessions in safe waves of 6 + 30
lightweight API desk actors) found this reliably stalled **every** Inbound Delivery page load under
just 6-way concurrent RF traffic - 15-30+ second waits, zero receipts posted, the entire downstream
cycle never got to run. Confirmed as an infrastructure capacity finding, not an app bug. **Fixed**
by the user via Portainer (`GUNICORN_WORKERS` 2 -> 4, confirmed live: 1 master + 4 workers) - this
is what triggered the container recreation that caused the hazard above.

## Resolved 2026-10-02: the whole "~30s stall under concurrent load" saga

The 2026-10-01 gunicorn fix above did **not** make the scaled test pass cleanly. Every rerun after
it still showed every RF persona's "open delivery" page load stalling for ~30s under concurrent
load, with the *exact* same symptom repeating no matter what was tried (bumping timeouts, giving
each persona its own browser process, wiping and reseeding production again). Chased it the hard
way, through several wrong turns, before finally nailing it down with live-instrumented
timestamps. Writing up the whole chain here because every wrong turn is a trap the next session
could fall into again.

**It was never the server.** Proven conclusively and repeatedly: direct `curl`, a hand-built raw
HTTP/2 client, and `bench execute` timing all showed gunicorn, both nginx layers, Redis and MariaDB
answering the exact same 4-to-20-way concurrent load in **15-60ms**, every time, throughout. The
~30s number was *always* a client-side/test-script artifact, not a backend one — it just took a
long chain of fixes to find every layer of artifact stacked on top of each other.

1. **Self-inflicted: most loadtest accounts had stale/mismatched passwords** from earlier sessions,
   different from what the current test script assumed. Logins silently failed with 401s, landing
   on the login page instead of `/wms` — which looks exactly like a server hang, because the test's
   own "has it settled?" check just sits there waiting for CSS classes that never appear on a login
   page. **Fixed**: reset all 50 loadtest accounts (`loadtest.{ana..teresa}@...`,
   `loadtest.desk{01..30}@...`) to one known password via `frappe.utils.password.update_password`.
2. **Real, but secondary: running several Playwright browser *contexts* in one shared Chromium
   process genuinely causes internal contention** on this 6.9GB host — confirmed directly: 3-4
   separate full browser *processes* (one per persona, ~607MB RSS each measured) settled in
   50-90ms every time; the same personas sharing *contexts* in one process did not. **Fixed**:
   `scale_loadtest.cjs`'s `runWave()` now launches one `chromium.launch()` per persona instead of
   one shared browser with N contexts; `WAVE_SIZE` dropped 6→4 to fit the new per-process memory
   budget rather than the old context-count ceiling.
3. **The actual ~30s mechanism, finally pinned down with instrumented timestamps**:
   `waitForSettled()`'s check for the empty-state used `page.locator(".empty").first().textContent()`
   — and `.textContent()` is an auto-*waiting* Playwright action, not a presence check. On a
   locator matching zero elements it silently retries for Playwright's own hidden **default 30s**
   before giving up, which the helper's own `.catch(() => null)` swallowed without a trace. The
   `receiving_worklist` API call it was "waiting on" had already answered in 37-57ms, every single
   time — proven by instrumenting `window.fetch` directly inside the page and dumping the actual
   `#app` HTML mid-poll (it showed the real, correctly-rendered screen within ~1-2s). **Fixed**:
   added `textOrEmpty()` (checks `.count()` before ever calling `.textContent()`) and switched every
   call site (`waitForSettled`, `noticeText`, the two inline `.empty` reads in `receiveAll`) to it.
4. **A second, independent bug exposed once #3 stopped masking it**: `waitForSettled()`'s default
   line-selector is `.line-head` — correct for `count.js`/`ship.js`, which render their detail
   lines with that literal class — but **wrong for `receive.js`**, which renders every line through
   the shared `Card()` component (`ui/kit.js`), i.e. `.card`, never `.line-head`. The receive screen
   had been rendering correctly and fast the entire time; the test just never recognized it, so it
   ran to its own full timeout on *every single delivery*, 100% of the time. **Fixed**: made the
   line-selector a parameter (default stays `.line-head`), `receiveAll()` passes `.card` explicitly.
5. **A third, independent bug exposed once #4 stopped masking it**: the receiving loop assumed
   per-line indexed fields (`data-fk="hu0"`, `"hu1"`, ...) and a `type${i}` select that don't exist.
   The real `receive.js` UI is one line at a time: tap an open (non-`.dim`) `Card` to select it,
   which renders a single generic `entryView` (`data-fk="hu"`/`"batch"`/`"serial"`/`"qty"`, no
   index), fill what that line needs, tap "Add to receipt", the list re-renders, repeat — only then
   does "Post receipt (N rows)" submit everything together. **Fixed**: rewrote the per-line loop in
   `receiveAll()` to match this (click open card → wait for `[data-fk="hu"]` → fill batch/serial/qty
   as needed, detected by presence on screen, not guessed from the line's label text → "Add to
   receipt" → repeat → "Post receipt"). **Verified live: real `Goods Receipt` docs posted
   (`GR-00000010` etc.), each creating real Putaway `Warehouse Task`s, confirmed directly in the DB**
   — not just a green test-script log line.

## Resolved 2026-10-02: putaway/pick "0 tasks confirmed" every wave

Separate investigation, same session, after #1-#5 above were already fixed and receiving was
working. Every wave's "Putaway race"/"Pick race" reported **0 tasks confirmed**, even though real
Putaway tasks genuinely existed (confirmed via `Warehouse Task` counts — 13 `Confirmed`, 13
`Assigned`, 124 correctly `On Hold` waiting on their Warehouse Order's own sequencing, which is
correct by-design behavior, not a bug).

1. **A real, confirmed application bug**: `pullWork()` (`tasks.js`) does
   `if (wo === undefined) return;` to detect `run()`'s own exclusive-busy guard silently
   no-op'ing (never calling the wrapped `fn`). But a *successful* call to
   `pull_next_warehouse_order()` that genuinely finds no work returns Python `None`, which Frappe's
   API layer serializes as a response body with **no `message` key at all** — confirmed with a raw
   `curl` POST to the endpoint, response body was literally `{}`. `api()`'s `once()` just returns
   `data.message`, i.e. `undefined` — **indistinguishable from the busy-guard case on the client**.
   The result: tapping "Get next work" on a genuinely empty queue showed *nothing at all* — no
   notice, no navigation — identical to the button being broken. **Fixed**: removed the faulty
   `wo === undefined` early-return in `tasks.js`'s `pullWork()`; deployed to all 5 production
   containers (`git pull upstream main` in backend/frontend/scheduler/both queue workers — pure JS,
   no `bench migrate`/restart needed since the frontend serves `/assets/` directly) and verified the
   new content was actually being served (`curl .../assets/frappe_wms/js/wms_rf/screens/tasks.js`)
   before retesting. **Verified live: "Get next work" on an empty queue now correctly shows "queue
   empty after confirming N task(s)" immediately, every wave, and a wave with real open work
   (Carla's) genuinely confirmed 9 real tasks before correctly reporting empty.**
2. **Two test-script races stacked on top of #1**, same family as the receiving bugs above —
   `joinQueue()` only slept a flat 400ms after clicking a queue button, racing `session.js`'s own
   `act()` (whose `notify.ok()` fires only *after* `run()`'s `finally` has cleared `S.busy` — a
   module-level flag, so a still-in-flight join can make the very next "Get next work" call hit its
   OWN busy-guard and silently no-op, independent of bug #1). And `driveTaskWizard()` read the route
   hash once after a flat 300ms sleep per step instead of polling for it to actually change,
   occasionally re-processing (or double-submitting a scan into) the same step and burning its
   6-step guard without the wizard genuinely progressing. **Fixed**: `joinQueue()` now waits for
   the "Leave queue" button to actually appear (confirming `current_queue` is set, i.e. `act()`'s
   round-trip is done) instead of a flat sleep; `driveTaskWizard()` now polls for the hash to change
   before reading the next step, and returns as soon as the *one* task it was given is confirmed
   instead of trying to keep driving a Warehouse Order's auto-chained next task against the same
   6-step guard. `pullAndWorkLoop()`'s outer loop also now only increments its own `confirmed`
   counter when `driveTaskWizard()` actually returns `"confirmed"`, not unconditionally.

## Resolved 2026-10-02: two more test-script races (post-submit nav, Count's empty-scope redirect)

Found during a final validation pass after the above; both are test-script-only, no app changes.

1. **`receiveAll()`'s "Post receipt" step only slept a flat 1000ms** after tapping the button.
   `submit()` (`receive.js`) on success calls `finishFlow(..., "#/tasks/inbound", ...)`, auto-
   navigating away — the very next loop iteration's `goto()` to a *different* delivery could fire
   mid-transition and occasionally pick up leftover Putaway-task `.card` elements before the real
   navigation replaced them, surfacing as a confusing `"Line X does not belong to Inbound Delivery
   Y"` validation error (a stale card reference from the test, not a real data-integrity bug).
   **Fixed**: now polls for either the hash to actually leave the current delivery, or a new
   notice to appear (submit failed validation, stayed put) — up to 15s — before reading the result.
2. **`stageCount()` waited on `.line-head` alone** after opening a count, same family as receiving
   bug #4 above: `count.js`'s `enter()` redirects straight back to `#/count` (the list screen,
   rendered via the shared `listScreen()` helper using `.card`/`.empty`, never `.line-head`)
   whenever `snapshot_count` finds no stock in the count's scope — an expected, common outcome
   (`ValidationError: No stock found for the given count scope`, HTTP 417), not a hang. Waiting on
   `.line-head` alone can't tell that apart from a genuine stall; it just burns the full 15s either
   way. **Fixed**: now distinguishes "redirected back to `#/count`" from a genuine timeout by
   watching the hash, not just the one selector.

**Net effect of this whole chain, verified in one final clean run**: `BUG` count dropped from a
peak of 59-60 (almost entirely test-script false positives) down to 40 — all genuinely remaining,
expected business-validation outcomes (count scope with no stock yet, desk actors trying to
allocate against stock that hasn't been put away yet). Zero `"screen never settled"`, zero
`"does not belong to Inbound Delivery"`, zero silent "0 tasks confirmed" with no explanation.
**Real `Goods Receipt`s posted, real `Warehouse Task`s confirmed via the actual RF task wizard,
real queue-empty detection — the full receive → putaway cycle genuinely works under concurrent
load.** Not yet re-verified end-to-end for pick/count/ship beyond what's described above — see
Pending below.

**Not pursued (low-value, almost certainly not real bugs)**: a couple of `HU ... already nested`
and `does not belong to Inbound Delivery IBD-00000034 (cancelled)` errors surfaced during this
session's many reruns — traced to this session's own reused test data (an HU nested in a prior
run, one delivery cancelled the day before in an earlier session) rather than anything newly
broken. If they recur against genuinely fresh data, revisit; otherwise this is test-data staleness
from running the same script many times in one sitting, not a finding.

## Test-script notes for next time (`scale_loadtest.cjs`)

- Needs `LOADTEST_PASSWORD` (all 50 loadtest accounts now share one password — reset via
  `frappe.utils.password.update_password` if it ever drifts again; the password mismatch above is
  the single most time-expensive mistake in this whole engagement, worth checking *first* if
  anything looks like a stall) and `LOADTEST_IBD_NAMES` (comma-separated, however many Inbound
  Deliveries are currently open — check first, same as the older `production_loadtest.cjs` note
  below still applies).
- Set `LOADTEST_DEBUG_TIMING=1` for verbose per-call timestamp logging (`window.fetch` wrapped
  inside the page, every `waitForSettled` iteration's actual timing) — this is what cracked bug #3
  above open; leave it off for normal runs, the overhead and log volume aren't worth it otherwise.
- `WAVE_SIZE = 4` (one Chromium *process* per persona now, not a shared-context pool) — don't raise
  this without re-measuring per-process RSS against whatever this host has free at the time (~607MB
  measured per process on 2026-10-02; this host has 6.9GB total shared with the full production
  stack).
- This dev sandbox needed `LD_LIBRARY_PATH=/home/pino/.local/pw-libs/usr/lib/x86_64-linux-gnu` on
  the `node` invocation to get past `error while loading shared libraries: libasound.so.2` — same
  family of issue the older `production_loadtest.cjs` note below describes, different path; a fresh
  session should diagnose this itself rather than assume either path still applies.
- If a run ever needs to be killed mid-flight, prefer letting it finish or killing it and then
  **waiting a beat before immediately relaunching** — abrupt `kill -9` on a run with in-flight
  requests can leave orphaned server-side state for a short window; this was investigated as a
  possible cause of the ~30s stall (it wasn't the cause this time — ruled out via `SHOW FULL
  PROCESSLIST` coming back clean — but it's a real enough mechanism to stay cautious about).

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

## What's been fixed and deployed earlier (2026-09-30/10-01, chronological)

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

## Current production test data (as of 2026-10-02 — superseded 2026-09-30 data below is stale)

Production's **transactional** data was wiped and reseeded on 2026-10-02 (warehouse structure —
bins, process types, determination rules, the `OB_PPP` Pick-Pack-Pass setup below — was
deliberately preserved, since it was built up incrementally across many sessions and isn't
reproducible from a single script; only Purchase/Sales Orders, Inbound/Outbound Deliveries, Goods
Receipts, Warehouse Tasks/Orders, stock ledgers etc. were cleared). Current catalog:

- **200 items** (`WH-*` codes, e.g. `WH-BATERIA-002`, `WH-KIT-006`), 20 categories, varied weights,
  batch control, serial control, or neither — generated to exercise every Receive/Count field
  combination in one pass.
- 6 suppliers (`Pino Motors`, `Pino Encuadernaciones`, `Pino Chemicals`, `Pino Hardware Supply`,
  `Pino Industrial Parts`, `Pino Safety Gear`), 5 customers (`Andres's Robotics`, `Vega Logistics`,
  `Delta Manufacturing`, `Northwind Assembly`, `Rapido Logistics`).
- 17 Inbound Deliveries (`IBD-00000025`..`IBD-00000041`, 12 lines each), 15 Sales Orders
  (`SAL-ORD-2026-000{20..34}` roughly), 8 `WMS Physical Inventory Count` docs. **Check what's
  actually still open before reusing these names** (several have been received down during this
  session's many reruns) — `IBD-00000034` in particular is cancelled (pre-existing, from before
  this session; don't chase it as a new finding):
  ```python
  frappe.get_all("Inbound Delivery", filters={"name": ["like", "IBD-%"]}, fields=["name", "receipt_status"])
  ```
- A Pick-Pack-Pass setup exists (survived the wipe, since it's warehouse structure, not
  transactional): Warehouse Process Type `OB_PPP` + item-scoped (not item_group-scoped — see
  below) `Warehouse Process Type Determination Rule` rows.
  **must be item-scoped, not item_group-scoped**: `_create_pick_task_for_group` in
  `services/task.py` never passes `item_group` into `determine_process_type`'s context at all, so
  an item_group-scoped rule silently never matches — learned this the hard way, cost a full extra
  round-trip.
- **50 dedicated load-test accounts**, all sharing one password (reset together on 2026-10-02 after
  discovering most had drifted — see the stall saga above):
  - 20 RF personas: `loadtest.{ana,bruno,carla,diego,elena,felipe,gabriela,hugo,irene,javier,
    karina,luis,marta,nico,olivia,pablo,quinn,rosa,santiago,teresa}@pinohomelab.test` →
    `LOADTEST-RF{1..20}`.
  - 30 desk actors (API-only, no browser): `loadtest.desk{01..30}@pinohomelab.test`.
  - Password not stored in this repo — set as `LOADTEST_PASSWORD` before running (ask the user, or
    reset all 50 via `frappe.utils.password.update_password` if it's ever unclear/stale again —
    cheap insurance against repeating the single most expensive mistake in this engagement).

## The load test scripts

Two scripts exist now, both in `frappe_wms/tests/e2e/loadtest/`:

- **`scale_loadtest.cjs`** — the current, actively-maintained one (all of 2026-10-02's fixes
  above are in this file). 20 RF personas across waves of `WAVE_SIZE=4` (one Chromium *process*
  per persona, not shared contexts — see the stall saga above for why) through receive → putaway
  race → ad-hoc move/repack → desk-actor release-for-picking → pick race → count → ship/pack, plus
  30 concurrent API-only desk actors. This is the one to run and keep extending.
  ```bash
  cd frappe_wms/tests/e2e/loadtest
  export LD_LIBRARY_PATH=/home/pino/.local/pw-libs/usr/lib/x86_64-linux-gnu  # this host, 2026-10-02 - re-diagnose fresh elsewhere
  LOADTEST_PASSWORD='<the password>' \
  LOADTEST_IBD_NAMES='IBD-00000025,IBD-00000026,...'  \
  node scale_loadtest.cjs
  ```
  Optional: `LOADTEST_DEBUG_TIMING=1` for verbose per-call timestamp logging (see the stall-saga
  notes above). `LOADTEST_SKIP_HAIRPIN_BYPASS=1` if running from a genuinely different machine than
  production (the DNS-hairpin bypass only matters when the test and production share a host).
- **`production_loadtest.cjs`** — the older, smaller 5-persona version (single shared browser,
  `LOADTEST-RF{1..5}` only). Superseded by `scale_loadtest.cjs` for anything beyond a quick smoke
  check; kept around but not actively maintained.

Both need Playwright's Chromium available; if the sandbox is missing system libs, the exact fix is
environment-specific (this session needed `LD_LIBRARY_PATH` pointed at a prepared libs directory;
an earlier session needed `CHROMIUM_EXECUTABLE_PATH` *and* `LD_LIBRARY_PATH` to get past `error
while loading shared libraries: libasound.so.2`) — diagnose fresh in whatever sandbox you're in
rather than assuming either prior workaround still applies.

**Before your next run**, update `LOADTEST_IBD_NAMES` (or Stage 1's `receiveAll(...)` call for the
older script) to whatever's actually still open — check with:

```python
frappe.get_all("Inbound Delivery", filters={"name": ["like", "IBD-%"]}, fields=["name", "receipt_status"])
```

A delivery whose backing PO is already fully received will just no-op (0 lines), not error — but
better to point it at genuinely fresh data. Create more via a small one-off script (see the pattern
in the findings doc's "wipe and reseed" section) rather than hand-editing existing records.

## Pending / next steps

Roughly in priority order. **Items 2-8 below pre-date the 2026-10-02 wipe/reseed** — the specific
item names they mention (`LT-JUEGO-LLAVES` etc.) no longer exist, but the underlying product
findings are capability gaps, not data-dependent, so they're still believed valid; re-confirm
against the current `WH-*` catalog before acting on one.

1. **Run `scale_loadtest.cjs` through a full clean cycle end-to-end** (receive → putaway → pick →
   count → ship/pack) now that receive and putaway are both confirmed genuinely working under
   concurrent load (see the two "Resolved 2026-10-02" sections above). Pick/count/ship were
   exercised during this session's fixes but not re-verified in one single clean run *after* every
   fix landed together — the last full run still had some open `WH-*` Inbound Deliveries left to
   receive, so pick/ship never had full-catalog stock to work with yet. Minor, non-blocking
   observation from this session also worth a look if it keeps happening: the test's own random
   serial-number generator in `receiveAll()` occasionally collides across different items/deliveries
   in the same run (`Serial Nos SN-... does not belong to Item ...`) — cosmetic (test data, not a
   product bug) but worth making the generated serials more clearly unique if it keeps causing
   noise in the findings log.
2. **Finish exercising Pick-Pack-Pass → Pack.** The rule fix (item-scoped, not item_group-scoped)
   landed but wasn't run through a full clean cycle yet — `LT-JUEGO-LLAVES` still needed a Putaway
   confirm as of the last run. Confirmed finding already banked either way: **there is no API or
   RF path to create a Packing Order at all** (not a missing RF *screen* like other gaps — a
   missing *capability*, full stop; even the raw `frappe.client.insert` fallback 403's for a plain
   WMS Operator/Supervisor role on the Handling Unit doctype). Recommend a dedicated
   `create_packing_order` service/API function mirroring how `release_delivery_for_picking` already
   wraps `allocate_delivery` + `create_pick_tasks` — **don't** just grant raw Handling Unit
   permissions, HUs should stay created only through validated service functions.
3. **Sort/Stage/Load task types** — never genuinely exercised (a stage checks for queues beyond
   Putaway/Pick and has found none configured every run so far). Worth setting up a process type
   that actually uses them, same spirit as the Pick-Pack-Pass setup.
4. **Quality Inspection flow** — `Inspection Rule` exists and shipped auto-creation at GR, but this
   load test has never triggered one (no configured rule matches the current test items/
   warehouse). Set one up, receive against it, confirm the RF app can actually process it.
5. **Multi-line Sales Order fulfillment** beyond a single item — most test SOs so far are 1-2 lines
   fully allocatable; try a larger, partially-allocatable one to exercise short-pick/backorder
   behavior.
6. **Serial-controlled items can't be received in quantity >1** (finding, not yet fixed) — RF
   Receive defaults a line's quantity to the full remaining amount but offers only one serial
   input per line, and the backend requires each serial to move exactly 1 unit. Needs either a
   repeatable per-unit serial entry UI or a "split this line into N single-unit receipts"
   affordance.
7. **Move's destination HU field can't register a new HU** (finding, not yet fixed) — unlike
   Receive's HU field (explicit "If new HU, type" selector), Move's `destination_hu` only accepts
   an already-registered HU; scanning a fresh barcode is rejected as "not a known code". Moving
   stock into an HU-managed bin with no HU already in hand is a dead end.
8. **Intermittent `CSRFTokenError`** under concurrent RF sessions — seen a handful of times across
   many runs (e.g. on `resolve_scan`, `my_tasks`), not root-caused. `core/api.js` already has a
   stale-CSRF refresh-and-retry wrapper from an earlier fix; this may be a gap in that logic under
   genuine concurrency, or environmental (Playwright cookie jar). Worth a focused investigation if
   it keeps recurring.
9. Keep the findings doc current as you go — it's the living record of this engagement, read it
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
