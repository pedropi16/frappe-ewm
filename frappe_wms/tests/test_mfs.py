import socketserver
import threading
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.services import mfs
from frappe_wms.services.stock import post_entries


def record_call(values, telegram_type):
    frappe.flags.mfs_called = dict(values)


class _AckServer(socketserver.TCPServer):
    allow_reuse_address = True


class TestMFS(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.wh = "WMS-TEST-MFS-WH"
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, order_by="creation asc", limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        if not frappe.db.exists("WMS Warehouse", cls.wh):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.wh, "warehouse_name": cls.wh, "company": company, "default_stock_type": "AVAILABLE", "allow_negative_stock": 1}).insert(ignore_permissions=True)
        cls.st = f"{cls.wh}-ST"
        if not frappe.db.exists("Storage Type", cls.st):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.wh, "storage_type_code": "ST", "storage_type_name": "ST", "storage_role": "Storage", "capacity_check_method": "None", "active": 1, "hu_requirement": "Optional"}).insert(ignore_permissions=True)
        cls.bins = []
        for n in (1, 2):
            name = f"{cls.wh}-B{n}"
            cls.bins.append(name)
            if not frappe.db.exists("Storage Bin", name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": name, "warehouse": cls.wh, "storage_type": cls.st, "active": 1, "sequence": n}).insert(ignore_permissions=True)

    def _endpoint(self, port, **kw):
        code = f"PLC-{frappe.generate_hash(length=5)}"
        return frappe.get_doc({"doctype": "WMS MFS Endpoint", "endpoint_code": code, "endpoint_name": code, "mode": "Client", "host": "127.0.0.1", "port": port, "terminator": "\\n", "timeout_seconds": 2, "active": 1, **kw}).insert(ignore_permissions=True)

    def _types(self, endpoint):
        out = frappe.get_doc({"doctype": "WMS MFS Telegram Type", "type_code": f"OUT-{endpoint.name}", "endpoint": endpoint.name, "direction": "Outbound", "active": 1, "fields": [
            {"field_name": "id", "start": 0, "length": 4, "constant": "MOVE"}, {"field_name": "seq", "start": 4, "length": 5, "field_type": "Integer"},
            {"field_name": "hu", "start": 9, "length": 12}, {"field_name": "dest", "start": 21, "length": 8}]}).insert(ignore_permissions=True)
        inbound = frappe.get_doc({"doctype": "WMS MFS Telegram Type", "type_code": f"IN-{endpoint.name}", "endpoint": endpoint.name, "direction": "Inbound", "active": 1, "identifier_start": 0, "identifier_length": 4, "identifier_value": "DONE",
            "inbound_action": "Confirm Warehouse Task", "fields": [{"field_name": "id", "start": 0, "length": 4}, {"field_name": "task", "start": 4, "length": 18}, {"field_name": "quantity", "start": 22, "length": 6, "field_type": "Decimal"}]}).insert(ignore_permissions=True)
        return out, inbound

    def test_build_and_parse_use_the_fixed_layout(self):
        endpoint = self._endpoint(1)
        out, inbound = self._types(endpoint)
        raw = mfs.build(out.name, {"seq": 42, "hu": "HU123", "dest": "RACK-7"})
        self.assertEqual(raw, "MOVE00042HU123       RACK-7  ")
        with self.assertRaises(frappe.ValidationError): mfs.build(out.name, {"seq": 1, "hu": "WAY-TOO-LONG-FOR-IT", "dest": "X"})
        self.assertEqual(mfs.parse(inbound.name, "DONEWT-0000000123      5.5  "), {"id": "DONE", "task": "WT-0000000123", "quantity": 5.5})

    def test_a_telegram_is_sent_over_tcp_and_the_acknowledgement_is_logged(self):
        received = []

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                received.append(self.rfile.readline().decode().rstrip("\n"))
                self.wfile.write(b"ACK0\n")

        server = _AckServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        endpoint = self._endpoint(server.server_address[1], wait_for_ack=1)
        out, _inbound = self._types(endpoint)
        log = frappe.get_doc("WMS MFS Telegram", mfs.send(out.name, {"seq": 1, "hu": "HU9", "dest": "D1"}, "Handling Unit", "HU9"))
        thread.join(5); server.server_close()
        self.assertEqual(received, ["MOVE00001HU9         D1      "])
        self.assertEqual((log.status, log.acknowledgement, log.attempts, log.reference_name), ("Acknowledged", "ACK0", 1, "HU9"))

    def test_an_unreachable_plc_fails_the_telegram_and_the_scheduler_retries_it(self):
        endpoint = self._endpoint(1)  # nothing listens on port 1
        out, _inbound = self._types(endpoint)
        log = frappe.get_doc("WMS MFS Telegram", mfs.send(out.name, {"seq": 2, "hu": "H", "dest": "D"}))
        self.assertEqual((log.status, log.attempts), ("Failed", 1))
        mfs.retry_failed()
        self.assertEqual(frappe.db.get_value("WMS MFS Telegram", log.name, "attempts"), 2)

    def test_an_inbound_telegram_confirms_the_warehouse_task_it_names(self):
        endpoint = self._endpoint(1)
        _out, inbound = self._types(endpoint)
        post_entries([{"warehouse": self.wh, "product": self.item, "storage_bin": self.bins[0], "stock_type": "AVAILABLE", "stock_uom": self.uom, "quantity": 5, "movement_type": "701"}], "Storage Bin", self.bins[0], f"test-mfs:{frappe.generate_hash(length=6)}")
        task = frappe.get_doc({"doctype": "Warehouse Task", "task_type": "Internal Move", "warehouse": self.wh, "product": self.item, "planned_quantity": 5, "stock_uom": self.uom, "source_bin": self.bins[0], "destination_bin": self.bins[1],
            "stock_type_from": "AVAILABLE", "stock_type_to": "AVAILABLE", "movement_type": "301", "priority": "Normal", "status": "Open"}).insert(ignore_permissions=True)
        raw = "DONE" + task.name.ljust(18) + "5".ljust(6)
        log = frappe.get_doc("WMS MFS Telegram", mfs.receive(endpoint.name, raw))
        self.assertEqual((log.status, log.telegram_type), ("Processed", inbound.name))
        self.assertEqual(frappe.db.get_value("Warehouse Task", task.name, "status"), "Confirmed")
        unknown = frappe.get_doc("WMS MFS Telegram", mfs.receive(endpoint.name, "ZZZZ nothing"))
        self.assertEqual(unknown.status, "Failed")

    def test_call_method_handler_and_the_ppf_action_send_a_telegram(self):
        endpoint = self._endpoint(1)
        out, _inbound = self._types(endpoint)
        handler = frappe.get_doc({"doctype": "WMS MFS Telegram Type", "type_code": f"CM-{endpoint.name}", "endpoint": endpoint.name, "direction": "Inbound", "active": 1, "identifier_start": 0, "identifier_length": 4, "identifier_value": "PING",
            "inbound_action": "Call Method", "action_method": "Recorder", "fields": [{"field_name": "who", "start": 4, "length": 6}]}).insert(ignore_permissions=True)
        real = frappe.get_hooks
        with patch("frappe.get_hooks", side_effect=lambda name=None, *a, **k: {"wms_mfs_handlers": {"Recorder": ["frappe_wms.tests.test_mfs.record_call"]}}.get(name, real(name, *a, **k))):
            self.assertEqual(frappe.get_doc("WMS MFS Telegram", mfs.receive(endpoint.name, "PINGalice ")).status, "Processed")
        self.assertEqual(frappe.flags.mfs_called, {"who": "alice"})
        from frappe_wms.services.ppf import ACTIONS
        doc = frappe._dict(doctype="Handling Unit", name="HU77")
        message = ACTIONS["Send MFS Telegram"](doc, {"type": out.name, "values": {"seq": "7", "hu": "{{ doc.name }}", "dest": "OUT"}}, "After Insert")
        self.assertTrue(message.startswith("telegram MFS-"))
        self.assertEqual(frappe.db.get_value("WMS MFS Telegram", message.split()[1], "raw"), "MOVE00007HU77        OUT     ")

    def test_the_listener_processes_telegrams_a_plc_sends_over_a_connection(self):
        import socket
        endpoint = self._endpoint(0, mode="Server", host="127.0.0.1")
        handler = frappe.get_doc({"doctype": "WMS MFS Telegram Type", "type_code": f"LS-{endpoint.name}", "endpoint": endpoint.name, "direction": "Inbound", "active": 1, "identifier_start": 0, "identifier_length": 4, "identifier_value": "PING",
            "inbound_action": "Call Method", "action_method": "Recorder", "fields": [{"field_name": "who", "start": 4, "length": 6}]}).insert(ignore_permissions=True)
        server = socketserver.TCPServer(("127.0.0.1", 0), mfs._Handler)
        server.wms_endpoint = frappe.get_doc("WMS MFS Endpoint", endpoint.name)

        def plc():
            with socket.create_connection(server.server_address, timeout=3) as s:
                s.sendall(b"PINGbob   \nPINGeve   \n")

        thread = threading.Thread(target=plc, daemon=True)
        thread.start()
        real = frappe.get_hooks
        with patch("frappe.get_hooks", side_effect=lambda name=None, *a, **k: {"wms_mfs_handlers": {"Recorder": ["frappe_wms.tests.test_mfs.record_call"]}}.get(name, real(name, *a, **k))):
            server.handle_request()  # one connection, however many telegrams it carries
        thread.join(3); server.server_close()
        self.assertEqual(sorted(frappe.get_all("WMS MFS Telegram", filters={"endpoint": endpoint.name, "status": "Processed"}, pluck="raw")), ["PINGbob   ", "PINGeve   "])
        self.assertTrue(handler.name)
