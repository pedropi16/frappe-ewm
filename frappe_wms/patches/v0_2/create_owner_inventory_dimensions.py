from frappe_wms.install import ensure_owner_inventory_dimensions


def execute():
    # ERPNext Inventory Dimensions for the WMS owner / party entitled to dispose (fields wms_stock_owner, wms_entitled_party on every stock row).
    ensure_owner_inventory_dimensions()
