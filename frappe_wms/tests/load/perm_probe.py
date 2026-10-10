"""Permission probe: calls the sensitive whitelisted endpoints as a user with no WMS role (needs the e2e seed). Every row should be 403 except harmless ones.
    python3 perm_probe.py   (dev site on localhost:18001)"""
import json, requests, sys
BASE = "http://localhost:18001"
import os
seed = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "e2e", ".seed.json")))
admin = requests.Session(); admin.post(f"{BASE}/api/method/login", data={"usr": seed["admin"], "pwd": seed["admin_password"]})
email, pwd = "audit.noroles@example.com", "Audit-Probe-2026!"
r = admin.get(f"{BASE}/api/resource/User/{email}")
if r.status_code != 200:
    admin.post(f"{BASE}/api/resource/User", json={"email": email, "first_name": "Audit", "send_welcome_email": 0, "new_password": pwd, "user_type": "System User", "roles": [{"role": "Desk User"}]})
admin.put(f"{BASE}/api/resource/User/{email}", json={"new_password": pwd})
u = requests.Session(); lr = u.post(f"{BASE}/api/method/login", data={"usr": email, "pwd": pwd}); print("login", lr.status_code)
wh = seed["warehouse"]
calls = [
 ("locks.acquire_lock", {"object_type": "Warehouse Task", "object_name": "NOPE"}),
 ("locks.lock_status", {"object_type": "Warehouse Task", "object_name": "NOPE"}),
 ("locks.list_locks", {}),
 ("inbound.inbound_overview", {"inbound_delivery": "NOPE"}),
 ("inventory.record_sample_result", {"inspection_name": "NOPE", "sample_name": "x", "result": "Pass"}),
 ("labor.stop_indirect_labor", {"resource": "NOPE"}),
 ("packing_center.packing_tree", {"warehouse": wh, "bins": "[]"}),
 ("posting_change.check_lines", {"lines": "[]"}),
 ("slotting.classify_abc", {"warehouse": wh}),
 ("monitor.find_deliveries", {"doctype": "Outbound Delivery", "by": "number", "value": "x"}),
 ("monitor.process_deliveries", {"doctype": "Outbound Delivery", "action": "allocate", "names": "[]"}),
 ("monitor.delivery_rows", {"doctype": "Outbound Delivery", "names": "[]"}),
 ("adhoc.find_rows", {"warehouse": wh, "mode": "stock", "by": "product", "value": "x"}),
 ("adhoc.process_lines", {"lines": "[]"}),
 ("scanner.confirmation_details", {"task_name": "NOPE"}),
 ("scanner.move_details", {"warehouse": wh, "product": "x", "quantity": 1, "stock_type": "AVAILABLE"}),
 ("stock_adjustment.process_scrap_lines", {"lines": "[]"}),
 ("posting_change.process_lines", {"lines": "[]"}),
 ("docs.webhook_events", {}),
]
for m, a in calls:
    r = u.post(f"{BASE}/api/method/frappe_wms.api.{m}", data=a, headers={"X-Frappe-CSRF-Token": u.cookies.get("csrf_token", "")} )
    msg = ""
    try:
        j = r.json(); msg = (j.get("exc_type") or "") + " " + str(j.get("_server_messages") or j.get("message") or "")[:90]
    except Exception: msg = r.text[:90]
    print(f"{r.status_code} {m}  {msg}")
for dt in ("WMS Stock Balance", "Warehouse Task", "Outbound Delivery", "Handling Unit", "WMS Stock Ledger Entry"):
    r = u.get(f"{BASE}/api/resource/{dt}?limit_page_length=1"); print(r.status_code, "list", dt)
