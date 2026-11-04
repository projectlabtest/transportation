"""Transport Booking System - Flask web application."""

import os
import math
import re
import smtplib
import time
import hashlib
import secrets
from datetime import datetime, timedelta
from email.message import EmailMessage
from functools import wraps
from urllib.parse import urlencode

from flask import (Flask, flash, g, jsonify, redirect, render_template,
                   request, send_from_directory, session, url_for)

import database as db

GUARD_APPROVAL_PREFIX = "MAIN GUARD HOUSE - SG."


def pickup_is_at_gate(pickup_location):
    """True when the pickup point is inside SSD/LHC (trip may start first)."""
    text = (pickup_location or "").lower()
    return "ssd" in text or "lhc" in text


def create_app():
    app = Flask(__name__)
    production = os.environ.get("TRANSPORT_ENV", "").lower() == "production"
    secret_key = os.environ.get("TRANSPORT_SECRET_KEY")
    if production and not secret_key:
        raise RuntimeError("TRANSPORT_SECRET_KEY is required in production.")
    app.config.update(
        SECRET_KEY=secret_key or "lhc-transport-dev-key-change-before-deploy",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=production,
    )

    @app.teardown_appcontext
    def close_db(_exc):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    @app.context_processor
    def inject_globals():
        return {"session": session, "maps_directions_url": maps_directions_url,
                "pickup_at_gate": pickup_is_at_gate, "maps_point": maps_point}

    register_routes(app)
    return app


# ---------------------------------------------------------------------- #
# Request-scoped DB + helpers
# ---------------------------------------------------------------------- #
def get_db():
    if "db" not in g:
        g.db = db.get_conn()
    return g.db


def current_user():
    return session.get("user")


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("user"):
            flash("Please log in first.", "error")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapper


