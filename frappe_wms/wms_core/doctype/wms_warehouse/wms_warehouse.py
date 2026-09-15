import re

import frappe
from frappe.model.document import Document


def get_warehouse_code(warehouse_name):
	"""Derive the warehouse code from its name: 'Main DC' -> 'MAIN-DC'."""
	code = re.sub(r'[^A-Za-z0-9]+', '-', (warehouse_name or '').strip()).strip('-').upper()
	return code or None


class WMSWarehouse(Document):
	def before_insert(self):
		# Auto-fill the code from the name so the form can be saved with just a name.
		# Must run before insert (not before_validate): with autoname
		# "field:warehouse_code" the value is needed at naming time.
		if not self.get('warehouse_code') and self.get('warehouse_name'):
			self.warehouse_code = get_warehouse_code(self.warehouse_name)
		if not self.get('warehouse_code'):
			frappe.throw(
				frappe._(
					"Warehouse Code is required. Enter a name or code with letters or numbers "
					"to derive one (e.g. 'Main DC' -> 'MAIN-DC')."
				),
				title=frappe._("Missing Warehouse Code"),
			)
