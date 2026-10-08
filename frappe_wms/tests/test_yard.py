import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from frappe_wms.services import yard


class TestYardAndDockAppointments(IntegrationTestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        self.wh = f"WMS-TEST-YRD-{frappe.generate_hash(length=5).upper()}"
        frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": self.wh, "warehouse_name": self.wh, "company": company,
                        "default_stock_type": "AVAILABLE", "dock_slot_minutes": 60, "dock_changeover_minutes": 15}).insert(ignore_permissions=True)
        for code, role in (("DOOR", "Door"), ("YARD", "Yard")):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": self.wh, "storage_type_code": code, "storage_type_name": code, "storage_role": role,
                            "capacity_check_method": "None", "active": 1}).insert(ignore_permissions=True)
        self.doors = [frappe.get_doc({"doctype": "Storage Bin", "bin_code": f"{self.wh}-D{n}", "warehouse": self.wh, "storage_type": f"{self.wh}-DOOR",
                                      "active": 1, "sequence": n}).insert(ignore_permissions=True).name for n in (1, 2)]
        self.spot = frappe.get_doc({"doctype": "Storage Bin", "bin_code": f"{self.wh}-Y1", "warehouse": self.wh, "storage_type": f"{self.wh}-YARD",
                                    "active": 1, "sequence": 1}).insert(ignore_permissions=True).name
        self.t0 = add_to_date(now_datetime(), hours=2).replace(minute=0, second=0, microsecond=0)

    def _book(self, start, door=None, **kw):
        return yard.create_appointment(self.wh, kw.pop("direction", "Inbound"), start, door=door, **kw)

    def test_booking_assigns_free_doors_and_refuses_overlaps(self):
        a = self._book(self.t0, vehicle_registration="1234-ABC")
        b = self._book(self.t0, vehicle_registration="5678-DEF")
        self.assertEqual([frappe.db.get_value("WMS Dock Appointment", n, "door") for n in (a, b)], self.doors, "first free door, then the next")
        with self.assertRaises(frappe.ValidationError):
            self._book(self.t0, vehicle_registration="9999-XYZ")          # both doors taken
        with self.assertRaises(frappe.ValidationError):
            self._book(add_to_date(self.t0, minutes=65), door=self.doors[0])   # inside the 15-minute changeover
        self.assertTrue(self._book(add_to_date(self.t0, minutes=75), door=self.doors[0]))
        slots = {s["door"]: s["free_starts"] for s in yard.free_slots(self.wh, self.t0.date(), 60)}
        self.assertNotIn(str(self.t0), slots[self.doors[0]])

    def test_gate_door_and_departure(self):
        from frappe_wms.services.shipping import depart_shipment
        a = self._book(self.t0, vehicle_registration="1234-ABC", direction="Outbound")
        r = yard.check_in(self.wh, vehicle_registration="1234-ABC", yard_bin=self.spot)
        self.assertEqual((r["appointment"], r["status"], r["yard_bin"]), (a, "Checked In", self.spot))
        self.assertLess(frappe.db.get_value("WMS Dock Appointment", a, "arrival_delay_minutes"), 0, "arrived early")
        # a walk-in with the door already occupied cannot take it
        self.assertEqual(yard.to_door(a)["door"], self.doors[0])
        w = yard.check_in(self.wh, vehicle_registration="WALK-IN", direction="Inbound")["appointment"]
        self.assertTrue(frappe.db.get_value("WMS Dock Appointment", w, "walk_in"))
        with self.assertRaises(frappe.ValidationError):
            yard.to_door(w, self.doors[0])
        self.assertEqual(yard.to_door(w)["door"], self.doors[1])
        yard.complete(w)
        self.assertEqual(yard.check_out(w)["status"], "Checked Out")

        # departing a shipment completes the appointment that carries it
        shipment = frappe.get_doc({"doctype": "WMS Shipment", "shipment_number": frappe.generate_hash(length=8), "warehouse": self.wh,
                                   "status": "Loaded"}).insert(ignore_permissions=True, ignore_mandatory=True)
        frappe.db.set_value("WMS Dock Appointment", a, "shipment", shipment.name)
        depart_shipment(shipment.name)
        self.assertEqual(frappe.db.get_value("WMS Dock Appointment", a, "status"), "Completed")

    def test_walk_in_rule_and_no_shows(self):
        frappe.db.set_value("WMS Warehouse", self.wh, "appointment_check", "Block")
        with self.assertRaises(frappe.ValidationError):
            yard.check_in(self.wh, vehicle_registration="NOBODY", direction="Inbound")
        frappe.db.set_value("WMS Warehouse", self.wh, "appointment_check", "Warn")
        self.assertIn("needs_confirmation", yard.check_in(self.wh, vehicle_registration="NOBODY", direction="Inbound"))
        self.assertEqual(yard.check_in(self.wh, vehicle_registration="NOBODY", direction="Inbound", confirm_without_appointment=1)["status"], "Checked In")

        late = self._book(add_to_date(now_datetime(), hours=-5), vehicle_registration="LATE-1")
        frappe.db.set_value("WMS Warehouse", self.wh, "no_show_after_minutes", 60)
        yard.mark_no_shows()
        self.assertEqual(frappe.db.get_value("WMS Dock Appointment", late, "status"), "No Show")
        self.assertTrue(self._book(add_to_date(now_datetime(), hours=-5), door=frappe.db.get_value("WMS Dock Appointment", late, "door")),
                        "a no-show frees its door")
        board = yard.yard_board(self.wh)
        self.assertIn("NOBODY", [r.vehicle_registration for r in board["appointments"]])

    def test_a_truck_at_a_door_holds_it_outside_its_slot(self):
        # Booked for later, but already at the door: an automatic door choice must skip that door
        # at any time of day (the slot check alone would call it free).
        a = self._book(add_to_date(now_datetime(), hours=6), vehicle_registration="EARLY-1")
        yard.check_in(self.wh, appointment=a)
        self.assertEqual(yard.to_door(a)["door"], self.doors[0])
        w = yard.check_in(self.wh, vehicle_registration="NEXT-1", direction="Inbound")["appointment"]
        self.assertEqual(yard.to_door(w)["door"], self.doors[1])

    def test_transportation_unit_follows_the_appointment_and_yard_tasks_move_it(self):
        from frappe_wms.services import transport_unit as tu
        a = self._book(self.t0, vehicle_registration="1234-ABC", direction="Outbound")
        yard.check_in(self.wh, appointment=a, yard_bin=self.spot)
        unit = frappe.db.get_value("WMS Transportation Unit", {"dock_appointment": a}, ["name", "status", "yard_bin", "vehicle_registration"], as_dict=True)
        self.assertEqual((unit.status, unit.yard_bin, unit.vehicle_registration), ("In Yard", self.spot, "1234-ABC"), "checking in creates the unit on its yard spot")
        door = frappe.db.get_value("WMS Dock Appointment", a, "door")
        task = tu.request_yard_move(unit.name, door)
        with self.assertRaises(frappe.ValidationError): tu.request_yard_move(unit.name, self.doors[1])  # one open yard task at a time
        with self.assertRaises(frappe.ValidationError): tu.depart(unit.name)  # not while a yard task is open
        tu.confirm_yard_move(task)
        row = frappe.db.get_value("WMS Transportation Unit", unit.name, ["status", "door", "yard_bin"], as_dict=True)
        self.assertEqual((row.status, row.door, row.yard_bin), ("At Door", door, None))
        self.assertEqual(frappe.db.get_value("WMS Dock Appointment", a, "status"), "At Door", "the appointment follows the yard move")
        tu.start_work(unit.name, "Loading")
        tu.depart(unit.name)
        self.assertEqual(frappe.db.get_value("WMS Transportation Unit", unit.name, "status"), "Departed")
        self.assertEqual(frappe.db.get_value("WMS Dock Appointment", a, "status"), "Checked Out")

    def test_a_unit_without_an_appointment_takes_a_free_spot_and_two_units_never_share_one(self):
        from frappe_wms.services import transport_unit as tu
        first = tu.create_transport_unit(self.wh, vehicle_registration="AAA-111")
        second = tu.create_transport_unit(self.wh, vehicle_registration="BBB-222")
        self.assertEqual(tu.arrive(first)["yard_bin"], self.spot)
        self.assertIsNone(tu.arrive(second)["yard_bin"], "the only spot is taken: it waits without one")
        with self.assertRaises(frappe.ValidationError): tu.request_yard_move(second, self.spot)
        task = tu.request_yard_move(first, self.doors[0])
        tu.confirm_yard_move(task)
        self.assertEqual(tu.request_yard_move(second, self.spot) and "ok", "ok", "the spot is free again once the first unit went to its door")

    def _checkpoint(self):
        frappe.get_doc({"doctype": "Storage Type", "warehouse": self.wh, "storage_type_code": "GATE", "storage_type_name": "GATE", "storage_role": "Checkpoint",
                        "capacity_check_method": "None", "active": 1}).insert(ignore_permissions=True)
        return frappe.get_doc({"doctype": "Storage Bin", "bin_code": f"{self.wh}-G1", "warehouse": self.wh, "storage_type": f"{self.wh}-GATE",
                               "active": 1, "sequence": 1}).insert(ignore_permissions=True).name

    def test_checkpoint_seal_and_tu_activity(self):
        from frappe_wms.services import transport_unit as tu
        gate = self._checkpoint()
        frappe.db.set_value("WMS Warehouse", self.wh, {"yard_checkpoint_required": 1, "seal_required": 1})
        a = self._book(self.t0, vehicle_registration="CP-1", direction="Outbound")
        with self.assertRaises(frappe.ValidationError): yard.check_in(self.wh, appointment=a)          # checkpoint required
        with self.assertRaises(frappe.ValidationError): yard.check_in(self.wh, appointment=a, checkpoint=self.spot)  # not a checkpoint bin
        yard.check_in(self.wh, appointment=a, checkpoint=gate)
        unit = frappe.db.get_value("WMS Transportation Unit", {"dock_appointment": a}, ["name", "activity_status", "checkpoint"], as_dict=True)
        self.assertEqual((unit.activity_status, unit.checkpoint), ("Active", gate), "arrival at the checkpoint activates the TU")
        with self.assertRaises(frappe.ValidationError): tu.depart(unit.name, gate)                      # outbound without a seal
        frappe.db.set_value("WMS Transportation Unit", unit.name, "seal_number", "SEAL-9")
        with self.assertRaises(frappe.ValidationError): tu.depart(unit.name)                            # departure checkpoint required
        tu.depart(unit.name, gate)
        self.assertEqual(frappe.db.get_value("WMS Transportation Unit", unit.name, "activity_status"), "Completed")
        self.assertEqual(frappe.db.get_value("WMS Dock Appointment", a, "departure_checkpoint"), gate)

    def test_gate_check_holds_receipt_until_the_truck_is_at_a_door(self):
        a = self._book(self.t0, vehicle_registration="GC-1")
        frappe.db.set_value("WMS Dock Appointment", a, "inbound_delivery", "ID-X")  # Link fields are not enforced by the database
        yard.gate_check(self.wh, inbound_delivery="ID-X")                           # Off: nothing happens
        frappe.db.set_value("WMS Warehouse", self.wh, "yard_gate_check", "Block")
        yard.gate_check(self.wh, inbound_delivery="ID-OTHER")                       # no appointment carries it: allowed
        with self.assertRaises(frappe.ValidationError): yard.gate_check(self.wh, inbound_delivery="ID-X")  # truck not at a door yet
        frappe.db.set_value("WMS Dock Appointment", a, "status", "At Door")
        yard.gate_check(self.wh, inbound_delivery="ID-X")                           # at the door: fine

    def test_door_determination_rules_choose_the_door_first(self):
        frappe.get_doc({"doctype": "Door Determination Rule", "warehouse": self.wh, "priority": 1, "direction": "Outbound", "door": self.doors[1]}).insert(ignore_permissions=True)
        out = self._book(self.t0, vehicle_registration="OUT-1", direction="Outbound")
        inn = self._book(self.t0, vehicle_registration="IN-1", direction="Inbound")
        self.assertEqual(frappe.db.get_value("WMS Dock Appointment", out, "door"), self.doors[1], "the rule names door 2 for outbound")
        self.assertEqual(frappe.db.get_value("WMS Dock Appointment", inn, "door"), self.doors[0], "no rule for inbound: first free door")
        with self.assertRaises(frappe.ValidationError): self._book(self.t0, vehicle_registration="OUT-2", direction="Outbound")  # both doors taken
        later = self._book(add_to_date(self.t0, hours=3), vehicle_registration="OUT-3", direction="Outbound")
        self.assertEqual(frappe.db.get_value("WMS Dock Appointment", later, "door"), self.doors[1])

    def test_means_of_transport_payload_and_delivery_yard_status(self):
        from frappe_wms.services import transport_unit as tu
        mot = frappe.get_doc({"doctype": "Means of Transport", "code": f"T-{self.wh}", "max_payload_kg": 1000}).insert(ignore_permissions=True).name
        shipment = frappe.get_doc({"doctype": "WMS Shipment", "shipment_number": frappe.generate_hash(length=8), "warehouse": self.wh, "status": "Ready to Load",
                                   "total_weight": 1500}).insert(ignore_permissions=True, ignore_mandatory=True)
        a = self._book(self.t0, vehicle_registration="PAY-1", direction="Outbound", shipment=shipment.name)
        yard.check_in(self.wh, appointment=a)
        self.assertEqual(frappe.db.get_value("WMS Shipment", shipment.name, "yard_status"), "In Yard")
        unit = frappe.db.get_value("WMS Transportation Unit", {"dock_appointment": a}, "name")
        frappe.db.set_value("WMS Transportation Unit", unit, "means_of_transport", mot)
        yard.to_door(a)
        self.assertEqual(frappe.db.get_value("WMS Shipment", shipment.name, "yard_status"), "At Door")
        with self.assertRaises(frappe.ValidationError): tu.start_work(unit, "Loading")      # 1500 kg on a 1000 kg unit
        frappe.db.set_value("WMS Shipment", shipment.name, "total_weight", 900)
        tu.start_work(unit, "Loading")

    def test_cockpit_plans_a_truck_for_a_delivery_and_tracks_it(self):
        from frappe_wms.tests.bootstrap import TEST_ITEM, TEST_SUPPLIER
        uom = frappe.db.get_value("Item", TEST_ITEM, "stock_uom")
        if not frappe.db.exists("WMS Product", TEST_ITEM):
            frappe.get_doc({"doctype": "WMS Product", "item": TEST_ITEM, "stock_uom": uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.wh, "supplier": TEST_SUPPLIER,
                              "receiving_bin": self.spot, "items": [{"line_number": 1, "item": TEST_ITEM, "expected_quantity": 5, "stock_uom": uom, "expected_stock_type": "AVAILABLE"}]}).insert(ignore_permissions=True)
        ind.submit()
        self.assertIn(ind.name, [d.name for d in yard.cockpit(self.wh)["inbound_without_truck"]])
        r = yard.plan_truck(self.wh, "Inbound", self.t0, inbound_delivery=ind.name, vehicle_registration="PLAN-1", carrier="ACME")
        self.assertEqual(frappe.db.get_value("WMS Transportation Unit", r["unit"], ["status", "activity_status", "inbound_delivery"]), ("Planned", "Planned", ind.name))
        board = yard.cockpit(self.wh)
        self.assertNotIn(ind.name, [d.name for d in board["inbound_without_truck"]])
        [truck] = [t for t in board["trucks"] if t["name"] == r["appointment"]]
        self.assertEqual((truck["unit"], truck["next_action"]), (r["unit"], "Check In"))
        yard.check_in(self.wh, vehicle_registration="PLAN-1", yard_bin=self.spot)
        self.assertEqual(frappe.db.count("WMS Transportation Unit", {"dock_appointment": r["appointment"]}), 1, "check-in uses the planned unit")
        self.assertEqual([t["next_action"] for t in yard.cockpit(self.wh)["trucks"] if t["name"] == r["appointment"]], ["To Door"])
