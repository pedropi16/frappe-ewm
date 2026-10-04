from frappe_wms.services.production_supply import on_material_request_submit


def on_submit(doc, method=None):
    on_material_request_submit(doc)
