"""SAP-style selection screens for the WMS Monitor.

A selection is a dict of per-field criteria, the same shape SAP's "multiple selection" dialog
builds (a RANGES table per select-option):

    {"product": {"include": [{"op": "cp", "low": "SKU-001*"}, {"op": "bt", "low": "SKU-100", "high": "SKU-120"}],
                 "exclude": [{"op": "eq", "low": "SKU-00105"}]},
     "quantity": {"include": [{"op": "ge", "low": 10}]}}

Per field the result is (include_1 OR include_2 ...) AND NOT (exclude_1 OR exclude_2 ...) - an
empty include list means "anything" - and fields are ANDed together. Operators:

    eq =   ne !=   gt >   ge >=   lt <   le <=   bt between   nb not between
    cp matches pattern   np does not match pattern     ('*' any run of characters, '+' one character)

An "eq"/"ne" whose value contains '*' or '+' is treated as cp/np, like SAP does.

The compiled condition is plain SQL built only from fieldnames validated against the DocType
meta (or a view's declared virtual fields) and values passed through frappe.db.escape, and it is
appended to Frappe's own DatabaseQuery - so role permissions, User Permissions and the app's
permission_query_conditions still apply exactly as they do for frappe.get_list.
"""
import json

import frappe
from frappe import _
from frappe.model.db_query import DatabaseQuery
from frappe.utils import cint, flt, getdate, get_datetime

OPERATORS = {"eq", "ne", "gt", "ge", "lt", "le", "bt", "nb", "cp", "np"}
NUMERIC_TYPES = {"Int", "Float", "Currency", "Percent", "Check", "Rating", "Duration"}
DATE_TYPES = {"Date"}
DATETIME_TYPES = {"Datetime"}
TEXT_TYPES = {"Data", "Link", "Select", "Dynamic Link", "Small Text", "Text", "Long Text", "Read Only", "Barcode"}
STANDARD_FIELDS = {
    "name": ("Data", "ID"), "owner": ("Link", "Created By"), "modified_by": ("Link", "Last Modified By"),
    "creation": ("Datetime", "Created On"), "modified": ("Datetime", "Last Modified"), "docstatus": ("Int", "Document Status"),
}
MAX_VALUES_PER_FIELD = 20000  # a pasted Excel column; far beyond any hand-built selection
HARD_MAX_HITS = 50000


def _field_type(meta, fieldname, virtual):
    if fieldname in virtual:
        return virtual[fieldname].get("fieldtype", "Data")
    if fieldname in STANDARD_FIELDS:
        return STANDARD_FIELDS[fieldname][0]
    df = meta.get_field(fieldname)
    if not df or df.fieldtype not in (NUMERIC_TYPES | DATE_TYPES | DATETIME_TYPES | TEXT_TYPES) or cint(df.permlevel) > 0:
        frappe.throw(_("{0} cannot be used in a selection on {1}").format(fieldname, meta.name))
    return df.fieldtype


def selectable_fields(doctype, virtual=None):
    """Every field a user may put on the selection screen: standard fields, the DocType's own
    (permlevel 0) data fields, and any view-specific virtual fields."""
    meta = frappe.get_meta(doctype)
    out = [{"fieldname": f, "label": _(label), "fieldtype": ft} for f, (ft, label) in STANDARD_FIELDS.items()]
    for df in meta.fields:
        if df.fieldtype in (NUMERIC_TYPES | DATE_TYPES | DATETIME_TYPES | TEXT_TYPES) and not cint(df.permlevel) and not df.hidden:
            out.append({"fieldname": df.fieldname, "label": _(df.label or df.fieldname), "fieldtype": df.fieldtype,
                        "options": df.options if df.fieldtype in ("Select", "Link") else None})
    for fieldname, v in (virtual or {}).items():
        out.append({"fieldname": fieldname, "label": _(v["label"]), "fieldtype": v.get("fieldtype", "Data"),
                    "options": v.get("options"), "virtual": 1})
    return out


def _is_pattern(value):
    return isinstance(value, str) and ("*" in value or "+" in value)


def _like(value):
    # Escape SQL LIKE metacharacters the user typed literally, then map SAP's '*' and '+'.
    v = str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return v.replace("*", "%").replace("+", "_")


def _coerce(value, fieldtype):
    if value is None:
        return None
    if fieldtype in NUMERIC_TYPES:
        if isinstance(value, str) and not value.strip():
            return None
        try:
            return flt(value) if fieldtype != "Int" and fieldtype != "Check" else cint(flt(value))
        except Exception:
            frappe.throw(_("{0} is not a number").format(value))
    if fieldtype in DATE_TYPES:
        return str(getdate(value)) if str(value).strip() else None
    if fieldtype in DATETIME_TYPES:
        s = str(value).strip()
        if not s:
            return None
        return s if len(s) <= 10 else str(get_datetime(s))
    return str(value)


def _lit(value):
    # frappe.db.escape only takes strings; numbers are emitted as plain numeric literals.
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, (int, float)):
        return repr(value)
    return frappe.db.escape(str(value), percent=False)


def _column_expr(fieldtype, column):
    # NULL never compares, so "<> X" would silently drop blank rows; SAP treats blank as a value.
    if fieldtype in NUMERIC_TYPES:
        return f"ifnull({column}, 0)"
    if fieldtype in TEXT_TYPES:
        return f"ifnull({column}, '')"
    return column


