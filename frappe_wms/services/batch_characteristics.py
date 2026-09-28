import frappe
from frappe import _
from frappe_wms.utils import require_role

# SAP EWM batch determination, scoped down to what this app actually needs: a batch can carry
# any number of free-form (characteristic, value) pairs (Grade=A, Potency=98%, ...) - recorded
# here however the source of truth for them arrives (a lab result, a manual entry at receiving),
# and matched exactly against Outbound Delivery Item.required_characteristics by
# services/allocation._matching_batches_for_characteristics. No value ranges or tolerances, no
# formal classification system - an exact string match per characteristic.

CHARACTERISTIC_ROLES = ("WMS Inventory Controller", "WMS Supervisor")


def set_batch_characteristics(batch_no, values):
    # values: {characteristic: value} - replaces whatever this batch already had recorded for
    # each characteristic named, leaves any other existing characteristic on the batch untouched.
    require_role(*CHARACTERISTIC_ROLES)
    if not frappe.db.exists("Batch", batch_no): frappe.throw(_("Batch {0} does not exist").format(batch_no))
    for characteristic, value in values.items():
        existing = frappe.db.get_value("WMS Batch Characteristic Value", {"batch_no": batch_no, "characteristic": characteristic}, "name")
        if existing:
            frappe.db.set_value("WMS Batch Characteristic Value", existing, "value", value)
        else:
            frappe.get_doc({"doctype": "WMS Batch Characteristic Value", "batch_no": batch_no,
                "characteristic": characteristic, "value": value}).insert(ignore_permissions=True)
    return get_batch_characteristics(batch_no)


def get_batch_characteristics(batch_no):
    rows = frappe.get_all("WMS Batch Characteristic Value", filters={"batch_no": batch_no}, fields=["characteristic", "value"])
    return {r.characteristic: r.value for r in rows}
