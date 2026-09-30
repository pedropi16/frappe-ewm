from frappe.model.document import Document


class WMSDockAppointment(Document):
    def validate(self):
        from frappe_wms.services.yard import validate_appointment
        validate_appointment(self)
