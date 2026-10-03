import frappe
from frappe import _
from frappe.utils import cint

# HU labels (SSCC / GS1-128): the print spool (services/printing.py) only ever queued an event
# with a print_format NAME - there was no SSCC on a Handling Unit anywhere, and nothing that
# actually rendered label content for one to reference. This is the missing piece: a real SSCC
# generator plus a ZPL template a print daemon can fetch on demand for a queued "HU Label" event.


def _gs1_check_digit(digits):
    # GS1 Mod-10: weight 3/1 alternating from the rightmost digit.
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digits)))
    return (10 - (total % 10)) % 10


def _next_sscc_serial():
    # A plain counter on WMS Settings, not a WMS Number Range like Handling Unit/Shipment
    # numbering - that mechanism's own collision check (next_number() -> frappe.db.exists(
    # range_for, candidate)) requires range_for to name a real DocType to check existence
    # against, and its Select field's option list is exactly (and only) those real,
    # autoname-hooked doctypes; SSCC has neither. Row-locked the same way every other counter
    # in this app is, via a plain SELECT ... FOR UPDATE on the row about to be incremented.
    # Take the value from the locking read itself - a separate get_single_value afterwards is a
    # REPEATABLE READ snapshot read and could hand two concurrent labels the same serial.
    locked = frappe.db.sql("select value from `tabSingles` where doctype='WMS Settings' and field='sscc_next_serial' for update")
    next_value = cint(locked[0][0] if locked else 0) + 1
    frappe.db.set_single_value("WMS Settings", "sscc_next_serial", next_value)
    return next_value


def generate_sscc(hu_name):
    # SSCC-18: 1 extension digit + GS1 Company Prefix (7-10 digits) + a serial reference filling
    # out to 17 digits total + 1 Mod-10 check digit. Idempotent - an HU that already has one just
    # gets it back, matching every other "create or fetch" helper in this app.
    hu = frappe.get_doc("Handling Unit", hu_name)
    if hu.sscc:
        return hu.sscc
    prefix = (frappe.db.get_single_value("WMS Settings", "gs1_company_prefix") or "0614141").strip()
    if not prefix.isdigit():
        frappe.throw(_("WMS Settings.gs1_company_prefix must be numeric"))
    serial_len = 17 - 1 - len(prefix)
    if serial_len <= 0:
        frappe.throw(_("GS1 Company Prefix {0} is too long to leave room for an SSCC serial reference").format(prefix))
    serial_str = str(_next_sscc_serial()).zfill(serial_len)
    if len(serial_str) > serial_len:
        frappe.throw(_("SSCC serial range is exhausted for a {0}-digit GS1 Company Prefix").format(len(prefix)))
    extension_digit = "0"
    body = f"{extension_digit}{prefix}{serial_str}"
    sscc = f"{body}{_gs1_check_digit(body)}"
    hu.db_set("sscc", sscc, update_modified=False)
    return sscc


def render_hu_label_zpl(hu_name):
    # A ZPL-II template for a 4x6in thermal label. Emits the SSCC as a plain Code 128 symbol of
    # the raw digits - a fully GS1-128-compliant symbol additionally needs an FNC1 character
    # ahead of the (00) application identifier, whose exact escape sequence is printer/firmware-
    # specific (Zebra's own convention is the "^FD>;00..." ">;" shortcut, used below) - verify
    # against the actual target printer before relying on this for a real scan-and-ship workflow.
    # No real printer is configured yet, so this has been validated as syntactically well-formed
    # ZPL, not against physical hardware.
    hu = frappe.get_doc("Handling Unit", hu_name)
    sscc = hu.sscc or generate_sscc(hu_name)
    lines = [
        "^XA",
        "^CI28",
        f"^FO40,40^A0N,50,50^FD{hu.hu_number}^FS",
        f"^FO40,100^A0N,30,30^FDType: {hu.hu_type or ''}^FS",
        f"^FO40,140^A0N,30,30^FDWarehouse: {hu.warehouse or ''}^FS",
        f"^FO40,180^A0N,30,30^FDBin: {hu.current_bin or ''}^FS",
        "^FO40,230^BY3",
        "^BCN,100,Y,N,N",
        f"^FD>;00{sscc}^FS",
        f"^FO40,350^A0N,25,25^FDSSCC (00) {sscc}^FS",
        "^XZ",
    ]
    return "\n".join(lines)


def render_bin_label_zpl(bin_name):
    # Same conventions as render_hu_label_zpl - a 4x6in ZPL-II label. ^BQ is ZPL's native QR
    # command: the printer draws the symbol itself from raw data, no QR image library needed
    # anywhere in this app. The QR encodes the bin's own code - exactly what every other
    # scan-the-bin flow already matches against (Storage Bin.name, autonamed from bin_code) -
    # so this one label is a full replacement for whatever (if anything) was on the bin before,
    # not a second label alongside it. Check digits print as plain text only, never inside the
    # QR: they must stay something a person reads standing at the bin, never something a photo
    # or a remote scan could lift - that's the entire point of requiring them.
    # No real printer is configured yet, so this has been validated as syntactically well-formed
    # ZPL, not against physical hardware.
    bin_doc = frappe.get_doc("Storage Bin", bin_name)
    context = bin_doc.warehouse or ""
    if bin_doc.storage_type: context = f"{context} - {bin_doc.storage_type}" if context else bin_doc.storage_type
    lines = [
        "^XA",
        "^CI28",
        f"^FO40,40^A0N,50,50^FD{bin_doc.bin_code}^FS",
        f"^FO40,100^A0N,28,28^FD{context}^FS",
        "^FO40,150^BQN,2,8",
        f"^FDQA,{bin_doc.bin_code}^FS",
        "^FO280,160^A0N,24,24^FDCheck digits^FS",
        f"^FO280,195^A0N,80,80^FD{bin_doc.check_digits or ''}^FS",
        "^XZ",
    ]
    return "\n".join(lines)
