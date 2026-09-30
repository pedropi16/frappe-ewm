import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class WMSSettings(Document):
	def validate(self):
		from frappe_wms.services.archiving import MIN_RETENTION_MONTHS
		months = cint(self.get("ledger_retention_months"))
		if 0 < months < MIN_RETENTION_MONTHS:
			frappe.throw(_("Keep stock ledger entries for at least {0} months (or 0 to keep them forever)").format(MIN_RETENTION_MONTHS))
