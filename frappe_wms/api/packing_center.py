"""WMS Monitor Packing Center - see services/packing_center.py."""
import frappe

from frappe_wms.services import packing_center as pc
from frappe_wms.services.concurrency import retry_on_deadlock
from frappe_wms.services.locks import guard
from frappe_wms.utils import parse_json, require_wms_access


def _check(warehouse):
    require_wms_access()
    frappe.get_doc("WMS Warehouse", warehouse).check_permission("read")


@frappe.whitelist()
@retry_on_deadlock
def packing_tree(warehouse, bins, hus=None, balances=None, extra_hus=None):
    _check(warehouse)
    lists = [parse_json(x, "list") if x not in (None, "") else None for x in (hus, balances, extra_hus)]
    return pc.packing_tree(warehouse, parse_json(bins, "bins"), *lists)


@frappe.whitelist()
@retry_on_deadlock
def move_nodes(warehouse, items, destination_kind, destination, idempotency_key):
    _check(warehouse)
    return pc.move_nodes(warehouse, parse_json(items, "items"), destination_kind, destination, idempotency_key)


@frappe.whitelist()
@retry_on_deadlock
@guard("Handling Unit", "parent_hu")
def create_hus(warehouse, storage_bin=None, packaging_material=None, hu_type=None, hu_number=None, quantity=1, parent_hu=None):
    _check(warehouse)
    return pc.create_hus(warehouse, storage_bin, packaging_material, hu_type, hu_number, quantity, parent_hu)


@frappe.whitelist()
def packing_materials():
    """Active packing materials with the HU type each implies (and whether it numbers itself)."""
    require_wms_access()
    rows = frappe.get_all("Packaging Material", filters={"active": 1}, fields=["name", "packaging_material_name", "hu_type", "tare_weight"], order_by="name asc")
    for r in rows:
        r["numbering_mode"] = frappe.db.get_value("Handling Unit Type", r.hu_type, "numbering_mode") if r.hu_type else None
    return rows


@frappe.whitelist()
@retry_on_deadlock
def post_differences(warehouse, items, remarks=None, idempotency_key=None):
    _check(warehouse)
    return pc.post_differences(warehouse, parse_json(items, "items"), remarks, idempotency_key or frappe.generate_hash(length=12))
