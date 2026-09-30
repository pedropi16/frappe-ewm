#!/usr/bin/env python3
"""Concurrent, multi-user warehouse-day simulator for the MAD1 DC built by seed_dc.py.

Runs OUTSIDE bench, as a plain HTTP client - every actor logs in as its own seeded user through
/api/method/login, pulls its CSRF token out of the RF app's /wms boot page (the same way the RF
app does), logs on to its own RF Resource, and then calls exactly the whitelisted endpoints the
RF app / desk buttons call. Nothing reaches into the database directly, so what this exercises
is the real request path: permissions, CSRF, session handling, row locks and all.

    python3 simulate.py --url http://wms.test:8000 --minutes 10 [--seed 7] [--report out.json]

Actors (one thread each):
  buyer        creates + submits Purchase Orders (desk REST, Purchase User)
  inb-sup      turns new POs into Inbound Deliveries, submits some of them
  receiver x4  receive open deliveries on RF (new pallet HUs, batches, serials), then put away
               by pulling Warehouse Orders from the Putaway queue
  sales        creates + submits Sales Orders (desk REST, Sales User)
  out-sup      Sales Order -> Outbound Delivery -> submit -> release (single) or wave release
  picker x8    pull pick Warehouse Orders, create a carton per order, confirm picks into it;
               occasionally short-pick via the OOS exception
  loader x3    build shipments for fully picked deliveries, load every HU, depart
  packer x3    consolidate staged cartons of one delivery onto a pallet (repack)
  inventory x3 ad-hoc pallet moves, physical counts with variances, replenishment
  chaos        deliberately races: two receivers on the same line, two confirms on one task,
               double-submit with one idempotency key

Every non-2xx response is recorded with its endpoint, actor, HTTP status, exception type and
message; the report groups them so a new failure mode stands out from expected business
rejections (e.g. "not enough stock").
"""
import argparse
import collections
import json
import random
import re
import threading
import time
import traceback
import uuid
from datetime import date, timedelta

import requests

WH = "MAD1"
DOMAIN = "mad1.example.test"
PASSWORD = "Mad1-Test-2026!"

# Rejections a real operator can legitimately hit - recorded, but not counted as defects.
EXPECTED_PATTERNS = [
    r"No stock could be allocated", r"not fully picked", r"already on an open shipment",
    r"No work waiting", r"Nothing left", r"has no outstanding quantity", r"already fully picked",
    r"None of these deliveries have a staged", r"is also picked for",
    r"left to receive", r"Shipment is not open for loading", r"is already loaded",
]


class ApiError(Exception):
    def __init__(self, status, exc_type, message, method, traceback_text=None):
        super().__init__(f"{method}: {status} {exc_type}: {message}")
        self.status, self.exc_type, self.message, self.method = status, exc_type, message, method
        self.traceback = traceback_text


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.calls = collections.Counter()
        self.errors = []
        self.events = collections.Counter()
        self.latency = collections.defaultdict(list)

    def call(self, method, secs):
        with self.lock:
            self.calls[method] += 1
            self.latency[method].append(secs)

    def error(self, actor, err, context=None):
        with self.lock:
            self.errors.append({"actor": actor, "method": getattr(err, "method", "?"), "status": getattr(err, "status", None),
                                "exc_type": getattr(err, "exc_type", type(err).__name__),
                                "message": (getattr(err, "message", None) or str(err))[:600], "context": context,
                                "traceback": getattr(err, "traceback", None),
                                "t": round(time.time(), 1)})

    def event(self, name, n=1):
        with self.lock:
            self.events[name] += n


def _strip_html(s):
    return re.sub(r"<[^>]+>", "", s or "")


class Client:
    def __init__(self, base, email, password, stats, name):
        self.base, self.email, self.password, self.stats, self.name = base.rstrip("/"), email, password, stats, name
        self.s = requests.Session()
        self.csrf = None

    def login(self):
        r = self.s.post(f"{self.base}/api/method/login", data={"usr": self.email, "pwd": self.password}, timeout=60)
        r.raise_for_status()
        self.refresh_csrf()

    def refresh_csrf(self):
        html = self.s.get(f"{self.base}/wms", timeout=60).text
        m = re.search(r"window\.WMS\s*=\s*([\s\S]*?);\s*</script>", html)
        if m:
            self.csrf = json.loads(m.group(1)).get("csrf")
            return
        # not a WMS-role user (buyer, sales clerk): they work in the desk, so take the desk's token
        html = self.s.get(f"{self.base}/desk", timeout=60).text
        m = re.search(r'csrf_token\s*=\s*"([^"]+)"', html)
        if not m: raise RuntimeError(f"{self.email}: no CSRF token on /wms or /desk")
        self.csrf = m.group(1)

    def call(self, method, _retry_csrf=True, **kwargs):
        data = {k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in kwargs.items() if v is not None}
        t0 = time.time()
        r = self.s.post(f"{self.base}/api/method/{method}", data=data, headers={"X-Frappe-CSRF-Token": self.csrf or "", "Accept": "application/json"}, timeout=120)
        self.stats.call(method, time.time() - t0)
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code == 200:
            return body.get("message")
        exc_type = body.get("exc_type") or ""
        message = ""
        if body.get("_server_messages"):
            try:
                msgs = [json.loads(m) for m in json.loads(body["_server_messages"])]
                message = " | ".join(_strip_html(m.get("message", "")) for m in msgs)
            except Exception:
                message = body["_server_messages"][:300]
        if not message and body.get("exception"): message = body["exception"]
        if not message: message = r.text[:300]
        if r.status_code == 400 and "CSRF" in (exc_type + message) and _retry_csrf:
            self.refresh_csrf()
            return self.call(method, _retry_csrf=False, **kwargs)
        tb = None
        if body.get("exc"):  # only sent when the site runs in developer mode
            try: tb = "\n".join(json.loads(body["exc"]))[-2500:]
            except Exception: tb = str(body["exc"])[-2500:]
        raise ApiError(r.status_code, exc_type, message, method, tb)


