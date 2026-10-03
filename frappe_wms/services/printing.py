"""Print output: spool entries created by WMS Print Determination Rules (or a manual reprint),
rendered to ZPL or PDF and delivered to the printer.

Printers are WMS Resources of type Printer:
  Network (Raw TCP)  the server sends the job straight to printer_host:printer_port (9100)
                     right after the transaction commits; failures retry from the scheduler.
  Print Agent        the on-site agent (frappe_wms/print_agent/wms_print_agent.py) claims jobs
                     through agent_claim(), prints them locally and reports back - for printers
                     the server cannot reach and for PDF.
"""
import base64
import socket

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, now_datetime

from frappe_wms.utils import require_role

PRINT_ROLES = ("WMS Operator", "WMS Supervisor", "WMS Process Engineer", "WMS Integration User", "WMS Packer")
DEFAULT_PDF_FORMATS = {"Outbound Delivery": "WMS Packing List", "WMS Shipment": "WMS Shipment Manifest", "Handling Unit": "WMS HU Label", "Storage Bin": "WMS Bin Label"}
MAX_DIRECT_ATTEMPTS = 5


def _matching_rule(warehouse, event):
    # Same "priority ascending, first full match wins" pattern as Process/Bin Determination
    # Rule (services/determination.py) - kept here rather than folded into that module since
    # print determination only ever matches on warehouse + event, not the wider item/storage
    # dimensions the other rule tables support.
    return frappe.db.get_value("WMS Print Determination Rule",
        {"active": 1, "warehouse": warehouse, "event": event}, ["name", "output_device", "print_format"],
        order_by="priority asc", as_dict=True)


def _device(name):
    return frappe.db.get_value("WMS Resource", name, ["name", "resource_type", "printer_connection", "printer_language", "printer_host",
                                                       "printer_port", "agent_printer_name", "active"], as_dict=True) if name else None


def _queue(reference_doctype, reference_name, event, warehouse, output_device, print_format):
    spool = frappe.get_doc({
        "doctype": "WMS Print Spool", "warehouse": warehouse, "event": event,
        "reference_doctype": reference_doctype, "reference_name": reference_name,
        "output_device": output_device, "print_format": print_format,
        "status": "Queued", "requested_at": now_datetime(), "requested_by": frappe.session.user,
    })
    spool.insert(ignore_permissions=True)
    device = _device(output_device)
    if device and device.printer_connection == "Network (Raw TCP)" and not frappe.flags.in_test:
        frappe.enqueue("frappe_wms.services.printing.send_direct", queue="short", spool_name=spool.name, enqueue_after_commit=True)
    return spool.name


def create_print_spool(reference_doctype, reference_name, event, warehouse):
    # Printing is opt-in, exactly like WMS Number Range: no matching Print Determination Rule
    # for this warehouse+event means nothing gets queued, not an error - configure a rule only
    # where you actually want this event to print something.
    rule = _matching_rule(warehouse, event)
    if not rule: return None
    return _queue(reference_doctype, reference_name, event, warehouse, rule.output_device, rule.print_format)


def request_print(reference_doctype, reference_name, output_device=None, print_format=None):
    """A manual print / reprint (Repack Center "Print label", Monitor actions). Without an
    explicit printer, the warehouse's Print Determination Rule for event "Manual" decides."""
    require_role(*PRINT_ROLES)
    doc = frappe.get_doc(reference_doctype, reference_name)
    warehouse = doc.get("warehouse")
    if not output_device:
        rule = _matching_rule(warehouse, "Manual")
        if not rule:
            frappe.throw(_("Choose a printer, or set up a WMS Print Determination Rule for event Manual in warehouse {0}").format(warehouse))
        output_device, print_format = rule.output_device, print_format or rule.print_format
    device = _device(output_device)
    if not device or device.resource_type != "Printer": frappe.throw(_("{0} is not a printer").format(output_device))
    return _queue(reference_doctype, reference_name, "Manual", warehouse, output_device, print_format)


# ------------------------------------------------------------------ rendering

