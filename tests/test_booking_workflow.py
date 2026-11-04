import json
import os
import re
import struct
import tempfile
import os
import unittest
from unittest.mock import patch

import database
from app import (create_app, booking_email_text, maps_directions_url,
                 parse_coordinates)


class CoordinatorWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_file = database.DB_FILE
        database.DB_FILE = os.path.join(self.temp_dir.name, "test.db")
        database.init_db()

        conn = database.get_conn()
        requester = conn.execute(
            "INSERT INTO users (username, password_hash, full_name, role)"
            " VALUES (?,?,?,?)",
            ("requester", database.hash_password("password"), "Test Requester",
             "requester"))
        self.requester_id = requester.lastrowid
        booking = conn.execute(
            "INSERT INTO bookings (booking_code, requested_by, purpose,"
            " passenger_count, vehicle_type_needed, departure_date, status)"
            " VALUES (?,?,?,?,?,?,?)",
            ("TRP-2099-0001", self.requester_id, "Test trip", 2, "Van",
             "2099-01-01", "pending"))
        self.booking_id = booking.lastrowid
        self.admin_id = conn.execute(
            "SELECT id FROM users WHERE username='admin'").fetchone()["id"]
        self.coordinator_id = conn.execute(
            "SELECT id FROM users WHERE username='coordinator'").fetchone()["id"]
        self.driver_id = conn.execute(
            "SELECT id FROM users WHERE username='driver1'").fetchone()["id"]
        conn.commit()
        conn.close()

        app = create_app()
        app.config["TESTING"] = True
        self.client = app.test_client()
        self.set_user("coordinator", self.coordinator_id)

    def tearDown(self):
        database.DB_FILE = self.original_db_file
        self.temp_dir.cleanup()

    def set_user(self, role, user_id):
        with self.client.session_transaction() as session:
            session["user"] = {
                "id": user_id,
                "username": role,
                "full_name": role.title(),
                "role": role,
            }

    def booking_status(self):
        conn = database.get_conn()
        try:
            return conn.execute(
                "SELECT status FROM bookings WHERE id=?",
                (self.booking_id,)).fetchone()["status"]
        finally:
            conn.close()

    def test_android_app_download_is_available_without_login(self):
        apk_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "driver-app", "app", "build", "outputs", "apk", "debug",
            "app-debug.apk")
        if not os.path.isfile(apk_path):
            self.skipTest("Android APK not built (run Android Studio build).")
        response = self.client.get("/download/android")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.mimetype, "application/vnd.android.package-archive")
        self.assertIn(
            "attachment; filename=Transport.apk",
            response.headers["Content-Disposition"])
        self.assertTrue(response.data.startswith(b"PK"))

    def test_approval_records_log_without_reserving_vehicle(self):
        response = self.client.post(
            f"/bookings/{self.booking_id}/approve", follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.booking_status(), "approved")
        conn = database.get_conn()
        try:
            log = conn.execute(
                "SELECT action FROM booking_logs WHERE booking_id=?"
                " ORDER BY id DESC LIMIT 1",
                (self.booking_id,)).fetchone()
            vehicle = conn.execute(
                "SELECT status FROM vehicles WHERE plate_number='N/A-001'"
            ).fetchone()
            self.assertEqual(log["action"], "APPROVED")
            self.assertEqual(vehicle["status"], "available")
            self.assertEqual(database.dashboard_stats(conn)["approved"], 1)
        finally:
            conn.close()

    def test_assignment_is_blocked_until_approval(self):
        response = self.client.post(
            f"/bookings/{self.booking_id}/assign",
            data={"vehicle_id": "1"})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.booking_status(), "pending")

    def test_approved_booking_can_be_assigned(self):
        conn = database.get_conn()
        conn.execute(
            "UPDATE bookings SET status='approved' WHERE id=?",
            (self.booking_id,))
        conn.commit()
        conn.close()

        response = self.client.post(
            f"/bookings/{self.booking_id}/assign",
            data={"vehicle_id": "1", "driver_id": str(self.driver_id)})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.booking_status(), "assigned")
        self.client.post(f"/bookings/{self.booking_id}/unassign")
        self.assertEqual(self.booking_status(), "approved")

    def test_assignment_requires_a_driver(self):
        conn = database.get_conn()
        conn.execute(
            "UPDATE bookings SET status='approved' WHERE id=?",
            (self.booking_id,))
        conn.commit()
        conn.close()

        response = self.client.post(
            f"/bookings/{self.booking_id}/assign",
            data={"vehicle_id": "1"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.booking_status(), "approved")

    def test_requester_does_not_set_vehicle_type_vehicle_defines_it(self):
        self.set_user("requester", self.requester_id)
        response = self.client.post("/bookings/new", data={
            "purpose": "No type booking",
            "nature_of_trip": "official",
            "department": "Treasury",
            "passenger_count": "2",
            "pickup_location": "A",
            "dropoff_location": "B",
            "departure_date": "2099-08-08",
        })
        self.assertEqual(response.status_code, 302)
        conn = database.get_conn()
        row = conn.execute(
            "SELECT id, vehicle_type_needed, status FROM bookings"
            " WHERE purpose='No type booking'").fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["vehicle_type_needed"], "")
        self.assertEqual(row["status"], "pending")

        self.set_user("coordinator", self.coordinator_id)
        new_id = row["id"]
        self.client.post(f"/bookings/{new_id}/approve", data={})
        self.client.post(f"/bookings/{new_id}/assign", data={
            "vehicle_id": "1",
            "driver_id": str(self.driver_id),
        })
        conn = database.get_conn()
        row = conn.execute(
            "SELECT vehicle_type_needed, status FROM bookings WHERE id=?",
            (new_id,)).fetchone()
        assigned_type = conn.execute(
            "SELECT vehicle_type FROM vehicles WHERE id=1").fetchone()[0]
        conn.close()
        self.assertEqual(row["vehicle_type_needed"], assigned_type)
        self.assertEqual(row["status"], "assigned")

    def test_booking_can_have_multiple_driver_assignments(self):
        conn = database.get_conn()
        conn.execute("UPDATE bookings SET status='approved',"
                     " pickup_location='SSD Main Gate',"
                     " guard_approved_at='2099-01-01 07:00:00'"
                     " WHERE id=?", (self.booking_id,))
        van2 = conn.execute(
            "INSERT INTO vehicles (plate_number, vehicle_name, vehicle_type,"
            " capacity, status) VALUES ('N/A-099','Van 2','Van',15,'available')"
        ).lastrowid
        driver2 = conn.execute(
            "SELECT id FROM users WHERE username='driver2'").fetchone()["id"]
        conn.commit()
        conn.close()

        response = self.client.post(
            f"/bookings/{self.booking_id}/assign",
            data={"vehicle_id": ["1", str(van2)],
                  "driver_id": [str(self.driver_id), str(driver2)]})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.booking_status(), "assigned")

        conn = database.get_conn()
        rows = conn.execute(
            "SELECT driver_id, vehicle_id, status FROM booking_assignments"
            " WHERE booking_id=? ORDER BY id", (self.booking_id,)).fetchall()
        conn.close()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["driver_id"], self.driver_id)
        self.assertEqual(rows[1]["driver_id"], driver2)

        # driver 1 completes their trip; booking is still not completed.
        self.set_user("driver", self.driver_id)
        self.client.post(f"/my-trips/{self.booking_id}/status",
                         data={"status": "accepted"})
        self.client.post(f"/my-trips/{self.booking_id}/status",
                         data={"status": "in_progress"})
        conn = database.get_conn()
        conn.execute("UPDATE bookings SET end_mileage=150 WHERE id=?",
                     (self.booking_id,))
        conn.commit()
        conn.close()
        self.client.post(f"/my-trips/{self.booking_id}/status",
                         data={"status": "completed"})
        self.assertNotEqual(self.booking_status(), "completed")

        # driver 2 completes theirs; now the booking is completed.
        self.set_user("driver", driver2)
        self.client.post(f"/my-trips/{self.booking_id}/status",
                         data={"status": "accepted"})
        self.client.post(f"/my-trips/{self.booking_id}/status",
                         data={"status": "in_progress"})
        self.client.post(f"/my-trips/{self.booking_id}/status",
                         data={"status": "completed"})
        self.assertEqual(self.booking_status(), "completed")

        conn = database.get_conn()
        vehicles_available = conn.execute(
            "SELECT status FROM vehicles WHERE id IN (1, ?)",
            (van2,)).fetchall()
        conn.close()
        self.assertTrue(all(r["status"] == "available"
                            for r in vehicles_available))

    def test_arrival_button_appears_only_after_opening_pickup_map(self):
        conn = database.get_conn()
        conn.execute(
            "UPDATE bookings SET status='on_the_way', assigned_driver_id=?,"
            " pickup_location='SSD Main Gate' WHERE id=?",
            (self.driver_id, self.booking_id))
        conn.commit()
        conn.close()
        self.set_user("driver", self.driver_id)

        page = self.client.get("/my-trips").get_data(as_text=True)
        self.assertIn("OPEN PICKUP IN GOOGLE MAPS", page)
        self.assertNotIn(f"/my-trips/{self.booking_id}/arrival", page)

        response = self.client.post(f"/my-trips/{self.booking_id}/pickup-map")
        self.assertEqual(response.status_code, 302)
        self.assertIn("google.com/maps/dir/", response.headers["Location"])

        page = self.client.get("/my-trips").get_data(as_text=True)
        self.assertIn(f"/my-trips/{self.booking_id}/arrival", page)
        self.assertNotIn("OPEN PICKUP IN GOOGLE MAPS", page)

        conn = database.get_conn()
        try:
            row = conn.execute(
                "SELECT pickup_map_opened_at FROM bookings WHERE id=?",
                (self.booking_id,)).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row["pickup_map_opened_at"])

    def test_driver_can_start_inside_pickup_without_guard_approval(self):
        conn = database.get_conn()
        conn.execute("UPDATE bookings SET pickup_location='SSD Main Gate'"
                     " WHERE id=?", (self.booking_id,))
        conn.commit()
        conn.close()
        self.client.post(f"/bookings/{self.booking_id}/approve", data={})
        self.client.post(f"/bookings/{self.booking_id}/assign", data={
            "vehicle_id": "1", "driver_id": str(self.driver_id)})
        self.set_user("driver", self.driver_id)
        self.client.post(f"/my-trips/{self.booking_id}/status",
                         data={"status": "accepted"})

        page = self.client.get("/my-trips").get_data(as_text=True)
        self.assertIn("START TRIP", page)

        self.client.post(f"/my-trips/{self.booking_id}/status",
                         data={"status": "in_progress"})
        self.assertEqual(self.booking_status(), "in_progress")

    def test_driver_cannot_start_outside_pickup_before_guard_approval(self):
        self.client.post(f"/bookings/{self.booking_id}/approve", data={})
        self.client.post(f"/bookings/{self.booking_id}/assign", data={
            "vehicle_id": "1", "driver_id": str(self.driver_id)})
        self.set_user("driver", self.driver_id)
        self.client.post(f"/my-trips/{self.booking_id}/status",
                         data={"status": "accepted"})
        self.client.post(f"/my-trips/{self.booking_id}/status",
                         data={"status": "in_progress"})
        # pickup is empty/outside SSD-LHC -> start is refused
        self.assertEqual(self.booking_status(), "accepted")

    def test_driver_cannot_complete_until_guard_records_end_odometer(self):
        conn = database.get_conn()
        conn.execute("UPDATE bookings SET pickup_location='SSD Main Gate',"
                     " guard_approved_at='2099-01-01 07:00:00'"
                     " WHERE id=?", (self.booking_id,))
        conn.commit()
        conn.close()
        self.client.post(f"/bookings/{self.booking_id}/approve", data={})
        self.client.post(f"/bookings/{self.booking_id}/assign", data={
            "vehicle_id": "1", "driver_id": str(self.driver_id)})
        self.set_user("driver", self.driver_id)
        self.client.post(f"/my-trips/{self.booking_id}/status",
                         data={"status": "accepted"})
        self.client.post(f"/my-trips/{self.booking_id}/status",
                         data={"status": "in_progress"})

        # no guard end odometer yet -> completion refused
        self.client.post(f"/my-trips/{self.booking_id}/status",
                         data={"status": "completed"})
        self.assertEqual(self.booking_status(), "in_progress")

        conn = database.get_conn()
        conn.execute("UPDATE bookings SET end_mileage=500 WHERE id=?",
                     (self.booking_id,))
        conn.commit()
        conn.close()

        self.client.post(f"/my-trips/{self.booking_id}/status",
                         data={"status": "completed"})
        self.assertEqual(self.booking_status(), "completed")

    def test_rejection_requires_reason_and_is_logged(self):
        self.client.post(f"/bookings/{self.booking_id}/decline")
        self.assertEqual(self.booking_status(), "pending")

        self.client.post(
            f"/bookings/{self.booking_id}/decline",
            data={"coordinator_remarks": "No vehicle available"})
        self.assertEqual(self.booking_status(), "declined")
        conn = database.get_conn()
        try:
            log = conn.execute(
                "SELECT action, details FROM booking_logs WHERE booking_id=?",
                (self.booking_id,)).fetchone()
            self.assertEqual(log["action"], "DECLINED")
            self.assertEqual(log["details"], "No vehicle available")
        finally:
            conn.close()

    def test_requester_cannot_approve(self):
        self.set_user("requester", self.requester_id)

        response = self.client.post(
            f"/bookings/{self.booking_id}/approve")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.booking_status(), "pending")

    def test_coordinator_can_print_trip_ticket_and_requester_cannot(self):
        conn = database.get_conn()
        conn.execute(
            "UPDATE bookings SET time_out_at='2099-01-01 08:15:00',"
            " time_in_at='2099-01-01 17:30:00', approved_by=?,"
            " start_mileage=21000, end_mileage=21340, distance_km=340"
            " WHERE id=?", (self.coordinator_id, self.booking_id))
        conn.commit()
        conn.close()
        response = self.client.get(f"/bookings/{self.booking_id}/print")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"TRIP TICKET", response.data)
        self.assertIn(b"Test Requester", response.data)
        self.assertIn(b"Test trip", response.data)
        self.assertIn(b"08:15", response.data)
        self.assertIn(b"17:30", response.data)
        self.assertIn(b"21,000", response.data)
        self.assertIn(b"21,340", response.data)
        detail = self.client.get(f"/bookings/{self.booking_id}").data
        self.assertIn(b"Approved by", detail)
        self.assertIn(b"Main Coordinator", detail)
        self.assertIn(b"window.print()", response.data)

        self.set_user("requester", self.requester_id)
        response = self.client.get(f"/bookings/{self.booking_id}/print")
        self.assertEqual(response.status_code, 403)

    def test_deactivated_users_are_hidden_by_default_but_can_be_restored(self):
        conn = database.get_conn()
        inactive_id = conn.execute(
            "INSERT INTO users (username, password_hash, full_name, role, active)"
            " VALUES (?,?,?,?,0)",
            ("inactive-user", database.hash_password("password"),
             "Inactive User", "requester")).lastrowid
        conn.commit()
        conn.close()
        self.set_user("admin", self.admin_id)

        hidden = self.client.get("/users")
        self.assertNotIn(b"inactive-user", hidden.data)
        self.assertIn(b"SHOW DEACTIVATED USERS", hidden.data)

        visible = self.client.get("/users?show_inactive=1")
        self.assertIn(b"inactive-user", visible.data)
        self.assertIn(b"INACTIVE", visible.data)

        self.client.post("/users", data={"action": "toggle", "id": inactive_id})
        restored = self.client.get("/users")
        self.assertIn(b"inactive-user", restored.data)
        conn = database.get_conn()
        try:
            active = conn.execute(
                "SELECT active FROM users WHERE id=?", (inactive_id,)
            ).fetchone()["active"]
            self.assertEqual(active, 1)
        finally:
            conn.close()

    def test_pwa_manifest_and_root_scoped_service_worker_are_available(self):
        manifest_response = self.client.get("/static/manifest.webmanifest")
        manifest = json.loads(manifest_response.get_data(as_text=True))
        worker_response = self.client.get("/service-worker.js")

        self.assertEqual(manifest_response.status_code, 200)
        self.assertEqual(manifest["start_url"], "/")
        self.assertEqual(
            {icon["sizes"] for icon in manifest["icons"]},
            {"192x192", "512x512"})
        for icon in manifest["icons"]:
            icon_response = self.client.get(icon["src"])
            icon_data = icon_response.get_data()
            size = int(icon["sizes"].split("x")[0])
            self.assertEqual(icon_response.status_code, 200)
            self.assertEqual(icon_data[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack(">II", icon_data[16:24]), (size, size))
            icon_response.close()
        self.assertEqual(worker_response.status_code, 200)
        self.assertEqual(
            worker_response.headers["Service-Worker-Allowed"], "/")
        manifest_response.close()
        worker_response.close()

    def test_coordinator_queue_exposes_review_actions(self):
        response = self.client.get("/bookings")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"APPROVE", response.data)
        self.assertIn(b"REJECT", response.data)

    def test_assigned_driver_can_confirm_client_pickup(self):
        conn = database.get_conn()
        conn.execute(
            "UPDATE bookings SET status='on_the_way', assigned_driver_id=?"
            " WHERE id=?", (self.driver_id, self.booking_id))
        conn.commit()
        conn.close()
        self.set_user("driver", self.driver_id)

        self.client.post(f"/my-trips/{self.booking_id}/arrival")
        response = self.client.post(
            f"/my-trips/{self.booking_id}/pickup", follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        conn = database.get_conn()
        try:
            booking = conn.execute(
                "SELECT status, picked_up_at FROM bookings WHERE id=?",
                (self.booking_id,)).fetchone()
            log = conn.execute(
                "SELECT action FROM booking_logs WHERE booking_id=?"
                " ORDER BY id DESC LIMIT 1",
                (self.booking_id,)).fetchone()
            self.assertEqual(booking["status"], "on_the_way")
            self.assertIsNotNone(booking["picked_up_at"])
            self.assertEqual(log["action"], "PICKED_UP")
        finally:
            conn.close()

        updates = self.client.get("/api/updates").get_json()
        self.assertEqual(updates["bookings"][0]["picked_up_at"],
                         booking["picked_up_at"])
        self.assertIsNotNone(updates["bookings"][0]["driver_arrived_at"])

    def test_email_body_uses_trip_detail_fields(self):
        conn = database.get_conn()
        conn.execute(
            "UPDATE bookings SET department='Life Hope Center',"
            " pickup_location='ssd', dropoff_location='manila',"
            " start_mileage=27000, end_mileage=28000, distance_km=1000,"
            " time_out_at='2026-10-30 12:08:37',"
            " time_in_at='2026-10-30 12:10:57',"
            " guard_name='Edd Guzman' WHERE id=?", (self.booking_id,))
        conn.commit()
        b = database.fetch_booking(conn, self.booking_id)
        conn.close()

        text = booking_email_text(b)
        self.assertTrue(text.startswith("Dear Sir/Madam,"))
        self.assertIn("Good day! Please see the details of this trip below.",
                      text)
        self.assertIn("Trip Details - TRP-2099-0001", text)
        self.assertIn("Purpose:\tTest trip", text)
        self.assertIn("Nature of Trip:\tOfficial", text)
        self.assertIn("Department / Account:\tLife Hope Center", text)
        self.assertIn("Pickup:\tssd", text)
        self.assertIn("Drop-off:\tmanila", text)
        self.assertIn("Approved by:\tMAIN GUARD HOUSE - SG. EDD GUZMAN",
                      text)
        self.assertIn("Time OUT:\t2026-10-30 12:08:37", text)
        self.assertIn("Start Odometer:\t27,000 km", text)
        self.assertIn("Time IN:\t2026-10-30 12:10:57", text)
        self.assertIn("End Odometer:\t28,000 km", text)
        self.assertIn("Distance Traveled:\t1,000 km", text)

        page = self.client.get(f"/bookings/{self.booking_id}").get_data(
            as_text=True)
        self.assertIn("SEND VIA EMAIL", page)
        self.assertIn("mailto:?subject=Trip%20Details%20-%20TRP-2099-0001",
                      page)
        self.assertIn("Start%20Odometer%3A%0927%2C000%20km", page)

    def test_parse_coordinates_from_text_and_maps_link(self):
        self.assertEqual(parse_coordinates("14.2204, 120.9680"),
                         (14.2204, 120.968))
        self.assertEqual(
            parse_coordinates(
                "https://www.google.com/maps/@14.2203604,120.9680119,17z"),
            (14.2203604, 120.9680119))
        self.assertIsNone(parse_coordinates("SSD Main Gate"))
        self.assertIsNone(parse_coordinates("999, 120"))

    def test_pickup_pin_is_saved_and_used_on_maps_links(self):
        self.set_user("requester", self.requester_id)
        response = self.client.post("/bookings/new", data={
            "purpose": "Pinned pickup",
            "nature_of_trip": "official",
            "department": "Treasury",
            "passenger_count": "2",
            "pickup_location": "SSD Main Gate",
            "pickup_lat": "14.220360",
            "pickup_lng": "120.968012",
            "dropoff_location": "Nuvali",
            "departure_date": "2099-08-09",
        })
        self.assertEqual(response.status_code, 302)
        conn = database.get_conn()
        try:
            row = conn.execute(
                "SELECT id, pickup_lat, pickup_lng FROM bookings"
                " WHERE purpose='Pinned pickup'").fetchone()
        finally:
            conn.close()
        self.assertAlmostEqual(row["pickup_lat"], 14.22036, places=5)
        self.assertAlmostEqual(row["pickup_lng"], 120.968012, places=5)

        page = self.client.get(f"/bookings/{row['id']}").get_data(as_text=True)
        self.assertIn("destination=14.22036%2C120.968012", page)

    def _register(self, username, email, password="secret123",
                  role="requester", full_name="New User"):
        with self.client.session_transaction() as session:
            session.clear()
        sent = self.client.post("/register", data={
            "action": "send_code", "email": email, "role": role})
        self.assertEqual(sent.status_code, 200)
        match = re.search(r"DEV CODE[^0-9]*(\d{6})",
                          sent.get_data(as_text=True))
        self.assertIsNotNone(match, "dev code should be shown without SMTP")
        return self.client.post("/register", data={
            "action": "create", "full_name": full_name, "email": email,
            "username": username, "password": password,
            "confirm_password": password, "code": match.group(1),
            "role": role})

    def test_requester_can_create_own_account_with_email_code(self):
        with self.client.session_transaction() as session:
            session.clear()
        page = self.client.get("/register")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'name="code"', page.data)

        response = self._register("maria.s", "maria@example.com",
                                  full_name="Maria Santos")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/bookings/new", response.headers["Location"])

        with self.client.session_transaction() as session:
            self.assertEqual(session["user"]["role"], "requester")

        conn = database.get_conn()
        try:
            row = conn.execute(
                "SELECT role, full_name, email FROM users"
                " WHERE username='maria.s'").fetchone()
        finally:
            conn.close()
        self.assertEqual(row["role"], "requester")
        self.assertEqual(row["full_name"], "Maria Santos")
        self.assertEqual(row["email"], "maria@example.com")

    def test_registration_requires_email_code(self):
        with self.client.session_transaction() as session:
            session.clear()
        self.client.post("/register", data={
            "action": "send_code", "email": "nocode@example.com"})
        self.client.post("/register", data={
            "action": "create", "full_name": "No Code",
            "email": "nocode@example.com", "username": "nocodeuser",
            "password": "secret123", "confirm_password": "secret123",
            "code": ""})
        conn = database.get_conn()
        try:
            user = conn.execute(
                "SELECT 1 FROM users WHERE username='nocodeuser'").fetchone()
        finally:
            conn.close()
        self.assertIsNone(user)

        wrong = self.client.post("/register", data={
            "action": "create", "full_name": "No Code",
            "email": "nocode@example.com", "username": "nocodeuser",
            "password": "secret123", "confirm_password": "secret123",
            "code": "000000"})
        self.assertEqual(wrong.status_code, 200)
        conn = database.get_conn()
        try:
            user = conn.execute(
                "SELECT 1 FROM users WHERE username='nocodeuser'").fetchone()
        finally:
            conn.close()
        self.assertIsNone(user)

    def test_driver_can_self_register_with_code(self):
        with self.client.session_transaction() as session:
            session.clear()
        response = self._register("newdriver", "driver@example.com",
                                  role="driver", full_name="New Driver")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/my-trips", response.headers["Location"])
        conn = database.get_conn()
        try:
            row = conn.execute(
                "SELECT role FROM users WHERE username='newdriver'").fetchone()
        finally:
            conn.close()
        self.assertEqual(row["role"], "driver")

    def test_mobile_register_needs_email_code(self):
        sent = self.client.post("/api/mobile/register/code",
                                json={"email": "app@example.com"})
        self.assertEqual(sent.status_code, 200)
        code = sent.get_json()["dev_code"]

        missing = self.client.post("/api/mobile/register", json={
            "full_name": "App User", "email": "app@example.com",
            "username": "appuser", "password": "secret123", "code": ""})
        self.assertEqual(missing.status_code, 400)

        created = self.client.post("/api/mobile/register", json={
            "full_name": "App User", "email": "app@example.com",
            "username": "appuser", "password": "secret123", "code": code,
            "role": "driver"})
        self.assertEqual(created.status_code, 201)
        data = created.get_json()
        self.assertEqual(data["user"]["role"], "driver")

        headers = {"Authorization": f"Bearer {data['access_token']}"}
        me = self.client.get("/api/mobile/me", headers=headers)
        self.assertEqual(me.status_code, 200)

        duplicate = self.client.post("/api/mobile/register", json={
            "full_name": "App User", "email": "app2@example.com",
            "username": "appuser", "password": "secret123", "code": "123456"})
        self.assertEqual(duplicate.status_code, 409)

    def test_activity_log_is_admin_only(self):
        self.set_user("admin", self.admin_id)
        admin_page = self.client.get(f"/bookings/{self.booking_id}").data
        self.assertIn(b"Activity Log", admin_page)

        self.set_user("coordinator", self.coordinator_id)
        coord_page = self.client.get(f"/bookings/{self.booking_id}").data
        self.assertNotIn(b"Activity Log", coord_page)

    def test_admin_can_reset_another_users_password(self):
        self.set_user("admin", self.admin_id)
        response = self.client.post("/users", data={
            "action": "update", "id": str(self.requester_id),
            "full_name": "Test Requester", "phone_whatsapp": "",
            "role": "requester", "new_password": "resetpass1"})
        self.assertEqual(response.status_code, 302)

        with self.client.session_transaction() as session:
            session.clear()
        login = self.client.post("/login", data={
            "username": "requester", "password": "resetpass1"})
        self.assertEqual(login.status_code, 302)

    def test_admin_can_save_email_settings(self):
        from app import smtp_configured, smtp_settings
        self.set_user("admin", self.admin_id)
        page = self.client.get("/settings")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Email Settings", page.data)

        response = self.client.post("/settings", data={
            "action": "save", "smtp_provider": "custom",
            "smtp_domain": "@ssd.org",
            "smtp_host": "smtp.example.com",
            "smtp_port": "587", "smtp_user": "sender@example.com",
            "smtp_password": "secret", "smtp_from": "sender@example.com",
            "smtp_tls": "on"})
        self.assertEqual(response.status_code, 302)

        settings = smtp_settings()
        self.assertEqual(settings["provider"], "custom")
        self.assertEqual(settings["domain"], "@ssd.org")
        self.assertEqual(settings["host"], "smtp.example.com")
        self.assertEqual(settings["user"], "sender@example.com")
        self.assertTrue(smtp_configured())
        self.assertIn("CONFIGURED", self.client.get("/settings").get_data(
            as_text=True))

    def test_email_settings_are_admin_only(self):
        self.set_user("coordinator", self.coordinator_id)
        self.assertEqual(self.client.get("/settings").status_code, 403)

    def test_trip_detail_shows_vehicle_and_driver_together(self):
        self.client.post(f"/bookings/{self.booking_id}/approve", data={})
        self.client.post(f"/bookings/{self.booking_id}/assign", data={
            "vehicle_id": "1", "driver_id": str(self.driver_id)})

        response = self.client.get(f"/bookings/{self.booking_id}")
        body = response.get_data(as_text=True)
        self.assertIn("Vehicle / Driver", body)
        self.assertIn("SEND VIA EMAIL", body)
        self.assertIn("mailto:", body)
        self.assertNotIn("<th>Vehicle</th><th>Driver</th>", body)

        self.set_user("requester", self.requester_id)
        body = self.client.get(f"/bookings/{self.booking_id}").get_data(
            as_text=True)
        self.assertNotIn("SEND VIA EMAIL", body)

    def test_driver_decline_returns_trip_to_coordinator(self):
        self.client.post(f"/bookings/{self.booking_id}/approve", data={})
        self.client.post(f"/bookings/{self.booking_id}/assign", data={
            "vehicle_id": "1", "driver_id": str(self.driver_id)})
        self.set_user("driver", self.driver_id)
        self.client.post(f"/my-trips/{self.booking_id}/status",
                         data={"status": "declined", "reason": "Emergency"})

        self.assertEqual(self.booking_status(), "approved")
        conn = database.get_conn()
        try:
            row = conn.execute(
                "SELECT assigned_vehicle_id, assigned_driver_id FROM bookings"
                " WHERE id=?", (self.booking_id,)).fetchone()
            assignments = conn.execute(
                "SELECT COUNT(*) AS n FROM booking_assignments"
                " WHERE booking_id=?", (self.booking_id,)).fetchone()["n"]
            vehicle = conn.execute(
                "SELECT status FROM vehicles WHERE id=1").fetchone()["status"]
            log = conn.execute(
                "SELECT action FROM booking_logs WHERE booking_id=?"
                " ORDER BY id DESC LIMIT 1",
                (self.booking_id,)).fetchone()["action"]
            driver2 = conn.execute(
                "SELECT id FROM users WHERE username='driver2'").fetchone()["id"]
        finally:
            conn.close()

        self.assertIsNone(row["assigned_vehicle_id"])
        self.assertIsNone(row["assigned_driver_id"])
        self.assertEqual(assignments, 0)
        self.assertEqual(vehicle, "available")
        self.assertEqual(log, "DECLINED")

        # the coordinator can now assign a different driver
        self.set_user("coordinator", self.coordinator_id)
        self.client.post(f"/bookings/{self.booking_id}/assign", data={
            "vehicle_id": "1", "driver_id": str(driver2)})
        self.assertEqual(self.booking_status(), "assigned")

    def test_driver_confirms_dropoff_after_pickup(self):
        conn = database.get_conn()
        conn.execute(
            "UPDATE bookings SET status='in_progress', assigned_driver_id=?"
            " WHERE id=?", (self.driver_id, self.booking_id))
        conn.commit()
        conn.close()
        self.set_user("driver", self.driver_id)

        self.client.post(f"/my-trips/{self.booking_id}/arrival")
        self.client.post(f"/my-trips/{self.booking_id}/pickup")
        response = self.client.post(
            f"/my-trips/{self.booking_id}/dropoff", follow_redirects=True)
        self.assertEqual(response.status_code, 200)

        conn = database.get_conn()
        try:
            booking = conn.execute(
                "SELECT dropped_off_at FROM bookings WHERE id=?",
                (self.booking_id,)).fetchone()
            log = conn.execute(
                "SELECT action FROM booking_logs WHERE booking_id=?"
                " ORDER BY id DESC LIMIT 1",
                (self.booking_id,)).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(booking["dropped_off_at"])
        self.assertEqual(log["action"], "DROPPED_OFF")

    def test_dropoff_requires_pickup_first(self):
        conn = database.get_conn()
        conn.execute(
            "UPDATE bookings SET status='in_progress', assigned_driver_id=?"
            " WHERE id=?", (self.driver_id, self.booking_id))
        conn.commit()
        conn.close()
        self.set_user("driver", self.driver_id)

        self.client.post(f"/my-trips/{self.booking_id}/dropoff")
        conn = database.get_conn()
        try:
            row = conn.execute(
                "SELECT dropped_off_at FROM bookings WHERE id=?",
                (self.booking_id,)).fetchone()
        finally:
            conn.close()
        self.assertIsNone(row["dropped_off_at"])

    def test_arrival_and_pickup_times_are_visible_to_coordinator(self):
        conn = database.get_conn()
        conn.execute(
            "UPDATE bookings SET status='on_the_way', assigned_driver_id=?"
            " WHERE id=?", (self.driver_id, self.booking_id))
        conn.commit()
        conn.close()
        self.set_user("driver", self.driver_id)

        self.client.post(f"/my-trips/{self.booking_id}/arrival")
        self.client.post(f"/my-trips/{self.booking_id}/pickup")

        conn = database.get_conn()
        try:
            booking = conn.execute(
                "SELECT driver_arrived_at, picked_up_at FROM bookings WHERE id=?",
                (self.booking_id,)).fetchone()
            actions = [r["action"] for r in conn.execute(
                "SELECT action FROM booking_logs WHERE booking_id=? ORDER BY id",
                (self.booking_id,)).fetchall()]
        finally:
            conn.close()
        self.assertIsNotNone(booking["driver_arrived_at"])
        self.assertIsNotNone(booking["picked_up_at"])
        self.assertIn("ARRIVED_AT_PICKUP", actions)
        self.assertIn("PICKED_UP", actions)

        self.set_user("coordinator", self.coordinator_id)
        response = self.client.get("/bookings")
        self.assertIn(b"Driver Arrival", response.data)
        self.assertIn(booking["driver_arrived_at"].encode(), response.data)
        self.assertIn(booking["picked_up_at"].encode(), response.data)

    def test_driver_cannot_confirm_pickup_before_arriving(self):
        conn = database.get_conn()
        conn.execute(
            "UPDATE bookings SET status='on_the_way', assigned_driver_id=?"
            " WHERE id=?", (self.driver_id, self.booking_id))
        conn.commit()
        conn.close()
        self.set_user("driver", self.driver_id)

        self.client.post(f"/my-trips/{self.booking_id}/pickup")

        conn = database.get_conn()
        try:
            booking = conn.execute(
                "SELECT driver_arrived_at, picked_up_at FROM bookings WHERE id=?",
                (self.booking_id,)).fetchone()
            self.assertIsNone(booking["driver_arrived_at"])
            self.assertIsNone(booking["picked_up_at"])
        finally:
            conn.close()

    def test_driver_marks_on_the_way_and_client_sees_status_and_time(self):
        conn = database.get_conn()
        conn.execute(
            "UPDATE bookings SET status='accepted', assigned_driver_id=?"
            " WHERE id=?", (self.driver_id, self.booking_id))
        conn.commit()
        conn.close()
        self.set_user("driver", self.driver_id)

        response = self.client.post(
            f"/my-trips/{self.booking_id}/status",
            data={"status": "on_the_way"})

        self.assertEqual(response.status_code, 302)
        conn = database.get_conn()
        try:
            booking = conn.execute(
                "SELECT status, driver_on_the_way_at FROM bookings WHERE id=?",
                (self.booking_id,)).fetchone()
            log = conn.execute(
                "SELECT action, details FROM booking_logs WHERE booking_id=?",
                (self.booking_id,)).fetchone()
            self.assertEqual(booking["status"], "on_the_way")
            self.assertIsNotNone(booking["driver_on_the_way_at"])
            self.assertEqual(log["action"], "ON_THE_WAY")
            self.assertIn("on the way", log["details"])
        finally:
            conn.close()

        self.set_user("requester", self.requester_id)
        response = self.client.get("/my-bookings")
        self.assertIn(b"DRIVER ON THE WAY", response.data)
        response = self.client.get(f"/bookings/{self.booking_id}")
        self.assertIn(b"DRIVER ON THE WAY", response.data)
        self.assertIn(booking["driver_on_the_way_at"].encode(), response.data)

        self.set_user("driver", self.driver_id)
        self.client.post(f"/my-trips/{self.booking_id}/arrival")
        self.set_user("requester", self.requester_id)
        response = self.client.get("/my-bookings")
        self.assertIn(b"Driver arrived", response.data)
        response = self.client.get(f"/bookings/{self.booking_id}")
        self.assertIn(b"DRIVER ARRIVED AT PICKUP", response.data)
        arrival = self.client.get("/api/updates").get_json()["bookings"][0]
        self.assertIsNotNone(arrival["driver_arrived_at"])

        updates = self.client.get("/api/updates").get_json()
        self.assertEqual(updates["bookings"][0]["status"], "on_the_way")
        self.assertEqual(updates["bookings"][0]["status_label"],
                         "DRIVER ON THE WAY")
        self.assertEqual(updates["bookings"][0]["driver_on_the_way_at"],
                         booking["driver_on_the_way_at"])

    def test_only_assigned_driver_can_mark_on_the_way(self):
        conn = database.get_conn()
        conn.execute(
            "UPDATE bookings SET status='accepted', assigned_driver_id=?"
            " WHERE id=?", (self.driver_id, self.booking_id))
        other_driver_id = conn.execute(
            "SELECT id FROM users WHERE username='driver2'").fetchone()["id"]
        conn.commit()
        conn.close()
        self.set_user("driver", other_driver_id)

        response = self.client.post(
            f"/my-trips/{self.booking_id}/status",
            data={"status": "on_the_way"})

        self.assertEqual(response.status_code, 302)
        conn = database.get_conn()
        try:
            status = conn.execute(
                "SELECT status FROM bookings WHERE id=?",
                (self.booking_id,)).fetchone()["status"]
            self.assertEqual(status, "accepted")
        finally:
            conn.close()

    def test_driver_trip_page_exposes_on_the_way_action(self):
        conn = database.get_conn()
        conn.execute(
            "UPDATE bookings SET status='accepted', assigned_driver_id=?,"
            " pickup_location='SSD Main Gate',"
            " guard_approved_at='2099-01-01 07:00:00' WHERE id=?",
            (self.driver_id, self.booking_id))
        conn.commit()
        conn.close()
        self.set_user("driver", self.driver_id)

        response = self.client.get("/my-trips")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"MARK DRIVER ON THE WAY", response.data)
        self.assertIn(b"START TRIP", response.data)
        self.assertNotIn(b"START SSD GATE GPS", response.data)
        self.assertNotIn(b"start_mileage", response.data)
        self.assertNotIn(b"end_mileage", response.data)

    def test_driver_can_share_location_with_requesting_client_and_coordinator(self):
        conn = database.get_conn()
        conn.execute(
            "UPDATE bookings SET status='on_the_way', assigned_driver_id=?"
            " WHERE id=?", (self.driver_id, self.booking_id))
        conn.commit()
        conn.close()
        self.set_user("driver", self.driver_id)

        start = self.client.post(
            f"/api/my-trips/{self.booking_id}/location",
            json={"action": "start"})
        update = self.client.post(
            f"/api/my-trips/{self.booking_id}/location",
            json={"latitude": 14.5995, "longitude": 120.9842})
        self.assertEqual(start.status_code, 200)
        self.assertTrue(start.get_json()["sharing"])
        self.assertEqual(update.status_code, 200)

        self.set_user("requester", self.requester_id)
        client_response = self.client.get(
            f"/api/bookings/{self.booking_id}/driver-location")
        self.assertEqual(client_response.status_code, 200)
        self.assertEqual(
            client_response.get_json()["location"]["latitude"], 14.5995)
        self.assertEqual(
            client_response.get_json()["location"]["longitude"], 120.9842)
        self.assertEqual(client_response.headers["Cache-Control"], "no-store")

        self.set_user("coordinator", self.coordinator_id)
        coordinator_response = self.client.get(
            f"/api/bookings/{self.booking_id}/driver-location")
        self.assertEqual(coordinator_response.status_code, 200)
        self.assertIsNotNone(coordinator_response.get_json()["location"])

    def test_driver_location_is_private_to_booking_participants(self):
        conn = database.get_conn()
        outsider = conn.execute(
            "INSERT INTO users (username,password_hash,full_name,role)"
            " VALUES (?,?,?,?)",
            ("outsider", database.hash_password("password"), "Other Requester",
             "requester"))
        outsider_id = outsider.lastrowid
        conn.execute(
            "UPDATE bookings SET status='on_the_way', assigned_driver_id=?,"
            " location_sharing=1, driver_latitude=14.5, driver_longitude=120.9,"
            " driver_location_updated_at=datetime('now','localtime')"
            " WHERE id=?", (self.driver_id, self.booking_id))
        conn.commit()
        conn.close()
        self.set_user("requester", outsider_id)

        response = self.client.get(
            f"/api/bookings/{self.booking_id}/driver-location")

        self.assertEqual(response.status_code, 403)

    def test_booking_details_show_live_map_to_client_and_coordinator(self):
        conn = database.get_conn()
        conn.execute(
            "UPDATE bookings SET status='on_the_way', assigned_driver_id=?"
            " WHERE id=?", (self.driver_id, self.booking_id))
        conn.commit()
        conn.close()

        for role, user_id in (("requester", self.requester_id),
                              ("coordinator", self.coordinator_id)):
            self.set_user(role, user_id)
            response = self.client.get(f"/bookings/{self.booking_id}")
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Driver Live Location", response.data)
            self.assertIn(b"/api/bookings/' + bookingId + '/driver-location",
                          response.data)
            if role == "requester":
                bookings_response = self.client.get("/my-bookings")
                self.assertIn(b"TRACK DRIVER", bookings_response.data)

        self.set_user("driver", self.driver_id)
        response = self.client.get(f"/bookings/{self.booking_id}")
        self.assertNotIn(b"Driver Live Location", response.data)

    def test_driver_location_requires_active_sharing_and_valid_coordinates(self):
        conn = database.get_conn()
        conn.execute(
            "UPDATE bookings SET status='on_the_way', assigned_driver_id=?"
            " WHERE id=?", (self.driver_id, self.booking_id))
        conn.commit()
        conn.close()
        self.set_user("driver", self.driver_id)

        before_start = self.client.post(
            f"/api/my-trips/{self.booking_id}/location",
            json={"latitude": 14.5, "longitude": 120.9})
        invalid = self.client.post(
            f"/api/my-trips/{self.booking_id}/location",
            json={"latitude": 95, "longitude": 120.9})
        self.assertEqual(before_start.status_code, 409)
        self.assertEqual(invalid.status_code, 400)

    def test_arrival_stops_and_clears_live_location(self):
        conn = database.get_conn()
        conn.execute(
            "UPDATE bookings SET status='on_the_way', assigned_driver_id=?,"
            " location_sharing=1, driver_latitude=14.5, driver_longitude=120.9,"
            " driver_location_updated_at=datetime('now','localtime')"
            " WHERE id=?", (self.driver_id, self.booking_id))
        conn.commit()
        conn.close()
        self.set_user("driver", self.driver_id)

        response = self.client.post(
            f"/my-trips/{self.booking_id}/arrival")
        self.assertEqual(response.status_code, 302)
        conn = database.get_conn()
        try:
            row = conn.execute(
                "SELECT location_sharing, driver_latitude, driver_longitude"
                " FROM bookings WHERE id=?", (self.booking_id,)).fetchone()
            self.assertEqual(row["location_sharing"], 0)
            self.assertIsNone(row["driver_latitude"])
            self.assertIsNone(row["driver_longitude"])
        finally:
            conn.close()
        location_response = self.client.get(
            f"/api/bookings/{self.booking_id}/driver-location")
        self.assertFalse(location_response.get_json()["sharing"])
        self.assertIsNone(location_response.get_json()["location"])

    def test_expired_location_is_not_returned_to_client(self):
        conn = database.get_conn()
        conn.execute(
            "UPDATE bookings SET status='on_the_way', assigned_driver_id=?,"
            " location_sharing=1, driver_latitude=14.5, driver_longitude=120.9,"
            " driver_location_updated_at='2020-01-01 00:00:00'"
            " WHERE id=?", (self.driver_id, self.booking_id))
        conn.commit()
        conn.close()
        self.set_user("requester", self.requester_id)

        response = self.client.get(
            f"/api/bookings/{self.booking_id}/driver-location")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["sharing"])
        self.assertIsNone(response.get_json()["location"])
        conn = database.get_conn()
        try:
            row = conn.execute(
                "SELECT location_sharing, driver_latitude, driver_longitude"
                " FROM bookings WHERE id=?", (self.booking_id,)).fetchone()
            self.assertEqual(row["location_sharing"], 0)
            self.assertIsNone(row["driver_latitude"])
            self.assertIsNone(row["driver_longitude"])
        finally:
            conn.close()

    def test_other_driver_cannot_confirm_pickup(self):
        conn = database.get_conn()
        conn.execute(
            "UPDATE bookings SET status='accepted', assigned_driver_id=?"
            " WHERE id=?", (self.driver_id, self.booking_id))
        conn.commit()
        other_driver_id = conn.execute(
            "SELECT id FROM users WHERE username='driver2'").fetchone()["id"]
        conn.close()
        self.set_user("driver", other_driver_id)

        response = self.client.post(
            f"/my-trips/{self.booking_id}/pickup")

        self.assertEqual(response.status_code, 302)
        conn = database.get_conn()
        try:
            self.assertIsNone(conn.execute(
                "SELECT picked_up_at FROM bookings WHERE id=?",
                (self.booking_id,)).fetchone()["picked_up_at"])
        finally:
            conn.close()

    def test_coordinator_queue_shows_pickup_state(self):
        conn = database.get_conn()
        conn.execute(
            "UPDATE bookings SET status='accepted', picked_up_at='2026-10-26 12:34:56'"
            " WHERE id=?", (self.booking_id,))
        conn.commit()
        conn.close()

        response = self.client.get("/bookings")

        self.assertIn(b"Client Pickup", response.data)
        self.assertIn(b"PICKED UP", response.data)
        self.assertIn(b"2026-10-26 12:34:56", response.data)

    def test_dashboard_has_automatic_refresh_fallback(self):
        self.set_user("admin", self.coordinator_id)

        response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<meta http-equiv="refresh" content="15">', response.data)

    def test_google_maps_directions_link_is_rendered_for_route(self):
        conn = database.get_conn()
        conn.execute(
            "UPDATE bookings SET pickup_location=?, dropoff_location=?"
            " WHERE id=?",
            ("LHC Main Gate", "NAIA Terminal 3", self.booking_id))
        conn.commit()
        conn.close()

        response = self.client.get(f"/bookings/{self.booking_id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"OPEN ROUTE IN MAPS", response.data)
        self.assertIn(b"www.google.com/maps/dir/", response.data)
        self.assertIn(b"origin=LHC+Main+Gate", response.data)
        self.assertIn(b"destination=NAIA+Terminal+3", response.data)

    def test_google_maps_url_encodes_locations(self):
        url = maps_directions_url("NAIA Terminal 3", "LHC & Main Gate")

        self.assertEqual(
            url,
            "https://www.google.com/maps/dir/?api=1&destination=NAIA+Terminal+3"
            "&origin=LHC+%26+Main+Gate")

    def mobile_driver_token(self):
        response = self.client.post(
            "/api/mobile/login",
            json={"username": "driver1", "password": "driver123"})
        self.assertEqual(response.status_code, 200)
        return response.get_json()["access_token"]

    def assign_test_booking_to_driver(self, status="assigned"):
        conn = database.get_conn()
        conn.execute(
            "UPDATE bookings SET status=?, assigned_driver_id=?,"
            " pickup_location='SSD Main Gate', dropoff_location='Test site',"
            " guard_approved_at='2099-01-01 07:00:00'"
            " WHERE id=?", (status, self.driver_id, self.booking_id))
        conn.commit()
        conn.close()

    def test_mobile_login_supports_roles_and_issues_revocable_token(self):
        coordinator_login = self.client.post(
            "/api/mobile/login",
            json={"username": "coordinator", "password": "coord123"})
        self.assertEqual(coordinator_login.status_code, 200)
        coordinator_headers = {
            "Authorization": "Bearer " + coordinator_login.get_json()["access_token"]}
        self.assertEqual(
            self.client.get("/api/mobile/my-trips",
                            headers=coordinator_headers).status_code, 403)

        malformed = self.client.post("/api/mobile/login", json=["not", "object"])
        self.assertEqual(malformed.status_code, 400)

        token = self.mobile_driver_token()
        headers = {"Authorization": f"Bearer {token}"}
        trips = self.client.get("/api/mobile/my-trips", headers=headers)
        self.assertEqual(trips.status_code, 200)
        self.assertEqual(trips.get_json()["trips"], [])

        logout = self.client.post("/api/mobile/logout", headers=headers)
        self.assertEqual(logout.status_code, 200)
        revoked = self.client.get("/api/mobile/my-trips", headers=headers)
        self.assertEqual(revoked.status_code, 401)

    def test_mobile_requester_can_create_and_view_own_booking(self):
        response = self.client.post(
            "/api/mobile/login",
            json={"username": "requester", "password": "password"})
        self.assertEqual(response.status_code, 200)
        headers = {"Authorization": f"Bearer {response.get_json()['access_token']}"}

        created = self.client.post(
            "/api/mobile/requester/bookings",
            headers=headers,
            json={
                "purpose": "Airport pickup",
                "nature_of_trip": "official",
                "department": "Logistics Dept",
                "passenger_count": 3,
                "vehicle_type_needed": "Van",
                "pickup_location": "SSD",
                "dropoff_location": "NAIA",
                "departure_date": "2099-01-02",
                "departure_time": "08:30",
                "is_roundtrip": True,
                "return_date": "2099-01-03",
                "return_time": "16:00",
                "remarks": "Mobile request",
            })
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.get_json()["status"], "pending")
        return_booking = created.get_json()["return_booking"]
        self.assertNotEqual(return_booking["id"], created.get_json()["id"])

        listed = self.client.get(
            "/api/mobile/requester/bookings", headers=headers)
        self.assertEqual(listed.status_code, 200)
        ids = {b["id"] for b in listed.get_json()["bookings"]}
        self.assertIn(created.get_json()["id"], ids)
        self.assertIn(return_booking["id"], ids)
        returns = [b for b in listed.get_json()["bookings"]
                   if b["id"] == return_booking["id"]][0]
        self.assertEqual(returns["pickup_location"], "NAIA")
        self.assertEqual(returns["dropoff_location"], "SSD")
        self.assertEqual(returns["departure_date"], "2099-01-03")
        self.assertEqual(returns["departure_time"], "16:00")

        cannot_review = self.client.get(
            "/api/mobile/coordinator/bookings", headers=headers)
        self.assertEqual(cannot_review.status_code, 403)

    def _requester_headers(self):
        response = self.client.post(
            "/api/mobile/login",
            json={"username": "requester", "password": "password"})
        return {"Authorization": f"Bearer {response.get_json()['access_token']}"}

    def test_mobile_official_booking_requires_department(self):
        headers = self._requester_headers()
        base = {
            "purpose": "Division errand",
            "vehicle_type_needed": "Sedan",
            "departure_date": "2099-04-04",
            "nature_of_trip": "official",
        }
        missing = self.client.post(
            "/api/mobile/requester/bookings", headers=headers, json=base)
        self.assertEqual(missing.status_code, 400)
        self.assertIn("Department", missing.get_json()["error"])

        with_dept = self.client.post(
            "/api/mobile/requester/bookings", headers=headers,
            json={**base, "department": "Treasury"})
        self.assertEqual(with_dept.status_code, 201)

        conn = database.get_conn()
        row = conn.execute(
            "SELECT nature_of_trip, department FROM bookings WHERE id=?",
            (with_dept.get_json()["id"],)).fetchone()
        conn.close()
        self.assertEqual(row["nature_of_trip"], "official")
        self.assertEqual(row["department"], "Treasury")

    def test_mobile_international_flight_sets_driver_departure(self):
        headers = self._requester_headers()
        created = self.client.post(
            "/api/mobile/requester/bookings", headers=headers,
            json={
                "purpose": "Airport run",
                "vehicle_type_needed": "Van",
                "departure_date": "2099-06-01",
                "nature_of_trip": "personal",
                "flight_type": "international",
                "flight_number": "ph 105",
                "flight_date": "2099-06-02",
                "flight_time": "10:00",
            })
        self.assertEqual(created.status_code, 201)
        booking = self.client.get(
            "/api/mobile/requester/bookings", headers=headers).get_json()
        trip = next(b for b in booking["bookings"]
                    if b["id"] == created.get_json()["id"])
        self.assertEqual(trip["flight_type"], "international")
        self.assertEqual(trip["flight_number"], "PH 105")
        self.assertEqual(trip["departure_date"], "2099-06-02")
        self.assertEqual(trip["departure_time"], "06:00")

        missing = self.client.post(
            "/api/mobile/requester/bookings", headers=headers,
            json={"purpose": "No time", "vehicle_type_needed": "Van",
                  "departure_date": "2099-06-01", "nature_of_trip": "personal",
                  "flight_type": "domestic", "flight_date": "2099-06-02"})
        self.assertEqual(missing.status_code, 400)

    def test_mobile_personal_booking_uses_requester_name(self):
        headers = self._requester_headers()
        created = self.client.post(
            "/api/mobile/requester/bookings", headers=headers,
            json={
                "purpose": "Personal ride",
                "vehicle_type_needed": "Sedan",
                "departure_date": "2099-05-05",
                "nature_of_trip": "personal",
                "department": "Should Be Ignored",
            })
        self.assertEqual(created.status_code, 201)

        conn = database.get_conn()
        row = conn.execute(
            "SELECT nature_of_trip, department, requested_by FROM bookings"
            " WHERE id=?", (created.get_json()["id"],)).fetchone()
        requester_name = conn.execute(
            "SELECT full_name FROM users WHERE id=?",
            (row["requested_by"],)).fetchone()["full_name"]
        conn.close()
        self.assertEqual(row["nature_of_trip"], "personal")
        self.assertEqual(row["department"], requester_name)

    def test_mobile_coordinator_can_review_and_assign_matching_vehicle(self):
        login = self.client.post(
            "/api/mobile/login",
            json={"username": "coordinator", "password": "coord123"})
        self.assertEqual(login.status_code, 200)
        headers = {"Authorization": f"Bearer {login.get_json()['access_token']}"}

        queue = self.client.get(
            "/api/mobile/coordinator/bookings", headers=headers)
        self.assertEqual(queue.status_code, 200)
        self.assertEqual(queue.get_json()["bookings"][0]["id"], self.booking_id)
        resources = self.client.get(
            "/api/mobile/coordinator/resources", headers=headers)
        self.assertEqual(resources.status_code, 200)

        approve = self.client.post(
            f"/api/mobile/coordinator/bookings/{self.booking_id}/review",
            headers=headers, json={"action": "approve"})
        self.assertEqual(approve.status_code, 200)
        assign = self.client.post(
            f"/api/mobile/coordinator/bookings/{self.booking_id}/review",
            headers=headers,
            json={
                "action": "assign",
                "vehicle_id": 1,
                "driver_id": self.driver_id,
                "coordinator_remarks": "Mobile assignment",
            })
        self.assertEqual(assign.status_code, 200)
        self.assertEqual(self.booking_status(), "assigned")

    def test_mobile_trip_actions_require_assignment_and_log_workflow(self):
        self.assign_test_booking_to_driver()
        token = self.mobile_driver_token()
        headers = {"Authorization": f"Bearer {token}"}
        trips = self.client.get("/api/mobile/my-trips", headers=headers)
        self.assertEqual(trips.status_code, 200)
        self.assertEqual(trips.get_json()["trips"][0]["booking_code"],
                         "TRP-2099-0001")

        accepted = self.client.post(
            f"/api/mobile/my-trips/{self.booking_id}/action",
            json={"action": "accept"}, headers=headers)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.get_json()["status"], "accepted")

        started = self.client.post(
            f"/api/mobile/my-trips/{self.booking_id}/action",
            json={"action": "start"}, headers=headers)
        self.assertEqual(started.status_code, 200)
        self.assertEqual(started.get_json()["status"], "in_progress")

        blocked = self.client.post(
            f"/api/mobile/my-trips/{self.booking_id}/action",
            json={"action": "complete"}, headers=headers)
        self.assertEqual(blocked.status_code, 409)

        conn = database.get_conn()
        conn.execute("UPDATE bookings SET end_mileage=1230 WHERE id=?",
                     (self.booking_id,))
        conn.commit()
        conn.close()

        completed = self.client.post(
            f"/api/mobile/my-trips/{self.booking_id}/action",
            json={"action": "complete"}, headers=headers)
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.get_json()["status"], "completed")

        conn = database.get_conn()
        try:
            log_actions = [row["action"] for row in conn.execute(
                "SELECT action FROM booking_logs WHERE booking_id=?"
                " ORDER BY id", (self.booking_id,)).fetchall()]
            self.assertEqual(log_actions, ["ACCEPTED", "IN_PROGRESS", "COMPLETED"])
        finally:
            conn.close()

    def test_mobile_tokens_reject_deactivated_drivers(self):
        token = self.mobile_driver_token()
        conn = database.get_conn()
        conn.execute("UPDATE users SET active=0 WHERE id=?", (self.driver_id,))
        conn.commit()
        conn.close()

        response = self.client.get(
            "/api/mobile/my-trips",
            headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(response.status_code, 401)

    def test_production_requires_secret_and_uses_secure_cookies(self):
        with patch.dict(os.environ, {
                "TRANSPORT_ENV": "production",
                "TRANSPORT_SECRET_KEY": ""}):
            with self.assertRaisesRegex(RuntimeError, "TRANSPORT_SECRET_KEY"):
                create_app()

        with patch.dict(os.environ, {
                "TRANSPORT_ENV": "production",
                "TRANSPORT_SECRET_KEY": "test-production-secret"}):
            app = create_app()
            self.assertTrue(app.config["SESSION_COOKIE_SECURE"])
            self.assertTrue(app.config["SESSION_COOKIE_HTTPONLY"])
            self.assertEqual(app.config["SESSION_COOKIE_SAMESITE"], "Lax")


if __name__ == "__main__":
    unittest.main()
