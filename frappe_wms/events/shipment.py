import frappe
from frappe_wms.services.determination import determine_route

def validate_shipment(doc, method=None):
    if not doc.route: doc.route = determine_route(doc.warehouse, carrier=doc.carrier)
    if not doc.route: return
    route = frappe.get_cached_doc("WMS Route", doc.route)
    if not doc.staging_bin and route.default_staging_bin: doc.staging_bin = route.default_staging_bin
    if not doc.door and route.default_door: doc.door = route.default_door
    if not doc.carrier and route.carrier: doc.carrier = route.carrier
