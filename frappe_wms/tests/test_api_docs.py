import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.api.docs import WEBHOOK_EVENTS, openapi, subscribe_webhook


class TestApiDocs(IntegrationTestCase):
    def test_openapi_lists_whitelisted_endpoints_with_parameters(self):
        frappe.set_user("Administrator")
        spec = openapi()
        op = spec["paths"]["/api/method/frappe_wms.api.scanner.confirm_task"]["post"]
        schema = op["requestBody"]["content"]["application/json"]["schema"]
        self.assertEqual(schema["required"], ["task_name"])
        self.assertIn("confirmed_quantity", schema["properties"])
        self.assertTrue(op["x-idempotent-retry"])

    def test_every_catalogue_event_becomes_a_valid_webhook(self):
        frappe.set_user("Administrator")
        for event in WEBHOOK_EVENTS:
            hook = frappe.get_doc("Webhook", subscribe_webhook(event, "https://example.invalid/hook", "s3cret"))
            self.assertEqual(hook.webhook_headers[0].value, event)
