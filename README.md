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
  scheduled job flags any drift between the two.
- RF/mobile execution UI at `/wms`: a scanner-friendly, chrome-free page
  (not a desk form) for confirming putaway/pick/move tasks, reporting
  exceptions, and looking up HU/bin contents by barcode. Requires the
  WMS Operator or WMS Supervisor role (System Manager also allowed).
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
