import frappe

no_cache = 1


def get_context(context):
    if frappe.session.user == "Guest":
        frappe.local.flags.redirect_location = "/login?redirect-to=/configurator"
        raise frappe.Redirect

    if "WMS Administrator" not in frappe.get_roles() and "System Manager" not in frappe.get_roles():
        frappe.throw(frappe._("You need the WMS Administrator or System Manager role to use the configurator"), frappe.PermissionError)

    return context
