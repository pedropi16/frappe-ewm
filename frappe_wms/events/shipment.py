import frappe
from frappe import _
from frappe_wms.services.determination import determine_route
from frappe_wms.utils import require_storage_role

def validate_shipment(doc, method=None):
    if not doc.route: doc.route = determine_route(doc.warehouse, carrier=doc.carrier)
    if doc.route:
        route = frappe.get_cached_doc("WMS Route", doc.route)
        if not doc.staging_bin and route.default_staging_bin: doc.staging_bin = route.default_staging_bin
        if not doc.door and route.default_door: doc.door = route.default_door
        if not doc.carrier and route.carrier: doc.carrier = route.carrier
    if doc.door: require_storage_role(doc.door, "Door", label=_("Door"))
