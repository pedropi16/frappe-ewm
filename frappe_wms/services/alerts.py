"""The hourly alert digest: what in each warehouse needs a person, pushed to supervisors as a
desk notification (and e-mail) instead of waiting for someone to open the Monitor's Alerts view.

WMS Settings > Alerts decides the channel and the recipients. A warehouse's digest goes out only
when its counts changed since the last one, so a problem nobody has fixed yet is not repeated
every hour.
"""
import json

import frappe
from frappe import _
from frappe.utils import get_url

# Monitor alert key -> what the digest calls it.
CHECKS = {
    "erp_sync_problems": "ERPNext postings not yet done",
    "negative_quants": "Negative quants",
    "aged_exceptions": "Task exceptions open for hours",
    "unplanned_requests": "Warehouse requests without tasks",
    "stalled_warehouse_orders": "Warehouse orders nobody took",
    "pending_approval_counts": "Counts waiting for approval",
}
CACHE_KEY = "frappe_wms:alert_digest:{0}"


def warehouse_counts(warehouse):
    from frappe_wms.api.monitor import get_alerts
    alerts = get_alerts(warehouse)
    counts = {key: len(alerts.get(key) or []) for key in CHECKS}
    # A queued posting still inside its first attempts is normal traffic - only failures count.
    counts["erp_sync_problems"] = sum(1 for r in alerts.get("erp_sync_problems") or [] if r.status == "Failed")
    return counts


def _recipients(warehouse):
    configured = frappe.db.get_single_value("WMS Settings", "alert_recipients")
    if configured:
        return [line.strip() for line in configured.replace(",", "\n").splitlines() if line.strip()]
    users = frappe.get_all("Has Role", filters={"role": "WMS Supervisor", "parenttype": "User"}, pluck="parent", distinct=True)
    out = []
    for user in users:
        if not frappe.db.get_value("User", user, "enabled"): continue
        allowed = frappe.get_all("User Permission", filters={"user": user, "allow": "WMS Warehouse"}, pluck="for_value")
        if not allowed or warehouse in allowed: out.append(user)
    return out


def send_alert_digest():
    """Scheduler (hourly)."""
    channel = frappe.db.get_single_value("WMS Settings", "alert_notifications") or "Off"
    if channel == "Off": return
    for warehouse in frappe.get_all("WMS Warehouse", filters={"active": 1}, pluck="name"):
        counts = warehouse_counts(warehouse)
        signature = json.dumps(counts, sort_keys=True)
        key = CACHE_KEY.format(warehouse)
        if (frappe.cache.get_value(key) or "") == signature: continue
        frappe.cache.set_value(key, signature)
        problems = [(CHECKS[k], n) for k, n in counts.items() if n]
        if not problems: continue
        recipients = _recipients(warehouse)
        if not recipients: continue
        subject = _("WMS {0}: {1}").format(warehouse, ", ".join(f"{label} ({n}{'+' if n >= 50 else ''})" for label, n in problems))
        link = get_url(f"/app/wms-monitor?warehouse={warehouse}")
        body = "<p>{0}</p><ul>{1}</ul><p><a href='{2}'>{3}</a></p>".format(
            _("These need attention in warehouse {0}:").format(warehouse),
            "".join(f"<li>{frappe.utils.escape_html(label)}: {n}</li>" for label, n in problems), link, _("Open the WMS Monitor"))
        users = [r for r in recipients if frappe.db.exists("User", r)]
        for user in users:
            frappe.get_doc({"doctype": "Notification Log", "for_user": user, "type": "Alert", "subject": subject[:140],
                            "email_content": body, "document_type": "WMS Warehouse", "document_name": warehouse}).insert(ignore_permissions=True)
        if channel == "Desk Notification and Email":
            emails = [frappe.db.get_value("User", r, "email") if r in users else r for r in recipients]
            frappe.sendmail(recipients=[e for e in emails if e], subject=subject, message=body, delayed=True)
