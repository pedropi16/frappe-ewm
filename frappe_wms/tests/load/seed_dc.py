"""Seed a realistic, fully-configured distribution center for workflow / concurrency testing.

    bench --site <site> execute frappe_wms.tests.load.seed_dc.run

Idempotent - re-running only creates what's missing. Everything is namespaced under warehouse
code MAD1 ("Madrid DC"), item codes SKU-#####, users *@mad1.example.test, so it never collides
with real data. What it builds, in the same order SETUP.md walks a real implementation through:

  Structure   ERPNext Warehouse + WMS Warehouse MAD1; storage types RECV / BULK / PICK / PACK /
              STAGE / DOOR / DIFF / QUAL; ~360 bins (4 dock doors in, 192 pallet rack positions,
              120 pick faces, packing stations, staging lanes, doors out, a difference bin).
  Rules       Putaway bin determination (pallet -> BULK, fast movers -> their PICK face), a
              single-step inbound Storage Process + fallback Process Determination Rule, two
              Routes (one with a marshalling stop), Count Tolerance Group, WO Creation Rule
              (cap 8 pick tasks per Warehouse Order), Replenishment Rules for fast movers.
  People      Resource Groups INBOUND / OUTBOUND / INVENTORY, Warehouse Queues per activity,
              24 RF resources, ~26 users across every WMS role plus purchasing/sales clerks.
  Master data 200 products in 6 item groups (batch-managed with shelf life, a few serial-managed,
              ABC classes, weights/volumes, EAN-13 barcodes), 12 suppliers, 30 customers.
  Stock       An opening stock load (go-live cutover) for ~150 of the products, pallets in BULK
              and loose quantities on the pick faces of fast movers.

run() returns (and prints as SEED_DC <json>) the names a driver needs: users + passwords, bins,
resources, product lists by kind.
"""
import json
import random

import frappe
from frappe.utils import add_days, nowdate

WH = "MAD1"
PASSWORD = "Mad1-Test-2026!"
EMAIL_DOMAIN = "mad1.example.test"
PRODUCT_COUNT = 200
SEED = 20260930

ITEM_GROUPS = {
    # group: (share of catalog, batch managed, shelf life days, serial managed)
    "Beverages": (0.22, True, 365, False),
    "Snacks": (0.20, True, 180, False),
    "Household": (0.22, False, 0, False),
    "Personal Care": (0.16, True, 720, False),
    "Small Appliances": (0.15, False, 0, False),
    "Electronics": (0.05, False, 0, True),
}

# (email local part, first name, roles, resource group or None)
USERS = [
    ("rec.ana", "Ana", ["WMS Receiver", "WMS Operator"], "INBOUND"),
    ("rec.luis", "Luis", ["WMS Receiver", "WMS Operator"], "INBOUND"),
    ("rec.marta", "Marta", ["WMS Receiver", "WMS Operator"], "INBOUND"),
    ("rec.javier", "Javier", ["WMS Receiver", "WMS Operator"], "INBOUND"),
    ("pick.bruno", "Bruno", ["WMS Picker", "WMS Operator"], "OUTBOUND"),
    ("pick.carla", "Carla", ["WMS Picker", "WMS Operator"], "OUTBOUND"),
    ("pick.diego", "Diego", ["WMS Picker", "WMS Operator"], "OUTBOUND"),
    ("pick.elena", "Elena", ["WMS Picker", "WMS Operator"], "OUTBOUND"),
    ("pick.fran", "Fran", ["WMS Picker", "WMS Operator"], "OUTBOUND"),
    ("pick.gema", "Gema", ["WMS Picker", "WMS Operator"], "OUTBOUND"),
    ("pick.hugo", "Hugo", ["WMS Picker", "WMS Operator"], "OUTBOUND"),
    ("pick.irene", "Irene", ["WMS Picker", "WMS Operator"], "OUTBOUND"),
    ("pack.jorge", "Jorge", ["WMS Packer", "WMS Operator"], "OUTBOUND"),
    ("pack.laura", "Laura", ["WMS Packer", "WMS Operator"], "OUTBOUND"),
    ("pack.mario", "Mario", ["WMS Packer", "WMS Operator"], "OUTBOUND"),
    ("load.nuria", "Nuria", ["WMS Loader", "WMS Operator"], "OUTBOUND"),
    ("load.oscar", "Oscar", ["WMS Loader", "WMS Operator"], "OUTBOUND"),
    ("load.paula", "Paula", ["WMS Loader", "WMS Operator"], "OUTBOUND"),
    ("inv.raul", "Raul", ["WMS Inventory Controller", "WMS Operator"], "INVENTORY"),
    ("inv.sara", "Sara", ["WMS Inventory Controller", "WMS Operator"], "INVENTORY"),
    ("inv.tomas", "Tomas", ["WMS Inventory Controller", "WMS Operator"], "INVENTORY"),
    ("sup.ursula", "Ursula", ["WMS Supervisor", "WMS Operator"], None),
    ("sup.victor", "Victor", ["WMS Supervisor", "WMS Operator"], None),
    ("md.wendy", "Wendy", ["WMS Master Data"], None),
    ("buy.ximena", "Ximena", ["Purchase User", "Purchase Manager", "Stock User"], None),
    ("sales.yago", "Yago", ["Sales User", "Sales Manager", "Stock User"], None),
    ("audit.zoe", "Zoe", ["WMS Auditor"], None),
]

