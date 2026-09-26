import frappe

no_cache = 1


def get_context(context):
    if frappe.session.user == "Guest":
        frappe.local.flags.redirect_location = "/login?redirect-to=/configurator"
        raise frappe.Redirect

    if "WMS Administrator" not in frappe.get_roles() and "System Manager" not in frappe.get_roles():
        frappe.throw(frappe._("You need the WMS Administrator or System Manager role to use the configurator"), frappe.PermissionError)

    # Lets the page write (apply a profile) with the signed-in session, no API key needed.
    try:
        context.csrf_token = frappe.sessions.get_csrf_token()
    except AttributeError:
        context.csrf_token = ""  # no session data (e.g. rendered outside a request): page stays read-only
    return context
