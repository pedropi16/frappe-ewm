import hashlib
import os

import frappe
from frappe.translate import get_translations_from_apps

no_cache = 1

_ASSET_ROOT = "/assets/frappe_wms"
_JS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "public", "js", "wms_rf")
_CSS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "public", "css", "wms_rf.css")
_hash_cache = {}


def _file_hash(path):
    # Content hash keyed on mtime: /assets is served with a long browser cache, so every module URL carries ?v=<hash> and a
    # deploy that changes one file refetches only that file.
    mtime = os.path.getmtime(path)
    hit = _hash_cache.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    with open(path, "rb") as f:
        digest = hashlib.md5(f.read()).hexdigest()[:10]
    _hash_cache[path] = (mtime, digest)
    return digest


def _import_map():
    imports = {}
    for base, _dirs, files in os.walk(_JS_DIR):
        if "vendor" in base.split(os.sep):
            continue
        for name in files:
            if not name.endswith(".js"):
                continue
            path = os.path.join(base, name)
            rel = os.path.relpath(path, _JS_DIR).replace(os.sep, "/")
            imports[f"#wms/{rel}"] = f"{_ASSET_ROOT}/js/wms_rf/{rel}?v={_file_hash(path)}"
    return imports


def _script_json(value):
    # JSON dropped into an inline <script>: a "</script>" inside any string (a translation, a name) would end the block early.
    return frappe.as_json(value).replace("</", "<\\/")


def get_context(context):
    if frappe.session.user == "Guest":
        frappe.local.flags.redirect_location = "/login?redirect-to=/wms"
        raise frappe.Redirect

    if "WMS Operator" not in frappe.get_roles() and "WMS Supervisor" not in frappe.get_roles() and "System Manager" not in frappe.get_roles():
        frappe.throw(frappe._("You need the WMS Operator or WMS Supervisor role to use the scanner app"), frappe.PermissionError)

    imports = _import_map()
    context.import_map = _script_json({"imports": imports})
    context.entry_url = imports["#wms/main.js"]
    context.css_url = f"{_ASSET_ROOT}/css/wms_rf.css?v={_file_hash(_CSS_FILE)}"
    context.zxing_url = f"{_ASSET_ROOT}/js/wms_rf/vendor/zxing-library.min.js?v={_file_hash(os.path.join(_JS_DIR, 'vendor', 'zxing-library.min.js'))}"

    context.boot = _script_json({
        "csrf": frappe.sessions.get_csrf_token(),
        "user": frappe.session.user,
        "fullname": frappe.utils.get_fullname(frappe.session.user),
        "lang": frappe.local.lang,
        "zxingUrl": context.zxing_url,
        # Scoped to this app's own catalog (desk strings + this page's), not the full Frappe/ERPNext boot dictionary:
        # small, and it covers every dynamic-value lookup (statuses, priorities, task types...) as well as literal _() calls.
        "messages": get_translations_from_apps(frappe.local.lang, ["frappe_wms"]),
    })
    context.lang = frappe.local.lang
    return context
