"""
database.py — SQLite DBMS layer for MediAI
Single-file database (mediai.db) — no server needed.
Also holds the RBAC tables (roles, permissions, role_permissions).
"""

import json
import secrets
import sqlite3
import datetime
import logging
from werkzeug.security import generate_password_hash, check_password_hash

import config

DB_PATH = config.DB_PATH
log = logging.getLogger("mediai.db")


# ── Type adapters / converters ────────────────────────────────────────────────
# Store dates as ISO text, read DATE / DATETIME columns back as Python objects
# (same behaviour the templates relied on with MySQL, e.g. .strftime()).

def _adapt_datetime(v):
    return v.strftime("%Y-%m-%d %H:%M:%S")


def _adapt_date(v):
    return v.isoformat()


def _convert_datetime(raw):
    s = raw.decode()
    try:
        return datetime.datetime.fromisoformat(s)
    except ValueError:
        return s


def _convert_date(raw):
    s = raw.decode()
    try:
        return datetime.date.fromisoformat(s[:10])
    except ValueError:
        return s


sqlite3.register_adapter(datetime.datetime, _adapt_datetime)
sqlite3.register_adapter(datetime.date, _adapt_date)
sqlite3.register_converter("DATETIME", _convert_datetime)
sqlite3.register_converter("DATE", _convert_date)


def _dict_factory(cursor, row):
    return {col[0]: row[i] for i, col in enumerate(cursor.description)}


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10,
                           detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = _dict_factory
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def query(sql, params=(), one=False):
    """Run a SELECT and return a list of dicts (or one dict / None)."""
    conn = get_conn()
    try:
        cur = conn.execute(sql, params)
        return cur.fetchone() if one else cur.fetchall()
    finally:
        conn.close()


def scalar(sql, params=()):
    row = query(sql, params, one=True)
    return next(iter(row.values())) if row else None


def execute(sql, params=()):
    """Run a single write statement, commit, return lastrowid."""
    conn = get_conn()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _since(days):
    """SQLite modifier for 'now minus N days' in local time."""
    return f"-{int(days)} days"


NOW = "(datetime('now','localtime'))"


# ── RBAC definitions ──────────────────────────────────────────────────────────

ROLES = {
    "patient": "Registered patient — runs diagnoses, keeps a diary, sees own consultations",
    "doctor":  "Clinician — reviews assigned consultations and orders lab tests",
    "lab":     "Lab technician — processes assigned lab orders",
    "admin":   "Administrator — verifies staff applications, manages staff and views analytics",
    "applicant": "Doctor / lab applicant awaiting verification — effective role of any "
                 "doctor or lab account that is not yet approved",
}

# Account status (users.status). Only `active` accounts get their role's permissions.
#   active    — normal account
#   pending   — doctor / lab applicant under verification (acts as `applicant`)
#   rejected  — application turned down (acts as `applicant`, read-only)
#   suspended — blocked by an admin, cannot log in
ACCOUNT_STATUSES = ("active", "pending", "rejected", "suspended")

PERMISSIONS = {
    "diagnosis.run":          "Run AI symptom diagnosis (form or chatbot)",
    "diagnosis.confirm":      "Confirm a diagnosis for adaptive learning",
    "report.export":          "Download / email diagnosis reports",
    "diary.manage":           "Log and view own symptom diary",
    "consultation.view_own":  "View own consultations as a patient",
    "consultation.review":    "Review and update assigned consultations",
    "consultation.view_all":  "View any consultation",
    "lab.order":              "Order lab tests for a consultation",
    "lab.process":            "View and submit results for assigned lab orders",
    "video.join":             "Join video calls for permitted consultations",
    "video.host":             "Start / end video calls",
    "analytics.view":         "View system-wide analytics",
    "staff.manage":           "Suspend / reactivate doctor and lab accounts",
    "staff.verify":           "Review staff applications, interview and approve / reject applicants",
    "application.manage_own": "View and update own staff application, join own interview",
    "rbac.view":              "View the role-permission matrix",
}

