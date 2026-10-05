"""Post Processing Framework: event-driven actions on documents (SAP PPF action profiles).

A PPF Action Profile belongs to a document type and lists actions: when (After Insert / On Update / On Submit / On Cancel / Status Change /
Scheduled), a condition (Python over `doc`), and what (create tasks for a Warehouse Request, print, notify, call a registered method, set a field,
create a ToDo). Every run is logged in PPF Action Log; a failing action is logged and never undoes the document it ran for. "Execute once" skips
documents an action already succeeded for.
"""
import json

import frappe
from frappe import _
from frappe.utils import now_datetime

EVENTS = {"after_insert": "After Insert", "on_update": "On Update", "on_submit": "On Submit", "on_cancel": "On Cancel"}


def _doctypes_with_profiles():
    cached = frappe.cache.get_value("wms_ppf_doctypes")
    if cached is None:
        cached = frappe.get_all("PPF Action Profile", filters={"active": 1}, pluck="reference_doctype", distinct=True)
        frappe.cache.set_value("wms_ppf_doctypes", cached)
    return set(cached)


def on_event(doc, method=None):
    if doc.doctype.startswith("PPF ") or frappe.flags.in_migrate or frappe.flags.in_install or frappe.flags.get("wms_ppf_off"): return
    if doc.doctype not in _doctypes_with_profiles(): return
    event = EVENTS.get(method)
    if not event: return
    run_actions(doc, event)
    if method == "on_update" and not doc.flags.in_insert: run_actions(doc, "Status Change")


def _matches(row, doc, event):
    if not row.active or row.event != event: return False
    if event == "Status Change":
        field = row.status_field or "status"
        if not doc.has_value_changed(field) or (row.status_value and str(doc.get(field)) != row.status_value): return False
    if row.condition and not frappe.safe_eval(row.condition, None, {"doc": doc, "frappe": frappe._dict(db=frappe.db, utils=frappe.utils)}): return False
    return True


def run_actions(doc, event):
    for profile in frappe.get_all("PPF Action Profile", filters={"active": 1, "reference_doctype": doc.doctype}, pluck="name"):
        profile_doc = frappe.get_cached_doc("PPF Action Profile", profile)
        if profile_doc.document_type and profile_doc.document_type != (doc.get("document_type") or ""): continue
        for row in sorted(profile_doc.actions, key=lambda r: r.sequence or 0):
            try:
                if not _matches(row, doc, event): continue
            except Exception as e:  # a bad condition is a configuration error: log it, do not break the document
                _log(profile, row, doc, event, "Failed", f"condition: {e}")
                continue
            if row.execute_once and frappe.db.exists("PPF Action Log", {"action_row": row.name, "reference_doctype": doc.doctype, "reference_name": doc.name, "status": "Success"}):
                continue
            _execute(profile, row, doc, event)


def _execute(profile, row, doc, event):
    frappe.db.savepoint("wms_ppf")
    try:
        message = ACTIONS[row.action_type](doc, json.loads(row.parameters) if row.parameters else {}, event)
        _log(profile, row, doc, event, "Success", message)
    except Exception as e:
        frappe.db.rollback(save_point="wms_ppf")
        frappe.clear_messages()
        _log(profile, row, doc, event, "Failed", str(e)[:1000])


def _log(profile, row, doc, event, status, message=None):
    frappe.get_doc({"doctype": "PPF Action Log", "profile": profile if isinstance(profile, str) else profile.name, "action_row": row.name, "action_type": row.action_type, "reference_doctype": doc.doctype,
        "reference_name": doc.name, "event": event, "status": status, "message": message, "executed_at": now_datetime()}).insert(ignore_permissions=True)


# ------------------------------------------------------------------ actions: (doc, parameters, event) -> message

def _create_tasks(doc, params, event):
    if doc.doctype != "Warehouse Request": frappe.throw(_("Create Tasks works on a Warehouse Request"))
    from frappe_wms.services.task import create_tasks_for_request
    return f"tasks: {create_tasks_for_request(doc.name)}"


def _print(doc, params, event):
    from frappe_wms.services.printing import create_print_spool
    spool = create_print_spool(doc.doctype, doc.name, params.get("event") or event, doc.get("warehouse"))
    return f"spool {spool}" if spool else "no print determination rule"


def _notify(doc, params, event):
    recipients = [r.strip() for r in (params.get("recipients") or "").split(",") if r.strip()]
    for role in [r.strip() for r in (params.get("roles") or "").split(",") if r.strip()]:
        recipients += frappe.get_all("Has Role", filters={"role": role, "parenttype": "User"}, pluck="parent")
    recipients = sorted({r for r in recipients if r not in ("Administrator", "Guest")})
    if not recipients: frappe.throw(_("No recipients"))
    frappe.sendmail(recipients=recipients, subject=frappe.render_template(params.get("subject") or "{{ doc.doctype }} {{ doc.name }}", {"doc": doc}),
        message=frappe.render_template(params.get("message") or "{{ doc.doctype }} {{ doc.name }}", {"doc": doc}), delayed=True)
    return f"mail to {len(recipients)}"


def registered_actions():
    registry = {}
    for name, paths in (frappe.get_hooks("wms_ppf_actions") or {}).items():
        path = paths[-1] if isinstance(paths, list) else paths
        registry[name] = frappe.get_attr(path) if isinstance(path, str) else path
    return registry


def _call_method(doc, params, event):
    fn = registered_actions().get(params.get("method"))
    if not fn: frappe.throw(_("{0} is not a registered PPF action (hooks.py wms_ppf_actions)").format(params.get("method")))
    return str(fn(doc, params) or "done")


def _set_field(doc, params, event):
    field, value = params["field"], params.get("value")
    frappe.db.set_value(doc.doctype, doc.name, field, value)
    doc.set(field, value)
    return f"{field} = {value}"


def _create_todo(doc, params, event):
    todo = frappe.get_doc({"doctype": "ToDo", "allocated_to": params.get("allocated_to"), "reference_type": doc.doctype, "reference_name": doc.name,
        "description": frappe.render_template(params.get("description") or "{{ doc.doctype }} {{ doc.name }}", {"doc": doc}), "priority": params.get("priority") or "Medium"}).insert(ignore_permissions=True)
    return f"todo {todo.name}"


ACTIONS = {"Create Tasks": _create_tasks, "Print": _print, "Notify": _notify, "Call Method": _call_method, "Set Field": _set_field, "Create ToDo": _create_todo}


def run_scheduled_actions():
    """Scheduler: every active Scheduled action runs for the documents its filters select (execute once per document unless switched off)."""
    for profile in frappe.get_all("PPF Action Profile", filters={"active": 1}, pluck="name"):
        profile_doc = frappe.get_cached_doc("PPF Action Profile", profile)
        for row in profile_doc.actions:
            if not row.active or row.event != "Scheduled": continue
            filters = (json.loads(row.parameters) if row.parameters else {}).get("filters") or {}
            for name in frappe.get_all(profile_doc.reference_doctype, filters=filters, pluck="name", limit=200):
                doc = frappe.get_doc(profile_doc.reference_doctype, name)
                try:
                    if row.condition and not frappe.safe_eval(row.condition, None, {"doc": doc}): continue
                except Exception: continue
                if row.execute_once and frappe.db.exists("PPF Action Log", {"action_row": row.name, "reference_name": name, "status": "Success"}): continue
                _execute(profile_doc, row, doc, "Scheduled")
