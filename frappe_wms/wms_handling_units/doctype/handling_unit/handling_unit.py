import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime
from frappe_wms.services.numbering import next_number
from frappe_wms.services.printing import create_print_spool


class HandlingUnit(Document):
    # Mirrors SAP EWM: the only things an operator must actually supply are a Storage Bin (or a
    # parent HU to nest into) and an HU Type (directly, or implied by a Packaging Material) -
    # Warehouse and HU Number are always derivable, never asked for.
    def before_insert(self):
        if self.packaging_material and not self.hu_type:
            self.hu_type = frappe.db.get_value("Packaging Material", self.packaging_material, "hu_type")
        if not self.hu_type:
            frappe.throw(_("HU Type is required (directly, or via a Packaging Material)"))

        if self.parent_hu:
            parent = frappe.db.get_value("Handling Unit", self.parent_hu, ["current_bin", "warehouse"], as_dict=True)
            if not parent:
                frappe.throw(_("Parent Handling Unit {0} not found").format(self.parent_hu))
            self.current_bin = self.current_bin or parent.current_bin
            self.warehouse = self.warehouse or parent.warehouse
            if self.current_bin != parent.current_bin:
                frappe.throw(_("A nested Handling Unit must be created in its parent's bin"))
        if not self.current_bin:
            frappe.throw(_("A Storage Bin or a Parent Handling Unit is required"))
        if not self.warehouse:
            self.warehouse = frappe.db.get_value("Storage Bin", self.current_bin, "warehouse")
        if not self.warehouse:
            frappe.throw(_("Cannot determine a Warehouse for Storage Bin {0}").format(self.current_bin))

        if self.packaging_material and not self.tare_weight:
            self.tare_weight = frappe.db.get_value("Packaging Material", self.packaging_material, "tare_weight") or 0

        # Internal HU Types are always system-numbered - any number the caller passed (e.g. a
        # stale form value) is silently replaced. External types keep whatever was scanned/typed.
        numbering_mode = frappe.db.get_value("Handling Unit Type", self.hu_type, "numbering_mode") or "External"
        if numbering_mode == "Internal":
            self.hu_number = next_number("Handling Unit", warehouse=self.warehouse, hu_type=self.hu_type)
        elif not self.hu_number:
            frappe.throw(_("HU Type {0} uses external numbering; an HU Number is required").format(self.hu_type))

        if not self.status: self.status = "Created"
        if not self.stock_status: self.stock_status = "Empty"

    def after_insert(self):
        frappe.get_doc({
            "doctype": "Handling Unit Event", "handling_unit": self.name, "event_type": "Created",
            "bin_after": self.current_bin, "parent_hu_after": self.parent_hu, "status_after": self.status,
            "event_timestamp": now_datetime(), "performed_by": frappe.session.user,
        }).insert(ignore_permissions=True)
        create_print_spool("Handling Unit", self.name, "HU Created", self.warehouse)