SUPPLIERS = ["Aguas del Norte SA", "Snackeria Iberica SL", "Limpiezas Hogar SA", "Cosmetica Levante SL",
    "ElectroHogar Distribucion SA", "TecnoImport Europa SL", "Bebidas del Sur SA", "Dulces Castilla SL",
    "Papelera Cantabrica SA", "Higiene Total SL", "Cafeteras Premium SA", "Audio Vision Iberia SL"]
CUSTOMERS = [f"{n}" for n in ("Supermercados Sol", "Hiper Norte", "Tiendas Ahorro", "Mercado Central Madrid",
    "Farmacia Plaza", "Ferreteria Lopez", "Electro Ciudad", "Bazar Oriente", "Cash Levante", "Gourmet Salamanca",
    "Autoservicio Rio", "Hogar y Mas", "Distribuciones Toledo", "Cadena Vecino", "Super Barrio", "Tienda 24h Retiro",
    "Mayorista Getafe", "Kiosko Atocha", "Drogueria Moderna", "Colmado Chamberi", "Online Shop ES", "Hosteleria Prado",
    "Catering Norte", "Hotel Gran Via", "Oficinas Castellana", "Colegio San Luis", "Gimnasio Fit", "Club Nautico",
    "Residencia Mayor", "Farmacia Sol")]


def _ins(doc):
    d = frappe.get_doc(doc)
    d.flags.wms_service_update = True
    d.insert(ignore_permissions=True)
    return d


def _ean13(n):
    base = f"84{n:010d}"
    total = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(base))
    return base + str((10 - total % 10) % 10)


def _company():
    return frappe.get_all("Company", limit=1, pluck="name")[0]


