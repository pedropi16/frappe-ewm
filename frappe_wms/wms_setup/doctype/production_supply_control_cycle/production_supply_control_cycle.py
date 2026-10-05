import frappe
from frappe import _
from frappe.model.document import Document


class ProductionSupplyControlCycle(Document):
    def validate(self):
        from frappe_wms.services.production_supply import psa_bins
        if self.staging_bin and self.staging_bin not in psa_bins(self.production_supply_area):
            frappe.throw(_("{0} is not a bin of Production Supply Area {1}").format(self.staging_bin, self.production_supply_area))
        if self.staging_method == "Crate Parts" and not (self.maximum_quantity and self.maximum_quantity > (self.minimum_quantity or 0)):
            frappe.throw(_("Crate Parts needs a maximum quantity above the minimum"))