ROLE_PERMISSIONS = {
    "patient": ["diagnosis.run", "diagnosis.confirm", "report.export",
                "diary.manage", "consultation.view_own", "video.join"],
    "doctor":  ["consultation.review", "lab.order", "video.join", "video.host",
                "analytics.view"],
    "lab":     ["lab.process"],
    "admin":   ["consultation.view_all", "analytics.view", "staff.manage",
                "staff.verify", "rbac.view"],
    "applicant": ["application.manage_own"],
}


# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS roles (
    name        TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS permissions (
    name        TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS role_permissions (
    role       TEXT NOT NULL REFERENCES roles(name)       ON DELETE CASCADE,
    permission TEXT NOT NULL REFERENCES permissions(name) ON DELETE CASCADE,
    PRIMARY KEY (role, permission)
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER  PRIMARY KEY AUTOINCREMENT,
    username      TEXT     UNIQUE NOT NULL COLLATE NOCASE,
    password_hash TEXT     NOT NULL,
    role          TEXT     NOT NULL DEFAULT 'patient' REFERENCES roles(name),
    status        TEXT     NOT NULL DEFAULT 'active',
    created_at    DATETIME NOT NULL DEFAULT {NOW},
    updated_at    DATETIME NOT NULL DEFAULT {NOW}
);

CREATE TABLE IF NOT EXISTS patients (
    id         INTEGER  PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER  NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    full_name  TEXT     NOT NULL,
    age        INTEGER,
    gender     TEXT,
    city       TEXT,
    created_at DATETIME NOT NULL DEFAULT {NOW},
    updated_at DATETIME NOT NULL DEFAULT {NOW}
);

CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER  PRIMARY KEY AUTOINCREMENT,
    session_key TEXT     UNIQUE NOT NULL,
    user_id     INTEGER  DEFAULT NULL REFERENCES users(id) ON DELETE SET NULL,
    mode        TEXT     NOT NULL DEFAULT 'form',
    created_at  DATETIME NOT NULL DEFAULT {NOW},
    updated_at  DATETIME NOT NULL DEFAULT {NOW}
);

CREATE TABLE IF NOT EXISTS diagnoses (
    id          INTEGER  PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER  NOT NULL REFERENCES sessions(id),
    user_id     INTEGER  DEFAULT NULL REFERENCES users(id) ON DELETE SET NULL,
    symptoms    TEXT     NOT NULL,
    top_disease TEXT     NOT NULL,
    predictions TEXT     NOT NULL,
    risk_score  INTEGER  NOT NULL DEFAULT 0,
    confidence  REAL     NOT NULL DEFAULT 0,
    doctor_type TEXT,
    created_at  DATETIME NOT NULL DEFAULT {NOW},
    updated_at  DATETIME NOT NULL DEFAULT {NOW}
);

CREATE TABLE IF NOT EXISTS adaptive_cases (
    id         INTEGER  PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER  DEFAULT NULL REFERENCES users(id) ON DELETE SET NULL,
    symptoms   TEXT     NOT NULL,
    disease    TEXT     NOT NULL,
    confirmed  INTEGER  NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT {NOW}
);

CREATE TABLE IF NOT EXISTS doctor_map (
    disease    TEXT     PRIMARY KEY COLLATE NOCASE,
    specialty  TEXT     NOT NULL,
    updated_at DATETIME NOT NULL DEFAULT {NOW}
);

CREATE TABLE IF NOT EXISTS symptom_stats (
    symptom    TEXT     PRIMARY KEY,
    frequency  INTEGER  NOT NULL DEFAULT 0,
    updated_at DATETIME NOT NULL DEFAULT {NOW}
);

CREATE TABLE IF NOT EXISTS symptom_logs (
    id         INTEGER  PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER  NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    log_date   DATE     NOT NULL,
    symptoms   TEXT     NOT NULL,
    severity   INTEGER  NOT NULL DEFAULT 1,
    notes      TEXT,
    created_at DATETIME NOT NULL DEFAULT {NOW},
    UNIQUE (user_id, log_date)
);