def ensure_structure(company):
    abbr = frappe.db.get_value("Company", company, "abbr")
    erp_wh = f"Madrid DC - {abbr}"
    if not frappe.db.exists("Warehouse", erp_wh):
        _ins({"doctype": "Warehouse", "warehouse_name": "Madrid DC", "company": company})
    if not frappe.db.exists("WMS Warehouse", WH):
        _ins({"doctype": "WMS Warehouse", "warehouse_code": WH, "warehouse_name": "Madrid DC", "company": company,
              "erpnext_warehouse": erp_wh, "default_stock_type": "AVAILABLE", "allow_negative_stock": 0})

    # code: (name, role, hu_managed, mixed products, capacity method)
    types = {
        "RECV": ("Inbound dock", "Receiving", 1, 1, "None"),
        "BULK": ("Pallet racking", "Storage", 1, 1, "HU Count"),
        "PICK": ("Pick faces", "Picking", 0, 0, "None"),
        "PACK": ("Packing stations", "Packing", 1, 1, "None"),
        "STAGE": ("Outbound staging lanes", "Staging", 1, 1, "None"),
        "DOOR": ("Outbound doors", "Door", 1, 1, "None"),
        "DIFF": ("Difference", "Difference", 0, 1, "None"),
        "QUAL": ("Quality hold", "Storage", 1, 1, "None"),
    }
    for code, (name, role, hu, mixed, cap) in types.items():
        st = f"{WH}-{code}"
        if not frappe.db.exists("Storage Type", st):
            _ins({"doctype": "Storage Type", "warehouse": WH, "storage_type_code": code, "storage_type_name": name,
                  "storage_role": role, "hu_managed": hu, "allow_mixed_products": mixed, "allow_mixed_stock_types": 1,
                  "allow_mixed_batches": 1, "capacity_check_method": cap, "active": 1})

    bins = []
    seq = 0

    def add(code, st, role="Standard", **kw):
        nonlocal seq
        seq += 1
        bins.append(code)
        if not frappe.db.exists("Storage Bin", code):
            _ins({"doctype": "Storage Bin", "bin_code": code, "warehouse": WH, "storage_type": f"{WH}-{st}",
                  "bin_role": role, "sequence": seq, "active": 1, **kw})

    for i in range(1, 5): add(f"{WH}-RECV-{i:02d}", "RECV", "Receiving")
    for aisle in "ABCD":
        for rack in range(1, 13):
            for level in range(1, 5):
                add(f"{WH}-{aisle}-{rack:02d}-{level}", "BULK", aisle=aisle, rack=f"{rack:02d}", level=str(level), maximum_hus=3)
    for aisle in ("P1", "P2", "P3", "P4"):
        for pos in range(1, 31):
            add(f"{WH}-{aisle}-{pos:02d}", "PICK", aisle=aisle, position=f"{pos:02d}")
    for i in range(1, 5): add(f"{WH}-PACK-{i:02d}", "PACK", "Packing")
    for i in range(1, 9): add(f"{WH}-STG-{i:02d}", "STAGE", "Staging")
    for i in range(1, 5): add(f"{WH}-DOOR-{i:02d}", "DOOR", "Door")
    add(f"{WH}-MARSHAL", "STAGE", "Staging")
    add(f"{WH}-DIFF", "DIFF", "Difference")
    for i in range(1, 5): add(f"{WH}-QUAL-{i:02d}", "QUAL")

    wh = frappe.get_doc("WMS Warehouse", WH)
    wh.default_receiving_bin = f"{WH}-RECV-01"
    wh.default_shipping_bin = f"{WH}-STG-01"
    wh.default_difference_bin = f"{WH}-DIFF"
    wh.default_picking_staging_bin = f"{WH}-STG-01"
    wh.save(ignore_permissions=True)
    return bins


def ensure_hu_types():
    for code, name, cat, mode, maxw in [("EUR-PAL", "EUR pallet 120x80", "Pallet", "External", 1000),
                                        ("TOTE", "Picking tote", "Tote", "Internal", 25),
                                        ("CARTON", "Shipping carton", "Box", "Internal", 30)]:
        if not frappe.db.exists("Handling Unit Type", code):
            _ins({"doctype": "Handling Unit Type", "hu_type_code": code, "hu_type_name": name, "category": cat,
                  "numbering_mode": mode, "maximum_weight": maxw, "nestable": 1, "active": 1})
    settings = frappe.get_single("WMS Settings")
    settings.default_handling_unit_type = "EUR-PAL"
    settings.save(ignore_permissions=True)


def ensure_partners():
    sg = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
    cg = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
    terr = frappe.db.get_value("Territory", {"is_group": 0}, "name")
    for s in SUPPLIERS:
        if not frappe.db.exists("Supplier", s):
            _ins({"doctype": "Supplier", "supplier_name": s, "supplier_group": sg, "supplier_type": "Company"})
    for c in CUSTOMERS:
        if not frappe.db.exists("Customer", c):
            _ins({"doctype": "Customer", "customer_name": c, "customer_group": cg, "territory": terr, "customer_type": "Company"})


