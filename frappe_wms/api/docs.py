"""Self-documenting API: an OpenAPI 3 description of every whitelisted frappe_wms endpoint,
built from the code (signatures and docstrings), so it can never drift from it.

    GET /api/method/frappe_wms.api.docs.openapi

Endpoints are served by Frappe at /api/method/<path> and /api/v2/method/<path>. Business events
are pushed with Frappe's own Webhook doctype (see docs/api.md for the event catalogue)."""
import importlib
import inspect
import pkgutil

import frappe

import frappe_wms.api as api_package

API_VERSION = "1.0"


def _endpoints():
    for mod in pkgutil.iter_modules(api_package.__path__):
        module = importlib.import_module(f"frappe_wms.api.{mod.name}")
        for name, fn in inspect.getmembers(module, inspect.isfunction):
            if fn in frappe.whitelisted and inspect.unwrap(fn).__module__ == module.__name__:
                yield mod.name, name, fn


@frappe.whitelist()
def openapi():
    frappe.only_for(("System Manager", "WMS Administrator"))
    paths = {}
    for mod, name, fn in sorted(_endpoints(), key=lambda e: e[:2]):
        sig = inspect.signature(fn)
        props = {p.name: ({} if p.default is p.empty else {"default": p.default if isinstance(p.default, (str, int, float, bool, type(None))) else str(p.default)})
                 for p in sig.parameters.values() if p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)}
        required = [p.name for p in sig.parameters.values() if p.default is p.empty and p.name in props]
        doc = inspect.getdoc(fn) or ""
        paths[f"/api/method/frappe_wms.api.{mod}.{name}"] = {"post": {
            "tags": [mod], "summary": doc.split("\n")[0] if doc else name, "description": doc,
            "x-idempotent-retry": "idempotency_key" in props,
            "requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": props, "required": required}}}},
            "responses": {"200": {"description": "{\"message\": <result>}"}, "417": {"description": "Validation error: {\"exception\", \"exc_type\", \"_server_messages\"}"}}}}
    return {"openapi": "3.0.3", "info": {"title": "frappe_wms API", "version": API_VERSION,
            "description": "Authenticate with `Authorization: token <api_key>:<api_secret>`. Endpoints with an idempotency_key argument can be retried safely with the same key."},
            "paths": paths}


# Curated business events: each one is a core Frappe Webhook on the named doctype event.
WEBHOOK_EVENTS = {
    "goods_receipt.posted": ("Goods Receipt", "on_submit", None),
    "goods_issue.posted": ("Goods Issue", "on_submit", None),
    "inbound_delivery.status_changed": ("Inbound Delivery", "on_update", "doc.has_value_changed('status')"),
    "outbound_delivery.status_changed": ("Outbound Delivery", "on_update", "doc.has_value_changed('status')"),
    "warehouse_task.confirmed": ("Warehouse Task", "on_update", "doc.has_value_changed('status') and doc.status == 'Confirmed'"),
    "shipment.status_changed": ("WMS Shipment", "on_update", "doc.has_value_changed('status')"),
    "dock_appointment.status_changed": ("WMS Dock Appointment", "on_update", "doc.has_value_changed('status')"),
}


@frappe.whitelist()
def webhook_events():
    return {k: {"doctype": v[0], "doc_event": v[1], "condition": v[2]} for k, v in WEBHOOK_EVENTS.items()}


@frappe.whitelist()
def subscribe_webhook(event, url, secret=None):
    """Creates the core Webhook that posts the whole document as JSON to `url` when `event` happens
    (signed with X-Frappe-Webhook-Signature when a secret is given)."""
    frappe.only_for(("System Manager", "WMS Administrator"))
    if event not in WEBHOOK_EVENTS: frappe.throw(frappe._("Unknown event {0}; see webhook_events").format(event))
    doctype, doc_event, condition = WEBHOOK_EVENTS[event]
    hook = frappe.get_doc({"doctype": "Webhook", "__newname": f"WMS {event} {frappe.generate_hash(length=4)}", "webhook_doctype": doctype, "webhook_docevent": doc_event, "condition": condition,
                           "request_url": url, "request_method": "POST", "request_structure": "JSON", "enable_security": 1 if secret else 0,
                           "webhook_secret": secret, "webhook_headers": [{"key": "X-WMS-Event", "value": event}]})
    hook.flags.ignore_permissions = True
    hook.insert()
    return hook.name
