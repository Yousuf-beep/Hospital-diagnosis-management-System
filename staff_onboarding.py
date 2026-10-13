"""
staff_onboarding.py — Self-registration and verification of doctors and lab technicians.

Flow
    applicant submits  ─►  submitted
    admin              ─►  under_review ─► interview_scheduled ─► interviewed ─► approved
                        └►  info_requested (applicant edits & resubmits ─► submitted)
                        └►  rejected (from any open state)

Until approved, the account has users.status = 'pending' and only the
`applicant` role's permissions. Approval requires a completed interview and a
fully ticked verification checklist.

Tables: staff_applications, application_events (audit trail)
"""

import json
import sqlite3
import datetime
import logging
from werkzeug.security import generate_password_hash

from database import get_conn, query, execute, NOW, updated_at_trigger

log = logging.getLogger("mediai.onboarding")

STATUS_LABELS = {
    "submitted":           "Submitted",
    "under_review":        "Under review",
    "info_requested":      "More information requested",
    "interview_scheduled": "Interview scheduled",
    "interviewed":         "Interview completed",
    "approved":            "Approved",
    "rejected":            "Rejected",
}
OPEN_STATUSES = ("submitted", "under_review", "info_requested",
                 "interview_scheduled", "interviewed")

INTERVIEW_MODES = {
    "video":     "Video call (in MediAI)",
    "phone":     "Phone call",
    "in_person": "In person",
}

CHECKLISTS = {
    "doctor": [
        ("identity",      "Identity verified — government ID matches the applicant"),
        ("license",       "Medical registration number verified with the issuing council"),
        ("qualification", "Degree / specialisation certificates verified"),
        ("experience",    "Work experience / references checked"),
    ],
    "lab": [
        ("identity",      "Identity verified — government ID matches the applicant"),
        ("accreditation", "Lab licence / accreditation (e.g. NABL) verified"),
        ("qualification", "Technician qualification (DMLT / BMLT / MSc) verified"),
        ("facility",      "Lab address, facilities and listed tests verified"),
    ],
}

# action -> (statuses it is allowed from, resulting status)
TRANSITIONS = {
    "start_review":       (("submitted",), "under_review"),
    "request_info":       (("submitted", "under_review", "interview_scheduled", "interviewed"), "info_requested"),
    "schedule_interview": (("submitted", "under_review", "interview_scheduled"), "interview_scheduled"),
    "complete_interview": (("interview_scheduled",), "interviewed"),
    "approve":            (("interviewed",), "approved"),
    "reject":             (OPEN_STATUSES, "rejected"),
}


def init_onboarding_tables():
    conn = get_conn()
    try:
        conn.executescript(f"""
            CREATE TABLE IF NOT EXISTS staff_applications (
                id                  INTEGER  PRIMARY KEY AUTOINCREMENT,
                user_id             INTEGER  NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                role                TEXT     NOT NULL CHECK (role IN ('doctor', 'lab')),
                status              TEXT     NOT NULL DEFAULT 'submitted',
                email               TEXT     NOT NULL,
                phone               TEXT     NOT NULL,
                license_number      TEXT     NOT NULL,
                license_authority   TEXT     NOT NULL,
                statement           TEXT,
                checklist           TEXT     NOT NULL DEFAULT '{{}}',
                info_request        TEXT,
                interview_at        DATETIME,
                interview_mode      TEXT,
                interview_location  TEXT,
                interview_notes     TEXT,
                internal_notes      TEXT,
                decision_reason     TEXT,
                reviewed_by         INTEGER  REFERENCES users(id) ON DELETE SET NULL,
                submitted_at        DATETIME NOT NULL DEFAULT {NOW},
                decided_at          DATETIME,
                updated_at          DATETIME NOT NULL DEFAULT {NOW}
            );

            CREATE TABLE IF NOT EXISTS application_events (
                id                   INTEGER  PRIMARY KEY AUTOINCREMENT,
                application_id       INTEGER  NOT NULL REFERENCES staff_applications(id) ON DELETE CASCADE,
                actor_user_id        INTEGER  REFERENCES users(id) ON DELETE SET NULL,
                action               TEXT     NOT NULL,
                note                 TEXT,
                visible_to_applicant INTEGER  NOT NULL DEFAULT 1,
                created_at           DATETIME NOT NULL DEFAULT {NOW}
            );

            CREATE INDEX IF NOT EXISTS idx_app_status ON staff_applications(status);
            CREATE INDEX IF NOT EXISTS idx_app_events ON application_events(application_id);
        """)
        conn.executescript(updated_at_trigger("staff_applications"))
        conn.commit()
    finally:
        conn.close()