CREATE INDEX IF NOT EXISTS idx_diagnoses_top_disease ON diagnoses(top_disease);
CREATE INDEX IF NOT EXISTS idx_diagnoses_created_at  ON diagnoses(created_at);
CREATE INDEX IF NOT EXISTS idx_diagnoses_user_id     ON diagnoses(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id      ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_adaptive_disease      ON adaptive_cases(disease);
CREATE INDEX IF NOT EXISTS idx_symptom_stats_freq    ON symptom_stats(frequency);
CREATE INDEX IF NOT EXISTS idx_logs_user_date        ON symptom_logs(user_id, log_date);

-- Views replace the old MySQL stored procedures
CREATE VIEW IF NOT EXISTS v_disease_distribution AS
    SELECT top_disease, COUNT(*) AS cnt
    FROM   diagnoses
    GROUP  BY top_disease;

CREATE VIEW IF NOT EXISTS v_patient_history AS
    SELECT d.id, d.user_id, d.top_disease, d.risk_score, d.confidence,
           d.doctor_type, d.created_at, s.mode
    FROM   diagnoses d
    JOIN   sessions  s ON s.id = d.session_id;
"""

# Tables with an updated_at column get a trigger emulating MySQL's
# "ON UPDATE CURRENT_TIMESTAMP".
UPDATED_AT_TABLES = ["users", "patients", "sessions", "diagnoses",
                     "doctor_map", "symptom_stats"]


def updated_at_trigger(table):
    return f"""
        CREATE TRIGGER IF NOT EXISTS trg_{table}_updated_at
        AFTER UPDATE ON {table}
        FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
        BEGIN
            UPDATE {table} SET updated_at = datetime('now','localtime')
            WHERE rowid = NEW.rowid;
        END;
    """


DOCTOR_MAP = [
    ("Malaria", "Infectious Disease Specialist"),
    ("Allergy", "Allergist"),
    ("Diabetes", "Endocrinologist"),
    ("Hypertension", "Cardiologist"),
    ("GERD", "Gastroenterologist"),
    ("Acne", "Dermatologist"),
    ("Psoriasis", "Dermatologist"),
    ("Typhoid", "General Physician"),
    ("Hepatitis A", "Hepatologist"),
    ("Hepatitis B", "Hepatologist"),
    ("Hepatitis C", "Hepatologist"),
    ("Hepatitis D", "Hepatologist"),
    ("Hepatitis E", "Hepatologist"),
    ("Alcoholic hepatitis", "Hepatologist"),
    ("Chronic cholestasis", "Hepatologist"),
    ("Urinary tract infection", "Urologist"),
    ("Migraine", "Neurologist"),
    ("(vertigo) Paroymsal  Positional Vertigo", "ENT Specialist"),
    ("Arthritis", "Rheumatologist"),
    ("Dengue", "Infectious Disease Specialist"),
    ("AIDS", "Infectious Disease Specialist"),
    ("Tuberculosis", "Pulmonologist"),
    ("Pneumonia", "Pulmonologist"),
    ("Bronchial Asthma", "Pulmonologist"),
    ("Varicose veins", "Vascular Surgeon"),
    ("Hypothyroidism", "Endocrinologist"),
    ("Hyperthyroidism", "Endocrinologist"),
    ("Hypoglycemia", "Endocrinologist"),
    ("Osteoarthristis", "Orthopedic Surgeon"),
    ("Paralysis (brain hemorrhage)", "Neurologist"),
    ("Jaundice", "Gastroenterologist"),
    ("Chicken pox", "Dermatologist"),
    ("Dimorphic hemmorhoids(piles)", "Gastroenterologist"),
    ("Drug Reaction", "Allergist"),
    ("Peptic ulcer diseae", "Gastroenterologist"),
    ("Fungal infection", "Dermatologist"),
    ("Common Cold", "General Physician"),
    ("Cervical spondylosis", "Orthopedic Surgeon"),
    ("Heart attack", "Cardiologist"),
    ("Gastroenteritis", "Gastroenterologist"),
    ("Impetigo", "Dermatologist"),
]


def add_column_if_missing(conn, table, column, ddl):
    """Lightweight migration: add a column to an existing table if absent."""
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        log.info("Migrated: added %s.%s", table, column)


def init_db():
    conn = get_conn()
    try:
        # WAL lets readers and a writer work concurrently (multi-user server)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        add_column_if_missing(conn, "users", "status", "TEXT NOT NULL DEFAULT 'active'")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_status ON users(status)")
        for t in UPDATED_AT_TABLES:
            conn.executescript(updated_at_trigger(t))

        # Seed / sync RBAC (idempotent — code is the source of truth)
        conn.executemany(
            "INSERT INTO roles (name, description) VALUES (?,?) "
            "ON CONFLICT(name) DO UPDATE SET description=excluded.description",
            ROLES.items())
        conn.executemany(
            "INSERT INTO permissions (name, description) VALUES (?,?) "
            "ON CONFLICT(name) DO UPDATE SET description=excluded.description",
            PERMISSIONS.items())
        conn.execute("DELETE FROM role_permissions")
        conn.executemany(
            "INSERT INTO role_permissions (role, permission) VALUES (?,?)",
            [(r, p) for r, perms in ROLE_PERMISSIONS.items() for p in perms])

        conn.executemany(
            "INSERT OR IGNORE INTO doctor_map (disease, specialty) VALUES (?,?)",
            DOCTOR_MAP)

        if not conn.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
            password = config.ADMIN_PASSWORD
            if password is None:
                password = secrets.token_urlsafe(12)
                log.warning("Created admin account - username: admin  password: %s  "
                            "(shown once; change it or set MEDIAI_ADMIN_PASSWORD)", password)
            conn.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?,?,'admin')",
                ("admin", generate_password_hash(password)))
        conn.commit()
    finally:
        conn.close()
    log.info("SQLite database ready at %s", DB_PATH)


# ── RBAC queries ──────────────────────────────────────────────────────────────

def change_password(user_id, new_password):
    execute("UPDATE users SET password_hash=? WHERE id=?",
            (generate_password_hash(new_password), user_id))


def verify_password(user_id, password):
    row = query("SELECT password_hash FROM users WHERE id=?", (user_id,), one=True)
    return bool(row) and check_password_hash(row["password_hash"], password)


def get_role_permissions(role):
    rows = query("SELECT permission FROM role_permissions WHERE role=?", (role,))
    return {r["permission"] for r in rows}


def get_permission_matrix():
    """Returns (roles, permissions, set of (role, permission) grants)."""
    roles = query("SELECT name, description FROM roles ORDER BY "
                  "CASE name WHEN 'patient' THEN 1 WHEN 'doctor' THEN 2 WHEN 'applicant' THEN 5 "
                  "WHEN 'lab' THEN 3 WHEN 'admin' THEN 4 ELSE 5 END")
    perms = query("SELECT name, description FROM permissions ORDER BY name")
    grants = {(r["role"], r["permission"])
              for r in query("SELECT role, permission FROM role_permissions")}
    return roles, perms, grants


# ── Auth ──────────────────────────────────────────────────────────────────────

def register_user(username, password, role="patient"):
    try:
        uid = execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
            (username, generate_password_hash(password), role))
        return uid, None
    except sqlite3.IntegrityError:
        return None, "Username already taken."


def effective_role(user):
    """Role whose permissions apply: unverified staff act as `applicant`."""
    if user["status"] == "active":
        return user["role"]
    if user["status"] in ("pending", "rejected"):
        return "applicant"
    return None          # suspended — no access at all


def get_user_access(user_id):
    """(user dict, effective role, permission set) — or (None, None, set())."""
    user = query("SELECT id, username, role, status FROM users WHERE id=?", (user_id,), one=True)
    if not user:
        return None, None, set()
    role = effective_role(user)
    return user, role, (get_role_permissions(role) if role else set())


def login_user(username, password):
    row = query("SELECT id, username, role, status, password_hash FROM users WHERE username=?",
                (username,), one=True)
    if not row or not check_password_hash(row["password_hash"], password):
        return None
    row.pop("password_hash")
    return row


def save_patient_profile(user_id, full_name, age, gender, city):
    execute("""
        INSERT INTO patients (user_id, full_name, age, gender, city)
        VALUES (?,?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET
            full_name=excluded.full_name, age=excluded.age,
            gender=excluded.gender, city=excluded.city
    """, (user_id, full_name, age, gender, city))


def get_patient_profile(user_id):
    return query("SELECT * FROM patients WHERE user_id=?", (user_id,), one=True)


# ── Sessions ──────────────────────────────────────────────────────────────────

def create_session(session_key, mode, user_id=None):
    conn = get_conn()
    try:
        conn.execute("""
            INSERT INTO sessions (session_key, mode, user_id) VALUES (?,?,?)
            ON CONFLICT(session_key) DO UPDATE SET
                mode=excluded.mode, user_id=excluded.user_id
        """, (session_key, mode, user_id))
        conn.commit()
        return conn.execute("SELECT id FROM sessions WHERE session_key=?",
                            (session_key,)).fetchone()["id"]
    finally:
        conn.close()


# ── Diagnoses ─────────────────────────────────────────────────────────────────

def save_diagnosis(session_id, symptoms, predictions, risk_score, doctor_type, user_id=None):
    top_disease = predictions[0][0] if predictions else "Unknown"
    confidence  = round(float(predictions[0][1]) * 100, 1) if predictions else 0
    conn = get_conn()
    try:
        cur = conn.execute("""
            INSERT INTO diagnoses
                (session_id, user_id, symptoms, top_disease, predictions,
                 risk_score, confidence, doctor_type)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            session_id, user_id,
            json.dumps(symptoms),
            str(top_disease),
            json.dumps([(str(d), round(float(p), 4)) for d, p in predictions]),
            int(risk_score), confidence, doctor_type
        ))
        diag_id = cur.lastrowid
        conn.executemany("""
            INSERT INTO symptom_stats (symptom, frequency) VALUES (?, 1)
            ON CONFLICT(symptom) DO UPDATE SET frequency = frequency + 1
        """, [(s,) for s in symptoms])
        conn.commit()
        return diag_id
    finally:
        conn.close()


