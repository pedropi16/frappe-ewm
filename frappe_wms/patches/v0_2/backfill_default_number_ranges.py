import frappe


def execute():
    # install.py's after_install() seeds a default global WMS Number Range for Handling Unit and
    # WMS Shipment - but after_install only ever runs on a brand-new site install, never again on
    # a site the app was already installed on (like production, from well before pack_by_instruction
    # and services/numbering.py's next_number() existed). Confirmed live: next_number() has no
    # fallback at all (throws "No active Number Range is configured for Handling Unit") unlike the
    # opt-in autoname_from_range() hook, so any already-installed site silently never got this and
    # the new case/pallet packing feature would break for every real user on first use.
    for range_for, prefix, number_length, end_number in [("Handling Unit", "HU-", 8, 99999999), ("WMS Shipment", "SHIP-", 8, 99999999)]:
        if frappe.db.exists("WMS Number Range", {"range_for": range_for, "warehouse": ["in", ["", None]], "hu_type": ["in", ["", None]]}):
            continue
        frappe.get_doc({
            "doctype": "WMS Number Range", "range_for": range_for, "prefix": prefix,
            "number_length": number_length, "start_number": 1, "end_number": end_number, "active": 1,
        }).insert(ignore_permissions=True)
