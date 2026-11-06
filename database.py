"""SQLite database layer for the LHC Transport Booking system."""

import hashlib
import os
import secrets
import sqlite3
import time
from datetime import datetime


def _resolve_db_file():
    env = os.environ.get("TRANSPORT_DB_PATH")
    if env:
        db_file = os.path.abspath(env)
        os.makedirs(os.path.dirname(db_file), exist_ok=True)
        return db_file
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "transport.db")


DB_FILE = _resolve_db_file()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'requester',
    phone_whatsapp TEXT DEFAULT '',
    email TEXT DEFAULT '',
    active INTEGER DEFAULT 1,
    date_added TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS vehicles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plate_number TEXT NOT NULL,
    vehicle_name TEXT NOT NULL,
    vehicle_type TEXT NOT NULL,
    capacity INTEGER DEFAULT 1,
    driver_id INTEGER REFERENCES users(id),
    status TEXT DEFAULT 'available',
    mileage INTEGER DEFAULT 0,
    remarks TEXT DEFAULT '',
    date_added TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_code TEXT UNIQUE NOT NULL,
    requested_by INTEGER REFERENCES users(id),
    is_guest INTEGER DEFAULT 0,
    guest_name TEXT DEFAULT '',
    guest_email TEXT DEFAULT '',
    guest_phone TEXT DEFAULT '',
    purpose TEXT NOT NULL,
    nature_of_trip TEXT DEFAULT 'official',
    department TEXT DEFAULT '',
    passenger_count INTEGER DEFAULT 1,
    vehicle_type_needed TEXT NOT NULL,
    pickup_location TEXT DEFAULT '',
    pickup_lat REAL,
    pickup_lng REAL,
    dropoff_location TEXT DEFAULT '',
    departure_date TEXT DEFAULT '',
    departure_time TEXT DEFAULT '',
    is_roundtrip INTEGER DEFAULT 0,
    return_date TEXT DEFAULT '',
    return_time TEXT DEFAULT '',
    remarks TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    assigned_vehicle_id INTEGER REFERENCES vehicles(id),
    assigned_driver_id INTEGER REFERENCES users(id),
    start_mileage INTEGER,
    end_mileage INTEGER,
    distance_km INTEGER,
    picked_up_at TEXT,
    driver_on_the_way_at TEXT,
    driver_arrived_at TEXT,
    dropped_off_at TEXT,
    pickup_map_opened_at TEXT,
    location_sharing INTEGER DEFAULT 0,
    driver_latitude REAL,
    driver_longitude REAL,
    driver_location_updated_at TEXT,
    ssd_gate_out_at TEXT,
    ssd_gate_in_at TEXT,
    time_out_at TEXT,
    time_in_at TEXT,
    approved_by INTEGER REFERENCES users(id),
    guard_approved_at TEXT,
    guard_name TEXT,
    ssd_gate_out_latitude REAL,
    ssd_gate_out_longitude REAL,
    ssd_gate_in_latitude REAL,
    ssd_gate_in_longitude REAL,
    ssd_gate_state TEXT,
    ssd_gate_candidate_state TEXT,
    ssd_gate_candidate_count INTEGER DEFAULT 0,
    coordinator_remarks TEXT DEFAULT '',
    merged_into INTEGER REFERENCES bookings(id),
    merged_note TEXT DEFAULT '',
    date_created TEXT DEFAULT (datetime('now', 'localtime')),
    date_updated TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS registration_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    attempts INTEGER DEFAULT 0,
    date_created TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_registration_codes_email
    ON registration_codes(email);

CREATE TABLE IF NOT EXISTS booking_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL REFERENCES bookings(id),
    action TEXT NOT NULL,
    performed_by INTEGER REFERENCES users(id),
    details TEXT DEFAULT '',
    timestamp TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS mobile_api_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    token_hash TEXT UNIQUE NOT NULL,
    expires_at INTEGER NOT NULL,
    revoked_at INTEGER,
    date_created TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_mobile_api_tokens_user
    ON mobile_api_tokens(user_id, expires_at);