def role_required(*roles):
    def deco(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            user = session.get("user")
            if not user:
                flash("Please log in first.", "error")
                return redirect(url_for("login"))
            if user["role"] not in roles:
                home_endpoint = home_for_role(user["role"])
                return render_template(
                    "access_denied.html", user=user,
                    allowed_roles=list(roles),
                    home_url=url_for(home_endpoint),
                    page_name=view.__name__.replace("_", " ").title()), 403
            return view(*args, **kwargs)
        return wrapper
    return deco


def mobile_driver_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        authorization = request.headers.get("Authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return jsonify({"error": "Driver authentication required."}), 401
        token_hash = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
        row = get_db().execute(
            "SELECT u.id, u.username, u.full_name, u.role"
            " FROM mobile_api_tokens t JOIN users u ON u.id=t.user_id"
            " WHERE t.token_hash=? AND t.revoked_at IS NULL"
            " AND t.expires_at>? AND u.active=1",
            (token_hash, int(time.time()))).fetchone()
        if not row:
            return jsonify({"error": "Driver token is invalid or expired."}), 401
        if row["role"] != "driver":
            return jsonify({"error": "This feature is only available to drivers."}), 403
        g.mobile_driver = dict(row)
        return view(*args, **kwargs)
    return wrapper


def mobile_role_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            authorization = request.headers.get("Authorization", "")
            scheme, _, token = authorization.partition(" ")
            if scheme.lower() != "bearer" or not token.strip():
                return jsonify({"error": "Authentication required."}), 401
            token_hash = hashlib.sha256(
                token.strip().encode("utf-8")).hexdigest()
            row = get_db().execute(
                "SELECT u.id, u.username, u.full_name, u.role"
                " FROM mobile_api_tokens t JOIN users u ON u.id=t.user_id"
                " WHERE t.token_hash=? AND t.revoked_at IS NULL"
                " AND t.expires_at>? AND u.active=1",
                (token_hash, int(time.time()))).fetchone()
            if not row:
                return jsonify({"error": "Token is invalid or expired."}), 401
            if row["role"] not in roles:
                return jsonify({"error": "This role cannot use this feature."}), 403
            g.mobile_user = dict(row)
            return view(*args, **kwargs)
        return wrapper
    return decorator


def home_for_role(role):
    return {
        "admin": "dashboard",
        "coordinator": "bookings",
        "guard": "bookings",
        "driver": "my_trips",
        "requester": "booking_form",
    }.get(role, "login")


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _distance_m(lat1, lon1, lat2, lon2):
    earth_radius_m = 6371000
    lat1, lon1, lat2, lon2 = map(
        math.radians, (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    haversine = (math.sin(dlat / 2) ** 2
                 + math.cos(lat1) * math.cos(lat2)
                 * math.sin(dlon / 2) ** 2)
    return 2 * earth_radius_m * math.asin(math.sqrt(haversine))


def _int_field(form, key, default=0):
    try:
        return int(form.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def whatsapp_link(phone, text):
    phone = (phone or "").strip()
    digits = "".join(ch for ch in phone if ch.isdigit())
    if not digits:
        return ""
    if digits.startswith("0"):
        digits = "63" + digits[1:]
    elif digits.startswith("9") and len(digits) == 10:
        digits = "63" + digits
    return f"https://wa.me/{digits}?text={text}"


def user_vehicle_summary(vehicle):
    if not vehicle:
        return "No vehicle registered yet."
    return (f"{vehicle['vehicle_name']} ({vehicle['plate_number']}) — "
            f"{vehicle['vehicle_type']}, {vehicle['capacity']} pax")


def driver_welcome_text(name, username, password, vehicle):
    lines = [
        f"Good day {name}!",
        "",
        "Your driver account has been created for TRANSPORT BOOKING.",
        f"Username: {username}",
    ]
    if password:
        lines.append(f"Password: {password}")
    else:
        lines.append("Password: (contact the admin if forgotten)")
    lines += ["", "Your vehicle:", "- " + user_vehicle_summary(vehicle),
              "", "Login: http://127.0.0.1:5001"]
    return "\n".join(lines)


def account_text(name, username, role):
    return (f"Good day {name}!\n\n"
            f"Your {role.upper()} account for TRANSPORT is now active.\n"
            f"Username: {username}\n"
            "Password: (contact the admin if forgotten)\n\n"
            "Login: http://127.0.0.1:5001")


def flight_departure(flight_type, flight_date, flight_time):
    """Driver departure (date, time) for a flight, or None if invalid."""
    lead = db.FLIGHT_LEAD_HOURS.get(flight_type)
    if not lead:
        return None
    try:
        flight_at = datetime.strptime(f"{flight_date} {flight_time}",
                                      "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    departs = flight_at - timedelta(hours=lead)
    return departs.strftime("%Y-%m-%d"), departs.strftime("%H:%M")


def resolve_leg(get, date_key, time_key, prefix=""):
    """Read one trip leg (outbound or return) and its optional flight.

    With a flight, the driver departure is computed from the flight time.
    Returns (leg, error).
    """
    flight_type = (get(f"{prefix}flight_type") or "none").strip().lower()
    if flight_type not in db.FLIGHT_TYPES:
        flight_type = "none"
    if flight_type == "none":
        return {
            "dep_date": (get(date_key) or "").strip(),
            "dep_time": (get(time_key) or "").strip(),
            "flight_type": "none", "flight_number": "",
            "flight_date": "", "flight_time": "",
        }, None
    flight_date = (get(f"{prefix}flight_date") or "").strip()
    flight_time = (get(f"{prefix}flight_time") or "").strip()
    driver_dep = flight_departure(flight_type, flight_date, flight_time)
    if driver_dep is None:
        label = "Return flight" if prefix else "Flight"
        return None, (f"{label}: enter the flight date and departure time.")
    return {
        "dep_date": driver_dep[0], "dep_time": driver_dep[1],
        "flight_type": flight_type,
        "flight_number": (get(f"{prefix}flight_number") or "").strip().upper(),
        "flight_date": flight_date, "flight_time": flight_time,
    }, None


def maps_directions_url(destination, origin=None):
    params = {"api": "1", "destination": destination}
    if origin:
        params["origin"] = origin
    return "https://www.google.com/maps/dir/?" + urlencode(params)


_COORDS_RE = re.compile(r"(-?\d{1,3}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)")


def parse_coordinates(text):
    """Pull lat,lng out of raw coordinates or a Google Maps link."""
    if not text:
        return None
    match = _COORDS_RE.search(text)
    if not match:
        return None
    lat, lng = float(match.group(1)), float(match.group(2))
    if -90 <= lat <= 90 and -180 <= lng <= 180:
        return lat, lng
    return None


def maps_point(label, lat=None, lng=None):
    """Exact coordinates when pinned, otherwise the typed place name."""
    if lat is not None and lng is not None:
        return f"{lat},{lng}"
    return label


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def email_is_valid(value):
    return bool(value) and bool(EMAIL_RE.match(value))


def smtp_configured():
    return bool(smtp_settings()["host"])


def smtp_settings():
    """SMTP values saved by the admin, with env vars as fallback."""
    saved = {}
    try:
        conn = db.get_conn()
        try:
            rows = conn.execute(
                "SELECT key, value FROM app_settings WHERE key LIKE 'smtp_%'"
            ).fetchall()
            saved = {row["key"]: row["value"] for row in rows}
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - never break the request
        saved = {}

    tls_value = saved.get("smtp_tls")
    if tls_value is None:
        tls_value = os.environ.get("TRANSPORT_SMTP_TLS", "1")
    return {
        "host": (saved.get("smtp_host")
                 or os.environ.get("TRANSPORT_SMTP_HOST", "")).strip(),
        "port": (saved.get("smtp_port")
                 or os.environ.get("TRANSPORT_SMTP_PORT", "587")).strip(),
        "user": (saved.get("smtp_user")
                 or os.environ.get("TRANSPORT_SMTP_USER", "")).strip(),
        "password": (saved.get("smtp_password")
                     or os.environ.get("TRANSPORT_SMTP_PASSWORD", "")),
        "from": (saved.get("smtp_from")
                 or os.environ.get("TRANSPORT_SMTP_FROM", "")).strip(),
        "tls": str(tls_value).lower() in ("1", "true", "yes", "on"),
        "provider": saved.get("smtp_provider") or "custom",
        "domain": saved.get("smtp_domain", ""),
        "configured": bool((saved.get("smtp_host")
                            or os.environ.get("TRANSPORT_SMTP_HOST", "")).strip()),
    }


def send_email(to_email, subject, body):
    """Send a plain-text email. Returns (ok, error_message)."""
    settings = smtp_settings()
    host = settings["host"]
    if not host:
        return False, "SMTP is not configured."
    try:
        port = int(settings["port"] or 587)
    except ValueError:
        return False, "SMTP port is not a number."
    user = settings["user"]
    password = settings["password"]
    sender = settings["from"] or user
    if not sender:
        return False, "No sender email (From) is set."

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = to_email
    message.set_content(body)

    try:
        with smtplib.SMTP(host, port, timeout=20) as server:
            if settings["tls"]:
                server.starttls()
            if user:
                server.login(user, password)
            server.send_message(message)
        return True, ""
    except Exception as exc:  # noqa: BLE001 - report any SMTP failure
        return False, str(exc)


def create_registration_code(conn, email):
    """Create a 6-digit code for an email address (15 minutes)."""
    code = f"{secrets.randbelow(900000) + 100000}"
    conn.execute("DELETE FROM registration_codes WHERE email=?", (email,))
    conn.execute(
        "INSERT INTO registration_codes (email, code_hash, expires_at)"
        " VALUES (?,?,?)",
        (email, hashlib.sha256(code.encode("utf-8")).hexdigest(),
         int(time.time()) + 15 * 60))
    conn.commit()
    return code


def check_registration_code(conn, email, code):
    """Return (ok, message) for a submitted email verification code."""
    row = conn.execute(
        "SELECT * FROM registration_codes WHERE email=?"
        " ORDER BY id DESC LIMIT 1", (email,)).fetchone()
    if not row:
        return False, "No code for this email. Click SEND CODE."
    if row["expires_at"] < int(time.time()):
        return False, "The code has expired. Click SEND CODE again."
    if (row["attempts"] or 0) >= 5:
        return False, "Too many wrong attempts. Request a new code."
    if hashlib.sha256(code.strip().encode("utf-8")).hexdigest() != row["code_hash"]:
        conn.execute("UPDATE registration_codes SET attempts=attempts+1"
                     " WHERE id=?", (row["id"],))
        conn.commit()
        return False, "Wrong code. Check your email and try again."
    conn.execute("DELETE FROM registration_codes WHERE email=?", (email,))
    conn.commit()
    return True, ""


def booking_email_text(b):
    """Field list used when a coordinator emails the trip details."""
    round_trip = "YES" if b["is_roundtrip"] else "NO"
    nature = (b["nature_of_trip"] or "official").capitalize()
    lines = [
        "Dear Sir/Madam,",
        "",
        "Good day! Please see the details of this trip below.",
        "",
        f"Trip Details - {b['booking_code']}",
        f"Purpose:\t{b['purpose']}",
        f"Nature of Trip:\t{nature}",
    ]
    if b["department"]:
        lines.append(f"Department / Account:\t{b['department']}")
    lines.append(f"Requester:\t{b['requester_name']}")
    if b["requester_phone"]:
        lines.append(f"Contact:\t{b['requester_phone']}")
    lines += [
        f"Passengers:\t{b['passenger_count']}",
        f"Pickup:\t{b['pickup_location'] or '-'}",
        f"Drop-off:\t{b['dropoff_location'] or '-'}",
        f"Round trip:\t{round_trip}",
    ]
    if b["vehicle_name"]:
        lines.append(f"Assigned vehicle:\t{b['vehicle_name']}"
                     f" ({b['plate_number'] or 'N/A'})")
    lines.append(f"Departure:\t{b['departure_date']} {b['departure_time']}")
    approver = b["guard_approval"] or b["approver_name"]
    if approver:
        lines.append(f"Approved by:\t{approver}")
    if b["driver_name"]:
        lines.append(f"Driver:\t{b['driver_name']}")
    if b["driver_phone"]:
        lines.append(f"Contact:\t{b['driver_phone']}")
    if b["time_out_at"]:
        lines.append(f"Time OUT:\t{b['time_out_at']}")
    if b["start_mileage"] is not None:
        lines.append(f"Start Odometer:\t{b['start_mileage']:,} km")
    if b["time_in_at"]:
        lines.append(f"Time IN:\t{b['time_in_at']}")
    if b["end_mileage"] is not None:
        lines.append(f"End Odometer:\t{b['end_mileage']:,} km")
    if b["distance_km"] is not None:
        lines.append(f"Distance Traveled:\t{b['distance_km']:,} km")
    lines.append(f"Created:\t{b['date_created']}")
    lines += [
        "",
        "Thank you.",
        "",
        "Best regards,",
        "TRANSPORT BOOKING SYSTEM",
    ]
    return "\n".join(lines)


def booking_summary_text(b):
    rt = "YES" if b["is_roundtrip"] else "NO"
    lines = [
        f"NEW TRIP ASSIGNMENT - {b['booking_code']}",
        f"Purpose: {b['purpose']}",
        f"Nature of trip: {(b['nature_of_trip'] or 'official').upper()}",
    ]
    if b["department"]:
        lines.append(f"Department/Account: {b['department']}")
    lines += [
        f"Passengers: {b['passenger_count']}",
        f"Vehicle needed: {b['vehicle_type_needed']}",
        f"Pickup: {b['pickup_location']}",
        f"Drop-off: {b['dropoff_location']}",
        f"Departure: {b['departure_date']} {b['departure_time']}",
        f"Round trip: {rt}",
    ]
    if b["is_roundtrip"]:
        lines.append(f"Return: {b['return_date']} {b['return_time']}")
    if b["vehicle_name"]:
        lines.append(f"Assigned vehicle: {b['vehicle_name']} "
                     f"({b['plate_number'] or 'N/A'})")
    lines.append(f"Requested by: {b['requester_name']}")
    if b["requester_phone"]:
        lines.append(f"Contact: {b['requester_phone']}")
    if b["driver_name"]:
        lines.append(f"Driver: {b['driver_name']}")
    if b["remarks"]:
        lines.append(f"Remarks: {b['remarks']}")
    return "\n".join(lines)


def _trip_window(b):
    """Return (start, end) datetimes for a booking's trip."""
    dep_date = (b["departure_date"] or "").strip()
    dep_time = (b["departure_time"] or "").strip() or "00:00"
    try:
        start = datetime.strptime(f"{dep_date} {dep_time}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    if b["is_roundtrip"] and (b["return_date"] or "").strip():
        ret_time = (b["return_time"] or "").strip() or "00:00"
        try:
            end = datetime.strptime(
                f"{b['return_date'].strip()} {ret_time}", "%Y-%m-%d %H:%M")
        except ValueError:
            end = start
    else:
        end = start
    if end < start:
        end = start
    return start, end


def driver_has_conflict(conn, driver_id, new_booking, exclude_booking_id=None):
    """True if assigning new_booking to driver_id conflicts with an
    existing non-completed booking (overlap or gap < 3 hours)."""
    if not driver_id:
        return False, None
    new_window = _trip_window(new_booking)
    if new_window is None:
        return False, None
    rows = conn.execute(
        "SELECT b.* FROM booking_assignments a"
        " JOIN bookings b ON b.id = a.booking_id"
        " WHERE a.driver_id=?"
        " AND b.status NOT IN ('completed','cancelled','declined')",
        (driver_id,)).fetchall()
    seen = set()
    for r in rows:
        if exclude_booking_id is not None and r["id"] == exclude_booking_id:
            continue
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        w = _trip_window(r)
        if w is None:
            continue
        s1, e1 = w
        s2, e2 = new_window
        if e1 < s2:
            gap = s2 - e1
        elif e2 < s1:
            gap = s1 - e2
        else:
            gap = timedelta(0)  # overlap
        if gap < timedelta(hours=3):
            return True, r["booking_code"]
    return False, None


def _driver_assignment(conn, booking_id, driver_id):
    """The driver's assignment row for a booking (or None)."""
    db.ensure_driver_assignments(conn, driver_id)
    return conn.execute(
        "SELECT * FROM booking_assignments WHERE booking_id=?"
        " AND driver_id=?", (booking_id, driver_id)).fetchone()


def _sync_booking_primary(conn, booking_id, assignment_id):
    """Mirror the primary driver's assignment fields onto the booking
    (keeps legacy single-driver views/tests working)."""
    a = conn.execute("SELECT * FROM booking_assignments WHERE id=?",
                     (assignment_id,)).fetchone()
    b = conn.execute("SELECT assigned_driver_id FROM bookings WHERE id=?",
                     (booking_id,)).fetchone()
    if not a or not b or b["assigned_driver_id"] != a["driver_id"]:
        return
    conn.execute(
        "UPDATE bookings SET driver_on_the_way_at=?, driver_arrived_at=?,"
        " picked_up_at=?, dropped_off_at=?,"
        " start_mileage=COALESCE(?, start_mileage),"
        " end_mileage=COALESCE(?, end_mileage),"
        " distance_km=COALESCE(?, distance_km),"
        " date_updated=? WHERE id=?",
        (a["driver_on_the_way_at"], a["driver_arrived_at"], a["picked_up_at"],
         a["dropped_off_at"], a["start_mileage"], a["end_mileage"],
         a["distance_km"], _now(), booking_id))


def apply_assignments(conn, booking, pairs, remarks, performed_by):
    """Validate and persist (vehicle_id, driver_id) assignments.
    Returns (ok, message) where message is the error or the summary."""
    booking_id = booking["id"]
    if not pairs:
        return False, "A driver is required to assign this booking."
    if len({d for _, d in pairs}) != len(pairs):
        return False, ("The same driver cannot be assigned twice to one "
                       "booking.")
    total_capacity = 0
    for vid, did in pairs:
        v = conn.execute("SELECT * FROM vehicles WHERE id=?",
                         (vid,)).fetchone()
        if not v:
            return False, "Please select a valid vehicle."
        total_capacity += v["capacity"] or 0
        driver = conn.execute(
            "SELECT id FROM users WHERE id=? AND role='driver' AND active=1",
            (did,)).fetchone()
        if not driver:
            return False, "Selected driver is not active."
        conflict, code = driver_has_conflict(
            conn, did, booking, exclude_booking_id=booking_id)
        if conflict:
            return False, (f"Driver already has a conflicting trip ({code}) "
                           f"within 3 hours.")
        vconflict = conn.execute(
            "SELECT b.booking_code FROM booking_assignments a"
            " JOIN bookings b ON b.id = a.booking_id"
            " WHERE a.vehicle_id=? AND b.id != ? AND b.status NOT IN"
            " ('completed','cancelled','declined')",
            (vid, booking_id)).fetchone()
        if vconflict:
            return False, (f"Vehicle is already assigned to "
                           f"{vconflict['booking_code']}.")
    if total_capacity < booking["passenger_count"]:
        return False, (f"Total vehicle capacity ({total_capacity}) is less "
                       f"than {booking['passenger_count']} passengers.")

    existing = db.fetch_assignments(conn, booking_id)
    existing_keys = {(a["vehicle_id"], a["driver_id"]): a for a in existing}
    keep_ids = set()
    for vid, did in pairs:
        a = existing_keys.get((vid, did))
        if a:
            keep_ids.add(a["id"])
        else:
            conn.execute(
                "INSERT INTO booking_assignments"
                " (booking_id, vehicle_id, driver_id, status)"
                " VALUES (?,?,?,'assigned')", (booking_id, vid, did))
    for a in existing:
        if a["id"] not in keep_ids:
            conn.execute("DELETE FROM booking_assignments WHERE id=?",
                         (a["id"],))
            conn.execute("UPDATE vehicles SET status='available' WHERE id=?",
                         (a["vehicle_id"],))
    for vid, _ in pairs:
        conn.execute("UPDATE vehicles SET status='in_use' WHERE id=?",
                     (vid,))
    first = db.fetch_assignments(conn, booking_id)
    if first:
        # The vehicle the coordinator picks defines the trip's vehicle type.
        vrow = conn.execute("SELECT vehicle_type FROM vehicles WHERE id=?",
                            (first[0]["vehicle_id"],)).fetchone()
        final_type = vrow["vehicle_type"] if vrow else ""
        conn.execute(
            "UPDATE bookings SET assigned_vehicle_id=?, assigned_driver_id=?,"
            " vehicle_type_needed=?, coordinator_remarks=?, date_updated=?"
            " WHERE id=?",
            (first[0]["vehicle_id"], first[0]["driver_id"], final_type,
             remarks, _now(), booking_id))
    db.recompute_booking_status(conn, booking_id)
    names = ", ".join(f"{a['plate_number']} ({a['driver_name']})"
                      for a in db.fetch_assignments(conn, booking_id))
    db.log_action(conn, booking_id, "ASSIGNED", performed_by,
                  f"{names}; Remarks: {remarks}")
    return True, names


def _mobile_trip_payload(booking):
    return {
        "id": booking["id"],
        "booking_code": booking["booking_code"],
        "status": booking["status"],
        "purpose": booking["purpose"],
        "nature_of_trip": booking["nature_of_trip"] or "official",
        "department": booking["department"] or "",
        "passenger_count": booking["passenger_count"],
        "pickup_location": booking["pickup_location"],
        "dropoff_location": booking["dropoff_location"],
        "departure_date": booking["departure_date"],
        "departure_time": booking["departure_time"],
        "flight_type": booking["flight_type"] or "none",
        "flight_number": booking["flight_number"] or "",
        "flight_date": booking["flight_date"] or "",
        "flight_time": booking["flight_time"] or "",
        "is_roundtrip": bool(booking["is_roundtrip"]),
        "return_date": booking["return_date"],
        "return_time": booking["return_time"],
        "remarks": booking["remarks"],
        "requester_name": booking["requester_name"],
        "requester_phone": booking["requester_phone"],
        "requested_by": booking["requested_by"],
        "vehicle_type_needed": booking["vehicle_type_needed"],
        "assigned_vehicle_id": booking["assigned_vehicle_id"],
        "assigned_driver_id": booking["assigned_driver_id"],
        "vehicle_name": booking["vehicle_name"],
        "plate_number": booking["plate_number"],
        "driver_name": booking["driver_name"],
        "coordinator_remarks": booking["coordinator_remarks"],
        "start_mileage": booking["start_mileage"],
        "end_mileage": booking["end_mileage"],
        "distance_km": booking["distance_km"],
        "driver_on_the_way_at": booking["driver_on_the_way_at"],
        "driver_arrived_at": booking["driver_arrived_at"],
        "picked_up_at": booking["picked_up_at"],
        "dropped_off_at": booking["dropped_off_at"],
        "time_out_at": booking["time_out_at"],
        "time_in_at": booking["time_in_at"],
        "approver_name": booking["approver_name"] if "approver_name" in booking.keys() else None,
    }


# Routes
# ---------------------------------------------------------------------- #
def register_routes(app):

    @app.route("/download/android")
    def download_android_app():
        apk_directory = os.path.join(
            app.root_path, "driver-app", "app", "build",
            "outputs", "apk", "debug")
        apk_path = os.path.join(apk_directory, "app-debug.apk")
        if not os.path.isfile(apk_path):
            return ("The Android APK is not built yet. Build it with "
                    "Android Studio (driver-app) and place app-debug.apk at "
                    "driver-app/app/build/outputs/apk/debug/.", 404)
        return send_from_directory(
            apk_directory, "app-debug.apk", as_attachment=True,
            download_name="Transport.apk",
            mimetype="application/vnd.android.package-archive",
            max_age=0)

    @app.route("/service-worker.js")
    def service_worker():
        response = send_from_directory(
            app.static_folder, "service-worker.js",
            mimetype="application/javascript")
        response.headers["Service-Worker-Allowed"] = "/"
        response.headers["Cache-Control"] = "no-cache"
        return response

    # ---------------- Auth ---------------- #
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            conn = get_db()
            row = conn.execute(
                "SELECT * FROM users WHERE username=? AND active=1",
                (username,)).fetchone()
            if row and db.verify_password(password, row["password_hash"]):
                session["user"] = {
                    "id": row["id"],
                    "username": row["username"],
                    "full_name": row["full_name"],
                    "role": row["role"],
                }
                flash(f"Welcome, {row['full_name']}!", "ok")
                return redirect(url_for("home"))
            flash("Invalid username or password.", "error")
        return render_template("login.html", current=session.get("user"))

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if session.get("user"):
            return redirect(url_for("home"))

        dev_code = None
        sent_to = None
        if request.method == "POST":
            action = request.form.get("action", "create")
            full_name = " ".join(request.form.get("full_name", "").split())
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            confirm = request.form.get("confirm_password", "")
            phone = request.form.get("phone_whatsapp", "").strip()
            email = request.form.get("email", "").strip().lower()
            code = request.form.get("code", "").strip()
            role = request.form.get("role", "requester")
            if role not in ("requester", "driver"):
                role = "requester"

            conn = get_db()

            if action == "send_code":
                if not email_is_valid(email):
                    flash("Enter a valid email address.", "error")
                else:
                    taken = conn.execute(
                        "SELECT 1 FROM users WHERE LOWER(email)=LOWER(?)",
                        (email,)).fetchone()
                    if taken:
                        flash("This email already has an account.",
                              "error")
                    else:
                        pending = create_registration_code(conn, email)
                        ok, smtp_error = send_email(
                            email, "Transport Booking - verification code",
                            f"Your verification code is {pending}.\n\n"
                            f"It is valid for 15 minutes. If you did not request this, "
                            f"please ignore this email.")
                        sent_to = email
                        if ok:
                            flash(f"The code was sent to {email}.", "ok")
                        elif not smtp_configured():
                            dev_code = pending
                            flash("Email sending (SMTP) is not configured yet. "
                                  "Use the code below for now.",
                                  "info")
                        else:
                            flash(f"Could not send the email: {smtp_error}",
                                  "error")
            else:
                error = None
                if not full_name:
                    error = "Enter your full name."
                elif not email_is_valid(email):
                    error = "Enter a valid email address."
                elif not re.fullmatch(r"[A-Za-z0-9._-]{3,30}", username):
                    error = ("Username: 3-30 characters lang, letters, "
                             "numbers, dot, underscore o dash.")
                elif len(password) < 6:
                    error = "Password must be at least 6 characters."
                elif password != confirm:
                    error = "Password and confirm password do not match."
                elif not code:
                    error = "Enter the code sent to your email."

                if not error:
                    taken = conn.execute(
                        "SELECT 1 FROM users WHERE LOWER(username)=LOWER(?)",
                        (username,)).fetchone()
                    if taken:
                        error = f"Username '{username}' is already taken."
                    else:
                        email_used = conn.execute(
                            "SELECT 1 FROM users WHERE LOWER(email)=LOWER(?)",
                            (email,)).fetchone()
                        if email_used:
                            error = "This email already has an account."
                if not error:
                    ok, message = check_registration_code(conn, email, code)
                    if not ok:
                        error = message

                if error:
                    flash(error, "error")
                else:
                    conn.execute(
                        "INSERT INTO users (username, password_hash,"
                        " full_name, role, phone_whatsapp, email)"
                        " VALUES (?,?,?,?,?,?)",
                        (username, db.hash_password(password), full_name,
                         role, phone, email))
                    conn.commit()
                    session["user"] = {
                        "id": conn.execute(
                            "SELECT id FROM users WHERE username=?",
                            (username,)).fetchone()["id"],
                        "username": username,
                        "full_name": full_name,
                        "role": role,
                    }
                    flash(f"Welcome, {full_name}! Your email is verified "
                          f"and your account is ready.", "ok")
                    return redirect(url_for(
                        "my_trips" if role == "driver" else "booking_form"))
        return render_template("register.html", dev_code=dev_code,
                               sent_to=sent_to)

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        flash("Logged out.", "ok")
        return redirect(url_for("login"))

    @app.route("/account/password", methods=["GET", "POST"])
    @login_required
    def change_password():
        user = current_user()
        if request.method == "POST":
            current = request.form.get("current_password", "")
            new = request.form.get("new_password", "")
            confirm = request.form.get("confirm_password", "")
            conn = get_db()
            row = conn.execute(
                "SELECT password_hash FROM users WHERE id=?",
                (user["id"],)).fetchone()

            error = None
            if not row or not db.verify_password(current,
                                                 row["password_hash"]):
                error = "Current password is incorrect."
            elif len(new) < 6:
                error = "New password must be at least 6 characters."
            elif new != confirm:
                error = "New password and confirmation do not match."
            elif new == current:
                error = "The new password must be different from the current one."

            if error:
                flash(error, "error")
            else:
                conn.execute(
                    "UPDATE users SET password_hash=? WHERE id=?",
                    (db.hash_password(new), user["id"]))
                conn.commit()
                flash("Your password has been updated.", "ok")
                return redirect(url_for("home"))
        return render_template("change_password.html")

    @app.route("/api/mobile/login", methods=["POST"])
    def mobile_login():
        if not request.is_json:
            return jsonify({"error": "Send a JSON request."}), 400
        payload = request.get_json()
        if not isinstance(payload, dict):
            return jsonify({"error": "Expected a JSON object."}), 400
        username = payload.get("username", "")
        password = payload.get("password", "")
        if not isinstance(username, str) or not isinstance(password, str):
            return jsonify({"error": "Username and password must be text."}), 400
        username = username.strip()
        if not username or not password:
            return jsonify({"error": "Username and password are required."}), 400
        row = get_db().execute(
            "SELECT * FROM users WHERE username=? AND active=1",
            (username,)).fetchone()
        if not row or not db.verify_password(password, row["password_hash"]):
            return jsonify({"error": "Invalid username or password."}), 401
        if row["role"] not in db.ROLES:
            return jsonify({"error": "This account role is not supported."}), 403
        token = secrets.token_urlsafe(48)
        expires_at = int(time.time()) + 30 * 24 * 60 * 60
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        conn = get_db()
        conn.execute(
            "INSERT INTO mobile_api_tokens (user_id, token_hash, expires_at)"
            " VALUES (?,?,?)", (row["id"], token_hash, expires_at))
        conn.commit()
        return jsonify({
            "access_token": token,
            "token_type": "Bearer",
            "expires_at": expires_at,
            "user": {
                "id": row["id"],
                "username": row["username"],
                "full_name": row["full_name"],
                "role": row["role"],
            },
        })

    @app.route("/api/mobile/register/code", methods=["POST"])
    def mobile_register_code():
        if not request.is_json:
            return jsonify({"error": "Send a JSON request."}), 400
        payload = request.get_json()
        if not isinstance(payload, dict):
            return jsonify({"error": "Expected a JSON object."}), 400
        email = payload.get("email", "")
        if not isinstance(email, str):
            return jsonify({"error": "Email must be text."}), 400
        email = email.strip().lower()
        if not email_is_valid(email):
            return jsonify({"error": "Enter a valid email address."}), 400

        conn = get_db()
        taken = conn.execute(
            "SELECT 1 FROM users WHERE LOWER(email)=LOWER(?)",
            (email,)).fetchone()
        if taken:
            return jsonify({"error": "Email already has an account."}), 409

        code = create_registration_code(conn, email)
        ok, smtp_error = send_email(
            email, "Transport Booking - verification code",
            f"Your verification code is {code}.\n\nIt is valid for 15 minutes.")
        response = {"sent": ok, "email": email}
        if not ok and not smtp_configured():
            response["dev_code"] = code
            response["note"] = "SMTP is not configured; use dev_code."
        elif not ok:
            response["error"] = smtp_error
            return jsonify(response), 502
        return jsonify(response)

    @app.route("/api/mobile/register", methods=["POST"])
    def mobile_register():
        if not request.is_json:
            return jsonify({"error": "Send a JSON request."}), 400
        payload = request.get_json()
        if not isinstance(payload, dict):
            return jsonify({"error": "Expected a JSON object."}), 400
        full_name = payload.get("full_name", "")
        username = payload.get("username", "")
        password = payload.get("password", "")
        phone = payload.get("phone_whatsapp", "")
        email = payload.get("email", "")
        code = payload.get("code", "")
        role = payload.get("role", "requester")
        for value in (full_name, username, password, phone, email, code, role):
            if not isinstance(value, str):
                return jsonify({"error": "Account fields must be text."}), 400
        full_name = " ".join(full_name.split())
        username = username.strip()
        phone = phone.strip()
        email = email.strip().lower()
        role = role.strip().lower()
        if role not in ("requester", "driver"):
            return jsonify({
                "error": "Role must be requester or driver."
            }), 400
        if not full_name:
            return jsonify({"error": "Full name is required."}), 400
        if not email_is_valid(email):
            return jsonify({"error": "Enter a valid email address."}), 400
        if not re.fullmatch(r"[A-Za-z0-9._-]{3,30}", username):
            return jsonify({
                "error": "Username must be 3-30 letters, numbers, dot, "
                         "underscore or dash."
            }), 400
        if len(password) < 6:
            return jsonify({
                "error": "Password must be at least 6 characters."
            }), 400
        if not code.strip():
            return jsonify({
                "error": "Email verification code is required."
            }), 400

        conn = get_db()
        taken = conn.execute(
            "SELECT 1 FROM users WHERE LOWER(username)=LOWER(?)",
            (username,)).fetchone()
        if taken:
            return jsonify({"error": "Username already exists."}), 409
        email_used = conn.execute(
            "SELECT 1 FROM users WHERE LOWER(email)=LOWER(?)",
            (email,)).fetchone()
        if email_used:
            return jsonify({"error": "Email already has an account."}), 409

        ok, message = check_registration_code(conn, email, code)
        if not ok:
            return jsonify({"error": message}), 400

        cur = conn.execute(
            "INSERT INTO users (username, password_hash, full_name, role,"
            " phone_whatsapp, email) VALUES (?,?,?,?,?,?)",
            (username, db.hash_password(password), full_name, role, phone,
             email))
        user_id = cur.lastrowid

        token = secrets.token_urlsafe(48)
        expires_at = int(time.time()) + 30 * 24 * 60 * 60
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        conn.execute(
            "INSERT INTO mobile_api_tokens (user_id, token_hash, expires_at)"
            " VALUES (?,?,?)", (user_id, token_hash, expires_at))
        conn.commit()
        return jsonify({
            "access_token": token,
            "token_type": "Bearer",
            "expires_at": expires_at,
            "user": {
                "id": user_id,
                "username": username,
                "full_name": full_name,
                "role": role,
                "email": email,
            },
        }), 201

    @app.route("/api/mobile/logout", methods=["POST"])
    @mobile_role_required(*db.ROLES)
    def mobile_logout():
        scheme, _, token = request.headers.get(
            "Authorization", "").partition(" ")
        token_hash = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
        conn = get_db()
        conn.execute(
            "UPDATE mobile_api_tokens SET revoked_at=?"
            " WHERE token_hash=? AND revoked_at IS NULL",
            (int(time.time()), token_hash))
        conn.commit()
        return jsonify({"logged_out": True})

    @app.route("/api/mobile/me")
    @mobile_role_required(*db.ROLES)
    def mobile_me():
        return jsonify({"user": g.mobile_user})

    @app.route("/api/mobile/password", methods=["POST"])
    @mobile_role_required(*db.ROLES)
    def mobile_change_password():
        if not request.is_json:
            return jsonify({"error": "Send a JSON request."}), 400
        payload = request.get_json()
        if not isinstance(payload, dict):
            return jsonify({"error": "Expected a JSON object."}), 400
        current = payload.get("current_password", "")
        new = payload.get("new_password", "")
        if not isinstance(current, str) or not isinstance(new, str):
            return jsonify({"error": "Passwords must be text."}), 400
        if len(new) < 6:
            return jsonify({
                "error": "New password must be at least 6 characters."
            }), 400

        conn = get_db()
        row = conn.execute(
            "SELECT password_hash FROM users WHERE id=?",
            (g.mobile_user["id"],)).fetchone()
        if not row or not db.verify_password(current, row["password_hash"]):
            return jsonify({"error": "Current password is incorrect."}), 401
        if current == new:
            return jsonify({
                "error": "New password must be different from the current one."
            }), 400

        conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                     (db.hash_password(new), g.mobile_user["id"]))
        conn.commit()
        return jsonify({"changed": True})

    @app.route("/api/mobile/my-trips")
    @mobile_driver_required
    def mobile_my_trips():
        rows = db.fetch_bookings(
            get_db(), driver_id=g.mobile_driver["id"])
        return jsonify({
            "driver": {
                "id": g.mobile_driver["id"],
                "full_name": g.mobile_driver["full_name"],
            },
            "trips": [_mobile_trip_payload(row) for row in rows],
        })

    @app.route("/api/mobile/requester/bookings", methods=["GET", "POST"])
    @mobile_role_required("requester")
    def mobile_requester_bookings():
        conn = get_db()
        user = g.mobile_user
        if request.method == "GET":
            rows = db.fetch_bookings(conn, requested_by=user["id"])
            return jsonify({
                "user": user,
                "bookings": [_mobile_trip_payload(row) for row in rows],
            })
        if not request.is_json:
            return jsonify({"error": "Send a JSON request."}), 400
        payload = request.get_json()
        if not isinstance(payload, dict):
            return jsonify({"error": "Expected a JSON object."}), 400
        purpose = payload.get("purpose", "")
        vehicle_type = payload.get("vehicle_type_needed", "")
        pickup = payload.get("pickup_location", "")
        dropoff = payload.get("dropoff_location", "")
        remarks = payload.get("remarks", "")
        nature = payload.get("nature_of_trip", "official")
        department = payload.get("department", "")
        is_roundtrip = payload.get("is_roundtrip", False)
        passengers = payload.get("passenger_count", 1)
        leg_fields = (
            "departure_date", "departure_time", "flight_type",
            "flight_number", "flight_date", "flight_time",
            "return_date", "return_time", "return_flight_type",
            "return_flight_number", "return_flight_date", "return_flight_time")
        text_fields = (purpose, vehicle_type, pickup, dropoff, remarks,
                       *(payload.get(name, "") for name in leg_fields))
        if any(not isinstance(value, str) for value in text_fields):
            return jsonify({"error": "Booking text fields must be strings."}), 400
        if not isinstance(nature, str):
            return jsonify({"error": "Nature of trip must be a string."}), 400
        nature = nature.strip().lower()
        if nature not in db.TRIP_NATURES:
            return jsonify({"error": "Nature of trip must be official or "
                                     "personal."}), 400
        if not isinstance(department, str):
            return jsonify({"error": "Department must be a string."}), 400
        department = department.strip()
        if nature == "personal":
            department = user["full_name"]
        elif not department:
            return jsonify({"error": "Department / Account Name is required "
                                     "for official trips."}), 400
        vehicle_type = vehicle_type.strip()
        if vehicle_type and vehicle_type not in db.VEHICLE_TYPES:
            return jsonify({"error": "Vehicle type is invalid."}), 400
        if isinstance(passengers, bool) or not isinstance(passengers, int) or passengers < 1:
            return jsonify({"error": "Passenger count must be a positive whole number."}), 400
        if not isinstance(is_roundtrip, bool):
            return jsonify({"error": "Round-trip value must be true or false."}), 400

        outbound, error = resolve_leg(lambda key: payload.get(key, ""),
                                      "departure_date", "departure_time")
        if error:
            return jsonify({"error": error}), 400
        legs = [("outbound", outbound, pickup.strip(), dropoff.strip())]
        if is_roundtrip:
            return_leg, error = resolve_leg(lambda key: payload.get(key, ""),
                                            "return_date", "return_time",
                                            "return_")
            if error:
                return jsonify({"error": error}), 400
            if not return_leg["dep_date"]:
                return jsonify({"error": "Return date is required for round trips."}), 400
            legs.append(("return", return_leg, dropoff.strip(), pickup.strip()))
        if not purpose.strip() or not outbound["dep_date"]:
            return jsonify({
                "error": "Purpose and departure date are required."
            }), 400
        for _label, leg, _p, _d in legs:
            try:
                time.strptime(leg["dep_date"], "%Y-%m-%d")
            except ValueError:
                return jsonify({"error": "Dates must use YYYY-MM-DD format."}), 400

        pickup_coords = parse_coordinates(pickup)
        if pickup_coords is None:
            raw_lat = payload.get("pickup_lat")
            raw_lng = payload.get("pickup_lng")
            if (isinstance(raw_lat, (int, float))
                    and not isinstance(raw_lat, bool)
                    and isinstance(raw_lng, (int, float))
                    and not isinstance(raw_lng, bool)
                    and -90 <= raw_lat <= 90 and -180 <= raw_lng <= 180):
                pickup_coords = (raw_lat, raw_lng)

        created = []
        for label, leg, leg_pickup, leg_dropoff in legs:
            coords = pickup_coords if label == "outbound" else None
            code = db.next_booking_code(conn)
            cur = conn.execute(
                "INSERT INTO bookings (booking_code, requested_by, purpose,"
                " nature_of_trip, department, passenger_count,"
                " vehicle_type_needed,"
                " pickup_location, pickup_lat, pickup_lng,"
                " dropoff_location, departure_date,"
                " departure_time, is_roundtrip, return_date, return_time,"
                " remarks, flight_type, flight_number, flight_date,"
                " flight_time, status)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending')",
                (code, user["id"], purpose.strip(), nature, department,
                 passengers, vehicle_type, leg_pickup,
                 coords[0] if coords else None,
                 coords[1] if coords else None,
                 leg_dropoff,
                 leg["dep_date"], leg["dep_time"], 0, "", "",
                 remarks.strip(), leg["flight_type"], leg["flight_number"],
                 leg["flight_date"], leg["flight_time"]))
            leg_note = f" [round trip {label} leg]" if is_roundtrip else ""
            db.log_action(conn, cur.lastrowid, "REQUESTED", user["id"],
                          f"{vehicle_type} for {passengers} pax"
                          f" ({nature.upper()}"
                          + (f" - {department}" if department else "")
                          + ")" + leg_note)
            created.append({"id": cur.lastrowid, "booking_code": code,
                            "status": "pending"})
        conn.commit()
        response = dict(created[0])
        if is_roundtrip:
            response["return_booking"] = created[1]
        return jsonify(response), 201

    @app.route("/api/mobile/requester/bookings/<int:booking_id>/cancel",
               methods=["POST"])
    @mobile_role_required("requester")
    def mobile_requester_cancel(booking_id):
        conn = get_db()
        booking = db.fetch_booking(conn, booking_id)
        if not booking or booking["requested_by"] != g.mobile_user["id"]:
            return jsonify({"error": "Booking not found."}), 404
        if booking["status"] in ("completed", "declined", "cancelled"):
            return jsonify({"error": "Booking is already closed."}), 409
        if booking["assigned_vehicle_id"]:
            conn.execute(
                "UPDATE vehicles SET status='available' WHERE id=?",
                (booking["assigned_vehicle_id"],))
        conn.execute(
            "UPDATE bookings SET status='cancelled', date_updated=?,"
            " location_sharing=0, driver_latitude=NULL, driver_longitude=NULL,"
            " driver_location_updated_at=NULL WHERE id=?",
            (_now(), booking_id))
        db.log_action(conn, booking_id, "CANCELLED", g.mobile_user["id"], "")
        conn.commit()
        return jsonify({"status": "cancelled"})

    @app.route("/api/mobile/coordinator/bookings")
    @mobile_role_required("admin", "coordinator")
    def mobile_coordinator_bookings():
        status = request.args.get("status", "").strip() or None
        rows = db.fetch_bookings(get_db(), status=status)
        return jsonify({
            "bookings": [_mobile_trip_payload(row) for row in rows],
        })

    @app.route("/api/mobile/coordinator/resources")
    @mobile_role_required("admin", "coordinator")
    def mobile_coordinator_resources():
        conn = get_db()
        return jsonify({
            "vehicles": [{
                "id": vehicle["id"],
                "plate_number": vehicle["plate_number"],
                "vehicle_name": vehicle["vehicle_name"],
                "vehicle_type": vehicle["vehicle_type"],
                "capacity": vehicle["capacity"],
                "status": vehicle["status"],
                "driver_id": vehicle["driver_id"],
                "driver_name": vehicle["driver_name"],
            } for vehicle in db.fetch_vehicles(conn)],
            "drivers": [{
                "id": driver["id"],
                "full_name": driver["full_name"],
            } for driver in db.fetch_users(conn, role="driver")],
            "vehicle_types": list(db.VEHICLE_TYPES),
        })

    @app.route("/api/mobile/coordinator/bookings/<int:booking_id>/review",
               methods=["POST"])
    @mobile_role_required("admin", "coordinator")
    def mobile_coordinator_review(booking_id):
        if not request.is_json:
            return jsonify({"error": "Send a JSON request."}), 400
        payload = request.get_json()
        if not isinstance(payload, dict):
            return jsonify({"error": "Expected a JSON object."}), 400
        action = payload.get("action")
        remarks = payload.get("coordinator_remarks", "")
        if not isinstance(remarks, str):
            return jsonify({"error": "Coordinator remarks must be text."}), 400
        remarks = remarks.strip()
        conn = get_db()
        booking = db.fetch_booking(conn, booking_id)
        if not booking:
            return jsonify({"error": "Booking not found."}), 404
        actor_id = g.mobile_user["id"]

        if action == "approve":
            if booking["status"] != "pending":
                return jsonify({"error": "Only pending bookings can be approved."}), 409
            conn.execute(
                "UPDATE bookings SET status='approved', coordinator_remarks=?,"
                " date_updated=? WHERE id=?",
                (remarks, _now(), booking_id))
            db.log_action(conn, booking_id, "APPROVED", actor_id, remarks)
            conn.commit()
            return jsonify({"status": "approved"})

        if action == "decline":
            if booking["status"] not in ("pending", "approved"):
                return jsonify({"error": "Only pending or approved bookings can be declined."}), 409
            if not remarks:
                return jsonify({"error": "A reason is required to reject."}), 400
            conn.execute(
                "UPDATE bookings SET status='declined', coordinator_remarks=?,"
                " date_updated=?, location_sharing=0, driver_latitude=NULL,"
                " driver_longitude=NULL, driver_location_updated_at=NULL"
                " WHERE id=?", (remarks, _now(), booking_id))
            db.log_action(conn, booking_id, "DECLINED", actor_id, remarks)
            conn.commit()
            return jsonify({"status": "declined"})

        if action == "assign":
            if booking["status"] not in ("approved", "assigned", "accepted"):
                return jsonify({"error": "Approve the booking before assigning."}), 409
            vehicle_id = payload.get("vehicle_id")
            driver_id = payload.get("driver_id")
            if isinstance(vehicle_id, bool) or not isinstance(vehicle_id, int):
                return jsonify({"error": "Select a valid vehicle."}), 400
            if (isinstance(driver_id, bool) or not isinstance(driver_id, int)):
                return jsonify({"error": "A driver is required to assign this booking."}), 400
            ok, message = apply_assignments(
                conn, booking, [(vehicle_id, driver_id)], remarks, actor_id)
            if not ok:
                return jsonify({"error": message}), 409
            conn.commit()
            return jsonify({"status": "assigned"})

        return jsonify({"error": "Unknown coordinator action."}), 400

    @app.route("/api/mobile/my-trips/<int:booking_id>/action",
               methods=["POST"])
    @mobile_driver_required
    def mobile_trip_action(booking_id):
        if not request.is_json:
            return jsonify({"error": "Send a JSON request."}), 400
        payload = request.get_json()
        if not isinstance(payload, dict):
            return jsonify({"error": "Expected a JSON object."}), 400
        action = payload.get("action")
        conn = get_db()
        user = g.mobile_driver
        booking = db.fetch_booking(conn, booking_id)
        a = (_driver_assignment(conn, booking_id, user["id"])
             if booking else None)
        if not booking or not a:
            return jsonify({"error": "Trip not found."}), 404

        if action in ("accept", "on_the_way", "start", "complete", "decline"):
            new_status = {
                "accept": "accepted",
                "on_the_way": "on_the_way",
                "start": "in_progress",
                "complete": "completed",
                "decline": "declined",
            }[action]
            allowed = {
                "assigned": ("accepted", "declined"),
                "accepted": ("on_the_way", "in_progress", "declined"),
                "on_the_way": ("in_progress", "declined"),
                "in_progress": ("completed",),
            }
            if new_status not in allowed.get(a["status"], []):
                return jsonify({"error": "Invalid status transition."}), 409
            reason = payload.get("reason", "")
            if not isinstance(reason, str):
                return jsonify({"error": "Reason must be text."}), 400
            reason = reason.strip()
            if new_status == "declined" and not reason:
                return jsonify({"error": "A reason is required to decline."}), 400
            if new_status == "completed" and booking["end_mileage"] is None:
                return jsonify({
                    "error": "Wait for the guard to record the end odometer "
                             "before completing this trip."
                }), 409
            if (new_status == "in_progress"
                    and not pickup_is_at_gate(booking["pickup_location"])
                    and not booking["guard_approved_at"]):
                return jsonify({
                    "error": "Wait for the guard to approve this trip before "
                             "starting it (pickup is outside SSD/LHC)."
                }), 409

            # Odometer readings are recorded by the guard.
            start_mileage = a["start_mileage"]
            end_mileage = a["end_mileage"]
            distance = a["distance_km"]

            now = _now()
            conn.execute(
                "UPDATE booking_assignments SET status=?, start_mileage=?,"
                " end_mileage=?, distance_km=?, date_updated=?,"
                " driver_on_the_way_at=CASE WHEN ?='on_the_way'"
                " THEN ? ELSE driver_on_the_way_at END,"
                " accepted_at=CASE WHEN ?='accepted' THEN ? ELSE accepted_at END,"
                " started_at=CASE WHEN ?='in_progress' THEN ? ELSE started_at END,"
                " declined_reason=CASE WHEN ?='declined' THEN ?"
                " ELSE declined_reason END,"
                " completed_at=CASE WHEN ?='completed' THEN ?"
                " ELSE completed_at END"
                " WHERE id=?",
                (new_status, start_mileage, end_mileage, distance, now,
                 new_status, now, new_status, now,
                 new_status, now, new_status, reason,
                 new_status, now, a["id"]))
            if new_status == "completed":
                conn.execute(
                    "UPDATE vehicles SET status='available' WHERE id=?",
                    (a["vehicle_id"],))
            elif new_status == "declined":
                conn.execute(
                    "UPDATE vehicles SET status='available' WHERE id=?",
                    (a["vehicle_id"],))
                # Return the trip to the coordinator for a new driver.
                conn.execute("DELETE FROM booking_assignments WHERE id=?",
                             (a["id"],))
                remaining = conn.execute(
                    "SELECT vehicle_id, driver_id FROM booking_assignments"
                    " WHERE booking_id=? LIMIT 1", (booking_id,)).fetchone()
                if remaining:
                    conn.execute(
                        "UPDATE bookings SET assigned_vehicle_id=?,"
                        " assigned_driver_id=? WHERE id=?",
                        (remaining["vehicle_id"], remaining["driver_id"],
                         booking_id))
                else:
                    conn.execute(
                        "UPDATE bookings SET status='approved',"
                        " assigned_vehicle_id=NULL, assigned_driver_id=NULL,"
                        " driver_on_the_way_at=NULL, driver_arrived_at=NULL,"
                        " picked_up_at=NULL, dropped_off_at=NULL,"
                        " location_sharing=0, driver_latitude=NULL,"
                        " driver_longitude=NULL,"
                        " driver_location_updated_at=NULL WHERE id=?",
                        (booking_id,))
            if new_status in ("declined", "completed"):
                conn.execute(
                    "UPDATE bookings SET location_sharing=0, driver_latitude=NULL,"
                    " driver_longitude=NULL, driver_location_updated_at=NULL"
                    " WHERE id=?", (booking_id,))
            _sync_booking_primary(conn, booking_id, a["id"])
            db.recompute_booking_status(conn, booking_id)

            detail = reason
            if new_status == "accepted":
                detail = "Driver accepted the trip."
            elif new_status == "on_the_way":
                detail = "Driver marked as on the way."
            elif new_status == "in_progress":
                detail = "Trip started."
                if reason:
                    detail += f" | {reason}"
            elif new_status == "completed":
                detail = "Trip completed."
                if reason:
                    detail += f" | {reason}"
            db.log_action(conn, booking_id, new_status.upper(), user["id"], detail)
            conn.commit()
            return jsonify({"status": new_status})

        if action == "arrival":
            if a["status"] not in ("on_the_way", "in_progress"):
                return jsonify({"error": "Mark yourself on the way first."}), 409
            if a["driver_arrived_at"]:
                return jsonify({"error": "Arrival is already confirmed."}), 409
            arrived_at = _now()
            conn.execute(
                "UPDATE booking_assignments SET driver_arrived_at=?,"
                " date_updated=? WHERE id=?",
                (arrived_at, arrived_at, a["id"]))
            conn.execute(
                "UPDATE bookings SET location_sharing=0, driver_latitude=NULL,"
                " driver_longitude=NULL, driver_location_updated_at=NULL"
                " WHERE id=?", (booking_id,))
            _sync_booking_primary(conn, booking_id, a["id"])
            db.log_action(conn, booking_id, "ARRIVED_AT_PICKUP", user["id"],
                          f"Driver arrived at pickup at {arrived_at}")
            conn.commit()
            return jsonify({"driver_arrived_at": arrived_at})

        if action == "pickup":
            if a["status"] not in ("accepted", "on_the_way", "in_progress"):
                return jsonify({"error": "Accept the trip before confirming pickup."}), 409
            if not a["driver_arrived_at"]:
                return jsonify({"error": "Confirm arrival before client pickup."}), 409
            if a["picked_up_at"]:
                return jsonify({"error": "Client pickup is already confirmed."}), 409
            picked_up_at = _now()
            conn.execute(
                "UPDATE booking_assignments SET picked_up_at=?, date_updated=?"
                " WHERE id=?", (picked_up_at, picked_up_at, a["id"]))
            conn.execute(
                "UPDATE bookings SET location_sharing=0, driver_latitude=NULL,"
                " driver_longitude=NULL, driver_location_updated_at=NULL"
                " WHERE id=?", (booking_id,))
            _sync_booking_primary(conn, booking_id, a["id"])
            db.log_action(conn, booking_id, "PICKED_UP", user["id"],
                          f"Client picked up at {picked_up_at}")
            conn.commit()
            return jsonify({"picked_up_at": picked_up_at})

        if action == "dropoff":
            if not a["picked_up_at"]:
                return jsonify({"error": "Confirm pickup before drop-off."}), 409
            if a["dropped_off_at"]:
                return jsonify({"error": "Drop-off is already confirmed."}), 409
            dropped_off_at = _now()
            conn.execute(
                "UPDATE booking_assignments SET dropped_off_at=?,"
                " date_updated=? WHERE id=?",
                (dropped_off_at, dropped_off_at, a["id"]))
            _sync_booking_primary(conn, booking_id, a["id"])
            db.log_action(conn, booking_id, "DROPPED_OFF", user["id"],
                          f"Client dropped off at {dropped_off_at}")
            conn.commit()
            return jsonify({"dropped_off_at": dropped_off_at})

        return jsonify({"error": "Unknown trip action."}), 400

    @app.route("/")
    def home():
        user = current_user()
        if not user:
            return redirect(url_for("login"))
        return redirect(url_for(home_for_role(user["role"])))

    # ---------------- Admin: Dashboard ---------------- #
    @app.route("/dashboard")
    @role_required("admin")
    def dashboard():
        conn = get_db()
        stats = db.dashboard_stats(conn)
        recent = db.fetch_bookings(conn)[:10]
        return render_template("dashboard.html", stats=stats, recent=recent)

    # ---------------- Admin: email settings ---------------- #
    @app.route("/settings", methods=["GET", "POST"])
    @role_required("admin")
    def settings():
        conn = get_db()
        if request.method == "POST":
            action = request.form.get("action", "save")
            if action == "test":
                to_email = request.form.get("test_email", "").strip()
                if not email_is_valid(to_email):
                    flash("Enter a valid test email address.", "error")
                else:
                    ok, error = send_email(
                        to_email, "Transport Booking - test email",
                        "Test email mula sa Transport Booking System.\n\n"
                        "If you can read this, the email settings work.")
                    if ok:
                        flash(f"Test email sent to {to_email}.", "ok")
                    else:
                        flash(f"Could not send: {error}", "error")
            else:
                host = request.form.get("smtp_host", "").strip()
                domain = request.form.get("smtp_domain", "").strip()
                sender = request.form.get("smtp_from", "").strip()
                fields = ("smtp_provider", "smtp_domain", "smtp_host",
                          "smtp_port", "smtp_user", "smtp_password",
                          "smtp_from")
                for key in fields:
                    value = request.form.get(key, "").strip()
                    if key == "smtp_password" and not value:
                        continue  # keep the saved password
                    conn.execute(
                        "INSERT INTO app_settings (key, value) VALUES (?,?)"
                        " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, value))
                conn.execute(
                    "INSERT INTO app_settings (key, value) VALUES (?,?)"
                    " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    ("smtp_tls", "1" if request.form.get("smtp_tls") else "0"))
                conn.commit()
                flash("Email settings saved.", "ok")
                if host and ("@" in host or " " in host):
                    flash("Note: SMTP Host must be a server name "
                          "(e.g. mail.example.com), not an email or domain.",
                          "error")
                if (domain and sender
                        and not sender.lower().endswith(domain.lower())):
                    flash(f"Note: From ({sender}) does not match the domain "
                          f"({domain}).", "info")
            return redirect(url_for("settings"))

        current = smtp_settings()
        saved_password = bool(current["password"])
        return render_template("settings.html", smtp=current,
                               saved_password=saved_password)

    # ---------------- API: live updates (polling) ---------------- #
    @app.route("/api/updates")
    def api_updates():
        user = session.get("user")
        if not user:
            return jsonify({"bookings": []}), 401
        conn = get_db()
        if user["role"] == "requester":
            rows = db.fetch_bookings(conn, requested_by=user["id"])
        elif user["role"] == "driver":
            rows = db.fetch_bookings(conn, driver_id=user["id"])
        else:
            rows = db.fetch_bookings(conn)
        payload = {"bookings": [
            {"id": r["id"], "booking_code": r["booking_code"],
             "status": r["status"],
             "status_label": {
                 "on_the_way": "DRIVER ON THE WAY",
             }.get(r["status"], r["status"].replace("_", " ").upper()),
             "picked_up_at": r["picked_up_at"],
             "dropped_off_at": r["dropped_off_at"],
             "driver_on_the_way_at": r["driver_on_the_way_at"],
             "driver_arrived_at": r["driver_arrived_at"],
             "location_sharing": bool(r["location_sharing"]),
             "driver_location_updated_at": r["driver_location_updated_at"],
             "vehicle_name": r["vehicle_name"] or "",
             "plate_number": r["plate_number"] or "",
             "driver_name": r["driver_name"] or "",
             "date_updated": r["date_updated"]}
            for r in rows]}
        response = jsonify(payload)
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response

    # ---------------- Admin: Users ---------------- #
    @app.route("/users", methods=["GET", "POST"])
    @role_required("admin")
    def users():
        conn = get_db()
        welcome = None
        if request.method == "POST":
            action = request.form.get("action")
            if action == "add":
                username = request.form.get("username", "").strip()
                password = request.form.get("password", "").strip()
                full_name = request.form.get("full_name", "").strip()
                role = request.form.get("role", "requester")
                phone = request.form.get("phone_whatsapp", "").strip()

                v_plate = request.form.get("v_plate_number", "").strip()
                v_name = request.form.get("v_vehicle_name", "").strip()
                v_type = request.form.get("v_vehicle_type", "")
                v_cap = max(1, _int_field(request.form, "v_capacity", 1))
                v_remarks = request.form.get("v_remarks", "").strip()
                v_mileage = max(0, _int_field(request.form, "v_mileage", 0))
                has_vehicle = bool(v_plate or v_name)

                if not username or not password or not full_name:
                    flash("Username, password, and full name are required.", "error")
                elif role not in db.ROLES:
                    flash("Invalid role.", "error")
                elif has_vehicle and role != "driver":
                    flash("Vehicle details are only for the Driver role.", "error")
                elif has_vehicle and (not v_plate or not v_name
                                      or v_type not in db.VEHICLE_TYPES):
                    flash("Complete the Plate, Vehicle Name, and Type - or leave all blank.", "error")
                else:
                    dup = conn.execute(
                        "SELECT username FROM users WHERE LOWER(username)=LOWER(?)",
                        (username,)).fetchone()
                    dup_plate = conn.execute(
                        "SELECT plate_number FROM vehicles"
                        " WHERE LOWER(plate_number)=LOWER(?)",
                        (v_plate,)).fetchone() if has_vehicle else None
                    if dup:
                        flash(f"Username '{dup['username']}' already exists. "
                              "Choose a different username.", "error")
                    elif dup_plate:
                        flash(f"Plate '{dup_plate['plate_number']}' already encoded. "
                              "Choose a different plate number.", "error")
                    else:
                        cur = conn.execute(
                            "INSERT INTO users (username, password_hash,"
                            " full_name, role, phone_whatsapp) VALUES (?,?,?,?,?)",
                            (username, db.hash_password(password), full_name,
                             role, phone))
                        new_uid = cur.lastrowid
                        vehicle = None
                        if has_vehicle:
                            conn.execute(
                                "INSERT INTO vehicles (plate_number, vehicle_name,"
                                " vehicle_type, capacity, driver_id, status,"
                                " mileage, remarks)"
                                " VALUES (?,?,?,?,?,'available',?,?)",
                                (v_plate, v_name, v_type, v_cap, new_uid,
                                 v_mileage, v_remarks))
                            vehicle = {"plate_number": v_plate,
                                       "vehicle_name": v_name,
                                       "vehicle_type": v_type,
                                       "capacity": v_cap}
                        conn.commit()

                        if role == "driver":
                            wa_url = ""
                            if phone:
                                wa_url = whatsapp_link(
                                    phone, driver_welcome_text(
                                        full_name, username, password, vehicle))
                            welcome = {"full_name": full_name,
                                       "username": username, "phone": phone,
                                       "vehicle": vehicle, "wa_url": wa_url}
                        else:
                            flash(f"User '{username}' ({role.upper()}) added.", "ok")
            elif action == "toggle":
                uid = _int_field(request.form, "id")
                conn.execute("UPDATE users SET active = 1 - active WHERE id=?", (uid,))
                conn.commit()
                flash("User status updated.", "ok")
                return redirect(url_for("users"))
            elif action == "update":
                uid = _int_field(request.form, "id")
                full_name = request.form.get("full_name", "").strip()
                phone = request.form.get("phone_whatsapp", "").strip()
                role = request.form.get("role", "")
                password = request.form.get("new_password", "").strip()
                if role not in db.ROLES:
                    flash("Invalid role.", "error")
                else:
                    conn.execute(
                        "UPDATE users SET full_name=?, phone_whatsapp=?, role=?"
                        " WHERE id=?", (full_name, phone, role, uid))
                    if password:
                        conn.execute(
                            "UPDATE users SET password_hash=? WHERE id=?",
                            (db.hash_password(password), uid))
                    conn.commit()
                    flash("User updated.", "ok")
                return redirect(url_for("users"))

            if welcome is None:
                return redirect(url_for("users"))

        show_inactive = request.args.get("show_inactive") == "1"
        user_query = (
            "SELECT u.*, v.plate_number AS v_plate, v.vehicle_name AS v_name,"
            " v.vehicle_type AS v_type, v.capacity AS v_capacity,"
            " v.mileage AS v_mileage"
            " FROM users u LEFT JOIN vehicles v ON v.driver_id = u.id")
        if not show_inactive:
            user_query += " WHERE u.active=1"
        user_query += " ORDER BY u.active DESC, u.full_name"
        all_users = conn.execute(user_query).fetchall()

        def user_wa_url(u):
            if not u["phone_whatsapp"]:
                return ""
            if u["role"] == "driver":
                vehicle = None
                if u["v_plate"]:
                    vehicle = {"plate_number": u["v_plate"],
                               "vehicle_name": u["v_name"],
                               "vehicle_type": u["v_type"],
                               "capacity": u["v_capacity"]}
                msg = driver_welcome_text(u["full_name"], u["username"], "",
                                          vehicle)
            else:
                msg = account_text(u["full_name"], u["username"], u["role"])
            return whatsapp_link(u["phone_whatsapp"], msg)

        return render_template("users.html", users=all_users,
                               vtypes=db.VEHICLE_TYPES, welcome=welcome,
                               user_wa_url=user_wa_url,
                               show_inactive=show_inactive)

    # ---------------- Admin: Vehicles ---------------- #
    @app.route("/vehicles", methods=["GET", "POST"])
    @role_required("admin", "coordinator")
    def vehicles():
        conn = get_db()
        if request.method == "POST":
            action = request.form.get("action")
            if action == "add":
                plate = request.form.get("plate_number", "").strip()
                name = request.form.get("vehicle_name", "").strip()
                vtype = request.form.get("vehicle_type", "")
                capacity = _int_field(request.form, "capacity", 1)
                driver_id = _int_field(request.form, "driver_id") or None
                status = request.form.get("status", "available")
                mileage = max(0, _int_field(request.form, "mileage", 0))
                remarks = request.form.get("remarks", "").strip()
                if not plate or not name or vtype not in db.VEHICLE_TYPES:
                    flash("Plate number, vehicle name, and valid type are required.",
                          "error")
                elif status not in db.VEHICLE_STATUSES:
                    flash("Invalid status.", "error")
                else:
                    conn.execute(
                        "INSERT INTO vehicles (plate_number, vehicle_name,"
                        " vehicle_type, capacity, driver_id, status, mileage,"
                        " remarks) VALUES (?,?,?,?,?,?,?,?)",
                        (plate, name, vtype, max(1, capacity), driver_id,
                         status, mileage, remarks))
                    conn.commit()
                    flash(f"Vehicle '{plate}' encoded.", "ok")
            elif action == "update":
                vid = _int_field(request.form, "id")
                plate = request.form.get("plate_number", "").strip()
                name = request.form.get("vehicle_name", "").strip()
                vtype = request.form.get("vehicle_type", "")
                capacity = _int_field(request.form, "capacity", 1)
                driver_id = _int_field(request.form, "driver_id") or None
                status = request.form.get("status", "available")
                mileage = max(0, _int_field(request.form, "mileage", 0))
                remarks = request.form.get("remarks", "").strip()
                if not plate or not name or vtype not in db.VEHICLE_TYPES:
                    flash("Plate number, vehicle name, and valid type are required.",
                          "error")
                else:
                    conn.execute(
                        "UPDATE vehicles SET plate_number=?, vehicle_name=?,"
                        " vehicle_type=?, capacity=?, driver_id=?, status=?,"
                        " mileage=?, remarks=? WHERE id=?",
                        (plate, name, vtype, max(1, capacity), driver_id,
                         status, mileage, remarks, vid))
                    conn.commit()
                    flash("Vehicle updated.", "ok")
            elif action == "delete":
                vid = _int_field(request.form, "id")
                used = conn.execute(
                    "SELECT COUNT(*) AS n FROM bookings WHERE assigned_vehicle_id=?",
                    (vid,)).fetchone()["n"]
                if used:
                    flash("Cannot delete: vehicle is used in past bookings. "
                          "Set status to INACTIVE instead.", "error")
                else:
                    conn.execute("DELETE FROM vehicles WHERE id=?", (vid,))
                    conn.commit()
                    flash("Vehicle deleted.", "ok")
            return redirect(url_for("vehicles"))

        all_vehicles = db.fetch_vehicles(conn)
        drivers = db.fetch_users(conn, role="driver")
        return render_template("vehicles.html", vehicles=all_vehicles,
                               drivers=drivers, vtypes=db.VEHICLE_TYPES,
                               vstatuses=db.VEHICLE_STATUSES)

    # ---------------- Coordinator: Booking queue ---------------- #
    @app.route("/bookings")
    @role_required("admin", "coordinator", "guard")
    def bookings():
        conn = get_db()
        if current_user()["role"] == "guard":
            user_id = current_user()["id"]
            rows = conn.execute(
                "SELECT b.*, v.plate_number, v.vehicle_name,"
                " v.vehicle_type, du.full_name AS driver_name"
                " FROM bookings b"
                " JOIN vehicles v ON v.id = b.assigned_vehicle_id"
                " LEFT JOIN users du ON du.id = b.assigned_driver_id"
                " WHERE b.status IN ('assigned', 'accepted', 'on_the_way',"
                " 'in_progress', 'completed')"
                " AND b.status <> 'completed'"
                " AND b.end_mileage IS NULL"
                " ORDER BY b.departure_date, b.departure_time, b.id").fetchall()
            history = conn.execute(
                "SELECT b.*, v.plate_number, v.vehicle_name"
                " FROM bookings b"
                " LEFT JOIN vehicles v ON v.id = b.assigned_vehicle_id"
                " WHERE b.approved_by = ? AND b.guard_approved_at IS NOT NULL"
                " ORDER BY b.guard_approved_at DESC, b.id DESC LIMIT 50",
                (user_id,)).fetchall()
            return render_template("guard_queue.html", bookings=rows,
                                   history=history)
        status = request.args.get("status", "").strip() or None
        date_from = request.args.get("date_from", "").strip() or None
        date_to = request.args.get("date_to", "").strip() or None
        rows = db.fetch_bookings(conn, status=status,
                                 date_from=date_from, date_to=date_to)
        drivers = db.fetch_users(conn, role="driver")
        all_vehicles = db.fetch_vehicles(conn)
        stats = db.dashboard_stats(conn)
        assignments_by_booking = {
            b["id"]: [{"vehicle_id": a["vehicle_id"],
                       "driver_id": a["driver_id"],
                       "plate_number": a["plate_number"],
                       "vehicle_name": a["vehicle_name"],
                       "driver_name": a["driver_name"],
                       "status": a["status"]}
                      for a in db.fetch_assignments(conn, b["id"])]
            for b in rows}
        return render_template("bookings.html", bookings=rows, drivers=drivers,
                               vehicles=all_vehicles, vtypes=db.VEHICLE_TYPES,
                               stats=stats, status=status,
                               assignments_by_booking=assignments_by_booking,
                               date_from=date_from or "", date_to=date_to or "",
                               whatsapp_link=whatsapp_link,
                               summary_text=booking_summary_text)

    @app.route("/bookings/<int:booking_id>")
    @role_required("admin", "coordinator", "guard", "requester", "driver")
    def booking_detail(booking_id):
        conn = get_db()
        user = current_user()
        b = db.fetch_booking(conn, booking_id)
        if not b:
            flash("Booking not found.", "error")
            return redirect(url_for("home"))
        if user["role"] == "requester" and b["requested_by"] != user["id"]:
            flash("You can only view your own bookings.", "error")
            return redirect(url_for("my_bookings"))
        if (user["role"] == "driver"
                and not _driver_assignment(conn, booking_id, user["id"])):
            flash("You can only view your assigned trips.", "error")
            return redirect(url_for("my_trips"))
        assignments = db.fetch_assignments(conn, booking_id)
        logs = db.fetch_logs(conn, booking_id) if user["role"] == "admin" \
            else None
        return render_template("booking_detail.html", b=b, logs=logs,
                               assignments=assignments,
                               user=user, whatsapp_link=whatsapp_link,
                               summary_text=booking_summary_text,
                               email_text=booking_email_text)

    @app.route("/bookings/<int:booking_id>/print")
    @role_required("admin", "coordinator")
    def print_trip_ticket(booking_id):
        conn = get_db()
        b = db.fetch_booking(conn, booking_id)
        if not b:
            flash("Booking not found.", "error")
            return redirect(url_for("bookings"))
        return render_template("trip_ticket.html", b=b)

    @app.route("/bookings/<int:booking_id>/assign", methods=["POST"])
    @role_required("admin", "coordinator")
    def assign_booking(booking_id):
        conn = get_db()
        b = db.fetch_booking(conn, booking_id)
        if not b:
            flash("Booking not found.", "error")
            return redirect(url_for("bookings"))
        if b["status"] not in ("approved", "assigned", "accepted"):
            flash(f"Cannot assign a booking with status '{b['status']}'.",
                  "error")
            return redirect(url_for("bookings"))

        vehicle_ids = request.form.getlist("vehicle_id")
        driver_ids = request.form.getlist("driver_id")
        remarks = request.form.get("coordinator_remarks", "").strip()
        if len(vehicle_ids) != len(driver_ids):
            flash("Every vehicle row needs a driver.", "error")
            return redirect(url_for("bookings"))
        pairs = []
        for raw_v, raw_d in zip(vehicle_ids, driver_ids):
            try:
                vid = int(raw_v)
            except (TypeError, ValueError):
                vid = 0
            try:
                did = int(raw_d)
            except (TypeError, ValueError):
                did = 0
            if not vid or not did:
                flash("Select a vehicle and a driver in every row.", "error")
                return redirect(url_for("bookings"))
            pairs.append((vid, did))

        ok, message = apply_assignments(
            conn, b, pairs, remarks, session["user"]["id"])
        if not ok:
            flash(message, "error")
            return redirect(url_for("bookings"))
        conn.commit()
        flash(f"Booking {b['booking_code']} assigned: {message}.", "ok")
        return redirect(url_for("bookings"))

    @app.route("/bookings/<int:booking_id>/approve", methods=["POST"])
    @role_required("admin", "coordinator")
    def approve_booking(booking_id):
        conn = get_db()
        b = db.fetch_booking(conn, booking_id)
        if not b:
            flash("Booking not found.", "error")
            return redirect(url_for("bookings"))
        if b["status"] != "pending":
            flash("Only pending bookings can be approved.", "error")
            return redirect(url_for("bookings"))

        remarks = request.form.get("coordinator_remarks", "").strip()
        raw_start = request.form.get("start_mileage", "").strip()
        start_mileage = None
        if raw_start:
            if not raw_start.isdigit() or int(raw_start) <= 0:
                flash("Start odometer must be a positive whole number (km).",
                      "error")
                return redirect(url_for("bookings"))
            start_mileage = int(raw_start)

        now = _now()
        conn.execute(
            "UPDATE bookings SET status='approved', coordinator_remarks=?,"
            " approved_by=?, start_mileage=COALESCE(?, start_mileage),"
            " time_out_at=COALESCE(?, time_out_at), date_updated=?"
            " WHERE id=?",
            (remarks, session["user"]["id"], start_mileage,
             now if start_mileage is not None else None, now, booking_id))
        detail = remarks
        if start_mileage is not None:
            detail = (f"Start odometer: {start_mileage:,} km"
                      + (f" | {remarks}" if remarks else ""))
        db.log_action(conn, booking_id, "APPROVED", session["user"]["id"],
                      detail)
        conn.commit()
        flash(f"Booking {b['booking_code']} approved.", "ok")
        return redirect(url_for("bookings"))

    @app.route("/bookings/<int:booking_id>/guard-approve", methods=["POST"])
    @role_required("guard")
    def guard_approve(booking_id):
        conn = get_db()
        user = current_user()
        b = db.fetch_booking(conn, booking_id)
        if not b:
            flash("Booking not found.", "error")
            return redirect(url_for("bookings"))
        if not b["assigned_vehicle_id"] or not b["assigned_driver_id"]:
            flash("Cannot approve: no vehicle and driver assigned yet.",
                  "error")
            return redirect(url_for("bookings"))
        if (b["vehicle_type"] or "") != (b["vehicle_type_needed"] or ""):
            flash(f"Cannot approve: the assigned vehicle is "
                  f"{b['vehicle_type']}, but this trip needs "
                  f"{b['vehicle_type_needed']}.", "error")
            return redirect(url_for("bookings"))

        raw = request.form.get("start_mileage", "").strip()
        if not raw.isdigit() or int(raw) <= 0:
            flash("Start odometer must be a positive whole number (km).",
                  "error")
            return redirect(url_for("bookings"))
        guard_name = " ".join(
            request.form.get("guard_name", "").split()).strip()
        if len(guard_name) < 3 or len(guard_name) > 60:
            flash("Enter the guard name (e.g. Edd Guzman) before "
                  "approving.", "error")
            return redirect(url_for("bookings"))
        mileage = int(raw)
        now = _now()
        # Guard approval never starts the trip: it only records the
        # start odometer and the Time OUT stamp. The driver starts the
        # trip (inside pickups) or starts after approval (outside).
        conn.execute(
            "UPDATE bookings SET start_mileage=?,"
            " time_out_at=?, approved_by=?, guard_approved_at=?, guard_name=?,"
            " date_updated=? WHERE id=?",
            (mileage, now, user["id"], now, guard_name, now, booking_id))
        conn.execute(
            "UPDATE booking_assignments SET"
            " start_mileage=COALESCE(start_mileage, ?), date_updated=?"
            " WHERE booking_id=?", (mileage, now, booking_id))
        db.log_action(conn, booking_id, "GUARD_APPROVED", user["id"],
                      f"{guard_name.upper()} | Start odometer: {mileage:,} km"
                      f" (time OUT {now})")
        conn.commit()
        flash(f"Trip {b['booking_code']} approved. Start odometer "
              f"{mileage:,} km.", "ok")
        return redirect(url_for("bookings"))

    @app.route("/bookings/<int:booking_id>/odometer", methods=["POST"])
    @role_required("admin", "coordinator", "guard")
    def record_odometer(booking_id):
        conn = get_db()
        user = current_user()
        b = db.fetch_booking(conn, booking_id)
        if not b:
            flash("Booking not found.", "error")
            return redirect(url_for("bookings"))

        phase = request.form.get("phase", "").strip()
        raw = request.form.get("mileage", "").strip()
        if not raw.isdigit() or int(raw) <= 0:
            flash("Odometer must be a positive whole number (km).", "error")
            return redirect(url_for("bookings"))
        value = int(raw)
        now = _now()

        if phase == "start":
            if b["status"] not in ("approved", "assigned", "accepted",
                                   "on_the_way", "in_progress"):
                flash("Record the start odometer after approval, before the "
                      "trip is completed.", "error")
                return redirect(url_for("bookings"))
            conn.execute(
                "UPDATE bookings SET start_mileage=?,"
                " time_out_at=COALESCE(time_out_at, ?), date_updated=?"
                " WHERE id=?", (value, now, now, booking_id))
            conn.execute(
                "UPDATE booking_assignments SET start_mileage=?,"
                " date_updated=? WHERE booking_id=?",
                (value, now, booking_id))
            db.log_action(conn, booking_id, "START_ODOMETER", user["id"],
                          f"Start odometer: {value:,} km (time OUT {now})")
            flash(f"Start odometer {value:,} km recorded for "
                  f"{b['booking_code']}.", "ok")
        elif phase == "end":
            if b["start_mileage"] is None:
                flash("Record the start odometer first.", "error")
                return redirect(url_for("bookings"))
            if value < b["start_mileage"]:
                flash(f"End odometer ({value:,}) cannot be lower than start "
                      f"({b['start_mileage']:,}).", "error")
                return redirect(url_for("bookings"))
            distance = value - b["start_mileage"]
            conn.execute(
                "UPDATE bookings SET end_mileage=?, distance_km=?,"
                " time_in_at=?, date_updated=? WHERE id=?",
                (value, distance, now, now, booking_id))
            conn.execute(
                "UPDATE booking_assignments SET end_mileage=?,"
                " distance_km=?, date_updated=? WHERE booking_id=?",
                (value, distance, now, booking_id))
            if b["assigned_vehicle_id"]:
                conn.execute(
                    "UPDATE vehicles SET mileage=? WHERE id=?",
                    (value, b["assigned_vehicle_id"]))
            db.log_action(conn, booking_id, "END_ODOMETER", user["id"],
                          f"End odometer: {value:,} km ({distance:,} km total, "
                          f"time IN {now})")
            flash(f"End odometer {value:,} km recorded for "
                  f"{b['booking_code']} ({distance:,} km total).", "ok")
        else:
            flash("Unknown odometer entry.", "error")
            return redirect(url_for("bookings"))

        conn.commit()
        return redirect(url_for("bookings"))

    @app.route("/bookings/<int:booking_id>/unassign", methods=["POST"])
    @role_required("admin", "coordinator")
    def unassign_booking(booking_id):
        conn = get_db()
        b = db.fetch_booking(conn, booking_id)
        if not b:
            flash("Booking not found.", "error")
            return redirect(url_for("bookings"))
        if b["status"] not in ("assigned", "accepted"):
            flash("Only assigned/accepted bookings can be unassigned.", "error")
            return redirect(url_for("bookings"))
        if b["assigned_vehicle_id"]:
            conn.execute("UPDATE vehicles SET status='available' WHERE id=?",
                         (b["assigned_vehicle_id"],))
        conn.execute(
            "UPDATE bookings SET status='approved', assigned_vehicle_id=NULL,"
            " assigned_driver_id=NULL, location_sharing=0, driver_latitude=NULL,"
            " driver_longitude=NULL, driver_location_updated_at=NULL,"
            " date_updated=? WHERE id=?",
            (_now(), booking_id))
        db.log_action(conn, booking_id, "UNASSIGNED", session["user"]["id"], "")
        conn.commit()
        flash(f"Booking {b['booking_code']} unassigned.", "ok")
        return redirect(url_for("bookings"))

    @app.route("/bookings/<int:booking_id>/decline", methods=["POST"])
    @role_required("admin", "coordinator")
    def decline_booking(booking_id):
        conn = get_db()
        b = db.fetch_booking(conn, booking_id)
        if not b:
            flash("Booking not found.", "error")
            return redirect(url_for("bookings"))
        if b["status"] not in ("pending", "approved"):
            flash("Only pending bookings can be declined.", "error")
            return redirect(url_for("bookings"))
        remarks = request.form.get("coordinator_remarks", "").strip()
        if not remarks:
            flash("Please provide a reason for declining this booking.", "error")
            return redirect(url_for("bookings"))
        conn.execute(
            "UPDATE bookings SET status='declined', coordinator_remarks=?,"
            " date_updated=?, location_sharing=0, driver_latitude=NULL,"
            " driver_longitude=NULL, driver_location_updated_at=NULL"
            " WHERE id=?", (remarks, _now(), booking_id))
        db.log_action(conn, booking_id, "DECLINED", session["user"]["id"], remarks)
        conn.commit()
        flash(f"Booking {b['booking_code']} declined.", "ok")
        return redirect(url_for("bookings"))

    # ---------------- Requester: New booking ---------------- #
    @app.route("/bookings/new", methods=["GET", "POST"])
    @role_required("requester", "admin", "coordinator")
    def booking_form():
        conn = get_db()
        user = current_user()
        if request.method == "POST":
            purpose = request.form.get("purpose", "").strip()
            passenger_count = max(1, _int_field(request.form, "passenger_count", 1))
            vtype = request.form.get("vehicle_type_needed", "").strip()
            if vtype not in db.VEHICLE_TYPES:
                vtype = ""
            pickup = request.form.get("pickup_location", "").strip()
            dropoff = request.form.get("dropoff_location", "").strip()
            is_rt = 1 if request.form.get("is_roundtrip") else 0
            remarks = request.form.get("remarks", "").strip()
            nature = request.form.get("nature_of_trip", "").strip().lower()
            if nature not in db.TRIP_NATURES:
                nature = "official"
            department = request.form.get("department", "").strip()
            if nature == "personal":
                department = user["full_name"]

            outbound, error = resolve_leg(request.form.get, "departure_date",
                                          "departure_time")
            return_leg = None
            if not error and is_rt:
                return_leg, error = resolve_leg(request.form.get, "return_date",
                                                "return_time", "return_")

            if error:
                flash(error, "error")
            elif not purpose or not outbound["dep_date"]:
                flash("Purpose and departure date are required.", "error")
            elif nature == "official" and not department:
                flash("Department / Account Name is required for official "
                      "trips.", "error")
            elif is_rt and not return_leg["dep_date"]:
                flash("Return date is required for round trips.", "error")
            else:
                pickup_coords = None
                raw_lat = request.form.get("pickup_lat", "").strip()
                raw_lng = request.form.get("pickup_lng", "").strip()
                if raw_lat and raw_lng:
                    try:
                        pickup_coords = (float(raw_lat), float(raw_lng))
                    except ValueError:
                        pickup_coords = None
                if pickup_coords is None:
                    pickup_coords = parse_coordinates(pickup)
                if pickup_coords and not (-90 <= pickup_coords[0] <= 90
                                          and -180 <= pickup_coords[1] <= 180):
                    pickup_coords = None

                # Each leg becomes its own booking so each gets its own driver.
                legs = [("outbound", outbound, pickup, dropoff, pickup_coords)]
                if return_leg:
                    legs.append(("return", return_leg, dropoff, pickup, None))
                codes = []
                for label, leg, leg_pickup, leg_dropoff, coords in legs:
                    code = db.next_booking_code(conn)
                    cur = conn.execute(
                        "INSERT INTO bookings (booking_code, requested_by, purpose,"
                        " nature_of_trip, department, passenger_count,"
                        " vehicle_type_needed,"
                        " pickup_location, pickup_lat, pickup_lng,"
                        " dropoff_location, departure_date,"
                        " departure_time, is_roundtrip, return_date, return_time,"
                        " remarks, flight_type, flight_number, flight_date,"
                        " flight_time, status)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
                        "'pending')",
                        (code, user["id"], purpose, nature, department,
                         passenger_count, vtype,
                         leg_pickup,
                         coords[0] if coords else None,
                         coords[1] if coords else None,
                         leg_dropoff,
                         leg["dep_date"], leg["dep_time"], 0, "", "",
                         remarks, leg["flight_type"], leg["flight_number"],
                         leg["flight_date"], leg["flight_time"]))
                    leg_note = (f" [round trip {label} leg]" if return_leg
                                else "")
                    flight_note = ""
                    if leg["flight_type"] != "none":
                        flight_note = (f" | {leg['flight_type'].upper()} flight"
                                       f" {leg['flight_number']}"
                                       f" {leg['flight_date']}"
                                       f" {leg['flight_time']}")
                    db.log_action(conn, cur.lastrowid, "REQUESTED", user["id"],
                                  f"{vtype or 'Vehicle type to be set by coordinator'}"
                                  f" for {passenger_count} pax"
                                  f" ({nature.upper()}"
                                  + (f" - {department}" if department else "")
                                  + ")" + leg_note + flight_note)
                    codes.append(code)
                conn.commit()
                if return_leg:
                    flash(f"Round trip submitted: {codes[0]} (outbound) and "
                          f"{codes[1]} (return).", "ok")
                else:
                    flash(f"Booking {codes[0]} submitted!", "ok")
                return redirect(url_for("my_bookings"))
        return render_template("booking_form.html", vtypes=db.VEHICLE_TYPES,
                               natures=db.TRIP_NATURES, user=user)

    @app.route("/bookings/<int:booking_id>/cancel", methods=["POST"])
    @role_required("requester", "admin")
    def cancel_booking(booking_id):
        conn = get_db()
        user = current_user()
        b = db.fetch_booking(conn, booking_id)
        if not b:
            flash("Booking not found.", "error")
            return redirect(url_for("home"))
        if user["role"] == "requester" and b["requested_by"] != user["id"]:
            flash("You can only cancel your own bookings.", "error")
            return redirect(url_for("my_bookings"))
        if b["status"] in ("completed", "declined", "cancelled"):
            flash("Booking is already closed.", "error")
        else:
            if b["assigned_vehicle_id"]:
                conn.execute("UPDATE vehicles SET status='available' WHERE id=?",
                             (b["assigned_vehicle_id"],))
            conn.execute(
                "UPDATE bookings SET status='cancelled', date_updated=?"
                ", location_sharing=0, driver_latitude=NULL,"
                " driver_longitude=NULL, driver_location_updated_at=NULL"
                " WHERE id=?", (_now(), booking_id))
            db.log_action(conn, booking_id, "CANCELLED", user["id"], "")
            conn.commit()
            flash(f"Booking {b['booking_code']} cancelled.", "ok")
        return redirect(request.referrer or url_for("home"))

    # ---------------- Requester: My bookings ---------------- #
    @app.route("/my-bookings")
    @role_required("requester", "admin", "coordinator")
    def my_bookings():
        conn = get_db()
        user = current_user()
        rows = db.fetch_bookings(conn, requested_by=user["id"])
        return render_template("my_bookings.html", bookings=rows)

    # ---------------- Driver: My trips ---------------- #
    @app.route("/my-trips")
    @role_required("driver")
    def my_trips():
        conn = get_db()
        user = current_user()
        db.ensure_driver_assignments(conn, user["id"])
        rows = db.fetch_driver_assignments(conn, user["id"])
        return render_template("my_trips.html", bookings=rows,
                               whatsapp_link=whatsapp_link)

    @app.route("/my-trips/<int:booking_id>/status", methods=["POST"])
    @role_required("driver")
    def driver_trip_status(booking_id):
        conn = get_db()
        user = current_user()
        b = db.fetch_booking(conn, booking_id)
        a = _driver_assignment(conn, booking_id, user["id"]) if b else None
        if not b or not a:
            flash("Trip not found.", "error")
            return redirect(url_for("my_trips"))

        new_status = request.form.get("status", "")
        reason = request.form.get("reason", "").strip()
        allowed = {
            "assigned": ("accepted", "declined"),
            "accepted": ("on_the_way", "in_progress", "declined"),
            "on_the_way": ("in_progress", "declined"),
            "in_progress": ("completed",),
        }
        if new_status not in allowed.get(a["status"], []):
            flash("Invalid status transition.", "error")
            return redirect(url_for("my_trips"))

        if new_status == "declined" and not reason:
            flash("Please provide a reason for declining.", "error")
            return redirect(url_for("my_trips"))

        if new_status == "completed" and b["end_mileage"] is None:
            flash("The guard has not recorded the END ODOMETER yet. "
                  "Wait for it before completing the trip.", "error")
            return redirect(url_for("my_trips"))

        if (new_status == "in_progress"
                and not pickup_is_at_gate(b["pickup_location"])
                and not b["guard_approved_at"]):
            flash("Pickup is outside SSD/LHC - wait for the guard to approve "
                  "the trip before starting", "error")
            return redirect(url_for("my_trips"))

        # Odometer readings are recorded by the guard, not the driver.
        start_mileage = a["start_mileage"]
        end_mileage = a["end_mileage"]
        distance = a["distance_km"]

        now = _now()
        conn.execute(
            "UPDATE booking_assignments SET status=?, start_mileage=?,"
            " end_mileage=?, distance_km=?, date_updated=?,"
            " driver_on_the_way_at=CASE WHEN ?='on_the_way'"
            " THEN ? ELSE driver_on_the_way_at END,"
            " accepted_at=CASE WHEN ?='accepted' THEN ? ELSE accepted_at END,"
            " started_at=CASE WHEN ?='in_progress' THEN ? ELSE started_at END,"
            " declined_reason=CASE WHEN ?='declined' THEN ?"
            " ELSE declined_reason END,"
            " completed_at=CASE WHEN ?='completed' THEN ? ELSE completed_at END"
            " WHERE id=?",
            (new_status, start_mileage, end_mileage, distance, now,
             new_status, now, new_status, now,
             new_status, now, new_status, reason,
             new_status, now, a["id"]))

        if new_status == "completed":
            conn.execute("UPDATE vehicles SET status='available'"
                         " WHERE id=?", (a["vehicle_id"],))
        elif new_status == "declined":
            conn.execute("UPDATE vehicles SET status='available' WHERE id=?",
                         (a["vehicle_id"],))
            # Return the trip to the coordinator for a new driver.
            conn.execute("DELETE FROM booking_assignments WHERE id=?",
                         (a["id"],))
            remaining = conn.execute(
                "SELECT vehicle_id, driver_id FROM booking_assignments"
                " WHERE booking_id=? LIMIT 1", (booking_id,)).fetchone()
            if remaining:
                conn.execute(
                    "UPDATE bookings SET assigned_vehicle_id=?,"
                    " assigned_driver_id=? WHERE id=?",
                    (remaining["vehicle_id"], remaining["driver_id"],
                     booking_id))
            else:
                conn.execute(
                    "UPDATE bookings SET status='approved',"
                    " assigned_vehicle_id=NULL, assigned_driver_id=NULL,"
                    " driver_on_the_way_at=NULL, driver_arrived_at=NULL,"
                    " picked_up_at=NULL, dropped_off_at=NULL,"
                    " location_sharing=0, driver_latitude=NULL,"
                    " driver_longitude=NULL, driver_location_updated_at=NULL"
                    " WHERE id=?", (booking_id,))
        if new_status in ("declined", "completed"):
            conn.execute(
                "UPDATE bookings SET location_sharing=0, driver_latitude=NULL,"
                " driver_longitude=NULL, driver_location_updated_at=NULL"
                " WHERE id=?", (booking_id,))
        _sync_booking_primary(conn, booking_id, a["id"])
        db.recompute_booking_status(conn, booking_id)

        detail = reason
        if new_status == "on_the_way":
            detail = "Driver marked as on the way."
        elif new_status == "in_progress":
            detail = "Trip started." + (f" | {reason}" if reason else "")
        elif new_status == "completed":
            detail = "Trip completed." + (
                f" | {reason}" if reason else "")
        db.log_action(conn, booking_id, new_status.upper(), user["id"], detail)
        conn.commit()
        flash(f"Booking {b['booking_code']} marked as {new_status.replace('_',' ')}.",
              "ok")
        return redirect(url_for("my_trips"))

    @app.route("/my-trips/<int:booking_id>/pickup", methods=["POST"])
    @role_required("driver")
    def driver_confirm_pickup(booking_id):
        conn = get_db()
        user = current_user()
        b = db.fetch_booking(conn, booking_id)
        a = _driver_assignment(conn, booking_id, user["id"]) if b else None
        if not b or not a:
            flash("Trip not found.", "error")
            return redirect(url_for("my_trips"))
        if a["status"] not in ("accepted", "on_the_way", "in_progress"):
            flash("Accept the trip before confirming client pickup.", "error")
            return redirect(url_for("my_trips"))
        if not a["driver_arrived_at"]:
            flash("Mark your arrival at the pickup location before confirming pickup.",
                  "error")
            return redirect(url_for("my_trips"))
        if a["picked_up_at"]:
            flash("Client pickup has already been confirmed.", "info")
            return redirect(url_for("my_trips"))

        picked_up_at = _now()
        conn.execute(
            "UPDATE booking_assignments SET picked_up_at=?, date_updated=?"
            " WHERE id=?", (picked_up_at, picked_up_at, a["id"]))
        conn.execute(
            "UPDATE bookings SET location_sharing=0, driver_latitude=NULL,"
            " driver_longitude=NULL, driver_location_updated_at=NULL"
            " WHERE id=?", (booking_id,))
        _sync_booking_primary(conn, booking_id, a["id"])
        db.log_action(conn, booking_id, "PICKED_UP", user["id"],
                      f"Client picked up at {picked_up_at}")
        conn.commit()
        flash(f"Client pickup confirmed for {b['booking_code']}.", "ok")
        return redirect(url_for("my_trips"))

    @app.route("/my-trips/<int:booking_id>/dropoff", methods=["POST"])
    @role_required("driver")
    def driver_confirm_dropoff(booking_id):
        conn = get_db()
        user = current_user()
        b = db.fetch_booking(conn, booking_id)
        a = _driver_assignment(conn, booking_id, user["id"]) if b else None
        if not b or not a:
            flash("Trip not found.", "error")
            return redirect(url_for("my_trips"))
        if not a["picked_up_at"]:
            flash("Confirm client pickup before marking the drop-off.",
                  "error")
            return redirect(url_for("my_trips"))
        if a["dropped_off_at"]:
            flash("Drop-off has already been confirmed.", "info")
            return redirect(url_for("my_trips"))

        dropped_off_at = _now()
        conn.execute(
            "UPDATE booking_assignments SET dropped_off_at=?, date_updated=?"
            " WHERE id=?", (dropped_off_at, dropped_off_at, a["id"]))
        _sync_booking_primary(conn, booking_id, a["id"])
        db.log_action(conn, booking_id, "DROPPED_OFF", user["id"],
                      f"Client dropped off at {dropped_off_at}")
        conn.commit()
        flash(f"Client drop-off confirmed for {b['booking_code']}.", "ok")
        return redirect(url_for("my_trips"))

    @app.route("/my-trips/<int:booking_id>/pickup-map", methods=["POST"])
    @role_required("driver")
    def driver_pickup_map(booking_id):
        conn = get_db()
        user = current_user()
        b = db.fetch_booking(conn, booking_id)
        a = _driver_assignment(conn, booking_id, user["id"]) if b else None
        if not b or not a:
            flash("Trip not found.", "error")
            return redirect(url_for("my_trips"))
        if not b["pickup_map_opened_at"]:
            opened_at = _now()
            conn.execute(
                "UPDATE bookings SET pickup_map_opened_at=?, date_updated=?"
                " WHERE id=?", (opened_at, opened_at, booking_id))
            db.log_action(conn, booking_id, "PICKUP_MAP_OPENED", user["id"],
                          f"Driver opened the pickup route on Google Maps at "
                          f"{opened_at}")
            conn.commit()
        return redirect(maps_directions_url(
            maps_point(b["pickup_location"], b["pickup_lat"],
                       b["pickup_lng"])))

    @app.route("/my-trips/<int:booking_id>/arrival", methods=["POST"])
    @role_required("driver")
    def driver_confirm_arrival(booking_id):
        conn = get_db()
        user = current_user()
        b = db.fetch_booking(conn, booking_id)
        a = _driver_assignment(conn, booking_id, user["id"]) if b else None
        if not b or not a:
            flash("Trip not found.", "error")
            return redirect(url_for("my_trips"))
        if a["status"] not in ("on_the_way", "in_progress"):
            flash("Mark yourself on the way before confirming arrival.", "error")
            return redirect(url_for("my_trips"))
        if a["driver_arrived_at"]:
            flash("Arrival has already been confirmed.", "info")
            return redirect(url_for("my_trips"))

        arrived_at = _now()
        conn.execute(
            "UPDATE booking_assignments SET driver_arrived_at=?, date_updated=?"
            " WHERE id=?", (arrived_at, arrived_at, a["id"]))
        conn.execute(
            "UPDATE bookings SET location_sharing=0, driver_latitude=NULL,"
            " driver_longitude=NULL, driver_location_updated_at=NULL"
            " WHERE id=?", (booking_id,))
        _sync_booking_primary(conn, booking_id, a["id"])
        db.log_action(conn, booking_id, "ARRIVED_AT_PICKUP", user["id"],
                      f"Driver arrived at pickup at {arrived_at}")
        conn.commit()
        flash(f"Arrival confirmed for {b['booking_code']}.", "ok")
        return redirect(url_for("my_trips"))

    @app.route("/api/my-trips/<int:booking_id>/location", methods=["POST"])
    @role_required("driver")
    def driver_location(booking_id):
        conn = get_db()
        user = current_user()
        b = db.fetch_booking(conn, booking_id)
        if not b or not _driver_assignment(conn, booking_id, user["id"]):
            return jsonify({"error": "Trip not found."}), 404
        if not request.is_json:
            return jsonify({"error": "Send a JSON request."}), 400
        payload = request.get_json()
        action = payload.get("action") if isinstance(payload, dict) else None

        if action == "stop":
            conn.execute(
                "UPDATE bookings SET location_sharing=0, driver_latitude=NULL,"
                " driver_longitude=NULL, driver_location_updated_at=NULL"
                " WHERE id=?", (booking_id,))
            conn.commit()
            return jsonify({"sharing": False})

        if b["status"] != "on_the_way" or b["driver_arrived_at"]:
            return jsonify({
                "error": "Live location is only available while on the way to pickup."
            }), 409

        if action == "start":
            conn.execute(
                "UPDATE bookings SET location_sharing=1, driver_latitude=NULL,"
                " driver_longitude=NULL, driver_location_updated_at=NULL"
                " WHERE id=?", (booking_id,))
            conn.commit()
            return jsonify({"sharing": True})

        latitude = payload.get("latitude") if isinstance(payload, dict) else None
        longitude = payload.get("longitude") if isinstance(payload, dict) else None
        if (isinstance(latitude, bool) or not isinstance(latitude, (int, float))
                or not -90 <= latitude <= 90 or not math.isfinite(latitude)
                or isinstance(longitude, bool)
                or not isinstance(longitude, (int, float))
                or not -180 <= longitude <= 180
                or not math.isfinite(longitude)):
            return jsonify({"error": "Valid latitude and longitude are required."}), 400
        if not b["location_sharing"]:
            return jsonify({"error": "Start live location sharing first."}), 409

        updated_at = _now()
        conn.execute(
            "UPDATE bookings SET driver_latitude=?, driver_longitude=?,"
            " driver_location_updated_at=?, date_updated=? WHERE id=?",
            (latitude, longitude, updated_at, updated_at, booking_id))
        conn.commit()
        return jsonify({"sharing": True, "updated_at": updated_at})

    @app.route("/api/bookings/<int:booking_id>/driver-location")
    @login_required
    def booking_driver_location(booking_id):
        conn = get_db()
        user = current_user()
        b = db.fetch_booking(conn, booking_id)
        if not b:
            return jsonify({"error": "Booking not found."}), 404
        allowed = (
            user["role"] in ("admin", "coordinator")
            or (user["role"] == "requester"
                and b["requested_by"] == user["id"])
            or (user["role"] == "driver"
                and b["assigned_driver_id"] == user["id"])
        )
        if not allowed:
            return jsonify({"error": "You cannot view this booking."}), 403

        location = None
        sharing = (
            b["status"] == "on_the_way"
            and not b["driver_arrived_at"]
            and bool(b["location_sharing"])
        )
        if sharing:
            if b["driver_location_updated_at"]:
                try:
                    updated_epoch = time.mktime(
                        time.strptime(b["driver_location_updated_at"],
                                      "%Y-%m-%d %H:%M:%S"))
                    fresh = 0 <= time.time() - updated_epoch <= 60
                except ValueError:
                    fresh = False
                if fresh:
                    location = {
                        "latitude": b["driver_latitude"],
                        "longitude": b["driver_longitude"],
                        "updated_at": b["driver_location_updated_at"],
                    }
                else:
                    sharing = False
                    conn.execute(
                        "UPDATE bookings SET location_sharing=0,"
                        " driver_latitude=NULL, driver_longitude=NULL,"
                        " driver_location_updated_at=NULL WHERE id=?",
                        (booking_id,))
                    conn.commit()
        response = jsonify({
            "sharing": sharing,
            "location": location,
            "arrived_at": b["driver_arrived_at"],
            "status": b["status"],
        })
        response.headers["Cache-Control"] = "no-store"
        return response

    return app


if __name__ == "__main__":
    app = create_app()
    db.init_db()
    host = os.environ.get("TRANSPORT_HOST", "127.0.0.1")
    port = int(os.environ.get("TRANSPORT_PORT", "5001"))
    print(f"\n  Transport Booking System\n  Open http://{host}:{port}"
          "  (default: admin / admin123)\n")
    app.run(host=host, port=port, debug=False)