def _event(conn, app_id, actor, action, note=None, visible=True):
    conn.execute("""
        INSERT INTO application_events (application_id, actor_user_id, action, note, visible_to_applicant)
        VALUES (?,?,?,?,?)
    """, (app_id, actor, action, note, 1 if visible else 0))


# ── Applicant side ────────────────────────────────────────────────────────────

def submit_application(role, username, password, profile, app):
    """Create a pending account + profile + application in one transaction.
    `profile` holds doctor_profiles / lab_profiles columns, `app` holds
    staff_applications columns. Returns (user_id, error)."""
    if role not in ("doctor", "lab"):
        return None, "Invalid role."
    conn = get_conn()
    try:
        uid = conn.execute(
            "INSERT INTO users (username, password_hash, role, status) VALUES (?,?,?,'pending')",
            (username, generate_password_hash(password), role)).lastrowid
        _insert_profile(conn, role, uid, profile)
        app_id = conn.execute("""
            INSERT INTO staff_applications
                (user_id, role, email, phone, license_number, license_authority, statement)
            VALUES (?,?,?,?,?,?,?)
        """, (uid, role, app["email"], app["phone"], app["license_number"],
              app["license_authority"], app.get("statement"))).lastrowid
        _event(conn, app_id, uid, "submitted", "Application submitted")
        conn.commit()
        log.info("New %s application #%s from %s", role, app_id, username)
        return uid, None
    except sqlite3.IntegrityError:
        conn.rollback()
        return None, "That username is already taken."
    finally:
        conn.close()


