# Frappe WMS

MVP warehouse management and execution app for Frappe Framework v16.

## Scope
- Warehouse structure, bins and stock types
- Handling units and nested HU hierarchy
- Immutable WMS stock ledger and rebuildable balances
- Inbound delivery, goods receipt and putaway task execution
- Warehouse requests and executable tasks, including partial confirmation
- Outbound delivery, allocation, picking, packing, shipment and goods issue
- Scanner-oriented whitelisted API
- Process and bin determination rules
- ERPNext integration: each WMS Warehouse is auto-linked to a matching
  ERPNext Warehouse, and posted Goods Receipt/Goods Issue documents mirror
  a Stock Entry (Material Receipt/Material Issue) into ERPNext so its own
  Bin quantities and valuation stay reconciled with the WMS ledger. A daily
  scheduled job flags any drift between the two. When a Goods Receipt/Issue
  originates from a Purchase Order/Sales Order (via the "Create > Inbound/
  Outbound Delivery" button on the PO/SO, or
  `frappe_wms.services.procurement`/`services.sales`), it posts an ERPNext
  Purchase Receipt/Delivery Note against that order instead of a generic
  Stock Entry, correctly updating the order's received/delivered
  percentage; cancelling the Goods Receipt/Issue cancels the matching
  ERPNext document. A Goods Receipt/Issue must be either fully order-linked
  or fully standalone — mixed lines are rejected rather than mis-posted.
- RF/mobile execution UI at `/wms`: a scanner-friendly, chrome-free,
  menu-driven page (not a desk form) closest in spirit to SAP's RF UI —
  a home menu leads to Tasks (confirm any planned putaway/pick/move/stage/
  load task, or report an exception), Receive (pick an open Inbound
  Delivery, scan a Handling Unit per line — a brand-new HU barcode is
  auto-registered on the spot — and post the Goods Receipt, which
  immediately raises its putaway tasks), Ship (pick a delivery that's
  fully picked but not yet issued, confirm/adjust the suggested staged HU
  per line, and post the Goods Issue), Pack (complete an open Packing
  Order in one tap), Move (an ad-hoc bin-to-bin/HU-to-HU transfer with no
  planning step, for on-the-spot corrections), Count (record physical
  inventory quantities and auto-post once every line is counted), Quality
  (complete an inspection's pass/fail split), and Lookup (HU/bin
  contents by barcode). Requires the WMS Operator/Receiver/Loader/
  Supervisor role depending on the action (System Manager always allowed).
- Wave management: a WMS Wave groups outbound deliveries for combined
  release. Releasing a wave allocates every delivery in it and generates
  pick tasks using either the Single Order strategy (one task per
  allocation) or Cluster (allocations for the same product/bin/HU/staging
  bin across every delivery in the wave are combined into one pick task,
  and a single confirmation splits the picked quantity back across each
  order's allocation). Pick tasks inherit the source bin's sequence for
  pick-path ordering, which the RF UI's task queue already sorts by.
- Physical inventory / cycle counting: a WMS Physical Inventory Count
  snapshots WMS Stock Balance for a warehouse (optionally scoped to a bin,
  storage type, or product), records counted quantities, then posts any
  variance as an inventory gain/loss against the immutable ledger.
- Quality inspection: a WMS Quality Inspection moves a quantity out of an
  inspection stock type (e.g. QUALITY) and splits it between a
  passed-to and failed-to stock type (e.g. AVAILABLE / DAMAGED), reusing
  the existing stock-type-change movement.
- Slotting & replenishment: a Replenishment Rule pins a minimum/target
  quantity for a product in a pick-face bin, sourced from a bulk storage
  type. An hourly job checks every active rule and raises a Warehouse
  Request (and its Warehouse Task) when a pick bin falls to or below its
  minimum, without duplicating a request that's already pending.
- Warehouse Monitor at `/app/wms-monitor`: a desk page (roles: WMS
  Supervisor/Administrator/Inventory Controller/Auditor, System Manager)
  for searching movements and warehouse data across entities, similar in
  spirit to SAP EWM's monitor — pick a warehouse to see summary counts
  (open tasks by type, exceptions, pending replenishment, deliveries in
  progress, open counts/inspections, open waves, active resources, each
  linking to the matching filtered desk list view) plus searchable sections
  for stock ledger movements, warehouse tasks, handling units, inbound and
  outbound deliveries, waves (with a one-click Release action per draft
  wave), resource workload (open task count per active WMS Resource), and
  warehouse queues.
- Centralized workspace at `/app/wms`: every WMS doctype (structure,
  inbound, inventory, outbound, handling units, execution, shipping,
  setup) is reachable from a single desk workspace, plus quick-access
  shortcuts to the WMS Monitor, the RF Scanner, WMS Settings, and the
  ERPNext masters/settings a warehouse operation regularly needs (Stock
  Settings, Warehouse, Item, Purchase Order, Sales Order) so day-to-day
  work doesn't require leaving the WMS workspace. Visibility is left to
  Frappe's normal per-doctype/per-role permissions; nothing here restricts
  access beyond that.
- WMS Settings (`/app/wms-settings`): a single app-wide configuration
  doctype, starting with `enforce_wms_only_stock_movements` (below) and a
  default Handling Unit Type for the RF app's auto-registration flow.
- ERPNext stock enforcement: once a `WMS Warehouse` is linked to an
  ERPNext `Warehouse` (which happens automatically - see above), direct
  Stock Entry, Delivery Note, Purchase Receipt, or Stock Reconciliation
  postings against that warehouse from ERPNext's own desk are blocked with
  a message pointing at the matching WMS transaction (Goods Receipt/Goods
  Issue/Physical Inventory Count/RF Move). This keeps the WMS stock ledger
  authoritative: all movement on a WMS-managed warehouse must go through
  frappe_wms, which mirrors it into ERPNext itself (see ERPNext
  integration, above). Warehouses that are *not* linked to a WMS Warehouse
  are completely unaffected, so other ERPNext flows (manufacturing,
  subcontracting, etc.) on other warehouses keep working normally. This
  can be turned off site-wide from WMS Settings as an escape hatch.
  Known gap: this does not cover every ERPNext path that can move stock
  (e.g. Purchase/Sales Invoice with "Update Stock", Subcontracting, Asset
  scrapping, Job Card) - only the four primary stock documents.

## Install
```bash
cd frappe-bench
bench get-app /path/to/frappe_wms_full_app
bench --site your-site install-app frappe_wms
bench --site your-site migrate
bench build --app frappe_wms
```

This app expects ERPNext master DocTypes (`Item`, `UOM`, `Batch`, `Serial No`, `Company`, `Supplier`, and `Customer`). Install ERPNext before installing this app.

## Production warning
This is a complete MVP scaffold, not a certified SAP EWM replacement. Validate accounting integration, concurrency, permissions, barcode hardware, reversal rules, and migration data in a non-production site before go-live.
# frappe-ewm
