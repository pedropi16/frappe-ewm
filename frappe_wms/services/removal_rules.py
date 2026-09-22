import frappe
from frappe import _
from frappe.utils import flt


def strategy_fifo(balances, context):
    return sorted(balances, key=lambda b: (b.first_receipt_date or "", b.name))


def strategy_lifo(balances, context):
    return sorted(balances, key=lambda b: (b.first_receipt_date or "", b.name), reverse=True)


def strategy_fefo(balances, context):
    # Stable sort over the FIFO-ordered list keeps first_receipt_date/name as the tiebreak - a
    # balance with no shelf_life_expiry_date (product has no shelf_life_days configured) sorts
    # after every dated one, reducing to plain FIFO when nothing has an expiry.
    base = strategy_fifo(balances, context)
    return sorted(base, key=lambda b: (0, b.shelf_life_expiry_date) if b.shelf_life_expiry_date else (1, None))


def strategy_stringent_fifo(balances, context):
    # True "stringent" enforcement (blocking picks that violate global FIFO order across
    # allocations by other rules/deliveries) is out of scope for P1 - this only guarantees the
    # sort itself is strict receipt order, never combined with a custom sort_fields override
    # (enforced in the Removal Rule doctype's own validate()).
    return strategy_fifo(balances, context)


def strategy_partial_quantity_first(balances, context):
    return sorted(balances, key=lambda b: flt(b.quantity))


def strategy_by_quantity(balances, context):
    return sorted(balances, key=lambda b: -flt(b.quantity))


def strategy_fixed_bin(balances, context):
    fixed_bin = context.get("fixed_bin")
    if not fixed_bin: frappe.throw(_("Removal rule strategy 'Fixed Bin' requires a fixed bin"))
    return [b for b in balances if b.storage_bin == fixed_bin]


class _Reversor:
    # Wraps one sort key so a mixed asc/desc multi-field sort can use a single sorted() call -
    # tuple comparison short-circuits on the first differing element, so this only ever compares
    # two values that came from the same field (same type), even across differently-typed fields.
    __slots__ = ("obj",)
    def __init__(self, obj): self.obj = obj
    def __eq__(self, other): return self.obj == other.obj
    def __lt__(self, other): return other.obj < self.obj


def strategy_custom_sort(balances, context):
    sort_fields = context.get("sort_fields") or []
    def sort_key(b):
        parts = []
        for row in sort_fields:
            value = b.get(row.field)
            part = (value is None, value if value is not None else "")
            parts.append(_Reversor(part) if row.direction == "Desc" else part)
        return tuple(parts)
    return sorted(balances, key=sort_key) if sort_fields else balances


def get_removal_strategies():
    # Aggregated from every installed app's hooks.py wms_removal_strategies dict (frappe_wms's
    # own built-ins are declared the same way, in frappe_wms/hooks.py) - a genuine extension
    # point, not a hardcoded registry only this app can add to. frappe.get_hooks() already
    # merges same-named dict hooks across apps into one dict keyed by strategy name, with each
    # value a list of one entry per app that registered it - the last one wins, so an app
    # loaded after frappe_wms can override a built-in strategy by reusing its name.
    merged = frappe.get_hooks("wms_removal_strategies") or {}
    registry = {}
    for name, paths in merged.items():
        path = paths[-1] if isinstance(paths, list) else paths
        registry[name] = frappe.get_attr(path) if isinstance(path, str) else path
    return registry


def apply_strategy(strategy_name, balances, *, sort_fields=None, fixed_bin=None):
    if strategy_name == "Fixed Bin":
        return strategy_fixed_bin(balances, {"fixed_bin": fixed_bin})
    if sort_fields:
        return strategy_custom_sort(balances, {"sort_fields": sort_fields})
    registry = get_removal_strategies()
    fn = registry.get(strategy_name)
    if not fn: frappe.throw(_("Unknown removal strategy: {0}").format(strategy_name))
    return fn(balances, {})


def match_removal_rule(warehouse, item=None, item_group=None, stock_type=None):
    context = {"item": item, "item_group": item_group, "stock_type": stock_type}
    rule_names = frappe.get_all("Removal Rule", filters={"active": 1, "warehouse": warehouse}, pluck="name", order_by="priority asc")
    for name in rule_names:
        rule = frappe.get_cached_doc("Removal Rule", name)
        if all(not rule.get(key) or rule.get(key) == context.get(key) for key in ("item", "item_group", "stock_type")):
            return rule
    return None
