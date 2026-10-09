"""Dialog locks (SAP enqueue): an object opened for change is locked for everyone else until the user leaves it.

Others can still view it. A lock is a redis key with a time to live that the open screen renews, so a closed
browser or a crashed session frees it by itself (SAP: the lock dies with the session). Only the user-facing
APIs check locks; background jobs and the ERP integration are never blocked by a screen somebody left open.
"""
import functools
import inspect
import json

import frappe
from frappe import _
from frappe.utils import now_datetime

TTL = 300  # seconds without a renewal; the screens renew every minute
PREFIX = "wms_lock|"


def _key(doctype, name):
    return frappe.cache.make_key(f"{PREFIX}{doctype}|{name}")


def holder(doctype, name):
    raw = frappe.cache.get(_key(doctype, name))
    return json.loads(raw) if raw else None


def status(doctype, name):
    h = holder(doctype, name)
    return {**h, "mine": h["user"] == frappe.session.user} if h else None


def _refuse(doctype, name, h):
    frappe.throw(_("{0} {1} is being changed by {2} since {3}. You can display it, but not change it until they leave it.").format(
        doctype, name, h["user"], str(h["since"])[11:16]), title=_("Locked"))


def acquire(doctype, name, purpose=None):
    me = frappe.session.user
    value = json.dumps({"user": me, "since": str(now_datetime()), "purpose": purpose or ""})
    for _try in range(2):  # a lock that expired between our two reads is simply taken
        if frappe.cache.set(_key(doctype, name), value, nx=True, ex=TTL): return status(doctype, name)
        h = holder(doctype, name)
        if h and h["user"] == me:
            frappe.cache.expire(_key(doctype, name), TTL)
            return {**h, "mine": True}
        if h: _refuse(doctype, name, h)


def acquire_many(objects, purpose=None):
    """All or nothing: [(doctype, name), ...]."""
    taken = []
    try:
        for doctype, name in objects:
            fresh = not holder(doctype, name)
            acquire(doctype, name, purpose)
            if fresh: taken.append((doctype, name))
    except Exception:
        for o in taken: release(*o)
        raise


def renew(doctype, name):
    h = holder(doctype, name)
    if h and h["user"] == frappe.session.user: frappe.cache.expire(_key(doctype, name), TTL)
    return bool(h and h["user"] == frappe.session.user)


def release(doctype, name, force=False):
    h = holder(doctype, name)
    if h and (force or h["user"] == frappe.session.user): frappe.cache.delete(_key(doctype, name))


def require_free(doctype, name):
    """Refuses when somebody else has the object open for change."""
    h = holder(doctype, name)
    if h and h["user"] != frappe.session.user: _refuse(doctype, name, h)


def require_free_many(objects):
    for o in objects: require_free(*o)


def all_locks():
    prefix = frappe.cache.make_key(PREFIX)
    out = []
    for key in frappe.cache.keys(prefix + b"*"):
        doctype, _sep, name = key[len(prefix):].decode().partition("|")
        h = holder(doctype, name)
        if h: out.append({"object_type": doctype, "object_name": name, **h})
    return sorted(out, key=lambda l: l["since"])


# Documents the Desk opens in change mode (public/js/frappe_wms.js locks them on open). A save from the form is refused when
# somebody else holds the lock; saves by services (RF confirmation, ERP sync, jobs) are not.
LOCKED_FORMS = ("WMS Stock Adjustment", "WMS Posting Change", "WMS Physical Inventory Count", "WMS Wave", "Warehouse Order",
                "WMS Quality Inspection", "WMS Shipment", "VAS Order")


def on_validate(doc, method=None):
    if doc.doctype in LOCKED_FORMS and not doc.is_new() and frappe.local.form_dict.get("cmd") == "frappe.desk.form.save.savedocs":
        require_free(doc.doctype, doc.name)


def guard(doctype, param):
    """Decorator for an API method that changes the document named by `param`; doctype "$x" reads it from the parameter x."""
    def deco(fn):
        sig = inspect.signature(fn)
        @functools.wraps(fn)
        def wrapper(*a, **k):
            args = sig.bind(*a, **k).arguments
            require_free(args[doctype[1:]] if doctype.startswith("$") else doctype, args[param])
            return fn(*a, **k)
        return wrapper
    return deco
