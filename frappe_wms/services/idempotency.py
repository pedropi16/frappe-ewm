import frappe
from frappe.utils.synchronization import filelock

_TTL_SECONDS = 24 * 60 * 60


def run_once(key, fn):
    """Run fn() at most once per (user, key); a repeat of the same request returns the first result.

    The RF app resends a request when a response was lost (dead-WiFi zone, page reload mid-submit). Endpoints that create
    documents have no natural key to dedupe on the way stock ledger postings do, so the client mints one key per user
    action and reuses it on every resend. Without a key this is a plain call. Keys live 24h in the cache - only a
    just-resent request needs the answer, not a permanent ledger.
    """
    if not key:
        return fn()
    cache_key = f"wms_idem:{frappe.session.user}:{key}"
    with filelock(cache_key):
        hit = frappe.cache.get_value(cache_key)
        if hit is not None:
            result = dict(hit["result"]) if isinstance(hit["result"], dict) else hit["result"]
            if isinstance(result, dict): result["replayed"] = True
            return result
        result = fn()
        frappe.cache.set_value(cache_key, {"result": result}, expires_in_sec=_TTL_SECONDS)
        return result
