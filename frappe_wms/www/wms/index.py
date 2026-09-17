import frappe
from frappe.translate import get_translations_from_apps

no_cache = 1


def get_context(context):
    if frappe.session.user == "Guest":
        frappe.local.flags.redirect_location = "/login?redirect-to=/wms"
        raise frappe.Redirect

    if "WMS Operator" not in frappe.get_roles() and "WMS Supervisor" not in frappe.get_roles() and "System Manager" not in frappe.get_roles():
        frappe.throw(frappe._("You need the WMS Operator or WMS Supervisor role to use the scanner app"), frappe.PermissionError)

    context.csrf_token = frappe.sessions.get_csrf_token()
    context.user = frappe.session.user
    context.user_fullname = frappe.utils.get_fullname(frappe.session.user)
    context.lang = frappe.local.lang
    # Scoped to this app's own catalog (desk strings + this page's), rather than the full
    # Frappe/ERPNext boot dictionary - small, and covers every dynamic-value lookup (statuses,
    # priorities, task/activity types, ...) alongside every literal _() call in this page,
    # since they all compile into the one frappe_wms.mo for the current language.
    context.messages = frappe.as_json(get_translations_from_apps(frappe.local.lang, ["frappe_wms"]))
    return context
