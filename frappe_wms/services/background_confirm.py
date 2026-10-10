"""SAP EWM "Confirm in background" for tasks and warehouse orders that already exist (Warehouse Monitor > Warehouse Tasks / Warehouse Orders): the full planned quantity is
confirmed without a person at the bin. What needs a person's choice - which serial numbers or batch leave a stock that holds more of them than the task takes - is not
guessed: such a task stays open and is handed back as "foreground", to be confirmed with its details."""
import frappe
from frappe import _

from frappe_wms.services.packing_center import _fail_text

DONE_STATUSES = ("Confirmed", "Cancelled", "Exception")


def _confirm_one(task_name):
    """-> "done" | "foreground" | "skipped" (already finished)"""
    from frappe_wms.services.task import _UNPACK, confirm_task, details_needed
    task = frappe.get_doc("Warehouse Task", task_name)
    if task.status in DONE_STATUSES: return "skipped"
    if any(k in details_needed(task, task.planned_quantity) for k in ("serial", "batch")): return "foreground"
    confirm_task(task.name, verify=False, destination_hu=_UNPACK if task.unpack_at_destination else None)
    return "done"


def _tasks_of(doctype, name):
    if doctype == "Warehouse Task": return [name]
    # in sequence: confirming one releases the task held behind it
    return frappe.get_all("Warehouse Task", filters={"warehouse_order": name, "status": ["not in", list(DONE_STATUSES)]}, order_by="sequence asc, name asc", pluck="name")


def confirm_in_background(doctype, names):
    """Each task / order on its own (a refused one does not stop the others). -> {"done": [{name, text}], "errors": [{name, error}], "foreground": [task names]}"""
    if doctype not in ("Warehouse Task", "Warehouse Order"): frappe.throw(_("Unsupported document"))
    out = {"done": [], "errors": [], "foreground": []}
    for i, name in enumerate(names):
        savepoint = f"bgc_{i}"
        frappe.db.savepoint(savepoint)
        confirmed, foreground = 0, []
        try:
            for task in _tasks_of(doctype, name):
                result = _confirm_one(task)
                if result == "done": confirmed += 1
                elif result == "foreground": foreground.append(task)
            if not confirmed and not foreground: out["errors"].append({"name": name, "error": _("Nothing left to confirm")}); continue
            out["foreground"] += foreground
            text = _("{0} task(s) confirmed").format(confirmed) + (_(", {0} need details: confirm in the foreground").format(len(foreground)) if foreground else "")
            out["done"].append({"name": name, "text": text})
        except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as e:
            frappe.db.rollback(save_point=savepoint)
            out["errors"].append({"name": name, "error": _fail_text(e)})
    return out
