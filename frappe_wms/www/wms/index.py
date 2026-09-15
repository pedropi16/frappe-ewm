import frappe

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
    return context
