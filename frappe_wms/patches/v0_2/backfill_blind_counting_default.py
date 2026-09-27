import frappe

def execute():
    # A new field's "default" only applies when a document is first created - WMS Settings is a
    # Single, and every site that installed frappe_wms before this field existed already has
    # that one row, so the field reads as unset (falsy) rather than the declared default of 1
    # until something explicitly saves it. Reproduced live: blind_counting read False on the
    # existing dev site despite the JSON default, silently defeating the whole feature on every
    # already-installed site (including production) - only a brand new install would ever see
    # the intended default. Backfill it once, here, same as the field's own declared default.
    if frappe.db.get_single_value("WMS Settings", "blind_counting") in (None, 0, "0"):
        frappe.db.set_single_value("WMS Settings", "blind_counting", 1)