def ensure_products(rng):
    # ERPNext v16 refuses has_batch_no/has_serial_no on an Item until this is switched on.
    if not frappe.db.get_single_value("Stock Settings", "enable_serial_and_batch_no_for_item"):
        frappe.db.set_single_value("Stock Settings", "enable_serial_and_batch_no_for_item", 1)
    for g in ITEM_GROUPS:
        if not frappe.db.exists("Item Group", g):
            _ins({"doctype": "Item Group", "item_group_name": g, "parent_item_group": "All Item Groups"})
    products = []
    n = 0
    groups = list(ITEM_GROUPS.items())
    counts = [round(share * PRODUCT_COUNT) for _, (share, *_r) in groups]
    counts[0] += PRODUCT_COUNT - sum(counts)
    for (group, (_share, batch, shelf, serial)), count in zip(groups, counts):
        for _ in range(count):
            n += 1
            code = f"SKU-{n:05d}"
            weight = round(rng.uniform(0.2, 2.5) if group != "Small Appliances" else rng.uniform(2, 9), 2)
            abc = "A" if n % 5 == 0 else ("B" if n % 3 == 0 else "C")
            full_hu = rng.choice([48, 60, 72, 96, 120]) if not serial else 20
            p = {"item": code, "group": group, "batch": batch, "serial": serial, "abc": abc, "full_hu": full_hu,
                 "rate": round(rng.uniform(1.5, 60 if group not in ("Small Appliances", "Electronics") else 250), 2)}
            products.append(p)
            if not frappe.db.exists("Item", code):
                _ins({"doctype": "Item", "item_code": code, "item_name": f"{group} product {n}", "item_group": group,
                      "stock_uom": "Nos", "is_stock_item": 1, "has_batch_no": 1 if batch else 0,
                      "has_serial_no": 1 if serial else 0, "valuation_rate": p["rate"] * 0.6, "standard_rate": p["rate"],
                      "shelf_life_in_days": shelf or 0, "weight_per_unit": weight, "weight_uom": "Kg",
                      "barcodes": [{"barcode": _ean13(n), "barcode_type": "EAN"}]})
            if not frappe.db.exists("WMS Product", code):
                _ins({"doctype": "WMS Product", "item": code, "stock_uom": "Nos", "warehouse_managed": 1,
                      "batch_control": 1 if batch else 0, "shelf_life_days": shelf or None,
                      "serial_control": "Required at Receipt" if serial else "None",
                      "default_hu_type": "EUR-PAL", "full_hu_quantity": full_hu,
                      "gross_weight_per_unit": weight, "volume_per_unit": round(weight / 400, 4),
                      "abc_indicator": abc, "active": 1})
    return products


