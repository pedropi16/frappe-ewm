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

	def after_insert(self):
		# Every WMS Warehouse needs a matching ERPNext Warehouse so goods receipt/issue
		# postings can be mirrored for accounting and inventory valuation (see
		# frappe_wms.services.erpnext_sync). Auto-create it rather than requiring manual setup.
		if self.erpnext_warehouse:
			return
		existing = frappe.db.get_value('Warehouse', {'warehouse_name': self.warehouse_name, 'company': self.company})
		if existing:
			# A Warehouse with this name/company already exists (e.g. a previous WMS
			# Warehouse of the same name was deleted but its ledger history wasn't) -
			# link to it rather than failing on the duplicate name.
			self.db_set('erpnext_warehouse', existing, update_modified=False)
			return
		erpnext_warehouse = frappe.get_doc({
			'doctype': 'Warehouse',
			'warehouse_name': self.warehouse_name,
			'company': self.company,
		})
		erpnext_warehouse.insert(ignore_permissions=True)
		self.db_set('erpnext_warehouse', erpnext_warehouse.name, update_modified=False)