def render_spool(spool):
    """-> {"format": "zpl"|"pdf", "content": text or base64, "filename"}"""
    device = _device(spool.output_device) or frappe._dict(printer_language="ZPL")
    language = device.printer_language or "ZPL"
    if spool.print_format and frappe.db.get_value("Print Format", spool.print_format, "raw_printing"):
        doc = frappe.get_doc(spool.reference_doctype, spool.reference_name)
        commands = frappe.db.get_value("Print Format", spool.print_format, "raw_commands") or ""
        return {"format": "zpl", "content": frappe.render_template(commands, {"doc": doc}), "filename": f"{spool.name}.zpl"}
    if language == "ZPL":
        return {"format": "zpl", "content": _zpl_for(spool.reference_doctype, spool.reference_name), "filename": f"{spool.name}.zpl"}
    pdf = frappe.get_print(spool.reference_doctype, spool.reference_name, print_format=spool.print_format or DEFAULT_PDF_FORMATS.get(spool.reference_doctype),
                           as_pdf=True, no_letterhead=0)
    return {"format": "pdf", "content": base64.b64encode(pdf).decode(), "filename": f"{spool.name}.pdf"}


def _zpl_for(doctype, name):
    from frappe_wms.services.labeling import render_hu_label_zpl
    if doctype == "Handling Unit":
        return render_hu_label_zpl(name)
    if doctype == "WMS Shipment":
        hus = frappe.get_all("Shipment Handling Unit", filters={"parent": name}, pluck="handling_unit", order_by="load_sequence asc")
        return "\n".join(render_hu_label_zpl(h) for h in hus)
    if doctype == "Outbound Delivery":
        from frappe_wms.services.shipping import delivery_handling_units
        return "\n".join(render_hu_label_zpl(h) for h in delivery_handling_units([name]))
    frappe.throw(_("{0} has no label layout - print it on a PDF printer or give the rule a raw (ZPL) Print Format").format(doctype))


# ------------------------------------------------------------------ direct network printing

def send_direct(spool_name):
    """Server -> printer over raw TCP. Commits its own outcome."""
    frappe.db.sql("update `tabWMS Print Spool` set status='Printing', claimed_at=%s where name=%s and status in ('Queued','Failed')",
                            (now_datetime(), spool_name))
    spool = frappe.get_doc("WMS Print Spool", spool_name)
    if spool.status != "Printing": return spool.status
    device = _device(spool.output_device)
    try:
        if not device or not device.printer_host: frappe.throw(_("Printer {0} has no network address").format(spool.output_device))
        payload = render_spool(spool)
        data = payload["content"].encode("utf-8") if payload["format"] == "zpl" else base64.b64decode(payload["content"])
        with socket.create_connection((device.printer_host, cint(device.printer_port) or 9100), timeout=10) as conn:
            conn.sendall(data)
    except Exception as e:
        spool.db_set({"status": "Failed", "attempts": cint(spool.attempts) + 1, "remarks": str(e)[:500]}, update_modified=True)
    else:
        spool.db_set({"status": "Printed", "printed_at": now_datetime(), "printed_by": frappe.session.user, "remarks": None}, update_modified=True)
    if not frappe.flags.in_test: frappe.db.commit()
    return frappe.db.get_value("WMS Print Spool", spool_name, "status")


def retry_print_jobs():
    """Scheduler: network jobs that failed (a printer was off), and agent jobs claimed but
    never confirmed (the agent died mid-job) go back into the queue."""
    stale = add_to_date(now_datetime(), minutes=-10)
    frappe.db.sql("update `tabWMS Print Spool` set status='Queued' where status='Printing' and claimed_at < %s", stale)
    for row in frappe.db.sql("""select s.name from `tabWMS Print Spool` s join `tabWMS Resource` r on r.name = s.output_device
            where r.printer_connection = 'Network (Raw TCP)' and s.status in ('Queued', 'Failed') and ifnull(s.attempts, 0) < %s
            order by s.requested_at limit 200""", MAX_DIRECT_ATTEMPTS, as_dict=True):
        send_direct(row.name)


