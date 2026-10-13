"""
workflow.py — Clinical Workflow DB Layer
Tables: doctor_profiles, lab_profiles, consultations, lab_orders, lab_results
"""
import json
import logging
from database import (get_conn, query, scalar, execute, NOW,
                      updated_at_trigger, add_column_if_missing)

log = logging.getLogger("mediai.workflow")

LAB_TEST_TYPES = [
    "Blood Test", "Urine Test", "X-Ray", "ECG", "MRI / CT Scan", "Ultrasound",
    "Stool Test", "Culture Test", "Liver Function Test", "Kidney Function Test",
    "Thyroid Profile", "Lipid Profile", "HbA1c", "Chest X-Ray",
    "Complete Blood Count (CBC)", "COVID-19 Test", "Dengue Test",
    "Malaria Test", "Typhoid Test", "HIV Test", "Other"
]

CONSULTATION_STATUSES = ("pending", "in_review", "completed", "cancelled")


def init_workflow_tables():
    conn = get_conn()
    try:
        conn.executescript(f"""
            CREATE TABLE IF NOT EXISTS doctor_profiles (
                id             INTEGER  PRIMARY KEY AUTOINCREMENT,
                user_id        INTEGER  NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                full_name      TEXT     NOT NULL,
                specialty      TEXT     NOT NULL,
                qualification  TEXT,
                experience_yrs INTEGER  DEFAULT 0,
                available      INTEGER  NOT NULL DEFAULT 1,
                created_at     DATETIME NOT NULL DEFAULT {NOW}
            );

            CREATE TABLE IF NOT EXISTS lab_profiles (
                id         INTEGER  PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER  NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                full_name  TEXT     NOT NULL,
                lab_name   TEXT     DEFAULT 'Central Lab',
                created_at DATETIME NOT NULL DEFAULT {NOW}
            );

            CREATE TABLE IF NOT EXISTS consultations (
                id               INTEGER  PRIMARY KEY AUTOINCREMENT,
                diagnosis_id     INTEGER  NOT NULL REFERENCES diagnoses(id),
                patient_user_id  INTEGER  NOT NULL REFERENCES users(id),
                doctor_user_id   INTEGER  NOT NULL REFERENCES users(id),
                status           TEXT     NOT NULL DEFAULT 'pending',
                doctor_notes     TEXT,
                final_diagnosis  TEXT,
                prescription     TEXT,
                follow_up_date   DATE,
                video_active     INTEGER  NOT NULL DEFAULT 0,
                video_started_at DATETIME DEFAULT NULL,
                created_at       DATETIME NOT NULL DEFAULT {NOW},
                updated_at       DATETIME NOT NULL DEFAULT {NOW}
            );

            CREATE TABLE IF NOT EXISTS lab_orders (
                id              INTEGER  PRIMARY KEY AUTOINCREMENT,
                consultation_id INTEGER  NOT NULL REFERENCES consultations(id),
                patient_user_id INTEGER  NOT NULL REFERENCES users(id),
                doctor_user_id  INTEGER  NOT NULL REFERENCES users(id),
                lab_user_id     INTEGER  DEFAULT NULL REFERENCES users(id) ON DELETE SET NULL,
                test_type       TEXT     NOT NULL,
                test_notes      TEXT,
                priority        TEXT     NOT NULL DEFAULT 'normal',
                status          TEXT     NOT NULL DEFAULT 'pending',
                created_at      DATETIME NOT NULL DEFAULT {NOW},
                updated_at      DATETIME NOT NULL DEFAULT {NOW}
            );

            CREATE TABLE IF NOT EXISTS lab_results (
                id           INTEGER  PRIMARY KEY AUTOINCREMENT,
                order_id     INTEGER  NOT NULL UNIQUE REFERENCES lab_orders(id),
                lab_user_id  INTEGER  NOT NULL REFERENCES users(id),
                result_text  TEXT     NOT NULL,
                result_value TEXT,
                unit         TEXT,
                normal_range TEXT,
                is_abnormal  INTEGER  NOT NULL DEFAULT 0,
                remarks      TEXT,
                created_at   DATETIME NOT NULL DEFAULT {NOW}
            );

            CREATE INDEX IF NOT EXISTS idx_consult_doctor   ON consultations(doctor_user_id);
            CREATE INDEX IF NOT EXISTS idx_consult_patient  ON consultations(patient_user_id);
            CREATE INDEX IF NOT EXISTS idx_consult_status   ON consultations(status);
            CREATE INDEX IF NOT EXISTS idx_laborder_status  ON lab_orders(status);
            CREATE INDEX IF NOT EXISTS idx_laborder_lab     ON lab_orders(lab_user_id);
            CREATE INDEX IF NOT EXISTS idx_laborder_consult ON lab_orders(consultation_id);
        """)
        for t in ("consultations", "lab_orders"):
            conn.executescript(updated_at_trigger(t))

        # Self-registration details (added for staff onboarding)
        for col, ddl in [("phone", "TEXT"), ("hospital", "TEXT")]:
            add_column_if_missing(conn, "doctor_profiles", col, ddl)
        for col, ddl in [("phone", "TEXT"), ("qualification", "TEXT"),
                         ("experience_yrs", "INTEGER DEFAULT 0"),
                         ("lab_address", "TEXT"), ("lab_city", "TEXT"),
                         ("lab_phone", "TEXT"), ("accreditation_number", "TEXT"),
                         ("accreditation_body", "TEXT"),
                         ("tests_offered", "TEXT NOT NULL DEFAULT '[]'"),
                         ("operating_hours", "TEXT")]:
            add_column_if_missing(conn, "lab_profiles", col, ddl)
        conn.commit()
    finally:
        conn.close()
    logging.getLogger("mediai.db").info("Workflow tables initialised.")


