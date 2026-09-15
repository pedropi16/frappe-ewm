from frappe import _

def get_data():
    return [{
        "module_name": "WMS Core",
        "type": "module",
        "label": _("Frappe WMS"),
        "color": "blue",
        "icon": "octicon octicon-package",
        "description": _("Warehouse management and execution"),
    }]