# ------------------------------------------------------------------ print agent

def agent_claim(output_device, limit=10):
    """The on-site agent takes the next jobs for its printer, rendered and ready to send."""
    require_role(*PRINT_ROLES)
    device = _device(output_device)
    if not device or device.resource_type != "Printer": frappe.throw(_("{0} is not a printer").format(output_device))
    jobs = []
    for name in frappe.get_all("WMS Print Spool", filters={"output_device": output_device, "status": "Queued"}, pluck="name",
                               order_by="requested_at asc", limit=cint(limit) or 10):
        frappe.db.sql("update `tabWMS Print Spool` set status='Printing', claimed_at=%s where name=%s and status='Queued'", (now_datetime(), name))
        spool = frappe.get_doc("WMS Print Spool", name)
        if spool.status != "Printing": continue  # another agent was faster
        try:
            payload = render_spool(spool)
        except Exception as e:
            spool.db_set({"status": "Failed", "attempts": cint(spool.attempts) + 1, "remarks": str(e)[:500]})
            continue
        jobs.append({"spool": name, "printer": device.agent_printer_name, **payload})
    return jobs


def list_queued_spools(warehouse=None, output_device=None):
    require_role(*PRINT_ROLES)
    filters = {"status": "Queued"}
    if warehouse: filters["warehouse"] = warehouse
    if output_device: filters["output_device"] = output_device
    return frappe.get_list("WMS Print Spool", filters=filters,
        fields=["name", "warehouse", "event", "reference_doctype", "reference_name",
            "output_device", "print_format", "requested_at", "requested_by"],
        order_by="requested_at asc", limit=100)


def mark_printed(spool_name):
    require_role(*PRINT_ROLES)
    spool = frappe.get_doc("WMS Print Spool", spool_name)
    if spool.status not in ("Queued", "Printing"): frappe.throw(_("Only a Queued or Printing spool entry can be marked Printed"))
    spool.db_set({"status": "Printed", "printed_at": now_datetime(), "printed_by": frappe.session.user}, update_modified=True)
    return {"spool": spool.name, "status": "Printed"}


def mark_failed(spool_name, reason=None):
    require_role(*PRINT_ROLES)
    spool = frappe.get_doc("WMS Print Spool", spool_name)
    if spool.status not in ("Queued", "Printing"): frappe.throw(_("Only a Queued or Printing spool entry can be marked Failed"))
    spool.db_set({"status": "Failed", "remarks": reason, "attempts": cint(spool.attempts) + 1}, update_modified=True)
    return {"spool": spool.name, "status": "Failed"}


def requeue(spool_name):
    require_role("WMS Supervisor", "WMS Administrator", "WMS Process Engineer")
    spool = frappe.get_doc("WMS Print Spool", spool_name)
    if spool.status not in ("Failed", "Printed", "Cancelled"): frappe.throw(_("{0} is still {1}").format(spool_name, spool.status))
    spool.db_set({"status": "Queued", "claimed_at": None, "attempts": 0, "remarks": None}, update_modified=True)
    if (_device(spool.output_device) or {}).get("printer_connection") == "Network (Raw TCP)" and not frappe.flags.in_test:
        frappe.enqueue("frappe_wms.services.printing.send_direct", queue="short", spool_name=spool.name, enqueue_after_commit=True)
    return {"spool": spool.name, "status": "Queued"}


# ------------------------------------------------------------------ print format helpers (Jinja)

def packing_list_data(delivery_name):
    """For the WMS Packing List print format: each shipping HU of the delivery with its contents."""
    from frappe_wms.services.packing_station import _node
    from frappe_wms.services.shipping import delivery_handling_units

    def flatten(node, depth=0, out=None):
        out = [] if out is None else out
        out.append({"hu": node, "depth": depth})
        for c in node.get("children") or []: flatten(c, depth + 1, out)
        return out
    rows = []
    for hu in delivery_handling_units([delivery_name]) or frappe.get_all("Handling Unit", filters={"outbound_delivery": delivery_name}, pluck="name"):
        rows += flatten(_node(hu))
    return rows