def _row_condition(row, fieldtype, column):
    op = (row.get("op") or "eq").lower()
    if op not in OPERATORS:
        frappe.throw(_("Unknown selection operator {0}").format(op))
    low, high = row.get("low"), row.get("high")
    if op in ("eq", "ne") and _is_pattern(low) and fieldtype in TEXT_TYPES:
        op = "cp" if op == "eq" else "np"
    if op in ("cp", "np"):
        cond = f"{_column_expr(fieldtype, column)} like {frappe.db.escape(_like(low or ''), percent=False)}"
        return cond if op == "cp" else f"not ({cond})"

    lo = _coerce(low, fieldtype)
    hi = _coerce(high, fieldtype)
    col = _column_expr(fieldtype, column)
    if fieldtype in NUMERIC_TYPES and lo is None and op not in ("bt", "nb"):
        lo = 0
    if fieldtype not in NUMERIC_TYPES and lo is None:
        lo = ""

    # A date typed against a Datetime column means the whole day, as in SAP.
    if fieldtype in DATETIME_TYPES:
        def day_start(v): return f"{v} 00:00:00" if v and len(v) <= 10 else v
        def day_end(v): return f"{v} 23:59:59.999999" if v and len(v) <= 10 else v
        if op in ("eq", "ne") and lo and len(lo) <= 10:
            op, lo, hi = ("bt" if op == "eq" else "nb"), lo, lo
        if op in ("bt", "nb"):
            lo, hi = day_start(lo or "1900-01-01"), day_end(hi or lo or "2999-12-31")
        elif op in ("gt", "le"):
            lo = day_end(lo)
        elif op in ("ge", "lt"):
            lo = day_start(lo)

    if op in ("bt", "nb"):
        if lo in (None, ""):
            lo = 0 if fieldtype in NUMERIC_TYPES else ("1900-01-01" if fieldtype in DATE_TYPES | DATETIME_TYPES else "")
        if hi in (None, ""):
            hi = lo
        cond = f"{col} between {_lit(lo)} and {_lit(hi)}"
        return cond if op == "bt" else f"not ({cond})"
    sql_op = {"eq": "=", "ne": "<>", "gt": ">", "ge": ">=", "lt": "<", "le": "<="}[op]
    return f"{col} {sql_op} {_lit(lo)}"


def _rows_condition(rows, fieldtype, column):
    rows = [r for r in rows or [] if isinstance(r, dict)]
    if not rows:
        return None
    if len(rows) > MAX_VALUES_PER_FIELD:
        frappe.throw(_("Too many values in one selection ({0}); the limit is {1}").format(len(rows), MAX_VALUES_PER_FIELD))
    # A pasted list of plain values is one IN (...) rather than thousands of ORs.
    plain, other = [], []
    for r in rows:
        is_plain = ((r.get("op") or "eq") == "eq" and not (_is_pattern(r.get("low")) and fieldtype in TEXT_TYPES)
                    and not (fieldtype in DATETIME_TYPES and r.get("low") and len(str(r.get("low"))) <= 10))
        (plain if is_plain else other).append(r)
    parts = []
    if len(plain) > 1:
        values = [_coerce(r.get("low"), fieldtype) for r in plain]
        values = ["" if v is None and fieldtype not in NUMERIC_TYPES else (0 if v is None else v) for v in values]
        parts.append(f"{_column_expr(fieldtype, column)} in ({', '.join(_lit(v) for v in values)})")
    else:
        other = plain + other
    parts += [_row_condition(r, fieldtype, column) for r in other]
    return "(" + " or ".join(parts) + ")"


def compile_selection(doctype, criteria, virtual=None):
    """Returns one SQL condition string (or "" for no criteria)."""
    if isinstance(criteria, str):
        criteria = json.loads(criteria or "{}")
    virtual = virtual or {}
    meta = frappe.get_meta(doctype)
    table = f"`tab{doctype}`"
    clauses = []
    for fieldname, spec in (criteria or {}).items():
        if not spec:
            continue
        if not isinstance(spec, dict):
            spec = {"include": [{"op": "eq", "low": spec}]}
        fieldtype = _field_type(meta, fieldname, virtual)
        if fieldname in virtual:
            v = virtual[fieldname]
            column = v["column"]
            wrap = lambda cond, v=v: v["sql"].format(cond=cond)  # noqa: E731
        else:
            column = f"{table}.`{fieldname}`"
            wrap = lambda cond: cond  # noqa: E731
        inc = _rows_condition(spec.get("include"), fieldtype, column)
        exc = _rows_condition(spec.get("exclude"), fieldtype, column)
        if inc:
            clauses.append(wrap(inc))
        if exc:
            clauses.append(f"not {wrap(exc)}")
    return " and ".join(clauses)


class _SelectionQuery(DatabaseQuery):
    def __init__(self, doctype, extra_condition):
        super().__init__(doctype)
        self._extra_condition = extra_condition

    def build_conditions(self):
        super().build_conditions()
        if self._extra_condition:
            self.conditions.append(f"({self._extra_condition})")


def run_selection(doctype, criteria, fields, *, base_filters=None, virtual=None, order_by=None, max_hits=500):
    condition = compile_selection(doctype, criteria, virtual)
    max_hits = min(cint(max_hits) or 500, HARD_MAX_HITS)
    return _SelectionQuery(doctype, condition).execute(
        fields=fields, filters=base_filters or {}, order_by=order_by or f"`tab{doctype}`.`modified` desc",
        limit_page_length=max_hits,
    )
