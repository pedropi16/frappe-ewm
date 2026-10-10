class WMSValidationError(Exception):
    pass

class WMSConcurrencyError(Exception):
    pass


import frappe


class ForegroundRequired(frappe.ValidationError):
    """A confirmation needs details only the user can give (which serial numbers, which batch): it cannot run in the background."""
