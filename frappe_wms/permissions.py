import frappe

def has_app_permission(app_name=None):
    if "System Manager" in frappe.get_roles():
        return True
    return any(role.startswith("WMS ") for role in frappe.get_roles())

def _warehouse_clause(user):
    # User Permissions remain authoritative. Empty means no additional SQL restriction.
    allowed = frappe.get_all("User Permission", filters={"user": user, "allow": "WMS Warehouse"}, pluck="for_value")
    if not allowed:
        return ""
    values = ", ".join(frappe.db.escape(x) for x in allowed)
    return f"`warehouse` in ({values})"

def ledger_query(user):
    return _warehouse_clause(user or frappe.session.user)

def task_query(user):
    return _warehouse_clause(user or frappe.session.user)

def ledger_has_permission(doc, user=None, permission_type=None):
    user = user or frappe.session.user
    if permission_type in {"write", "create", "delete", "submit", "cancel"}:
        return False
    allowed = frappe.get_all("User Permission", filters={"user": user, "allow": "WMS Warehouse"}, pluck="for_value")
    return not allowed or doc.warehouse in allowed

def balance_has_permission(doc, user=None, permission_type=None):
    # WMS Stock Balance is a materialized view over the stock ledger, maintained exclusively
    # by frappe_wms.services.stock. Direct edits would desync it from the ledger, so only the
    # service layer (which sets flags.ignore_permissions) may write it.
    user = user or frappe.session.user
    if permission_type in {"write", "create", "delete", "submit", "cancel"}:
        return False
    allowed = frappe.get_all("User Permission", filters={"user": user, "allow": "WMS Warehouse"}, pluck="for_value")
    return not allowed or doc.warehouse in allowed