def _insert_profile(conn, role, uid, p):
    if role == "doctor":
        conn.execute("""
            INSERT INTO doctor_profiles
                (user_id, full_name, specialty, qualification, experience_yrs,
                 phone, hospital, available)
            VALUES (?,?,?,?,?,?,?,0)
        """, (uid, p["full_name"], p["specialty"], p["qualification"],
              p["experience_yrs"], p.get("phone"), p.get("hospital")))
    else:
        conn.execute("""
            INSERT INTO lab_profiles
                (user_id, full_name, lab_name, phone, qualification, experience_yrs,
                 lab_address, lab_city, lab_phone, accreditation_number,
                 accreditation_body, tests_offered, operating_hours)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (uid, p["full_name"], p["lab_name"], p.get("phone"), p["qualification"],
              p["experience_yrs"], p["lab_address"], p["lab_city"], p.get("lab_phone"),
              p.get("accreditation_number"), p.get("accreditation_body"),
              json.dumps(p.get("tests_offered", [])), p.get("operating_hours")))


def resubmit_application(user_id, profile, app):
    """Applicant updates their details after an info request."""
    a = query("SELECT id, role, status FROM staff_applications WHERE user_id=?", (user_id,), one=True)
    if not a or a["status"] != "info_requested":
        return "Your application can only be edited when more information is requested."
    conn = get_conn()
    try:
        conn.execute("""
            UPDATE staff_applications
            SET email=?, phone=?, license_number=?, license_authority=?, statement=?,
                status='submitted'
            WHERE id=?
        """, (app["email"], app["phone"], app["license_number"], app["license_authority"],
              app.get("statement"), a["id"]))
        if a["role"] == "doctor":
            conn.execute("""
                UPDATE doctor_profiles SET full_name=?, specialty=?, qualification=?,
                    experience_yrs=?, phone=?, hospital=?
                WHERE user_id=?
            """, (profile["full_name"], profile["specialty"], profile["qualification"],
                  profile["experience_yrs"], profile.get("phone"), profile.get("hospital"), user_id))
        else:
            conn.execute("""
                UPDATE lab_profiles SET full_name=?, lab_name=?, phone=?, qualification=?,
                    experience_yrs=?, lab_address=?, lab_city=?, lab_phone=?,
                    accreditation_number=?, accreditation_body=?, tests_offered=?,
                    operating_hours=?
                WHERE user_id=?
            """, (profile["full_name"], profile["lab_name"], profile.get("phone"),
                  profile["qualification"], profile["experience_yrs"], profile["lab_address"],
                  profile["lab_city"], profile.get("lab_phone"), profile.get("accreditation_number"),
                  profile.get("accreditation_body"), json.dumps(profile.get("tests_offered", [])),
                  profile.get("operating_hours"), user_id))
        _event(conn, a["id"], user_id, "resubmitted", "Applicant updated the application")
        conn.commit()
        return None
    finally:
        conn.close()


# ── Reading ───────────────────────────────────────────────────────────────────

_APP_SELECT = """
    SELECT a.*, u.username, u.status AS account_status,
           COALESCE(dp.full_name, lp.full_name) AS full_name,
           dp.specialty, dp.hospital,
           COALESCE(dp.qualification, lp.qualification)   AS qualification,
           COALESCE(dp.experience_yrs, lp.experience_yrs) AS experience_yrs,
           lp.lab_name, lp.lab_address, lp.lab_city, lp.lab_phone,
           lp.accreditation_number, lp.accreditation_body,
           lp.tests_offered, lp.operating_hours,
           r.username AS reviewer_username
    FROM   staff_applications a
    JOIN   users u ON u.id = a.user_id
    LEFT   JOIN doctor_profiles dp ON dp.user_id = a.user_id AND a.role = 'doctor'
    LEFT   JOIN lab_profiles    lp ON lp.user_id = a.user_id AND a.role = 'lab'
    LEFT   JOIN users r ON r.id = a.reviewed_by
"""


def _decorate(a):
    if not a:
        return a
    a["status_label"]   = STATUS_LABELS.get(a["status"], a["status"])
    a["tests"]          = json.loads(a.get("tests_offered") or "[]")
    a["checks"]         = json.loads(a.get("checklist") or "{}")
    a["checklist_items"] = CHECKLISTS[a["role"]]
    a["checklist_done"] = all(a["checks"].get(k) for k, _ in CHECKLISTS[a["role"]])
    a["mode_label"]     = INTERVIEW_MODES.get(a.get("interview_mode") or "", "")
    a["is_open"]        = a["status"] in OPEN_STATUSES
    return a


def get_application(app_id):
    return _decorate(query(_APP_SELECT + " WHERE a.id=?", (app_id,), one=True))


def get_application_for_user(user_id):
    return _decorate(query(_APP_SELECT + " WHERE a.user_id=?", (user_id,), one=True))


def list_applications(status=None):
    if status == "open":
        rows = query(_APP_SELECT + " WHERE a.status IN (%s) ORDER BY a.submitted_at ASC"
                     % ",".join("?" * len(OPEN_STATUSES)), OPEN_STATUSES)
    elif status:
        rows = query(_APP_SELECT + " WHERE a.status=? ORDER BY a.updated_at DESC", (status,))
    else:
        rows = query(_APP_SELECT + " ORDER BY a.updated_at DESC")
    return [_decorate(r) for r in rows]


def application_counts():
    rows = query("SELECT status, COUNT(*) AS n FROM staff_applications GROUP BY status")
    counts = {r["status"]: r["n"] for r in rows}
    counts["open"] = sum(counts.get(s, 0) for s in OPEN_STATUSES)
    return counts


def get_events(app_id, include_internal=False):
    sql = """
        SELECT e.*, u.username AS actor_username, u.role AS actor_role
        FROM   application_events e
        LEFT   JOIN users u ON u.id = e.actor_user_id
        WHERE  e.application_id = ?
    """
    if not include_internal:
        sql += " AND e.visible_to_applicant = 1"
    return query(sql + " ORDER BY e.created_at ASC, e.id ASC", (app_id,))


# ── Admin side ────────────────────────────────────────────────────────────────

def save_checklist(app_id, admin_id, checked_keys):
    a = get_application(app_id)
    if not a or not a["is_open"]:
        return "This application is closed."
    checks = {k: (k in checked_keys) for k, _ in CHECKLISTS[a["role"]]}
    conn = get_conn()
    try:
        conn.execute("UPDATE staff_applications SET checklist=?, reviewed_by=? WHERE id=?",
                     (json.dumps(checks), admin_id, app_id))
        done = [label for k, label in CHECKLISTS[a["role"]] if checks[k]]
        _event(conn, app_id, admin_id, "checklist_updated",
               f"{len(done)}/{len(checks)} verification checks complete", visible=False)
        conn.commit()
    finally:
        conn.close()
    return None


def save_internal_notes(app_id, admin_id, notes):
    execute("UPDATE staff_applications SET internal_notes=?, reviewed_by=? WHERE id=?",
            (notes, admin_id, app_id))


def transition(app_id, admin_id, action, form):
    """Apply an admin action. `form` is the submitted form (dict-like).
    Returns (application, error)."""
    a = get_application(app_id)
    if not a:
        return None, "Application not found."
    if action not in TRANSITIONS:
        return a, "Unknown action."
    allowed_from, new_status = TRANSITIONS[action]
    if a["status"] not in allowed_from:
        return a, f"Cannot {action.replace('_', ' ')} while the application is '{a['status_label']}'."

    note = (form.get("note") or "").strip()
    sets = {"status": new_status, "reviewed_by": admin_id}
    event_note, visible = None, True

    if action == "start_review":
        event_note = "Your application is being reviewed by our verification team."

    elif action == "request_info":
        if not note:
            return a, "Please describe what information the applicant must provide."
        sets["info_request"] = note
        event_note = note

    elif action == "schedule_interview":
        mode = form.get("interview_mode", "")
        when = form.get("interview_at", "")
        if mode not in INTERVIEW_MODES:
            return a, "Choose an interview mode."
        try:
            when_dt = datetime.datetime.fromisoformat(when)
        except ValueError:
            return a, "Choose a valid interview date and time."
        if when_dt < datetime.datetime.now() - datetime.timedelta(minutes=5):
            return a, "The interview time must be in the future."
        location = (form.get("interview_location") or "").strip()
        if mode in ("phone", "in_person") and not location:
            return a, "Enter the phone number or address for the interview."
        sets.update(interview_at=when_dt, interview_mode=mode,
                    interview_location=location or None)
        event_note = (f"{'Rescheduled' if a['status'] == 'interview_scheduled' else 'Scheduled'}: "
                      f"{INTERVIEW_MODES[mode]} on {when_dt.strftime('%d %b %Y, %I:%M %p')}"
                      + (f" — {location}" if location else "")
                      + (f". {note}" if note else ""))

    elif action == "complete_interview":
        if not note:
            return a, "Record the interview assessment before marking it complete."
        sets["interview_notes"] = note
        event_note, visible = f"Interview assessment: {note}", False

    elif action == "approve":
        if not a["checklist_done"]:
            return a, "Complete every verification check before approving."
        sets["decided_at"] = datetime.datetime.now()
        sets["decision_reason"] = note or None
        event_note = "Application approved — welcome to MediAI!" + (f" {note}" if note else "")

    elif action == "reject":
        if not note:
            return a, "Please give a reason for the rejection."
        sets["decided_at"] = datetime.datetime.now()
        sets["decision_reason"] = note
        event_note = note

    conn = get_conn()
    try:
        cols = ", ".join(f"{k}=?" for k in sets)
        conn.execute(f"UPDATE staff_applications SET {cols} WHERE id=?",
                     (*sets.values(), app_id))
        if action == "approve":
            conn.execute("UPDATE users SET status='active' WHERE id=?", (a["user_id"],))
            if a["role"] == "doctor":
                conn.execute("UPDATE doctor_profiles SET available=1 WHERE user_id=?", (a["user_id"],))
        elif action == "reject":
            conn.execute("UPDATE users SET status='rejected' WHERE id=?", (a["user_id"],))
        _event(conn, app_id, admin_id, action, event_note, visible)
        conn.commit()
    finally:
        conn.close()
    log.info("Application #%s: %s by admin %s", app_id, action, admin_id)
    return get_application(app_id), None


def can_join_interview(a, user_id, permissions):
    """Interview video room: the applicant and any verifying admin, only while
    a video interview is scheduled."""
    if not a or a["status"] != "interview_scheduled" or a["interview_mode"] != "video":
        return False
    return a["user_id"] == user_id or "staff.verify" in permissions


init_onboarding_tables()