def ensure_rules(products, rng):
    def rule(doctype, filters, values):
        if not frappe.db.exists(doctype, filters):
            _ins({"doctype": doctype, **filters, **values})

    rule("Bin Determination Rule", {"warehouse": WH, "activity": "Putaway", "priority": 100},
         {"destination_storage_type": f"{WH}-BULK", "strategy": "Least Utilized Bin", "active": 1})
    rule("Bin Determination Rule", {"warehouse": WH, "activity": "Putaway", "priority": 50, "stock_type": "QUALITY"},
         {"destination_storage_type": f"{WH}-QUAL", "strategy": "Least Utilized Bin", "active": 1})

    if not frappe.db.exists("Storage Process", f"{WH}-INB-1STEP"):
        _ins({"doctype": "Storage Process", "process_code": f"{WH}-INB-1STEP", "process_name": "Receive and put away",
              "process_category": "Inbound", "warehouse": WH, "active": 1,
              "steps": [{"sequence": 1, "step_code": "PUTAWAY", "process_type": "GR_PUTAWAY",
                         "create_task_automatically": 1, "next_step_on_confirmation": 1}]})
    rule("Process Determination Rule", {"warehouse": WH, "document_type": "Inbound Delivery", "priority": 100},
         {"storage_process": f"{WH}-INB-1STEP", "active": 1})

    for code, name, door, staging, stops in [
        (f"{WH}-R-NORTH", "North route (Burgos/Santander)", f"{WH}-DOOR-01", f"{WH}-STG-02", []),
        (f"{WH}-R-SOUTH", "South route (Sevilla/Malaga)", f"{WH}-DOOR-03", f"{WH}-STG-05",
         [{"sequence": 1, "storage_bin": f"{WH}-MARSHAL", "stop_type": "Marshalling"}]),
    ]:
        if not frappe.db.exists("WMS Route", code):
            _ins({"doctype": "WMS Route", "route_code": code, "route_name": name, "origin_warehouse": WH,
                  "default_staging_bin": staging, "default_door": door, "stops": stops, "active": 1})

    rule("Count Tolerance Group", {"warehouse": WH, "priority": 100},
         {"tolerance_percentage": 5, "tolerance_quantity": 2, "requires_recount": 1, "requires_approval": 1, "active": 1})
    rule("WO Creation Rule", {"warehouse": WH, "activity": "Pick", "priority": 100}, {"maximum_tasks": 8, "active": 1})

    # Fast movers (every A-class product) get a fixed pick face + replenishment from BULK.
    pick_bins = [f"{WH}-P{a}-{p:02d}" for a in range(1, 5) for p in range(1, 31)]
    fast = [p for p in products if p["abc"] == "A" and not p["serial"]]
    for p, pick_bin in zip(fast, pick_bins):
        p["pick_bin"] = pick_bin
        rule("Replenishment Rule", {"warehouse": WH, "product": p["item"], "storage_bin": pick_bin},
             {"stock_type": "AVAILABLE", "minimum_quantity": 20, "target_quantity": 80,
              "source_storage_type": f"{WH}-BULK", "priority": "Normal", "active": 1})
        rule("Bin Determination Rule", {"warehouse": WH, "activity": "Putaway", "priority": 10, "item": p["item"]},
             {"destination_storage_type": f"{WH}-BULK", "strategy": "Least Utilized Bin", "active": 1})
    return fast


def ensure_people():
    for code, name in [("INBOUND", "Inbound team"), ("OUTBOUND", "Outbound team"), ("INVENTORY", "Inventory team")]:
        g = f"{WH}-{code}"
        if not frappe.db.exists("WMS Resource Group", g):
            _ins({"doctype": "WMS Resource Group", "group_code": g, "group_name": name, "warehouse": WH, "active": 1})
    for code, activity, group, st in [
        ("PUTAWAY", "Putaway", "INBOUND", None), ("PICK", "Pick", "OUTBOUND", None),
        ("MOVE", "Internal Move", "INVENTORY", None), ("STAGE", "Stage", "OUTBOUND", None),
        ("LOAD", "Load", "OUTBOUND", None), ("COUNT", "Inventory Count", "INVENTORY", None),
    ]:
        q = f"{WH}-Q-{code}"
        if not frappe.db.exists("Warehouse Queue", q):
            _ins({"doctype": "Warehouse Queue", "queue_code": q, "queue_name": f"{activity} queue", "warehouse": WH,
                  "activity": activity, "resource_group": f"{WH}-{group}", "storage_type": st,
                  "sequence_rule": "Priority Then FIFO", "active": 1})
    resources = []
    for i, (_local, _first, _roles, group) in enumerate(USERS, 1):
        if not group: group = "INVENTORY"
        code = f"{WH}-RF-{i:02d}"
        resources.append(code)
        if not frappe.db.exists("WMS Resource", code):
            _ins({"doctype": "WMS Resource", "resource_code": code, "warehouse": WH, "resource_group": f"{WH}-{group}",
                  "resource_type": "Scanner", "device_id": f"TC52-{i:04d}", "active": 1})
    for i in range(1, 5):
        code = f"{WH}-PACK-WC{i}"
        if not frappe.db.exists("Work Center", f"{WH}-{code}"):
            _ins({"doctype": "Work Center", "warehouse": WH, "work_center_code": code, "work_center_name": f"Packing station {i}",
                  "bin": f"{WH}-PACK-{i:02d}", "active": 1})

    users = []
    for local, first, roles, _group in USERS:
        email = f"{local}@{EMAIL_DOMAIN}"
        if frappe.db.exists("User", email):
            user = frappe.get_doc("User", email)
        else:
            user = frappe.get_doc({"doctype": "User", "email": email, "first_name": first, "last_name": "MAD1",
                                   "send_welcome_email": 0, "user_type": "System User", "enabled": 1})
        have = {r.role for r in user.roles}
        for role in roles + ["Desk User"]:
            if role not in have and frappe.db.exists("Role", role):
                user.append("roles", {"role": role})
        user.new_password = PASSWORD
        user.flags.ignore_password_policy = True
        user.save(ignore_permissions=True)
        users.append({"email": email, "roles": roles, "group": _group})
    return users, resources


