# Frappe WMS

MVP warehouse management and execution app for Frappe Framework v16.

## Scope
- Warehouse structure, bins and stock types
- Handling units and nested HU hierarchy
- Immutable WMS stock ledger and rebuildable balances
- Inbound delivery and goods receipt
- Warehouse requests and executable tasks
- Outbound delivery, allocation, packing, shipment and goods issue
- Scanner-oriented whitelisted API
- Process and bin determination rules

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