class Sim:
    def __init__(self, base, minutes, seed):
        self.base, self.deadline, self.rng_seed = base, time.time() + minutes * 60, seed
        self.stats = Stats()
        self.lock = threading.Lock()
        self.new_pos, self.new_sos = [], []           # hand-offs buyer->inb-sup, sales->out-sup
        self.carton_delivery = {}                       # carton HU -> outbound delivery it was picked for
        self.shipped_deliveries = set()
        self.admin = Client(base, "Administrator", "admin", self.stats, "admin")
        self.admin.login()
        self.products = self.admin.call("frappe.client.get_list", doctype="WMS Product", fields=["item", "batch_control", "serial_control", "full_hu_quantity", "abc_indicator"], limit_page_length=0)
        self.suppliers = [s["name"] for s in self.admin.call("frappe.client.get_list", doctype="Supplier", filters={"name": ["not like", "%Internal%"]}, limit_page_length=0) if not s["name"].startswith("_")]
        self.customers = [c["name"] for c in self.admin.call("frappe.client.get_list", doctype="Customer", filters={"name": ["not like", "%Internal%"]}, limit_page_length=0) if not c["name"].startswith("_")]
        self.company = self.admin.call("frappe.client.get_value", doctype="WMS Warehouse", filters={"name": WH}, fieldname="company")["company"]
        self.erp_wh = self.admin.call("frappe.client.get_value", doctype="WMS Warehouse", filters={"name": WH}, fieldname="erpnext_warehouse")["erpnext_warehouse"]
        self.bulk_bins = [b["name"] for b in self.admin.call("frappe.client.get_list", doctype="Storage Bin", filters={"storage_type": f"{WH}-BULK"}, limit_page_length=0)]
        self.catalog_by_supplier = {s: [] for s in self.suppliers}
        for i, p in enumerate(sorted(self.products, key=lambda p: p["item"])):
            self.catalog_by_supplier[self.suppliers[i % len(self.suppliers)]].append(p)
        self.product = {p["item"]: p for p in self.products}

    def alive(self):
        return time.time() < self.deadline

    # ------------------------------------------------------------------ helpers
    def user(self, local, name=None):
        c = Client(self.base, f"{local}@{DOMAIN}", PASSWORD, self.stats, name or local)
        c.login()
        return c

    def log_on(self, c, resource):
        try:
            c.call("frappe_wms.api.resource.log_on", resource_code=resource)
        except ApiError as e:
            self.stats.error(c.name, e, "log_on")

    def safe(self, c, fn, *a, context=None, **kw):
        try:
            return fn(*a, **kw)
        except ApiError as e:
            if any(re.search(p, e.message or "") for p in EXPECTED_PATTERNS):
                self.stats.event(f"expected-reject:{e.method.split('.')[-1]}")
            else:
                self.stats.error(c.name, e, context)
        except requests.RequestException as e:
            self.stats.error(c.name, e, context)
        except Exception as e:  # a bug in the simulator itself - keep it visible, keep running
            self.stats.error(c.name, e, (context or "") + " | " + traceback.format_exc()[-800:])
        return None

    def work_warehouse_order(self, c, rng, wo, carton_type=None):
        detail = c.call("frappe_wms.api.warehouse_order.warehouse_order_detail", wo_name=wo)
        carton = None
        for t in detail.get("tasks", []):
            if t["status"] in ("Confirmed", "Cancelled"): continue
            if t["status"] in ("On Hold", "Exception"): continue
            remaining = (t["planned_quantity"] or 0) - (t["confirmed_quantity"] or 0)
            kwargs = {"task_name": t["name"], "scanned_source": t["source_hu"] or t["source_bin"],
                      "scanned_destination": t["destination_bin"], "idempotency_key": f"{t['name']}:{t['confirmed_quantity'] or 0}:{uuid.uuid4().hex[:8]}"}
            if t["task_type"] == "Pick":
                if carton is None and carton_type:
                    carton = c.call("frappe_wms.api.handling_unit.create_handling_unit", hu_type=carton_type, storage_bin=t["destination_bin"], warehouse=WH)
                    carton = carton["name"] if isinstance(carton, dict) else carton
                    self.stats.event("carton-created")
                    if detail.get("reference_doctype") == "Outbound Delivery":
                        with self.lock: self.carton_delivery[carton] = detail.get("reference_name")
                if carton and rng.random() < 0.9:
                    kwargs["destination_hu"] = carton
                if rng.random() < 0.05:
                    # short pick: bin was short - report OOS with what was actually found
                    found = max(0, int(remaining) - rng.randint(1, max(1, int(remaining))))
                    c.call("frappe_wms.api.scanner.raise_exception", task_name=t["name"], exception_code="OOS", remarks="bin short", revised_quantity=found)
                    self.stats.event("pick-short-oos")
                    continue
            kwargs["confirmed_quantity"] = remaining
            if t["task_type"] == "Putaway" and rng.random() < 0.04:
                kwargs["confirmed_quantity"] = remaining + rng.randint(1, 3)  # found a few extra on the pallet
                self.stats.event("putaway-over-confirm")
            try:
                c.call("frappe_wms.api.scanner.confirm_task", **kwargs)
            except ApiError as e:
                if "Destination Handling Unit required" not in (e.message or "") and "scan the Handling Unit" not in (e.message or ""):
                    raise
                # what the operator does when the RF asks for a destination HU on a partial
                # quantity: grab a carton (pick) or label a fresh pallet (split putaway) and rescan
                self.stats.event(f"dest-hu-required-{t['task_type']}")
                if t["task_type"] == "Pick":
                    new = c.call("frappe_wms.api.handling_unit.create_handling_unit", hu_type=carton_type or "CARTON", storage_bin=t["destination_bin"], warehouse=WH)
                else:
                    new = c.call("frappe_wms.api.handling_unit.create_handling_unit", hu_type="EUR-PAL", hu_number=f"PAL{uuid.uuid4().int % 10**12:012d}", storage_bin=t["source_bin"], warehouse=WH)
                new = new["name"] if isinstance(new, dict) else new
                if t["task_type"] == "Pick" and carton is None: carton = new
                kwargs["destination_hu"] = new
                kwargs["idempotency_key"] = f"{t['name']}:{t['confirmed_quantity'] or 0}:{uuid.uuid4().hex[:8]}"
                c.call("frappe_wms.api.scanner.confirm_task", **kwargs)
            self.stats.event(f"confirm-{t['task_type']}")
            if rng.random() < 0.03:
                c.call("frappe_wms.api.scanner.confirm_task", **kwargs)  # lost response -> resend same key
                self.stats.event("confirm-resend")
        return carton

    # ------------------------------------------------------------------ actors
    def buyer(self):
        rng = random.Random(self.rng_seed + 1)
        c = self.user("buy.ximena", "buyer")
        while self.alive():
            def make():
                supplier = rng.choice(self.suppliers)
                items = rng.sample(self.catalog_by_supplier[supplier], k=min(len(self.catalog_by_supplier[supplier]), rng.randint(2, 6)))
                rows = []
                for p in items:
                    qty = rng.randint(1, 4) if p["serial_control"] != "None" else (p["full_hu_quantity"] or 48) * rng.choice([1, 1, 2]) - rng.choice([0, 0, 7])
                    rows.append({"item_code": p["item"], "qty": qty, "rate": round(rng.uniform(2, 40), 2), "warehouse": self.erp_wh,
                                 "schedule_date": str(date.today() + timedelta(days=2))})
                doc = c.call("frappe.client.insert", doc={"doctype": "Purchase Order", "supplier": supplier, "company": self.company,
                             "transaction_date": str(date.today()), "schedule_date": str(date.today() + timedelta(days=2)), "items": rows})
                doc = c.call("frappe.client.submit", doc=doc)
                with self.lock: self.new_pos.append(doc["name"])
                self.stats.event("po-submitted")
            self.safe(c, make, context="create PO")
            time.sleep(rng.uniform(4, 9))

    def inbound_supervisor(self):
        rng = random.Random(self.rng_seed + 2)
        c = self.user("sup.ursula", "inb-sup")
        self.log_on(c, f"{WH}-RF-22")
        while self.alive():
            with self.lock:
                pos, self.new_pos[:] = list(self.new_pos), []
            for po in pos:
                def make(po=po):
                    ibd = c.call("frappe_wms.api.inbound.create_inbound_delivery_from_purchase_order", purchase_order_name=po, warehouse=WH)
                    self.stats.event("ibd-created")
                    if rng.random() < 0.5:
                        doc = c.call("frappe.client.get", doctype="Inbound Delivery", name=ibd)
                        c.call("frappe.client.submit", doc=doc)
                        self.stats.event("ibd-submitted")
                self.safe(c, make, context=f"IBD from {po}")
            time.sleep(2)

    def receiver(self, idx, local):
        rng = random.Random(self.rng_seed + 10 + idx)
        c = self.user(local, f"recv-{local}")
        self.log_on(c, f"{WH}-RF-{idx:02d}")
        while self.alive():
            def receive():
                open_ = c.call("frappe_wms.api.inbound.list_open_inbound_deliveries") or []
                if not open_: return False
                d = rng.choice(open_[:4])  # everyone works the oldest trucks first -> real overlap
                doc = c.call("frappe.client.get", doctype="Inbound Delivery", name=d["name"])
                items = []
                for row in doc["items"]:
                    remaining = (row["expected_quantity"] or 0) - (row["received_quantity"] or 0)
                    if remaining <= 0: continue
                    p = self.product.get(row["item"], {})
                    qty = remaining if rng.random() < 0.75 else max(1, int(remaining * rng.uniform(0.3, 0.8)))
                    line = {"inbound_delivery_item": row["name"], "item": row["item"], "quantity": qty, "stock_uom": row["stock_uom"],
                            "handling_unit": f"PAL{uuid.uuid4().int % 10**12:012d}", "stock_type": row["expected_stock_type"]}
                    if p.get("batch_control"): line["batch_no"] = f"{row['item']}-L{rng.randint(100, 999)}"
                    if p.get("serial_control") not in (None, "None"):
                        line["quantity"] = 1
                        line["serial_no"] = f"SN{uuid.uuid4().hex[:10].upper()}"
                    items.append(line)
                if not items: return False
                idem = f"GR:{uuid.uuid4().hex}"
                res = c.call("frappe_wms.api.inbound.create_and_submit_goods_receipt", inbound_delivery=d["name"], items=items, idempotency_key=idem)
                self.stats.event("gr-posted")
                if rng.random() < 0.04:
                    c.call("frappe_wms.api.inbound.create_and_submit_goods_receipt", inbound_delivery=d["name"], items=items, idempotency_key=idem)
                    self.stats.event("gr-resend")
                return res
            self.safe(c, receive, context="receive")
            # then put away whatever the Putaway queue has for us
            for _ in range(3):
                wo = self.safe(c, c.call, "frappe_wms.api.warehouse_order.pull_next_warehouse_order", context="pull putaway")
                if not wo: break
                self.safe(c, self.work_warehouse_order, c, rng, wo, context=f"putaway WO {wo}")
            time.sleep(rng.uniform(1, 3))

    def sales(self):
        rng = random.Random(self.rng_seed + 3)
        c = self.user("sales.yago", "sales")
        stocked = [p for p in self.products if p["serial_control"] == "None"]
        while self.alive():
            def make():
                items = rng.sample(stocked, k=rng.randint(1, 7))
                rows = [{"item_code": p["item"], "qty": rng.choice([1, 2, 3, 5, 6, 10, 12, 24]), "rate": round(rng.uniform(3, 60), 2),
                         "warehouse": self.erp_wh, "delivery_date": str(date.today() + timedelta(days=1))} for p in items]
                doc = c.call("frappe.client.insert", doc={"doctype": "Sales Order", "customer": rng.choice(self.customers), "company": self.company,
                             "transaction_date": str(date.today()), "delivery_date": str(date.today() + timedelta(days=1)), "items": rows})
                doc = c.call("frappe.client.submit", doc=doc)
                with self.lock: self.new_sos.append(doc["name"])
                self.stats.event("so-submitted")
            self.safe(c, make, context="create SO")
            time.sleep(rng.uniform(2, 5))

    def outbound_supervisor(self):
        rng = random.Random(self.rng_seed + 4)
        c = self.user("sup.victor", "out-sup")
        self.log_on(c, f"{WH}-RF-23")
        wave_buffer = []
        while self.alive():
            with self.lock:
                sos, self.new_sos[:] = list(self.new_sos), []
            for so in sos:
                def make(so=so):
                    obd = c.call("frappe_wms.api.outbound.create_outbound_delivery_from_sales_order", sales_order_name=so, warehouse=WH)
                    doc = c.call("frappe.client.get", doctype="Outbound Delivery", name=obd)
                    doc["route"] = rng.choice([f"{WH}-R-NORTH", f"{WH}-R-SOUTH"])
                    doc = c.call("frappe.client.save", doc=doc)
                    c.call("frappe.client.submit", doc=doc)
                    self.stats.event("obd-submitted")
                    if rng.random() < 0.3:
                        wave_buffer.append(obd)
                    else:
                        c.call("frappe_wms.api.outbound.release_delivery_for_picking", delivery_name=obd)
                        self.stats.event("obd-released")
                self.safe(c, make, context=f"OBD from {so}")
            if len(wave_buffer) >= 3:
                batch, wave_buffer[:] = list(wave_buffer), []
                def wave():
                    w = c.call("frappe.client.insert", doc={"doctype": "WMS Wave", "warehouse": WH, "picking_strategy": "Single Order",
                               "deliveries": [{"outbound_delivery": d} for d in batch]})
                    c.call("frappe_wms.api.outbound.release_wave", wave_name=w["name"])
                    self.stats.event("wave-released")
                self.safe(c, wave, context=f"wave {batch}")
            time.sleep(2)

    def picker(self, idx, local):
        rng = random.Random(self.rng_seed + 30 + idx)
        c = self.user(local, f"pick-{local}")
        self.log_on(c, f"{WH}-RF-{idx:02d}")
        while self.alive():
            wo = self.safe(c, c.call, "frappe_wms.api.warehouse_order.pull_next_warehouse_order", context="pull pick")
            if wo:
                self.safe(c, self.work_warehouse_order, c, rng, wo, carton_type="CARTON", context=f"pick WO {wo}")
            else:
                time.sleep(rng.uniform(2, 4))

    def loader(self, idx, local):
        rng = random.Random(self.rng_seed + 50 + idx)
        c = self.user(local, f"load-{local}")
        self.log_on(c, f"{WH}-RF-{idx:02d}")
        while self.alive():
            def build():
                picked = c.call("frappe.client.get_list", doctype="Outbound Delivery",
                                filters={"warehouse": WH, "picking_status": "Picked", "docstatus": 1, "goods_issue_status": "Not Posted"},
                                fields=["name", "route"], limit_page_length=20, order_by="modified asc")
                rng.shuffle(picked)
                for d in picked[:2]:
                    with self.lock:
                        if d["name"] in self.shipped_deliveries: continue
                    group = [d["name"]]
                    try:
                        s = c.call("frappe_wms.api.shipping.create_shipment", warehouse=WH, outbound_deliveries=group, route=d.get("route"))
                    except ApiError as e:
                        # "HU X is also picked for OBD-..., ship them together" - do what it says
                        others = re.findall(r"OBD-\d+", (e.message or "").split("is also picked for", 1)[-1]) if "is also picked for" in (e.message or "") else []
                        if not others: raise
                        group += [o for o in others if o not in group]
                        s = c.call("frappe_wms.api.shipping.create_shipment", warehouse=WH, outbound_deliveries=group, route=d.get("route"))
                        self.stats.event("shipment-grouped")
                    with self.lock: self.shipped_deliveries.update(group)
                    self.stats.event("shipment-created")
            self.safe(c, build, context="create shipment")
            def load():
                for s in (c.call("frappe_wms.api.shipping.list_loadable_shipments") or [])[:3]:
                    doc = c.call("frappe.client.get", doctype="WMS Shipment", name=s["name"])
                    for hu in doc.get("handling_units", []):
                        if hu.get("loaded"): continue
                        c.call("frappe_wms.api.shipping.confirm_hu_loaded", shipment_name=s["name"], hu_name=hu["handling_unit"])
                        self.stats.event("hu-loaded")
                    doc = c.call("frappe.client.get", doctype="WMS Shipment", name=s["name"])
                    if doc["status"] == "Loaded":
                        c.call("frappe_wms.api.shipping.depart_shipment", shipment_name=s["name"])
                        self.stats.event("shipment-departed")
            self.safe(c, load, context="load")
            time.sleep(rng.uniform(2, 5))

    def packer(self, idx, local):
        rng = random.Random(self.rng_seed + 70 + idx)
        c = self.user(local, f"pack-{local}")
        self.log_on(c, f"{WH}-RF-{idx:02d}")
        while self.alive():
            def consolidate():
                # two staged cartons of the same delivery -> repack everything from one into the other
                hus = c.call("frappe_wms.api.handling_unit.list_handling_units", warehouse=WH, storage_bin=f"{WH}-STG-01") or []
                hus = [h for h in hus if h.get("hu_type") == "CARTON" and h.get("stock_status") != "Empty" and h.get("status") not in ("Loaded", "Shipped")]
                by_delivery = collections.defaultdict(list)
                with self.lock:
                    for h in hus:
                        if self.carton_delivery.get(h["name"]): by_delivery[self.carton_delivery[h["name"]]].append(h)
                pairs = [v for v in by_delivery.values() if len(v) >= 2]
                if not pairs: return
                a, b = rng.sample(rng.choice(pairs), 2)
                detail = c.call("frappe_wms.api.handling_unit.handling_unit_detail", hu_name=a["name"])
                items = [{"item": r["product"], "quantity": r["quantity"], "stock_type": r["stock_type"], "batch_no": r.get("batch_no"), "serial_no": r.get("serial_no"), "stock_uom": r.get("stock_uom")}
                         for r in (detail.get("stock") or [])]
                if not items: return
                c.call("frappe_wms.api.scanner.repack", source_hu=a["name"], destination_hu=b["name"], items=items, idempotency_key=f"RP:{uuid.uuid4().hex}")
                self.stats.event("repack")
            if rng.random() < 0.4:
                self.safe(c, consolidate, context="repack")
            time.sleep(rng.uniform(4, 8))

    def inventory(self, idx, local, role):
        rng = random.Random(self.rng_seed + 90 + idx)
        c = self.user(local, f"inv-{local}")
        self.log_on(c, f"{WH}-RF-{idx:02d}")
        while self.alive():
            if role == "mover":
                def move():
                    bin_ = rng.choice(self.bulk_bins)
                    ov = c.call("frappe_wms.api.scanner.bin_overview", bin_code=bin_)
                    stock = [r for r in (ov.get("stock") or []) if r.get("quantity", 0) > 0 and (r.get("available_quantity") or 0) >= r["quantity"]]
                    if not stock: return
                    r = rng.choice(stock)
                    dest = rng.choice(self.bulk_bins)
                    hu_rows = [x for x in (ov.get("stock") or []) if x.get("handling_unit") and x.get("handling_unit") == r.get("handling_unit")]
                    whole_hu = r.get("handling_unit") and len(hu_rows) == 1
                    c.call("frappe_wms.api.scanner.create_and_confirm_move", warehouse=WH, product=r["product"], quantity=r["quantity"], stock_uom=r["stock_uom"],
                           stock_type=r["stock_type"], source_bin=bin_, source_hu=r.get("handling_unit"), destination_bin=dest,
                           destination_hu=r.get("handling_unit") if whole_hu else None,
                           batch_no=r.get("batch_no"), idempotency_key=f"MV:{uuid.uuid4().hex}")
                    self.stats.event("adhoc-move")
                self.safe(c, move, context="move")
            elif role == "counter":
                def count():
                    bin_ = rng.choice(self.bulk_bins)
                    pic = c.call("frappe.client.insert", doc={"doctype": "WMS Physical Inventory Count", "warehouse": WH, "storage_bin": bin_,
                                 "count_date": str(date.today()), "status": "Draft"})
                    snap = c.call("frappe_wms.api.inventory.snapshot_count", count_name=pic["name"])
                    doc = c.call("frappe.client.get", doctype="WMS Physical Inventory Count", name=pic["name"])
                    counted = {}
                    for row in doc.get("items", []):
                        q = row.get("book_quantity") or 0
                        found = q + (rng.choice([-2, -1, 1, 3]) if rng.random() < 0.2 else 0)
                        # what a person can actually count: never below zero, a serial is there or not
                        counted[row["name"]] = min(max(found, 0), 1) if row.get("serial_no") else max(found, 0)
                    if counted:
                        c.call("frappe_wms.api.inventory.record_counts", count_name=pic["name"], counted_quantities=counted)
                    res = c.call("frappe_wms.api.inventory.post_count", count_name=pic["name"])
                    self.stats.event(f"count-{(res or {}).get('status', 'posted')}")
                    doc = c.call("frappe.client.get", doctype="WMS Physical Inventory Count", name=pic["name"])
                    if any(r.get("status") == "Pending Recount" for r in doc.get("items", [])):
                        # out of tolerance: recount (the second count agrees with the book)
                        c.call("frappe_wms.api.inventory.request_recount", count_name=pic["name"])
                        doc = c.call("frappe.client.get", doctype="WMS Physical Inventory Count", name=pic["name"])
                        again = {r["name"]: r.get("book_quantity") or 0 for r in doc.get("items", []) if r.get("status") in ("Open", "Pending Recount")}
                        if again: c.call("frappe_wms.api.inventory.record_counts", count_name=pic["name"], counted_quantities=again)
                        res = c.call("frappe_wms.api.inventory.post_count", count_name=pic["name"])
                        self.stats.event(f"recount-{(res or {}).get('status')}")
                self.safe(c, count, context="count")
            else:
                def inspect():
                    for qi in (c.call("frappe_wms.api.inventory.list_open_inspections") or [])[:2]:
                        q = qi.get("quantity") or 0
                        failed = 1 if q >= 2 and rng.random() < 0.5 else 0
                        c.call("frappe_wms.api.inventory.complete_inspection", inspection_name=qi["name"], passed_quantity=q - failed, failed_quantity=failed)
                        self.stats.event("inspection-completed")
                self.safe(c, inspect, context="inspection")

                def replenish():
                    c.call("frappe_wms.api.inventory.check_replenishment_needs")
                    self.stats.event("replenishment-check")
                    wo = c.call("frappe_wms.api.warehouse_order.pull_next_warehouse_order")
                    if wo: self.work_warehouse_order(c, rng, wo)
                self.safe(c, replenish, context="replenish")
            time.sleep(rng.uniform(3, 7))

    def supervisor_ops(self):
        rng = random.Random(self.rng_seed + 120)
        c = self.user("sup.ursula", "sup-ops")
        while self.alive():
            time.sleep(rng.uniform(6, 12))

            def approvals():
                for pic in c.call("frappe.client.get_list", doctype="WMS Physical Inventory Count",
                                  filters={"warehouse": WH, "status": "Under Review"}, limit_page_length=3) or []:
                    c.call("frappe_wms.api.inventory.approve_variance", count_name=pic["name"], remarks="checked on the floor")
                    self.stats.event("count-approved")
            self.safe(c, approvals, context="approve counts")

            def differences():
                for d in (c.call("frappe_wms.api.difference.list_open_differences", warehouse=WH) or [])[:3]:
                    if d.get("direction") == "Over":
                        c.call("frappe_wms.api.difference.clear_over_difference", name=d["name"], destination_bin=f"{WH}-QUAL-01",
                               destination_hu=(c.call("frappe_wms.api.handling_unit.create_handling_unit", hu_type="EUR-PAL", hu_number=f"PAL{uuid.uuid4().int % 10**12:012d}", storage_bin=f"{WH}-QUAL-01", warehouse=WH) or {}).get("name"))
                    else:
                        c.call("frappe_wms.api.difference.clear_short_difference", name=d["name"], remarks="confirmed short")
                    self.stats.event(f"difference-cleared-{d.get('direction')}")
            self.safe(c, differences, context="differences")

            def hold_resume():
                wos = c.call("frappe.client.get_list", doctype="Warehouse Order", filters={"warehouse": WH, "status": "Open"}, limit_page_length=10) or []
                if not wos: return
                wo = rng.choice(wos)["name"]
                c.call("frappe_wms.api.warehouse_order.block_warehouse_order", wo_name=wo, reason="dock congestion")
                self.stats.event("wo-held")
                time.sleep(rng.uniform(1, 4))
                c.call("frappe_wms.api.warehouse_order.resume_warehouse_order", wo_name=wo)
                self.stats.event("wo-resumed")
            if rng.random() < 0.5:
                self.safe(c, hold_resume, context="hold/resume")

            def customer_return():
                dns = self.admin.call("frappe.client.get_list", doctype="Delivery Note", filters={"docstatus": 1, "is_return": 0}, limit_page_length=20) or []
                if not dns: return
                ibd = c.call("frappe_wms.api.inbound.create_return_inbound_delivery", delivery_note=rng.choice(dns)["name"], warehouse=WH)
                self.stats.event("customer-return-created")
            if rng.random() < 0.3:
                self.safe(c, customer_return, context="customer return")

    def chaos(self):
        rng = random.Random(self.rng_seed + 99)
        a, b = self.user("rec.ana", "chaos-a"), self.user("rec.luis", "chaos-b")
        pa, pb = self.user("pick.hugo", "chaos-pa"), self.user("pick.irene", "chaos-pb")
        results = collections.Counter()
        while self.alive():
            time.sleep(rng.uniform(8, 15))

            def race_receipt():
                # two receivers post the full remainder of the same delivery line at the same instant
                open_ = a.call("frappe_wms.api.inbound.list_open_inbound_deliveries") or []
                if not open_: return
                d = open_[0]
                doc = a.call("frappe.client.get", doctype="Inbound Delivery", name=d["name"])
                row = next((r for r in doc["items"] if (r["expected_quantity"] or 0) > (r["received_quantity"] or 0) and self.product.get(r["item"], {}).get("serial_control") in (None, "None")), None)
                if not row: return
                rem = row["expected_quantity"] - (row["received_quantity"] or 0)
                out = {}
                def post(cli, tag):
                    line = {"inbound_delivery_item": row["name"], "item": row["item"], "quantity": rem, "stock_uom": row["stock_uom"],
                            "handling_unit": f"PAL{uuid.uuid4().int % 10**12:012d}", "stock_type": row["expected_stock_type"]}
                    if self.product.get(row["item"], {}).get("batch_control"): line["batch_no"] = f"{row['item']}-RACE"
                    try:
                        out[tag] = cli.call("frappe_wms.api.inbound.create_and_submit_goods_receipt", inbound_delivery=d["name"], items=[line], idempotency_key=f"GR:{uuid.uuid4().hex}")
                    except ApiError as e:
                        out[tag] = e
                ts = [threading.Thread(target=post, args=(a, "a")), threading.Thread(target=post, args=(b, "b"))]
                [t.start() for t in ts]; [t.join() for t in ts]
                ok = [k for k, v in out.items() if not isinstance(v, Exception)]
                results[f"race-receipt-ok-{len(ok)}"] += 1
                if len(ok) == 2:
                    self.stats.error("chaos", ApiError(200, "OverReceipt", f"both concurrent receipts of the full remaining {rem} on {d['name']}/{row['name']} succeeded", "create_and_submit_goods_receipt"), "race receipt")
                for v in out.values():
                    if isinstance(v, ApiError): self.stats.error("chaos", v, "race receipt (loser)")
            self.safe(a, race_receipt, context="race receipt")

            def race_confirm():
                # two pickers confirm the same open pick task at once
                tasks = self.admin.call("frappe.client.get_list", doctype="Warehouse Task", filters={"warehouse": WH, "task_type": "Pick", "status": ["in", ["Open", "Assigned"]], "docstatus": 0},
                                        fields=["name", "planned_quantity", "source_bin", "source_hu", "destination_bin"], limit_page_length=5)
                if not tasks: return
                t = rng.choice(tasks)
                cartons = {tag: (cli.call("frappe_wms.api.handling_unit.create_handling_unit", hu_type="CARTON", storage_bin=t["destination_bin"], warehouse=WH) or {}).get("name")
                           for tag, cli in (("a", pa), ("b", pb))}
                out = {}
                def conf(cli, tag):
                    try:
                        out[tag] = cli.call("frappe_wms.api.scanner.confirm_task", task_name=t["name"], confirmed_quantity=t["planned_quantity"], destination_hu=cartons[tag], idempotency_key=f"{t['name']}:0:{tag}{uuid.uuid4().hex[:6]}")
                    except ApiError as e:
                        out[tag] = e
                ts = [threading.Thread(target=conf, args=(pa, "a")), threading.Thread(target=conf, args=(pb, "b"))]
                [x.start() for x in ts]; [x.join() for x in ts]
                real = [v for v in out.values() if isinstance(v, dict) and not v.get("already_confirmed") and not v.get("replayed")]
                results[f"race-confirm-posted-{len(real)}"] += 1
                if len(real) > 1:
                    self.stats.error("chaos", ApiError(200, "DoubleConfirm", f"task {t['name']} confirmed twice concurrently: {out}", "confirm_task"), "race confirm")
                for v in out.values():
                    if isinstance(v, ApiError): self.stats.error("chaos", v, "race confirm (loser)")
            self.safe(a, race_confirm, context="race confirm")
        self.stats.event("chaos-summary:" + json.dumps(results))

    # ------------------------------------------------------------------ run
    def run(self):
        threads = [threading.Thread(target=self.buyer, name="buyer"), threading.Thread(target=self.inbound_supervisor, name="inb-sup"),
                   threading.Thread(target=self.sales, name="sales"), threading.Thread(target=self.outbound_supervisor, name="out-sup"),
                   threading.Thread(target=self.chaos, name="chaos"), threading.Thread(target=self.supervisor_ops, name="sup-ops")]
        for i, u in enumerate(["rec.ana", "rec.luis", "rec.marta", "rec.javier"], 1):
            threads.append(threading.Thread(target=self.receiver, args=(i, u), name=u))
        for i, u in enumerate(["pick.bruno", "pick.carla", "pick.diego", "pick.elena", "pick.fran", "pick.gema", "pick.hugo", "pick.irene"], 5):
            threads.append(threading.Thread(target=self.picker, args=(i, u), name=u))
        for i, u in enumerate(["pack.jorge", "pack.laura", "pack.mario"], 13):
            threads.append(threading.Thread(target=self.packer, args=(i, u), name=u))
        for i, u in enumerate(["load.nuria", "load.oscar", "load.paula"], 16):
            threads.append(threading.Thread(target=self.loader, args=(i, u), name=u))
        for i, (u, role) in enumerate([("inv.raul", "mover"), ("inv.sara", "counter"), ("inv.tomas", "replenisher")], 19):
            threads.append(threading.Thread(target=self.inventory, args=(i, u, role), name=u))
        for t in threads:
            t.daemon = True
            t.start()
            time.sleep(0.2)
        for t in threads:
            t.join(timeout=max(1, self.deadline - time.time() + 120))
        return self.report()

    def report(self):
        groups = collections.Counter()
        samples = {}
        for e in self.stats.errors:
            key = (e["method"].split(".")[-1], e["status"], e["exc_type"], re.sub(r"[A-Z0-9][A-Z0-9-]{5,}", "#", e["message"])[:140])
            groups[key] += 1
            samples.setdefault(key, e)
        lat = {m: {"n": len(v), "p50": round(sorted(v)[len(v) // 2], 3), "max": round(max(v), 3)} for m, v in self.stats.latency.items()}
        return {"calls": sum(self.stats.calls.values()), "events": dict(self.stats.events), "error_count": len(self.stats.errors),
                "error_groups": [{"count": n, "method": k[0], "status": k[1], "exc_type": k[2], "message": k[3], "sample": samples[k]} for k, n in groups.most_common()],
                "latency": dict(sorted(lat.items(), key=lambda kv: -kv[1]["max"]))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://wms.test:8000")
    ap.add_argument("--minutes", type=float, default=5)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--report", default=None)
    args = ap.parse_args()
    report = Sim(args.url, args.minutes, args.seed).run()
    text = json.dumps(report, indent=1, default=str)
    if args.report:
        open(args.report, "w").write(text)
    print(json.dumps({k: report[k] for k in ("calls", "events", "error_count")}, indent=1))
    for g in report["error_groups"][:40]:
        print(f"{g['count']:>4}  {g['method']:<40} {g['status']} {g['exc_type']:<28} {g['message']}")


if __name__ == "__main__":
    main()
