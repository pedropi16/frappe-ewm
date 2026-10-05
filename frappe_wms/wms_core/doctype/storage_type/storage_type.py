from frappe.model.document import Document


class StorageType(Document):
    def validate(self):
        # HU Requirement supersedes the hidden HU managed flag (default was "managed"); keep the two in step so code that still reads the flag is right.
        if self.is_new():
            if self.hu_managed is not None:  # set explicitly by an older caller
                self.hu_requirement = "Mandatory" if self.hu_managed else (self.hu_requirement if self.hu_requirement != "Mandatory" else "Optional")
        elif self.has_value_changed("hu_managed") and not self.has_value_changed("hu_requirement"):
            self.hu_requirement = "Mandatory" if self.hu_managed else (self.hu_requirement if self.hu_requirement != "Mandatory" else "Optional")
        self.hu_managed = 1 if self.hu_requirement == "Mandatory" else 0