CREATE TABLE IF NOT EXISTS booking_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL REFERENCES bookings(id),
    vehicle_id INTEGER REFERENCES vehicles(id),
    driver_id INTEGER NOT NULL REFERENCES users(id),
    status TEXT DEFAULT 'assigned',
    start_mileage INTEGER,
    end_mileage INTEGER,
    distance_km INTEGER,
    driver_on_the_way_at TEXT,
    driver_arrived_at TEXT,
    picked_up_at TEXT,
    dropped_off_at TEXT,
    accepted_at TEXT,
    started_at TEXT,
    completed_at TEXT,
    declined_reason TEXT DEFAULT '',
    date_created TEXT DEFAULT (datetime('now', 'localtime')),
    date_updated TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_booking_assignments_booking
    ON booking_assignments(booking_id);
CREATE INDEX IF NOT EXISTS idx_booking_assignments_driver
    ON booking_assignments(driver_id);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT DEFAULT (datetime('now', 'localtime')),
    user_id INTEGER REFERENCES users(id),
    action TEXT NOT NULL,
    details TEXT DEFAULT '',
    ip_address TEXT DEFAULT ''
);
"""

VEHICLE_TYPES = ("Van", "Sedan", "SUV", "Bus")
TRIP_NATURES = ("official", "personal")
FLIGHT_TYPES = ("none", "domestic", "international")
# Driver must leave this many hours before the flight departs.
FLIGHT_LEAD_HOURS = {"domestic": 3, "international": 4}
BOOKING_STATUSES = ("pending", "approved", "assigned", "accepted",
                    "on_the_way",
                    "in_progress", "completed", "declined", "cancelled")
VEHICLE_STATUSES = ("available", "in_use", "maintenance", "inactive")
ROLES = ("admin", "coordinator", "driver", "requester", "guard")


# ---------------------------------------------------------------------- #
# Connection helpers
# ---------------------------------------------------------------------- #
def get_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def hash_password(password, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return f"{salt}${dk.hex()}"


def verify_password(password, stored):
    try:
        salt, _ = stored.split("$", 1)
    except ValueError:
        return False
    return secrets.compare_digest(hash_password(password, salt), stored)


def _ensure_columns(conn, table, columns):
    """Add missing columns to an existing table (safe migration)."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, ddl in columns:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def init_db():
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        _ensure_columns(conn, "vehicles", [("mileage", "INTEGER DEFAULT 0")])
        _ensure_columns(conn, "users", [("email", "TEXT DEFAULT ''")])
        _ensure_columns(conn, "bookings", [
            ("nature_of_trip", "TEXT DEFAULT 'official'"),
            ("department", "TEXT DEFAULT ''"),
            ("start_mileage", "INTEGER"),
            ("end_mileage", "INTEGER"),
            ("distance_km", "INTEGER"),
            ("picked_up_at", "TEXT"),
            ("driver_on_the_way_at", "TEXT"),
            ("driver_arrived_at", "TEXT"),
            ("location_sharing", "INTEGER DEFAULT 0"),
            ("driver_latitude", "REAL"),
            ("driver_longitude", "REAL"),
            ("driver_location_updated_at", "TEXT"),
            ("ssd_gate_out_at", "TEXT"),
            ("ssd_gate_in_at", "TEXT"),
            ("ssd_gate_out_latitude", "REAL"),
            ("ssd_gate_out_longitude", "REAL"),
            ("ssd_gate_in_latitude", "REAL"),
            ("ssd_gate_in_longitude", "REAL"),
            ("ssd_gate_state", "TEXT"),
            ("ssd_gate_candidate_state", "TEXT"),
            ("ssd_gate_candidate_count", "INTEGER DEFAULT 0"),
            ("time_out_at", "TEXT"),
            ("time_in_at", "TEXT"),
            ("approved_by", "INTEGER REFERENCES users(id)"),
            ("guard_approved_at", "TEXT"),
            ("guard_name", "TEXT"),
            ("dropped_off_at", "TEXT"),
            ("pickup_map_opened_at", "TEXT"),
            ("pickup_lat", "REAL"),
            ("pickup_lng", "REAL"),
            ("merged_into", "INTEGER"),
            ("merged_note", "TEXT DEFAULT ''"),
            ("flight_type", "TEXT DEFAULT 'none'"),
            ("flight_number", "TEXT DEFAULT ''"),
            ("flight_date", "TEXT DEFAULT ''"),
            ("flight_time", "TEXT DEFAULT ''"),
            ("is_guest", "INTEGER DEFAULT 0"),
            ("guest_name", "TEXT DEFAULT ''"),
            ("guest_email", "TEXT DEFAULT ''"),
            ("guest_phone", "TEXT DEFAULT ''"),
        ])
        _ensure_columns(conn, "booking_assignments", [
            ("dropped_off_at", "TEXT"),
        ])
        # Older rows kept the departure/return stamps in the SSD gate columns.
        conn.execute(
            "UPDATE bookings SET time_out_at=ssd_gate_out_at"
            " WHERE time_out_at IS NULL AND ssd_gate_out_at IS NOT NULL")
        conn.execute(
            "UPDATE bookings SET time_in_at=ssd_gate_in_at"
            " WHERE time_in_at IS NULL AND ssd_gate_in_at IS NOT NULL")
        _seed(conn)
        _migrate_assignments(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate_assignments(conn):
    """Create assignment rows for existing single-assignment bookings."""
    rows = conn.execute(
        "SELECT * FROM bookings WHERE assigned_vehicle_id IS NOT NULL"
        " AND assigned_driver_id IS NOT NULL").fetchall()
    for b in rows:
        exists = conn.execute(
            "SELECT id FROM booking_assignments WHERE booking_id=?",
            (b["id"],)).fetchone()
        if exists:
            continue
        status = b["status"] if b["status"] in (
            "assigned", "accepted", "on_the_way", "in_progress", "completed"
        ) else "assigned"
        conn.execute(
            "INSERT INTO booking_assignments (booking_id, vehicle_id,"
            " driver_id, status, start_mileage, end_mileage, distance_km,"
            " driver_on_the_way_at, driver_arrived_at, picked_up_at,"
            " accepted_at, started_at, completed_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (b["id"], b["assigned_vehicle_id"], b["assigned_driver_id"],
             status, b["start_mileage"], b["end_mileage"], b["distance_km"],
             b["driver_on_the_way_at"], b["driver_arrived_at"],
             b["picked_up_at"],
             None if status == "assigned" else b["date_updated"],
             None if status in ("assigned", "accepted", "on_the_way")
             else b["date_updated"],
             b["date_updated"] if status == "completed" else None))