# ── Staff directory ───────────────────────────────────────────────────────────

def get_all_doctors():
    """Approved doctors (active and suspended) for the admin directory."""
    return query("""
        SELECT dp.*, u.username, u.status
        FROM   doctor_profiles dp
        JOIN   users u ON u.id = dp.user_id
        WHERE  u.status IN ('active', 'suspended')
        ORDER  BY u.status, dp.specialty, dp.full_name
    """)


def get_all_lab_techs():
    rows = query("""
        SELECT lp.*, u.username, u.status
        FROM   lab_profiles lp
        JOIN   users u ON u.id = lp.user_id
        WHERE  u.status IN ('active', 'suspended')
        ORDER  BY u.status, lp.lab_name, lp.full_name
    """)
    for r in rows:
        r["tests"] = _tests(r.get("tests_offered"))
    return rows


def _tests(raw):
    try:
        return json.loads(raw or "[]")
    except ValueError:
        return []


def get_doctor_profile(user_id):
    return query("SELECT * FROM doctor_profiles WHERE user_id=?", (user_id,), one=True)


def get_lab_profile(user_id):
    row = query("SELECT * FROM lab_profiles WHERE user_id=?", (user_id,), one=True)
    if row:
        row["tests"] = _tests(row.get("tests_offered"))
    return row


# ── Auto-assignment ───────────────────────────────────────────────────────────

def auto_assign_doctor(specialty):
    """
    Return user_id of the available doctor matching the specialty
    with fewest active consultations (load balancing).
    Falls back to any available doctor if none match specialty.
    """
    sql = """
        SELECT dp.user_id, COUNT(c.id) AS active_cases
        FROM   doctor_profiles dp
        JOIN   users u ON u.id = dp.user_id AND u.status = 'active'
        LEFT   JOIN consultations c
               ON c.doctor_user_id = dp.user_id
              AND c.status NOT IN ('completed', 'cancelled')
        WHERE  dp.available = 1 {extra}
        GROUP  BY dp.user_id
        ORDER  BY active_cases ASC
        LIMIT  1
    """
    row = query(sql.format(extra="AND dp.specialty = ? COLLATE NOCASE"), (specialty,), one=True)
    if not row:
        row = query(sql.format(extra=""), one=True)
    return row["user_id"] if row else None


