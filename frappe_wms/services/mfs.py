"""Material Flow System: fixed-layout telegrams between the WMS and PLCs over TCP (SAP EWM MFS).

A WMS MFS Endpoint is a PLC connection (the WMS connects as client, or listens as server). A WMS MFS Telegram Type describes one telegram's layout (fields at
fixed positions) and, for inbound ones, what it does. Outbound telegrams are built from values and sent (optionally waiting for an acknowledgement);
everything sent or received is logged in WMS MFS Telegram, failed sends are retried by the scheduler. Telegrams are started by a PPF action
(Send MFS Telegram) or by code; inbound ones can confirm a warehouse task, confirm a yard task or call a handler registered under hooks.py wms_mfs_handlers.
"""
import json
import socket
import socketserver

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

MAX_ATTEMPTS = 5


def _terminator(endpoint):
    return (endpoint.terminator or "").encode().decode("unicode_escape")


def _numeric(f):
    return f.field_type in ("Integer", "Decimal")


def build(type_name, values):
    """The telegram text of an outbound type for the given values."""
    t = frappe.get_cached_doc("WMS MFS Telegram Type", type_name)
    length = max((f.start or 0) + (f.length or 0) for f in t.fields)
    buf = [" "] * length
    for f in t.fields:
        raw = f.constant if f.constant not in (None, "") else values.get(f.field_name)
        if raw is None: frappe.throw(_("Telegram {0}: no value for {1}").format(type_name, f.field_name))
        text = str(int(flt(raw))) if f.field_type == "Integer" else str(raw)
        if len(text) > f.length: frappe.throw(_("Telegram {0}: {1} ({2}) is longer than its {3} positions").format(type_name, f.field_name, text, f.length))
        pad = f.pad or ("0" if _numeric(f) else " ")
        text = text.ljust(f.length, pad) if (f.justify or ("Right" if _numeric(f) else "Left")) == "Left" else text.rjust(f.length, pad)
        buf[f.start:f.start + f.length] = list(text)
    return "".join(buf)


def parse(type_name, raw):
    """{field: value} of a received telegram (numbers converted, text stripped)."""
    t = frappe.get_cached_doc("WMS MFS Telegram Type", type_name)
    out = {}
    for f in t.fields:
        text = raw[f.start:f.start + f.length].strip()
        out[f.field_name] = (int(text) if text.lstrip("-").isdigit() else flt(text)) if f.field_type == "Integer" and text else (flt(text) if f.field_type == "Decimal" and text else text)
    return out


def identify(endpoint, raw):
    for name in frappe.get_all("WMS MFS Telegram Type", filters={"endpoint": endpoint, "direction": "Inbound", "active": 1}, pluck="name"):
        t = frappe.get_cached_doc("WMS MFS Telegram Type", name)
        if t.identifier_value and raw[t.identifier_start or 0:(t.identifier_start or 0) + (t.identifier_length or len(t.identifier_value))] == t.identifier_value: return name
    return None


# ------------------------------------------------------------------ sending

def _read_frame(sock, endpoint):
    term, length, data = _terminator(endpoint).encode(), cint(endpoint.telegram_length), b""
    while True:
        chunk = sock.recv(4096)
        if not chunk: break
        data += chunk
        if length and len(data) >= length: return data[:length].decode(endpoint.encoding or "ascii")
        if term and term in data: return data.split(term)[0].decode(endpoint.encoding or "ascii")
    return data.decode(endpoint.encoding or "ascii") if data else None


def _deliver(log_name):
    log = frappe.get_doc("WMS MFS Telegram", log_name)
    endpoint = frappe.get_cached_doc("WMS MFS Endpoint", log.endpoint)
    log.attempts = cint(log.attempts) + 1
    try:
        with socket.create_connection((endpoint.host or "127.0.0.1", endpoint.port), timeout=cint(endpoint.timeout_seconds) or 5) as sock:
            sock.sendall((log.raw + _terminator(endpoint)).encode(endpoint.encoding or "ascii"))
            log.status, log.error = "Sent", None
            if endpoint.wait_for_ack:
                ack = _read_frame(sock, endpoint)
                log.acknowledgement = ack
                if ack: log.status = "Acknowledged"
    except OSError as e:
        log.status, log.error = "Failed", str(e)[:500]
    log.save(ignore_permissions=True)
    return log.status