def _seed(conn):
    """Create default admin + sample fleet on first run."""
    cur = conn.execute("SELECT COUNT(*) AS n FROM users")
    if cur.fetchone()["n"] > 0:
        return

    admin_hash = hash_password("admin123")
    coord_hash = hash_password("coord123")
    driver_hash = hash_password("driver123")

    cur = conn.execute(
        "INSERT INTO users (username, password_hash, full_name, role, phone_whatsapp)"
        " VALUES (?,?,?,?,?)",
        ("admin", admin_hash, "System Administrator", "admin", ""))
    admin_id = cur.lastrowid

    conn.execute(
        "INSERT INTO users (username, password_hash, full_name, role, phone_whatsapp)"
        " VALUES (?,?,?,?,?)",
        ("coordinator", coord_hash, "Main Coordinator", "coordinator", ""))
    conn.execute(
        "INSERT INTO users (username, password_hash, full_name, role, phone_whatsapp)"
        " VALUES (?,?,?,?,?)",
        ("driver1", driver_hash, "Driver One", "driver", ""))
    conn.execute(
        "INSERT INTO users (username, password_hash, full_name, role, phone_whatsapp)"
        " VALUES (?,?,?,?,?)",
        ("driver2", hash_password("driver123"), "Driver Two", "driver", ""))

    # Map driver usernames -> ids for sample fleet
    d1 = conn.execute("SELECT id FROM users WHERE username='driver1'").fetchone()["id"]
    d2 = conn.execute("SELECT id FROM users WHERE username='driver2'").fetchone()["id"]

    samples = [
        ("N/A-001", "Toyota Hiace Grandia", "Van", 15, d1, "available",
         "Main transport van"),
        ("N/A-002", "Toyota Innova", "SUV", 7, d2, "available", ""),
        ("N/A-003", "Honda City", "Sedan", 4, None, "available", ""),
        ("N/A-004", "Isuzu Bus", "Bus", 30, None, "available", ""),
    ]
    for plate, name, vtype, cap, drv, status, remarks in samples:
        conn.execute(
            "INSERT INTO vehicles (plate_number, vehicle_name, vehicle_type,"
            " capacity, driver_id, status, remarks) VALUES (?,?,?,?,?,?,?)",
            (plate, name, vtype, cap, drv, status, remarks))

    return admin_id