# ── Consultations ─────────────────────────────────────────────────────────────

def suspend_staff(user_id):
    """Block a doctor / lab account without destroying medical records.
    Their open work is handed to other active staff. Returns (message, error)."""
    user = query("SELECT role, status FROM users WHERE id=?", (user_id,), one=True)
    if not user or user["role"] not in ("doctor", "lab") or user["status"] != "active":
        return None, "Only active doctor or lab accounts can be suspended."
    execute("UPDATE users SET status='suspended' WHERE id=?", (user_id,))

    moved = unassigned = 0
    if user["role"] == "doctor":
        execute("UPDATE doctor_profiles SET available=0 WHERE user_id=?", (user_id,))
        spec = scalar("SELECT specialty FROM doctor_profiles WHERE user_id=?", (user_id,))
        for c in query("SELECT id FROM consultations WHERE doctor_user_id=? "
                       "AND status IN ('pending','in_review')", (user_id,)):
            new_doc = auto_assign_doctor(spec)
            if new_doc:
                execute("UPDATE consultations SET doctor_user_id=?, status='pending' WHERE id=?",
                        (new_doc, c["id"]))
                moved += 1
            else:
                unassigned += 1
        what = "consultation(s)"
    else:
        for o in query("SELECT id, test_type FROM lab_orders WHERE lab_user_id=? AND status='pending'",
                       (user_id,)):
            new_lab = pick_lab_for_test(o["test_type"])
            execute("UPDATE lab_orders SET lab_user_id=? WHERE id=?", (new_lab, o["id"]))
            if new_lab:
                moved += 1
            else:
                unassigned += 1
        what = "pending lab order(s)"

    msg = f"Account suspended. {moved} open {what} reassigned."
    if unassigned:
        msg += f" {unassigned} could not be reassigned — no other active staff available."
    log.info("Suspended user %s: %s", user_id, msg)
    return msg, None


def reactivate_staff(user_id):
    user = query("SELECT role, status FROM users WHERE id=?", (user_id,), one=True)
    if not user or user["role"] not in ("doctor", "lab") or user["status"] != "suspended":
        return "Only suspended doctor or lab accounts can be reactivated."
    execute("UPDATE users SET status='active' WHERE id=?", (user_id,))
    if user["role"] == "doctor":
        execute("UPDATE doctor_profiles SET available=1 WHERE user_id=?", (user_id,))
    return None


def create_consultation(diagnosis_id, patient_user_id, doctor_user_id):
    existing = query(
        "SELECT id FROM consultations WHERE diagnosis_id=? AND patient_user_id=?",
        (diagnosis_id, patient_user_id), one=True)
    if existing:
        return existing["id"]
    return execute("""
        INSERT INTO consultations (diagnosis_id, patient_user_id, doctor_user_id, status)
        VALUES (?,?,?,'pending')
    """, (diagnosis_id, patient_user_id, doctor_user_id))


def get_doctor_consultations(doctor_user_id, status=None):
    base = """
        SELECT c.*,
               p.full_name  AS patient_name, p.age, p.gender, p.city,
               d.top_disease, d.confidence, d.risk_score,
               d.symptoms, d.predictions, d.doctor_type,
               d.created_at AS diagnosis_time
        FROM   consultations c
        JOIN   patients  p ON p.user_id = c.patient_user_id
        JOIN   diagnoses d ON d.id      = c.diagnosis_id
        WHERE  c.doctor_user_id = ?
    """
    if status:
        return query(base + " AND c.status=? ORDER BY c.created_at DESC",
                     (doctor_user_id, status))
    return query(base + " ORDER BY c.created_at DESC", (doctor_user_id,))


