import frappe
from frappe import _
from frappe.model.document import Document


class ProductionSupplyControlCycle(Document):
    def validate(self):
        from frappe_wms.services.production_supply import psa_bins
        if self.staging_bin and self.staging_bin not in psa_bins(self.production_supply_area):
            frappe.throw(_("{0} is not a bin of Production Supply Area {1}").format(self.staging_bin, self.production_supply_area))
        if self.staging_method == "Kanban":
            if not self.staging_bin: frappe.throw(_("A kanban control cycle needs its kanban bin as the staging bin"))
            if not self.source_storage_type: frappe.throw(_("A kanban control cycle needs the storage type its bin is refilled from"))
            if not (self.maximum_quantity and self.maximum_quantity > (self.minimum_quantity or 0)): frappe.throw(_("Kanban needs a maximum quantity above the minimum"))
        if self.staging_method == "Crate Parts" and not (self.maximum_quantity and self.maximum_quantity > (self.minimum_quantity or 0)):
            frappe.throw(_("Crate Parts needs a maximum quantity above the minimum"))

    def on_update(self):
        from frappe_wms.services.production_supply import sync_kanban_rule
        if self.staging_method == "Kanban" or self.kanban_rule: sync_kanban_rule(self)