# ---------------------------------------------------------------------- #
# Booking code generator
# ---------------------------------------------------------------------- #
def next_booking_code(conn):
    year = time.localtime().tm_year
    prefix = f"TRP-{year}-"
    row = conn.execute(
        "SELECT booking_code FROM bookings WHERE booking_code LIKE ?"
        " ORDER BY id DESC LIMIT 1", (prefix + "%",)).fetchone()
    if row is None:
        n = 1
    else:
        try:
            n = int(row["booking_code"].split("-")[-1]) + 1
        except ValueError:
            n = 1
    return f"{prefix}{n:04d}"


def log_action(conn, booking_id, action, performed_by, details=""):
    conn.execute(
        "INSERT INTO booking_logs (booking_id, action, performed_by, details)"
        " VALUES (?,?,?,?)",
        (booking_id, action, performed_by, details))


def log_audit(conn, user_id, action, details="", ip_address=""):
    conn.execute(
        "INSERT INTO audit_logs (user_id, action, details, ip_address)"
        " VALUES (?,?,?,?)",
        (user_id, action, details, ip_address))


# ---------------------------------------------------------------------- #
# Queries
# ---------------------------------------------------------------------- #
def fetch_users(conn, role=None, active_only=True):
    q = "SELECT u.* FROM users u WHERE 1=1"
    args = []
    if active_only:
        q += " AND u.active = 1"
    if role:
        q += " AND u.role = ?"
        args.append(role)
    q += " ORDER BY u.full_name"
    return conn.execute(q, args).fetchall()


def fetch_vehicles(conn, vtype=None, status=None, available_only=False):
    q = ("SELECT v.*, u.full_name AS driver_name FROM vehicles v"
         " LEFT JOIN users u ON u.id = v.driver_id"
         " WHERE 1=1")
    args = []
    if vtype:
        q += " AND v.vehicle_type = ?"
        args.append(vtype)
    if status:
        q += " AND v.status = ?"
        args.append(status)
    if available_only:
        q += " AND v.status IN ('available','in_use')"
    q += " ORDER BY v.vehicle_type, v.vehicle_name"
    return conn.execute(q, args).fetchall()


def fetch_bookings(conn, status=None, requested_by=None, driver_id=None,
                   date_from=None, date_to=None, include_merged=False):
    q = ("SELECT b.*,"
         " COALESCE(CASE WHEN b.is_guest=1 THEN b.guest_name END, ru.full_name)"
         " AS requester_name,"
         " COALESCE(CASE WHEN b.is_guest=1 THEN b.guest_phone END,"
         " ru.phone_whatsapp) AS requester_phone,"
         " v.plate_number, v.vehicle_name, v.vehicle_type,"
         " du.full_name AS driver_name,"
         " du.phone_whatsapp AS driver_phone,"
         " au.full_name AS approver_name,"
         " CASE WHEN b.guard_name IS NOT NULL AND b.guard_name <> ''"
         " THEN 'MAIN GUARD HOUSE - SG. ' || UPPER(b.guard_name)"
         " END AS guard_approval"
         " FROM bookings b"
         " LEFT JOIN users ru ON ru.id = b.requested_by"
         " LEFT JOIN vehicles v ON v.id = b.assigned_vehicle_id"
         " LEFT JOIN users du ON du.id = b.assigned_driver_id"
         " LEFT JOIN users au ON au.id = b.approved_by"
         " WHERE 1=1"
         + (" AND (b.status IS NULL OR b.status != 'merged')"
            if not include_merged else ""))

    args = []
    if status:
        q += " AND b.status = ?"
        args.append(status)
    if requested_by:
        q += " AND b.requested_by = ?"
        args.append(requested_by)
    if driver_id:
        q += (" AND (b.assigned_driver_id = ? OR EXISTS (SELECT 1 FROM"
              " booking_assignments ba WHERE ba.booking_id = b.id"
              " AND ba.driver_id = ?))")
        args.extend([driver_id, driver_id])
    if date_from:
        q += " AND b.departure_date >= ?"
        args.append(date_from)
    if date_to:
        q += " AND b.departure_date <= ?"
        args.append(date_to)
    q += " ORDER BY b.date_created DESC"
    return conn.execute(q, args).fetchall()