def get_consultation(consultation_id):
    return query("""
        SELECT c.*,
               p.full_name  AS patient_name, p.age, p.gender, p.city,
               d.top_disease, d.confidence, d.risk_score,
               d.symptoms, d.predictions, d.doctor_type,
               d.created_at AS diagnosis_time,
               dp.full_name AS doctor_name, dp.specialty, dp.qualification
        FROM   consultations   c
        JOIN   patients        p  ON p.user_id  = c.patient_user_id
        JOIN   diagnoses       d  ON d.id       = c.diagnosis_id
        JOIN   doctor_profiles dp ON dp.user_id = c.doctor_user_id
        WHERE  c.id = ?
    """, (consultation_id,), one=True)


def update_consultation(consultation_id, doctor_notes, final_diagnosis,
                        prescription, follow_up_date, status):
    if status not in CONSULTATION_STATUSES:
        status = "in_review"
    execute("""
        UPDATE consultations
        SET doctor_notes=?, final_diagnosis=?, prescription=?,
            follow_up_date=?, status=?
        WHERE id=?
    """, (doctor_notes, final_diagnosis, prescription,
          follow_up_date or None, status, consultation_id))


def set_video_state(consultation_id, active):
    if active:
        execute("UPDATE consultations SET video_active=1, video_started_at=datetime('now','localtime') "
                "WHERE id=?", (consultation_id,))
    else:
        execute("UPDATE consultations SET video_active=0 WHERE id=?", (consultation_id,))


def get_patient_consultations(patient_user_id):
    return query("""
        SELECT c.*,
               dp.full_name AS doctor_name, dp.specialty,
               d.top_disease, d.confidence, d.risk_score,
               d.created_at AS diagnosis_time,
               COUNT(lo.id)                                           AS total_labs,
               COALESCE(SUM(CASE WHEN lo.status='pending'   THEN 1 ELSE 0 END), 0) AS pending_labs,
               COALESCE(SUM(CASE WHEN lo.status='completed' THEN 1 ELSE 0 END), 0) AS done_labs
        FROM   consultations   c
        JOIN   doctor_profiles dp ON dp.user_id   = c.doctor_user_id
        JOIN   diagnoses       d  ON d.id         = c.diagnosis_id
        LEFT   JOIN lab_orders lo ON lo.consultation_id = c.id
        WHERE  c.patient_user_id = ?
        GROUP  BY c.id
        ORDER  BY c.created_at DESC
    """, (patient_user_id,))


# ── Lab Orders ────────────────────────────────────────────────────────────────

def pick_lab_for_test(test_type):
    """Active lab tech that offers this test (falling back to any active lab)
    with the fewest pending orders."""
    row = query("""
        SELECT lp.user_id,
               instr(COALESCE(lp.tests_offered, '[]'), ?) > 0 AS offers,
               COUNT(lo.id) AS pending
        FROM   lab_profiles lp
        JOIN   users u ON u.id = lp.user_id AND u.status = 'active'
        LEFT   JOIN lab_orders lo
               ON lo.lab_user_id = lp.user_id AND lo.status = 'pending'
        GROUP  BY lp.user_id
        ORDER  BY offers DESC, pending ASC
        LIMIT  1
    """, (json.dumps(test_type),), one=True)
    return row["user_id"] if row else None


def create_lab_order(consultation_id, patient_user_id, doctor_user_id,
                     test_type, test_notes="", priority="normal"):
    """Auto-assign to lab tech with fewest pending orders."""
    if priority not in ("normal", "urgent"):
        priority = "normal"
    lab_user_id = pick_lab_for_test(test_type)
    return execute("""
        INSERT INTO lab_orders
            (consultation_id, patient_user_id, doctor_user_id,
             lab_user_id, test_type, test_notes, priority, status)
        VALUES (?,?,?,?,?,?,?,'pending')
    """, (consultation_id, patient_user_id, doctor_user_id,
          lab_user_id, test_type, test_notes, priority))


