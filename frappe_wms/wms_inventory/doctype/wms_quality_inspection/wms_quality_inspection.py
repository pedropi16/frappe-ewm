from frappe.model.document import Document


class WMSQualityInspection(Document):
    def validate(self):
        if self.samples:
            from frappe_wms.services.quality import evaluate_samples
            self.sample_result = evaluate_samples(self)