def fetch_booking(conn, booking_id):
    return conn.execute(
        "SELECT b.*,"
        " COALESCE(CASE WHEN b.is_guest=1 THEN b.guest_name END, ru.full_name)"
        " AS requester_name,"
        " COALESCE(CASE WHEN b.is_guest=1 THEN b.guest_phone END,"
        " ru.phone_whatsapp) AS requester_phone,"
        " v.plate_number, v.vehicle_name, v.vehicle_type,"
        " v.capacity AS vehicle_capacity,"
        " du.full_name AS driver_name,"
        " du.phone_whatsapp AS driver_phone,"
        " au.full_name AS approver_name,"
        " CASE WHEN b.guard_name IS NOT NULL AND b.guard_name <> ''"
        " THEN 'MAIN GUARD HOUSE - SG. ' || UPPER(b.guard_name)"
        " END AS guard_approval"
        " FROM bookings b"
        " LEFT JOIN users ru ON ru.id = b.requested_by"
        " LEFT JOIN vehicles v ON v.id = b.assigned_vehicle_id"
        " LEFT JOIN users du ON du.id = b.assigned_driver_id"
        " LEFT JOIN users au ON au.id = b.approved_by"
        " WHERE b.id = ?", (booking_id,)).fetchone()


def fetch_all_logs(conn, limit=200, action=None, user_id=None):
    """System-wide audit log across all bookings and system events (admin only)."""
    # Booking logs
    bq = ("SELECT l.id, l.timestamp, l.action, l.details, l.performed_by AS user_id,"
          " b.booking_code, l.booking_id, u.full_name AS performed_by_name, "
          " 'booking' AS source, '' AS ip_address "
          "FROM booking_logs l "
          "LEFT JOIN bookings b ON b.id = l.booking_id "
          "LEFT JOIN users u ON u.id = l.performed_by "
          "WHERE 1=1")
    bargs = []
    if action:
        bq += " AND l.action = ?"
        bargs.append(action)
    if user_id:
        bq += " AND l.performed_by = ?"
        bargs.append(user_id)

    # Audit logs (login, logout, etc.)
    aq = ("SELECT a.id + 1000000 AS id, a.timestamp, a.action, a.details,"
          " a.user_id, NULL AS booking_code, NULL AS booking_id,"
          " u.full_name AS performed_by_name,"
          " 'system' AS source, a.ip_address "
          "FROM audit_logs a "
          "LEFT JOIN users u ON u.id = a.user_id "
          "WHERE 1=1")
    aargs = []
    if action:
        aq += " AND a.action = ?"
        aargs.append(action)
    if user_id:
        aq += " AND a.user_id = ?"
        aargs.append(user_id)

    q = bq + " UNION ALL " + aq + " ORDER BY timestamp DESC, id DESC LIMIT ?"
    args = bargs + aargs + [limit]
    return conn.execute(q, args).fetchall()


def fetch_assignments(conn, booking_id):
    """All vehicle/driver assignments for a booking."""
    return conn.execute(
        "SELECT a.*, v.plate_number, v.vehicle_name, v.vehicle_type,"
        " d.full_name AS driver_name, d.phone_whatsapp AS driver_phone"
        " FROM booking_assignments a"
        " JOIN vehicles v ON v.id = a.vehicle_id"
        " JOIN users d ON d.id = a.driver_id"
        " WHERE a.booking_id=? ORDER BY a.id", (booking_id,)).fetchall()


def fetch_driver_assignments(conn, driver_id):
    """Assignments belonging to a driver, with booking/vehicle details."""
    return conn.execute(
        "SELECT b.id, b.booking_code, b.purpose, b.nature_of_trip,"
        " b.department, b.passenger_count, b.vehicle_type_needed,"
        " b.pickup_location, b.pickup_lat, b.pickup_lng,"
        " b.dropoff_location, b.departure_date,"
        " b.departure_time, b.is_roundtrip, b.return_date, b.return_time,"
        " b.remarks, b.status AS booking_status, b.requested_by,"
        " b.ssd_gate_out_at, b.ssd_gate_in_at, b.ssd_gate_out_latitude,"
        " b.ssd_gate_out_longitude, b.ssd_gate_in_latitude,"
        " b.ssd_gate_in_longitude,"
        " b.location_sharing, b.driver_latitude, b.driver_longitude,"
        " b.driver_location_updated_at, b.pickup_map_opened_at,"
        " b.guard_approved_at, b.time_out_at, b.time_in_at,"
        " a.id AS assignment_id, a.status, a.start_mileage, a.end_mileage,"
        " a.distance_km, a.driver_on_the_way_at, a.driver_arrived_at,"
        " a.picked_up_at, a.dropped_off_at, a.declined_reason,"
        " v.plate_number, v.vehicle_name, v.vehicle_type,"
        " ru.full_name AS requester_name, ru.phone_whatsapp AS requester_phone"
        " FROM booking_assignments a"
        " JOIN bookings b ON b.id = a.booking_id"
        " LEFT JOIN vehicles v ON v.id = a.vehicle_id"
        " JOIN users ru ON ru.id = b.requested_by"
        " WHERE a.driver_id=?"
        " AND a.status != 'declined'"
        " AND b.status NOT IN ('cancelled','declined')"
        " ORDER BY b.date_created DESC", (driver_id,)).fetchall()


