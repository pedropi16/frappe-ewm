from frappe_wms.db_maintenance import ensure_indexes

def execute():
    # See db_maintenance.ensure_indexes for why the original v0_1 patch never actually ran -
    # this is a genuinely new patch path so already-migrated sites pick it up.
    ensure_indexes()