def get_lab_orders_for_consultation(consultation_id):
    return query("""
        SELECT lo.*,
               lr.result_text, lr.result_value, lr.unit,
               lr.normal_range, lr.is_abnormal, lr.remarks,
               lr.created_at AS result_time,
               lp.full_name  AS lab_tech_name
        FROM   lab_orders lo
        LEFT   JOIN lab_results  lr ON lr.order_id  = lo.id
        LEFT   JOIN lab_profiles lp ON lp.user_id   = lo.lab_user_id
        WHERE  lo.consultation_id = ?
        ORDER  BY lo.created_at DESC
    """, (consultation_id,))


_LAB_ORDER_SELECT = """
    SELECT lo.*,
           p.full_name  AS patient_name, p.age, p.gender,
           dp.full_name AS doctor_name, dp.specialty,
           d.top_disease,
           lr.result_text, lr.result_value, lr.unit, lr.is_abnormal
    FROM   lab_orders      lo
    JOIN   patients        p  ON p.user_id  = lo.patient_user_id
    JOIN   doctor_profiles dp ON dp.user_id = lo.doctor_user_id
    JOIN   consultations   c  ON c.id       = lo.consultation_id
    JOIN   diagnoses       d  ON d.id       = c.diagnosis_id
    LEFT   JOIN lab_results lr ON lr.order_id = lo.id
"""


def get_lab_tech_orders(lab_user_id, status=None):
    base = _LAB_ORDER_SELECT + " WHERE lo.lab_user_id = ?"
    if status:
        return query(base + " AND lo.status=? ORDER BY lo.priority DESC, lo.created_at ASC",
                     (lab_user_id, status))
    return query(base + " ORDER BY lo.priority DESC, lo.created_at ASC", (lab_user_id,))


def get_lab_order_for_tech(order_id, lab_user_id):
    """Single order, only if it is assigned to this lab tech."""
    return query(_LAB_ORDER_SELECT + " WHERE lo.id = ? AND lo.lab_user_id = ?",
                 (order_id, lab_user_id), one=True)


def submit_lab_result(order_id, lab_user_id, result_text, result_value,
                      unit, normal_range, is_abnormal, remarks):
    conn = get_conn()
    try:
        conn.execute("""
            INSERT INTO lab_results
                (order_id, lab_user_id, result_text, result_value,
                 unit, normal_range, is_abnormal, remarks)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(order_id) DO UPDATE SET
                result_text  = excluded.result_text,
                result_value = excluded.result_value,
                unit         = excluded.unit,
                normal_range = excluded.normal_range,
                is_abnormal  = excluded.is_abnormal,
                remarks      = excluded.remarks
        """, (order_id, lab_user_id, result_text, result_value,
              unit, normal_range, is_abnormal, remarks))
        conn.execute("UPDATE lab_orders SET status='completed' WHERE id=?", (order_id,))
        conn.commit()
    finally:
        conn.close()


def get_workflow_stats():
    return {
        "pending_consults":   scalar("SELECT COUNT(*) FROM consultations WHERE status='pending'"),
        "completed_consults": scalar("SELECT COUNT(*) FROM consultations WHERE status='completed'"),
        "pending_labs":       scalar("SELECT COUNT(*) FROM lab_orders WHERE status='pending'"),
        "completed_labs":     scalar("SELECT COUNT(*) FROM lab_orders WHERE status='completed'"),
        "active_doctors":     scalar("SELECT COUNT(*) FROM doctor_profiles dp JOIN users u "
                                     "ON u.id = dp.user_id WHERE u.status='active' AND dp.available=1"),
        "lab_techs":          scalar("SELECT COUNT(*) FROM lab_profiles lp JOIN users u "
                                     "ON u.id = lp.user_id WHERE u.status='active'"),
    }


# Auto-run on import
init_workflow_tables()