def get_recent_diagnoses(limit=10, days=None):
    base = """
        SELECT d.*, s.mode FROM diagnoses d
        JOIN sessions s ON s.id = d.session_id
    """
    if days:
        return query(base + """
            WHERE d.created_at >= datetime('now','localtime', ?)
            ORDER BY d.created_at DESC LIMIT ?
        """, (_since(days), limit))
    return query(base + " ORDER BY d.created_at DESC LIMIT ?", (limit,))


def get_patient_history(user_id):
    return query("SELECT * FROM v_patient_history WHERE user_id=? "
                 "ORDER BY created_at DESC", (user_id,))


def get_top_symptoms(limit=10):
    return query("SELECT symptom, frequency FROM symptom_stats "
                 "ORDER BY frequency DESC LIMIT ?", (limit,))


def get_disease_distribution(limit=10):
    return query("SELECT top_disease, cnt FROM v_disease_distribution "
                 "ORDER BY cnt DESC LIMIT ?", (limit,))


def get_stats_summary(days=None):
    if days:
        total_diagnoses = scalar(
            "SELECT COUNT(*) FROM diagnoses WHERE created_at >= datetime('now','localtime', ?)",
            (_since(days),))
    else:
        total_diagnoses = scalar("SELECT COUNT(*) FROM diagnoses")
    return {
        "total_diagnoses": total_diagnoses,
        "total_sessions":  scalar("SELECT COUNT(*) FROM sessions"),
        "total_patients":  scalar("SELECT COUNT(*) FROM users WHERE role='patient'"),
        "total_adaptive":  scalar("SELECT COUNT(*) FROM adaptive_cases"),
    }


def get_daily_diagnoses(days=7):
    rows = query("""
        SELECT DATE(created_at) AS date, COUNT(*) AS cnt
        FROM   diagnoses
        WHERE  created_at >= datetime('now','localtime', ?)
        GROUP  BY DATE(created_at)
        ORDER  BY date ASC
    """, (_since(days),))
    return [{"date": str(r["date"]), "cnt": r["cnt"]} for r in rows]


# ── Adaptive Cases ────────────────────────────────────────────────────────────

def store_case_db(symptoms, disease, user_id=None):
    execute("INSERT INTO adaptive_cases (symptoms, disease, user_id) VALUES (?,?,?)",
            (",".join(symptoms), disease, user_id))


# ── Doctor Map ────────────────────────────────────────────────────────────────

def get_doctor_db(disease):
    row = query("SELECT specialty FROM doctor_map WHERE disease=?",
                (disease.strip(),), one=True)
    return row["specialty"] if row else "General Physician"


# ── Auto-run on import ────────────────────────────────────────────────────────
init_db()
