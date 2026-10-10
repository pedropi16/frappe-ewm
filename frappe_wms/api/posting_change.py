import frappe
from frappe_wms.services.locks import guard
from frappe_wms.services.concurrency import retry_on_deadlock
from frappe_wms.services.posting_change import (
    post_posting_change as _post_posting_change,
    cancel_posting_change as _cancel_posting_change,
)


@frappe.whitelist()
@retry_on_deadlock
@guard("WMS Posting Change", "name")
def post_posting_change(name):
    return _post_posting_change(name)


@frappe.whitelist()
@retry_on_deadlock
@guard("WMS Posting Change", "name")
def cancel_posting_change(name):
    return _cancel_posting_change(name)


@frappe.whitelist()
@retry_on_deadlock
def process_lines(lines):
    """Posting changes of the worklist lines: each {name: WMS Stock Balance, quantity?, to_stock_type, to_stock_owner, ..., reason}."""
    from frappe_wms.services.posting_change import process_lines as _process
    from frappe_wms.utils import parse_json
    return _process(parse_json(lines, "lines"))


@frappe.whitelist()
@retry_on_deadlock
def check_lines(lines):
    """Enter on the worklist: what each line would change and where it would go, or why it is refused - nothing is created."""
    from frappe_wms.services.posting_change import check_lines as _check
    from frappe_wms.utils import parse_json
    return _check(parse_json(lines, "lines"))