def ensure_driver_assignments(conn, driver_id):
    """Create mirror assignment rows for legacy single-driver bookings."""
    rows = conn.execute(
        "SELECT b.* FROM bookings b WHERE b.assigned_driver_id=?"
        " AND NOT EXISTS (SELECT 1 FROM booking_assignments a"
        " WHERE a.booking_id=b.id)", (driver_id,)).fetchall()
    for b in rows:
        status = b["status"] if b["status"] in (
            "assigned", "accepted", "on_the_way", "in_progress",
            "completed") else "assigned"
        conn.execute(
            "INSERT INTO booking_assignments (booking_id, vehicle_id,"
            " driver_id, status, start_mileage, end_mileage, distance_km,"
            " driver_on_the_way_at, driver_arrived_at, picked_up_at,"
            " dropped_off_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (b["id"], b["assigned_vehicle_id"], driver_id, status,
             b["start_mileage"], b["end_mileage"], b["distance_km"],
             b["driver_on_the_way_at"], b["driver_arrived_at"],
             b["picked_up_at"], b["dropped_off_at"]))


def recompute_booking_status(conn, booking_id):
    """Derive the booking status from its assignments."""
    rows = conn.execute(
        "SELECT status FROM booking_assignments WHERE booking_id=?",
        (booking_id,)).fetchall()
    if not rows:
        return
    statuses = [r["status"] for r in rows]
    if all(s == "completed" for s in statuses):
        new_status = "completed"
    elif all(s == "declined" for s in statuses):
        new_status = "approved"
    elif any(s == "in_progress" for s in statuses):
        new_status = "in_progress"
    elif any(s == "on_the_way" for s in statuses):
        new_status = "on_the_way"
    elif any(s == "accepted" for s in statuses):
        new_status = "accepted"
    else:
        new_status = "assigned"
    conn.execute(
        "UPDATE bookings SET status=?, date_updated=? WHERE id=?",
        (new_status, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
         booking_id))


def fetch_logs(conn, booking_id):
    return conn.execute(
        "SELECT l.*, u.full_name AS performed_by_name FROM booking_logs l"
        " LEFT JOIN users u ON u.id = l.performed_by"
        " WHERE l.booking_id = ? ORDER BY l.timestamp", (booking_id,)).fetchall()


def dashboard_stats(conn):
    stats = {}
    for key, status in [
        ("pending", "pending"), ("approved", "approved"),
        ("assigned", "assigned"),
        ("accepted", "accepted"), ("on_the_way", "on_the_way"),
        ("in_progress", "in_progress"),
        ("completed", "completed"), ("declined", "declined"),
        ("cancelled", "cancelled")]:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM bookings WHERE status=?",
            (status,)).fetchone()
        stats[key] = row["n"]

    row = conn.execute(
        "SELECT COUNT(*) AS n FROM vehicles WHERE status='available'").fetchone()
    stats["vehicles_available"] = row["n"]
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM vehicles WHERE status='in_use'").fetchone()
    stats["vehicles_in_use"] = row["n"]
    row = conn.execute("SELECT COUNT(*) AS n FROM vehicles").fetchone()
    stats["vehicles_total"] = row["n"]
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM users WHERE active=1").fetchone()
    stats["users_total"] = row["n"]

    today = time.strftime("%Y-%m-%d")
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM bookings WHERE departure_date=?",
        (today,)).fetchone()
    stats["today_trips"] = row["n"]
    return stats
