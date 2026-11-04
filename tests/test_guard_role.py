"""Guard role: gate approval for assigned trips.

The guard sees only the trip number, vehicle type, plate number and
driver, enters the odometers, and can approve only when the assigned
vehicle matches the type the trip needs.
"""

import os
import tempfile
import unittest

import database
from app import create_app, home_for_role


class GuardRoleTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_file = database.DB_FILE
        database.DB_FILE = os.path.join(self.temp_dir.name, "test.db")
        database.init_db()

        conn = database.get_conn()
        requester = conn.execute(
            "INSERT INTO users (username, password_hash, full_name, role)"
            " VALUES (?,?,?,?)",
            ("requester", database.hash_password("pw"), "Req User",
             "requester"))
        self.requester_id = requester.lastrowid
        guard = conn.execute(
            "INSERT INTO users (username, password_hash, full_name, role)"
            " VALUES (?,?,?,?)",
            ("guard1", database.hash_password("guardpw"), "Gate Guard",
             "guard"))
        self.guard_id = guard.lastrowid
        coordinator = conn.execute(
            "INSERT INTO users (username, password_hash, full_name, role)"
            " VALUES (?,?,?,?)",
            ("gcoord", database.hash_password("coordpw"), "Main Coordinator",
             "coordinator"))
        self.coordinator_id = coordinator.lastrowid
        driver = conn.execute(
            "INSERT INTO users (username, password_hash, full_name, role)"
            " VALUES (?,?,?,?)",
            ("gdriver", database.hash_password("pw"), "Driver One",
             "driver"))
        self.driver_id = driver.lastrowid
        vehicle = conn.execute(
            "INSERT INTO vehicles (plate_number, vehicle_name, vehicle_type,"
            " capacity, status) VALUES (?,?,?,?,?)",
            ("TEST-001", "Test Van", "Van", 10, "in_use"))
        self.vehicle_id = vehicle.lastrowid

        # assigned trip the guard can approve
        self.booking_id = conn.execute(
            "INSERT INTO bookings (booking_code, requested_by, purpose,"
            " passenger_count, vehicle_type_needed, departure_date, status,"
            " assigned_vehicle_id, assigned_driver_id)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            ("TRP-2099-9001", self.requester_id, "Guard approval test", 2,
             "Van", "2099-09-09", "assigned", self.vehicle_id,
             self.driver_id)).lastrowid
        conn.execute(
            "INSERT INTO booking_assignments (booking_id, vehicle_id,"
            " driver_id, status) VALUES (?,?,?,'assigned')",
            (self.booking_id, self.vehicle_id, self.driver_id))
        # pending request: must not show up in the guard queue
        self.pending_id = conn.execute(
            "INSERT INTO bookings (booking_code, requested_by, purpose,"
            " passenger_count, vehicle_type_needed, departure_date, status)"
            " VALUES (?,?,?,?,?,?,?)",
            ("TRP-2099-9002", self.requester_id, "Pending test", 2,
             "Van", "2099-09-10", "pending")).lastrowid
        conn.commit()
        conn.close()

        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        database.DB_FILE = self.original_db_file
        self.temp_dir.cleanup()

    def login_as(self, role, user_id):
        with self.client.session_transaction() as session:
            session["user"] = {
                "id": user_id, "username": role, "full_name": role.title(),
                "role": role,
            }

    def booking_row(self, booking_id=None):
        conn = database.get_conn()
        try:
            return conn.execute(
                "SELECT * FROM bookings WHERE id=?",
                (booking_id or self.booking_id,)).fetchone()
        finally:
            conn.close()

    def approve_as_guard(self, mileage="21000", booking_id=None,
                         name="Edd Guzman"):
        return self.client.post(
            f"/bookings/{booking_id or self.booking_id}/guard-approve",
            data={"start_mileage": mileage, "guard_name": name})

    def test_guard_is_a_known_role(self):
        self.assertIn("guard", database.ROLES)

    def test_guard_home_is_the_booking_queue(self):
        self.assertEqual(home_for_role("guard"), "bookings")

    def test_guard_sees_only_trip_vehicle_plate_and_driver(self):
        self.login_as("guard", self.guard_id)
        response = self.client.get("/bookings")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("TRP-2099-9001", body)
        self.assertIn("TEST-001", body)
        self.assertIn("Driver One", body)
        self.assertIn("Start odo", body)
        # no coordinator-only columns/actions
        self.assertNotIn("PRINT TICKET", body)
        self.assertNotIn("REJECT", body)
        self.assertNotIn("WHATSAPP DRIVER", body)
        self.assertNotIn("DOWNLOAD ANDROID APP", body)
        self.assertNotIn("TRP-2099-9002", body)  # pending is not a guard task

    def test_guard_must_type_name_before_approving(self):
        self.login_as("guard", self.guard_id)
        self.approve_as_guard(name="")
        row = self.booking_row()
        self.assertIsNone(row["start_mileage"])
        self.assertIsNone(row["guard_approved_at"])

    def test_guard_approval_shows_na_approve_ko_log(self):
        self.login_as("guard", self.guard_id)
        self.approve_as_guard()
        page = self.client.get("/bookings").get_data(as_text=True)
        self.assertIn("My Approvals", page)
        self.assertIn("TRP-2099-9001", page)
        self.assertIn("MAIN GUARD HOUSE - SG. EDD GUZMAN", page)

    def test_guard_approve_requires_assignment_match(self):
        self.login_as("guard", self.guard_id)
        self.approve_as_guard(booking_id=self.pending_id)
        self.assertIsNone(self.booking_row(self.pending_id)["start_mileage"])

    def test_guard_cannot_approve_when_vehicle_type_mismatches(self):
        conn = database.get_conn()
        conn.execute("UPDATE vehicles SET vehicle_type='SUV' WHERE id=?",
                     (self.vehicle_id,))
        conn.commit()
        conn.close()
        self.login_as("guard", self.guard_id)
        response = self.client.get("/bookings")
        self.assertIn("MISMATCH", response.get_data(as_text=True))

        self.approve_as_guard()
        row = self.booking_row()
        self.assertIsNone(row["start_mileage"])
        self.assertIsNone(row["guard_approved_at"])

    def test_guard_can_approve_assigned_trip_with_start_odometer(self):
        self.login_as("guard", self.guard_id)
        response = self.approve_as_guard("21000")
        self.assertEqual(response.status_code, 302)

        row = self.booking_row()
        self.assertEqual(row["start_mileage"], 21000)
        self.assertEqual(row["approved_by"], self.guard_id)
        self.assertIsNotNone(row["time_out_at"])
        self.assertIsNotNone(row["guard_approved_at"])
        self.assertIsNone(row["time_in_at"])
        # guard approval records the stamps but does not start the trip
        self.assertEqual(row["status"], "assigned")
        conn = database.get_conn()
        try:
            assignment = conn.execute(
                "SELECT status FROM booking_assignments WHERE booking_id=?",
                (self.booking_id,)).fetchone()
        finally:
            conn.close()
        self.assertEqual(assignment["status"], "assigned")

    def test_guard_records_end_odometer_and_time_in(self):
        self.login_as("guard", self.guard_id)
        self.approve_as_guard("21000")
        self.client.post(f"/bookings/{self.booking_id}/odometer",
                         data={"phase": "end", "mileage": "21340"})
        row = self.booking_row()
        self.assertEqual(row["end_mileage"], 21340)
        self.assertEqual(row["distance_km"], 340)
        self.assertIsNotNone(row["time_in_at"])

    def test_end_odometer_must_not_be_below_start(self):
        self.login_as("guard", self.guard_id)
        self.approve_as_guard("21000")
        self.client.post(f"/bookings/{self.booking_id}/odometer",
                         data={"phase": "end", "mileage": "20999"})
        row = self.booking_row()
        self.assertIsNone(row["end_mileage"])
        self.assertIsNone(row["time_in_at"])

    def test_guard_cannot_use_coordinator_approve_route(self):
        self.login_as("guard", self.guard_id)
        response = self.client.post(
            f"/bookings/{self.booking_id}/approve", data={})
        self.assertEqual(response.status_code, 403)

    def test_guard_cannot_assign(self):
        self.login_as("guard", self.guard_id)
        response = self.client.post(
            f"/bookings/{self.booking_id}/assign",
            data={"vehicle_id": str(self.vehicle_id),
                  "driver_id": str(self.driver_id)})
        self.assertEqual(response.status_code, 403)

    def test_guard_cannot_decline(self):
        self.login_as("guard", self.guard_id)
        response = self.client.post(
            f"/bookings/{self.booking_id}/decline",
            data={"coordinator_remarks": "no"})
        self.assertEqual(response.status_code, 403)

    def test_guard_cannot_print_ticket(self):
        self.login_as("guard", self.guard_id)
        response = self.client.get(f"/bookings/{self.booking_id}/print")
        self.assertEqual(response.status_code, 403)

    def test_guard_cannot_manage_users(self):
        self.login_as("guard", self.guard_id)
        self.assertEqual(self.client.get("/users").status_code, 403)

    def test_ticket_shows_guard_time_out_in_and_odometer(self):
        self.login_as("guard", self.guard_id)
        self.approve_as_guard("21000")
        self.client.post(f"/bookings/{self.booking_id}/odometer",
                         data={"phase": "end", "mileage": "21340"})

        self.login_as("coordinator", self.coordinator_id)
        page = self.client.get(f"/bookings/{self.booking_id}/print").data
        row = self.booking_row()
        self.assertIn(b"TRIP TICKET", page)
        self.assertIn(b"21,000", page)
        self.assertIn(b"21,340", page)
        self.assertIn(row["time_out_at"][11:16].encode(), page)
        self.assertIn(row["time_in_at"][11:16].encode(), page)
        # "Approved by" is the name the guard typed, not the account name
        self.assertIn(b"MAIN GUARD HOUSE - SG. EDD GUZMAN", page)
        self.assertNotIn(b"Gate Guard", page)

    def test_completed_trip_leaves_the_guard_queue(self):
        self.login_as("guard", self.guard_id)
        self.approve_as_guard()
        self.client.post(f"/bookings/{self.booking_id}/odometer",
                         data={"phase": "end", "mileage": "21340"})
        conn = database.get_conn()
        conn.execute("UPDATE bookings SET status='completed' WHERE id=?",
                     (self.booking_id,))
        conn.commit()
        conn.close()

        page = self.client.get("/bookings").get_data(as_text=True)
        self.assertNotIn(f"/bookings/{self.booking_id}/guard-approve", page)
        self.assertNotIn(f"/bookings/{self.booking_id}/odometer", page)

    def test_driver_cannot_record_odometer(self):
        self.login_as("driver", self.driver_id)
        response = self.client.post(
            f"/bookings/{self.booking_id}/odometer",
            data={"phase": "start", "mileage": "1500"})
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