def send(type_name, values, reference_doctype=None, reference_name=None):
    t = frappe.get_cached_doc("WMS MFS Telegram Type", type_name)
    if t.direction != "Outbound" or not t.active: frappe.throw(_("{0} is not an active outbound telegram type").format(type_name))
    log = frappe.get_doc({"doctype": "WMS MFS Telegram", "endpoint": t.endpoint, "telegram_type": type_name, "direction": "Outbound", "status": "Queued", "raw": build(type_name, values),
        "payload": json.dumps(values, default=str), "reference_doctype": reference_doctype, "reference_name": reference_name}).insert(ignore_permissions=True)
    _deliver(log.name)
    return log.name


def retry_failed():
    """Scheduler: outbound telegrams that could not be delivered go out again (up to MAX_ATTEMPTS)."""
    for name in frappe.get_all("WMS MFS Telegram", filters={"direction": "Outbound", "status": "Failed", "attempts": ["<", MAX_ATTEMPTS]}, pluck="name", limit=50):
        _deliver(name)


# ------------------------------------------------------------------ receiving

def handlers():
    registry = {}
    for name, paths in (frappe.get_hooks("wms_mfs_handlers") or {}).items():
        path = paths[-1] if isinstance(paths, list) else paths
        registry[name] = frappe.get_attr(path) if isinstance(path, str) else path
    return registry


def receive(endpoint, raw):
    """Process one received telegram: log it, identify its type, run the type's action."""
    raw = (raw or "").rstrip("\r\n")
    log = frappe.get_doc({"doctype": "WMS MFS Telegram", "endpoint": endpoint, "direction": "Inbound", "status": "Received", "raw": raw}).insert(ignore_permissions=True)
    type_name = identify(endpoint, raw)
    if not type_name:
        log.db_set({"status": "Failed", "error": _("No telegram type matches this telegram")})
        return log.name
    t = frappe.get_cached_doc("WMS MFS Telegram Type", type_name)
    values = parse(type_name, raw)
    log.db_set({"telegram_type": type_name, "payload": json.dumps(values, default=str)})
    frappe.db.savepoint("wms_mfs")
    try:
        if t.inbound_action == "Confirm Warehouse Task":
            from frappe_wms.services.task import confirm_task
            confirm_task(values["task"], confirmed_quantity=values.get("quantity") or None, verify=False)  # the PLC confirms what it moved: nobody to scan
        elif t.inbound_action == "Confirm Yard Task":
            from frappe_wms.services.transport_unit import confirm_yard_move
            confirm_yard_move(values["task"])
        elif t.inbound_action == "Call Method":
            fn = handlers().get(t.action_method)
            if not fn: frappe.throw(_("{0} is not a registered MFS handler (hooks.py wms_mfs_handlers)").format(t.action_method))
            fn(values, t)
        log.db_set({"status": "Processed", "processed_at": now_datetime(), "error": None})
    except Exception as e:
        frappe.db.rollback(save_point="wms_mfs")
        frappe.clear_messages()
        log.db_set({"status": "Failed", "error": str(e)[:500]})
    return log.name


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        endpoint = self.server.wms_endpoint
        term, length = _terminator(endpoint).encode(), cint(endpoint.telegram_length)
        data = b""
        while True:
            chunk = self.request.recv(4096)
            if not chunk: break
            data += chunk
            while True:
                if length and len(data) >= length: frame, data = data[:length], data[length:]
                elif term and term in data: frame, data = data.split(term, 1)
                else: break
                receive(endpoint.name, frame.decode(endpoint.encoding or "ascii"))
                if not frappe.flags.in_test: frappe.db.commit()


def listen(endpoint):
    """Run on a server-mode endpoint (bench --site <site> execute frappe_wms.services.mfs.listen --kwargs "{'endpoint': 'PLC1'}", e.g. as a supervised process):
    accepts PLC connections and processes every telegram received until stopped."""
    doc = frappe.get_doc("WMS MFS Endpoint", endpoint)
    if doc.mode != "Server": frappe.throw(_("{0} is a client endpoint").format(endpoint))
    server = socketserver.TCPServer((doc.host or "0.0.0.0", doc.port), _Handler)
    server.wms_endpoint = doc
    server.serve_forever()