def ensure_opening_stock(products, rng):
    if frappe.db.exists("WMS Opening Stock Load", {"warehouse": WH, "status": "Posted"}):
        return None
    bulk_bins = [b for b in frappe.get_all("Storage Bin", filters={"storage_type": f"{WH}-BULK"}, pluck="name", order_by="sequence")]
    rows, hu_seq = [], 0
    for p in products:
        if p["serial"] or rng.random() < 0.25 and p["abc"] != "A":
            continue  # ~25% of the catalog starts with no stock at all - only inbound brings it in
        pallets = 2 if p["abc"] == "A" else 1
        for k in range(pallets):
            hu_seq += 1
            row = {"item": p["item"], "storage_bin": bulk_bins[(hu_seq - 1) % len(bulk_bins)], "handling_unit": f"OSL-{WH}-{hu_seq:06d}",
                   "hu_type": "EUR-PAL", "stock_type": "AVAILABLE", "quantity": p["full_hu"], "stock_uom": "Nos",
                   "valuation_rate": p["rate"] * 0.6}
            if p["batch"]:
                row["batch_no"] = f"{p['item']}-OB{k + 1}"
                row["shelf_life_expiry_date"] = add_days(nowdate(), rng.randint(60, 300))
                if not frappe.db.exists("Batch", row["batch_no"]):
                    _ins({"doctype": "Batch", "item": p["item"], "batch_id": row["batch_no"]})
            rows.append(row)
        if p.get("pick_bin"):
            row = {"item": p["item"], "storage_bin": p["pick_bin"], "stock_type": "AVAILABLE", "quantity": 40,
                   "stock_uom": "Nos", "valuation_rate": p["rate"] * 0.6}
            if p["batch"]:
                row["batch_no"] = f"{p['item']}-OB1"
                row["shelf_life_expiry_date"] = add_days(nowdate(), rng.randint(60, 300))
            rows.append(row)
    load = _ins({"doctype": "WMS Opening Stock Load", "warehouse": WH, "load_date": nowdate(), "status": "Draft",
                 "remarks": "MAD1 go-live cutover", "items": rows})
    from frappe_wms.services.opening_stock import post_opening_stock_load
    post_opening_stock_load(load.name)
    return load.name


def run():
    frappe.set_user("Administrator")
    rng = random.Random(SEED)
    company = _company()
    bins = ensure_structure(company)
    ensure_hu_types()
    ensure_partners()
    products = ensure_products(rng)
    fast = ensure_rules(products, rng)
    users, resources = ensure_people()
    frappe.db.commit()
    load = ensure_opening_stock(products, rng)
    frappe.db.commit()
    info = {
        "warehouse": WH, "company": company, "erpnext_warehouse": frappe.db.get_value("WMS Warehouse", WH, "erpnext_warehouse"),
        "password": PASSWORD, "users": users, "resources": resources, "suppliers": SUPPLIERS, "customers": CUSTOMERS,
        "products": products, "fast_movers": [p["item"] for p in fast], "bin_count": len(bins), "opening_load": load,
        "routes": [f"{WH}-R-NORTH", f"{WH}-R-SOUTH"],
    }
    print("SEED_DC " + json.dumps({k: v for k, v in info.items() if k != "products"} | {"product_count": len(products)}))
    return info
