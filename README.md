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
