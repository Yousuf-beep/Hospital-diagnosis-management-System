"""
app.py — MediAI Complete Flask Application
"""

import os, sys, json, time, hmac, uuid, secrets, logging, datetime

try:
    import flask, flask_socketio, flask_mail, pandas, sklearn, reportlab  # noqa: F401
except ImportError as e:
    sys.exit(
        f"\nMissing package '{e.name}' for this Python ({sys.executable}).\n"
        "Run the app with the project's virtual environment instead:\n"
        "    .venv\\Scripts\\python app.py\n"
        "or install the dependencies into this Python:\n"
        "    python -m pip install -r requirements.txt\n")

from collections import defaultdict, deque
from functools import wraps
from flask import (Flask, render_template, request, session, g,
                   jsonify, redirect, url_for, flash, Response)
from flask_socketio import SocketIO, emit, join_room, leave_room, disconnect
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
import pandas as pd

import config

DATASET_DIR = os.path.join(config.BASE_DIR, "dataset")
log = logging.getLogger("mediai")

from ml_model import predict_disease, symptom_columns, get_confidence
from chatbot_engine import (next_question, parse_free_text,
                             get_severity_question, get_severity_options,
                             get_possible_count)
from database import (
    create_session, save_diagnosis, store_case_db, get_doctor_db,
    get_recent_diagnoses, get_top_symptoms, get_disease_distribution,
    get_stats_summary, get_daily_diagnoses, get_patient_history,
    register_user, login_user, save_patient_profile, get_patient_profile,
    get_permission_matrix, query,
    change_password, verify_password, get_user_access
)
from mail import configure_mail, send_diagnosis_email, MailNotConfigured, notify
from regional_intelligence import get_regional_alerts, check_emergency
from symptom_diary import log_symptoms, get_diary, get_trend_data
from pdf_report import generate_report
from workflow import (
    get_all_doctors, get_all_lab_techs, suspend_staff, reactivate_staff,
    get_doctor_profile, get_lab_profile, auto_assign_doctor,
    create_consultation, get_doctor_consultations, get_consultation,
    update_consultation, get_patient_consultations, set_video_state,
    create_lab_order, get_lab_orders_for_consultation,
    get_lab_tech_orders, get_lab_order_for_tech, submit_lab_result,
    get_workflow_stats, LAB_TEST_TYPES
)
from staff_onboarding import (
    submit_application, resubmit_application, get_application,
    get_application_for_user, list_applications, application_counts,
    get_events, save_checklist, save_internal_notes, transition,
    can_join_interview, INTERVIEW_MODES, STATUS_LABELS
)

app = Flask(__name__)
app.config.update(
    SECRET_KEY=config.SECRET_KEY,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=config.HTTPS,
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(hours=8),
    MAX_CONTENT_LENGTH=1 * 1024 * 1024,      # 1 MB — forms only, no uploads
    TEMPLATES_AUTO_RELOAD=config.DEBUG,
)
if os.environ.get("MEDIAI_TRUST_PROXY", "").lower() in ("1", "true", "yes"):
    # Behind nginx / a load balancer: trust X-Forwarded-* for client IP and scheme
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# serve.py switches this to "gevent" for production; dev uses threads
ASYNC_MODE = os.environ.get("MEDIAI_ASYNC_MODE", "threading")
socketio = SocketIO(app, async_mode=ASYNC_MODE, cors_allowed_origins=config.CORS_ORIGINS)
app.jinja_env.filters["from_json"] = json.loads
configure_mail(app)


# ── Dataset lookups (normalised names) ────────────────────────────────────────
# The CSVs disagree on spacing/case/spelling of a few diseases
# (e.g. "hepatitis A" vs "Hepatitis A", "Diabetes " with a trailing space).

def _norm(name):
    return " ".join(str(name).split()).lower()


DISEASE_ALIASES = {
    _norm("Dimorphic hemmorhoids(piles)"): _norm("Dimorphic hemorrhoids(piles)"),
}

description_df = pd.read_csv(os.path.join(DATASET_DIR, "symptom_Description.csv"))
precaution_df  = pd.read_csv(os.path.join(DATASET_DIR, "symptom_precaution.csv"))
severity_df    = pd.read_csv(os.path.join(DATASET_DIR, "Symptom-severity.csv"))

DESCRIPTIONS = {_norm(r.Disease): r.Description for r in description_df.itertuples()}
PRECAUTIONS  = {_norm(row.iloc[0]): [p.strip() for p in row.iloc[1:5]
                                     if isinstance(p, str) and p.strip()]
                for _, row in precaution_df.iterrows()}
SEVERITY     = {str(s).strip(): int(w) for s, w in zip(severity_df["Symptom"], severity_df["weight"])}

SYMPTOM_SET = set(symptom_columns)

FALLBACK_POOL = [
    "nausea", "headache", "high_fever", "chills", "loss_of_appetite",
    "muscle_weakness", "dizziness", "sweating", "skin_rash", "back_pain",
    "fatigue", "cough", "breathlessness", "joint_pain", "stomach_pain",
]


# ── Security: CSRF, headers, login throttling ─────────────────────────────────

def csrf_token():
    token = session.get("_csrf")
    if not token:
        token = session["_csrf"] = secrets.token_urlsafe(32)
    return token


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def csrf_protect():
    """Every state-changing request must echo the session's CSRF token, either
    as a `csrf_token` form field or an `X-CSRFToken` header (base.html adds
    both automatically to forms and fetch() calls)."""
    if request.method in ("GET", "HEAD", "OPTIONS") or request.path.startswith("/socket.io"):
        return
    sent     = request.form.get("csrf_token") or request.headers.get("X-CSRFToken") or ""
    expected = session.get("_csrf", "")
    if not expected or not hmac.compare_digest(sent, expected):
        log.warning("CSRF check failed for %s %s", request.method, request.path)
        if _wants_json():
            return jsonify({"ok": False, "msg": "Session expired — reload the page."}), 400
        flash("Your session expired — please try again.", "warning")
        return redirect(request.referrer if _same_origin(request.referrer) else url_for("login"))


def _same_origin(url):
    return bool(url) and url.startswith(request.host_url)


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(self), microphone=(self), geolocation=()")
    if config.HTTPS:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if g.get("user"):
        resp.headers.setdefault("Cache-Control", "no-store")   # medical data — no caching
    return resp


_login_failures = defaultdict(deque)   # (ip, username) -> failure timestamps


def _login_key(username):
    return (request.remote_addr or "?", username.lower())


def _login_locked(username):
    q, cutoff = _login_failures[_login_key(username)], time.time() - config.LOGIN_WINDOW_SECONDS
    while q and q[0] < cutoff:
        q.popleft()
    return len(q) >= config.LOGIN_MAX_ATTEMPTS


MIN_PASSWORD_LEN = 8


# ── RBAC ──────────────────────────────────────────────────────────────────────

ROLE_HOME = {"doctor": "doctor_dashboard", "lab": "lab_dashboard",
             "admin": "admin_dashboard", "patient": "home",
             "applicant": "application_status"}


@app.before_request
def load_current_user():
    """Re-validate the session user on every request so role changes,
    approvals, suspensions and deleted accounts take effect immediately.
    session["role"] holds the *effective* role: doctor / lab accounts that are
    not yet verified act as `applicant`."""
    g.user, g.permissions = None, set()
    uid = session.get("user_id")
    if uid is None:
        return
    user, role, perms = get_user_access(uid)
    if not user or role is None:
        session.clear()
        if user:
            flash("Your account has been suspended. Please contact the administrator.", "danger")
        return
    session["role"] = role
    g.user, g.permissions = user, perms


def has_permission(perm):
    return perm in g.get("permissions", set())


@app.context_processor
def inject_rbac():
    return {"can": has_permission, "current_role": session.get("role")}


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not g.user:
            flash("Please log in first.", "warning")
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated


def _wants_json():
    """Endpoints called via fetch() get JSON errors instead of redirects."""
    return request.path.startswith(("/api/", "/video/start/", "/video/end/", "/send_report"))


def permission_required(*perms):
    """Allow the request if the user holds ANY of the given permissions."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not g.user:
                if _wants_json():
                    return jsonify({"ok": False, "msg": "Login required."}), 401
                flash("Please log in first.", "warning")
                return redirect(url_for("login", next=request.path))
            if not any(has_permission(p) for p in perms):
                if _wants_json():
                    return jsonify({"ok": False, "msg": "Access denied."}), 403
                flash("Access denied — your role cannot access that page.", "danger")
                return redirect(url_for(ROLE_HOME.get(session.get("role"), "login")))
            return f(*args, **kwargs)
        return decorated
    return decorator


def can_access_consultation(c, uid=None, perms=None):
    """Object-level check: admin sees all, doctors their assigned cases,
    patients their own."""
    if not c:
        return False
    uid   = uid if uid is not None else session.get("user_id")
    perms = perms if perms is not None else g.permissions
    if "consultation.view_all" in perms:
        return True
    if "consultation.review" in perms and c["doctor_user_id"] == uid:
        return True
    if "consultation.view_own" in perms and c["patient_user_id"] == uid:
        return True
    return False


def can_join_video(c, uid, perms):
    if "video.join" not in perms or not c:
        return False
    return c["doctor_user_id"] == uid or c["patient_user_id"] == uid


# ── Core helpers ──────────────────────────────────────────────────────────────

def _compute_risk(selected):
    return sum(SEVERITY.get(s, 0) for s in selected)


def _enrich_result(top_disease, selected):
    key = _norm(top_disease)
    key = DISEASE_ALIASES.get(key, key)
    desc        = DESCRIPTIONS.get(key, "No description available.")
    precautions = PRECAUTIONS.get(_norm(top_disease)) or PRECAUTIONS.get(key, [])
    doctor = get_doctor_db(top_disease)
    risk   = _compute_risk(selected)
    return desc, precautions, doctor, risk


def _run_prediction(selected, mode):
    """Run ML prediction, save to DB, auto-assign doctor. Returns template context."""
    predictions  = predict_disease(selected)
    top_disease  = predictions[0][0]
    confidence   = get_confidence(predictions)
    desc, precautions, doctor_specialty, risk = _enrich_result(top_disease, selected)
    emergency       = check_emergency(selected)
    user_id         = session.get("user_id")
    profile         = get_patient_profile(user_id) if user_id else None
    city            = (profile or {}).get("city") or ""
    regional_alerts = get_regional_alerts(city, predictions)

    skey    = str(uuid.uuid4())
    sid     = create_session(skey, mode, user_id)
    diag_id = save_diagnosis(sid, selected, predictions, risk, doctor_specialty, user_id)

    # Auto-assign to a doctor (only patients with a profile can be consulted)
    assigned_doctor = None
    if user_id and diag_id and profile:
        doc_uid = auto_assign_doctor(doctor_specialty)
        if doc_uid:
            create_consultation(diag_id, user_id, doc_uid)
            assigned_doctor = get_doctor_profile(doc_uid)

    return dict(
        predictions=predictions, disease=top_disease,
        description=desc, precautions=precautions,
        doctor=doctor_specialty, risk=risk, symptoms=selected,
        confidence=confidence, emergency=emergency,
        regional_alerts=regional_alerts, city=city,
        assigned_doctor=assigned_doctor,
    )


def _to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username  = request.form.get("username", "").strip()
        password  = request.form.get("password", "")
        full_name = request.form.get("full_name", "").strip()
        age       = _to_int(request.form.get("age"), None)
        gender    = request.form.get("gender", "")
        city      = request.form.get("city", "").strip()
        if not username or not password or not full_name:
            flash("Username, password and full name are required.", "danger")
            return render_template("register.html")
        if len(password) < MIN_PASSWORD_LEN:
            flash(f"Password must be at least {MIN_PASSWORD_LEN} characters.", "danger")
            return render_template("register.html")
        # Public registration always creates a patient — staff are created by admins
        uid, err = register_user(username, password, "patient")
        if err:
            flash(err, "danger")
            return render_template("register.html")
        save_patient_profile(uid, full_name, age, gender or None, city or None)
        flash("Account created! Please log in.", "success")
        return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if _login_locked(username):
            flash("Too many failed attempts — try again in a few minutes.", "danger")
            return render_template("login.html"), 429
        user = login_user(username, password)
        if user and user["status"] == "suspended":
            flash("This account has been suspended. Please contact the administrator.", "danger")
            return render_template("login.html"), 403
        if not user:
            _login_failures[_login_key(username)].append(time.time())
            log.info("Failed login for %r from %s", username, request.remote_addr)
            flash("Invalid username or password.", "danger")
            return render_template("login.html")
        _login_failures.pop(_login_key(username), None)
        session.clear()
        session.permanent   = True
        session["user_id"]  = user["id"]
        session["username"] = user["username"]
        role = "applicant" if user["status"] in ("pending", "rejected") else user["role"]
        session["role"]     = role
        nxt = request.args.get("next", "")
        if nxt.startswith("/") and not nxt.startswith("//"):
            return redirect(nxt)
        if role == "patient":
            flash(f"Welcome back, {user['username']}!", "success")
        return redirect(url_for(ROLE_HOME.get(role, "home")))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("login"))


# ── Staff self-registration & verification (applicant side) ──────────────────

def _specialties():
    return [r["specialty"] for r in
            query("SELECT DISTINCT specialty FROM doctor_map ORDER BY specialty")]


def _parse_staff_form(role, form):
    """Validate the doctor / lab application form. Returns (profile, app, errors)."""
    f = lambda k: (form.get(k) or "").strip()
    errors = []
    profile = {"full_name": f("full_name"), "phone": f("phone"),
               "qualification": f("qualification"),
               "experience_yrs": _to_int(form.get("experience_yrs"), -1)}
    app_data = {"email": f("email"), "phone": f("phone"),
                "license_number": f("license_number"),
                "license_authority": f("license_authority"),
                "statement": f("statement")[:2000]}
    if not profile["full_name"]:
        errors.append("Full name is required.")
    if "@" not in app_data["email"] or len(app_data["email"]) > 254:
        errors.append("A valid email address is required.")
    if len(profile["phone"]) < 7:
        errors.append("A valid phone number is required.")
    if not profile["qualification"]:
        errors.append("Qualification is required.")
    if not 0 <= profile["experience_yrs"] <= 60:
        errors.append("Experience must be between 0 and 60 years.")
    if not app_data["license_number"] or not app_data["license_authority"]:
        errors.append("Registration / licence number and issuing authority are required.")

    if role == "doctor":
        profile["specialty"] = f("specialty")
        profile["hospital"]  = f("hospital")
        if profile["specialty"] not in _specialties():
            errors.append("Choose a specialty from the list.")
    else:
        tests = [t for t in form.getlist("tests_offered") if t in LAB_TEST_TYPES]
        profile.update(lab_name=f("lab_name"), lab_address=f("lab_address"),
                       lab_city=f("lab_city"), lab_phone=f("lab_phone"),
                       accreditation_number=f("accreditation_number"),
                       accreditation_body=f("accreditation_body"),
                       operating_hours=f("operating_hours"), tests_offered=tests)
        if not profile["lab_name"] or not profile["lab_address"] or not profile["lab_city"]:
            errors.append("Lab name, address and city are required.")
        if not tests:
            errors.append("Select at least one test your lab performs.")
    return profile, app_data, errors


@app.route("/apply", methods=["GET", "POST"])
def apply():
    if g.user:
        return redirect(url_for(ROLE_HOME.get(session.get("role"), "home")))
    role = request.values.get("role", "doctor")
    if role not in ("doctor", "lab"):
        role = "doctor"
    ctx = dict(role=role, specialties=_specialties(), lab_tests=LAB_TEST_TYPES,
               form=request.form)
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        profile, app_data, errors = _parse_staff_form(role, request.form)
        if not username or len(username) > 64:
            errors.insert(0, "Choose a username.")
        if len(password) < MIN_PASSWORD_LEN:
            errors.insert(0, f"Password must be at least {MIN_PASSWORD_LEN} characters.")
        if not request.form.get("declaration"):
            errors.append("Please confirm the declaration.")
        if not errors:
            uid, err = submit_application(role, username, password, profile, app_data)
            if err:
                errors.append(err)
            else:
                session.clear()
                session.permanent   = True
                session["user_id"]  = uid
                session["username"] = username
                session["role"]     = "applicant"
                notify(app_data["email"], "MediAI — application received",
                       "We received your application",
                       f"Hello {profile['full_name']},\n\nThank you for applying to join MediAI as a "
                       f"{'doctor' if role == 'doctor' else 'lab technician'}. Our verification team "
                       "will review your details and may invite you to an interview. You can track "
                       "progress any time by logging in.")
                flash("Application submitted! We'll verify your details and keep you updated here.", "success")
                return redirect(url_for("application_status"))
        for e in errors:
            flash(e, "danger")
        return render_template("apply.html", **ctx), 400
    return render_template("apply.html", **ctx)


@app.route("/application")
@permission_required("application.manage_own")
def application_status():
    a = get_application_for_user(session["user_id"])
    if not a:
        flash("No application found for this account.", "warning")
        return redirect(url_for("logout"))
    return render_template("application_status.html", a=a,
                           events=get_events(a["id"]),
                           specialties=_specialties(), lab_tests=LAB_TEST_TYPES)


@app.route("/application/update", methods=["POST"])
@permission_required("application.manage_own")
def application_update():
    a = get_application_for_user(session["user_id"])
    if not a:
        return redirect(url_for("application_status"))
    profile, app_data, errors = _parse_staff_form(a["role"], request.form)
    if not errors:
        err = resubmit_application(session["user_id"], profile, app_data)
        if err:
            errors.append(err)
    if errors:
        for e in errors:
            flash(e, "danger")
    else:
        flash("Application updated and resubmitted for review.", "success")
    return redirect(url_for("application_status"))


@app.route("/interview/<int:aid>/call")
@permission_required("application.manage_own", "staff.verify")
def interview_call(aid):
    a = get_application(aid)
    if not can_join_interview(a, session["user_id"], g.permissions):
        flash("This interview is not available.", "warning")
        return redirect(url_for(ROLE_HOME.get(session.get("role"), "home")))
    is_host = "staff.verify" in g.permissions
    call = dict(kind="interview", id=aid, title="Verification Interview", label="Application",
                is_host=is_host, host_label="Interviewer", guest_label="Applicant",
                host_icon="fa-user-shield",
                host_name=session["username"] if is_host else "MediAI Verification Team",
                guest_name=a["full_name"], start_url=None, end_url=None,
                return_url=url_for("admin_application", aid=aid) if is_host
                           else url_for("application_status"))
    return render_template("video_call.html", call=call, role=session.get("role"),
                           username=session.get("username"),
                           started_at=a["interview_at"].strftime("%d %b %Y, %I:%M %p")
                                      if hasattr(a["interview_at"], "strftime") else None)


# ── Home & Diagnosis ──────────────────────────────────────────────────────────

@app.route("/")
@login_required
def home():
    if not has_permission("diagnosis.run"):
        return redirect(url_for(ROLE_HOME.get(session.get("role"), "login")))
    return render_template("index.html", symptoms=symptom_columns)


@app.route("/predict_form", methods=["POST"])
@permission_required("diagnosis.run")
def predict_form():
    selected = [s for s in request.form.getlist("symptom") if s in SYMPTOM_SET]
    if len(selected) < 3:
        return render_template("index.html", symptoms=symptom_columns,
                               error="Please select at least 3 symptoms.")
    ctx = _run_prediction(selected, "form")
    return render_template("result.html", **ctx)


# ── Chatbot ───────────────────────────────────────────────────────────────────

@app.route("/chatbot")
@permission_required("diagnosis.run")
def chatbot():
    return render_template("chatbot.html", phase="start", symptoms=list(symptom_columns))


@app.route("/chat_start", methods=["POST"])
@permission_required("diagnosis.run")
def chat_start():
    raw = request.form.get("first_symptom", "").strip()
    if not raw:
        return render_template("chatbot.html", phase="start",
                               symptoms=list(symptom_columns),
                               error="Please enter your main symptom.")
    first_symptom = parse_free_text(raw)[:64]
    # Chat state lives in the signed session cookie, not server memory,
    # so it survives restarts and never grows unbounded.
    state = {
        "asked": [first_symptom], "positive": [first_symptom],
        "negative": [], "severity_notes": {}, "step": 1
    }
    q, _ = next_question([first_symptom], [first_symptom], [])
    if q is None:
        q = next((s for s in FALLBACK_POOL if s != first_symptom), "nausea")
    state["asked"].append(q)
    state["step"] = 2
    session["chat"] = state
    sev_q    = get_severity_question(q)
    sev_opts = get_severity_options(sev_q) if sev_q else []
    possible = get_possible_count([first_symptom], [first_symptom], [])
    return render_template("chatbot.html", phase="chat", question=q,
                           first_symptom=first_symptom, sev_q=sev_q,
                           sev_opts=sev_opts, step=2, possible=possible)


@app.route("/chat_answer", methods=["POST"])
@permission_required("diagnosis.run")
def chat_answer():
    state   = session.get("chat")
    symptom = request.form.get("symptom", "").strip()
    # Only accept an answer to a question we actually asked
    if state is None or not symptom or symptom not in state["asked"]:
        flash("Your chat session expired — please start again.", "warning")
        return redirect(url_for("chatbot"))
    ans      = request.form.get("answer", "no")
    sev_note = request.form.get("severity_note", "")
    if ans == "yes":
        if symptom not in state["positive"]:
            state["positive"].append(symptom)
        if sev_note:
            state["severity_notes"][symptom] = sev_note[:100]
    elif symptom not in state["negative"]:
        state["negative"].append(symptom)
    # Mark this symptom as done BEFORE finding the next question
    # so next_question() never returns the same symptom again
    if symptom not in state["asked"]:
        state["asked"].append(symptom)
    state["step"] = state.get("step", 1) + 1
    q, _     = next_question(state["asked"], state["positive"], state["negative"])
    possible = get_possible_count(state["asked"], state["positive"], state["negative"])

    def finish(symptoms):
        session["last_severity_notes"] = state.get("severity_notes", {})
        session.pop("chat", None)
        ctx = _run_prediction(symptoms, "chatbot")
        return render_template("result.html", **ctx)

    # If no more useful questions AND enough symptoms → diagnose
    if (q is None or len(state["asked"]) > 12) and len(state["positive"]) >= 3:
        return finish(state["positive"])

    # Fallback: pick the next unasked common symptom — never repeat one
    if q is None:
        q = next((s for s in FALLBACK_POOL if s not in state["asked"]), None)
        if q is None:
            return finish(state["positive"] or ["fatigue"])

    state["asked"].append(q)
    session["chat"] = state
    sev_q    = get_severity_question(q)
    sev_opts = get_severity_options(sev_q) if sev_q else []
    msg = ("Please confirm at least 3 symptoms for a better diagnosis."
           if len(state["positive"]) < 3 else None)
    return render_template("chatbot.html", phase="chat", question=q,
                           message=msg, sev_q=sev_q, sev_opts=sev_opts,
                           step=state["step"], possible=possible)


@app.route("/confirm", methods=["POST"])
@permission_required("diagnosis.confirm")
def confirm():
    disease  = request.form.get("disease", "").strip()
    symptoms = [s for s in request.form.get("symptoms", "").split(",") if s]
    if not disease or not symptoms:
        flash("Nothing to confirm.", "warning")
        return redirect(url_for("home"))
    store_case_db(symptoms, disease, session.get("user_id"))
    return render_template("confirmed.html", disease=disease)


# ── PDF & Email ───────────────────────────────────────────────────────────────

@app.route("/download_report", methods=["POST"])
@permission_required("report.export")
def download_report():
    disease     = request.form.get("disease", "")
    confidence  = _to_float(request.form.get("confidence"))
    risk        = _to_int(request.form.get("risk"))
    doctor      = request.form.get("doctor", "")
    symptoms    = [s for s in request.form.get("symptoms", "").split(",") if s]
    precautions = [p for p in request.form.get("precautions", "").split("|") if p]
    description = request.form.get("description", "")
    city        = request.form.get("city", "")
    try:
        predictions = json.loads(request.form.get("predictions", "[]"))
    except ValueError:
        predictions = []
    user_id         = session["user_id"]
    profile         = get_patient_profile(user_id)
    diary           = get_diary(user_id, 14)
    regional_alerts = get_regional_alerts(city, predictions) if predictions else []
    sev_notes       = session.get("last_severity_notes", {})

    # Fetch assigned doctor from most recent consultation
    assigned_doctor = None
    consults = get_patient_consultations(user_id)
    if consults and consults[0].get("doctor_user_id"):
        assigned_doctor = get_doctor_profile(consults[0]["doctor_user_id"])

    pdf_bytes = generate_report(
        patient_name = profile["full_name"] if profile else session.get("username", "Patient"),
        age          = profile["age"]    if profile else None,
        gender       = profile["gender"] if profile else None,
        city         = profile["city"]   if profile else city,
        disease=disease, confidence=confidence, risk=risk, doctor=doctor,
        symptoms=symptoms, predictions=predictions, precautions=precautions,
        description=description, diary=diary,
        regional_alerts=regional_alerts, severity_notes=sev_notes,
        assigned_doctor=assigned_doctor,
    )
    safe_name = "".join(ch if ch.isalnum() else "_" for ch in disease) or "Report"
    return Response(pdf_bytes, mimetype="application/pdf",
                    headers={"Content-Disposition":
                             f"attachment; filename=MediAI_{safe_name}.pdf"})


@app.route("/send_report", methods=["POST"])
@permission_required("report.export")
def send_report():
    email       = request.form.get("email", "").strip()
    disease     = request.form.get("disease", "")
    confidence  = _to_float(request.form.get("confidence"))
    doctor      = request.form.get("doctor", "")
    risk        = _to_int(request.form.get("risk"))
    symptoms    = [s for s in request.form.get("symptoms", "").split(",") if s]
    precautions = [p for p in request.form.get("precautions", "").split("|") if p]
    profile     = get_patient_profile(session["user_id"])
    name        = profile["full_name"] if profile else "Patient"
    if not email:
        return jsonify({"ok": False, "msg": "No email provided."})
    if "@" not in email or len(email) > 254:
        return jsonify({"ok": False, "msg": "Please enter a valid email address."})
    try:
        send_diagnosis_email(email, name, disease, confidence, doctor, precautions, risk, symptoms)
        return jsonify({"ok": True, "msg": f"Report sent to {email}"})
    except MailNotConfigured as e:
        return jsonify({"ok": False, "msg": str(e)})
    except Exception:
        log.exception("Sending report email failed")
        return jsonify({"ok": False, "msg": "Could not send the email right now. Please try again later."})


# ── Diary ─────────────────────────────────────────────────────────────────────

@app.route("/diary")
@permission_required("diary.manage")
def diary():
    user_id = session["user_id"]
    trend   = get_trend_data(user_id, 14)
    entries = get_diary(user_id, 30)
    return render_template("diary.html", trend=trend, entries=entries,
                           symptoms=list(symptom_columns))


@app.route("/diary/log", methods=["POST"])
@permission_required("diary.manage")
def diary_log():
    user_id  = session["user_id"]
    log_date = request.form.get("log_date", "")
    symptoms = request.form.getlist("symptoms")
    severity = min(max(_to_int(request.form.get("severity"), 1), 1), 5)
    notes    = request.form.get("notes", "").strip()
    if log_date and symptoms:
        log_symptoms(user_id, log_date, symptoms, severity, notes)
        flash("Symptoms logged successfully!", "success")
    else:
        flash("Please select at least one symptom and a date.", "warning")
    return redirect(url_for("diary"))


# ── Profile ───────────────────────────────────────────────────────────────────

@app.route("/profile")
@login_required
def profile():
    user_id = session["user_id"]
    role    = session.get("role")
    account = g.user["role"]
    staff   = (get_doctor_profile(user_id) if account == "doctor"
               else get_lab_profile(user_id) if account == "lab" else None)
    return render_template("profile.html",
                           patient=get_patient_profile(user_id),
                           staff=staff,
                           history=get_patient_history(user_id) if role == "patient" else [],
                           permissions=sorted(g.permissions))


@app.route("/profile/password", methods=["POST"])
@login_required
def profile_change_password():
    current = request.form.get("current_password", "")
    new     = request.form.get("new_password", "")
    if not verify_password(session["user_id"], current):
        flash("Current password is incorrect.", "danger")
    elif len(new) < MIN_PASSWORD_LEN:
        flash(f"New password must be at least {MIN_PASSWORD_LEN} characters.", "danger")
    elif new != request.form.get("confirm_password", ""):
        flash("New passwords do not match.", "danger")
    else:
        change_password(session["user_id"], new)
        flash("Password updated.", "success")
    return redirect(url_for("profile"))


# ── Analytics ─────────────────────────────────────────────────────────────────

@app.route("/analytics")
@permission_required("analytics.view")
def analytics():
    days         = request.args.get("days", default=None, type=int)
    trend_days   = days or 7
    return render_template("analytics.html",
                           summary=get_stats_summary(days),
                           recent=get_recent_diagnoses(10, days),
                           top_symptoms=get_top_symptoms(8),
                           disease_dist=get_disease_distribution(),
                           daily=get_daily_diagnoses(trend_days),
                           days=days, trend_days=trend_days)


@app.route("/api/stats")
@permission_required("analytics.view")
def api_stats():
    days = request.args.get("days", default=None, type=int)
    return jsonify({
        "summary":      get_stats_summary(days),
        "top_symptoms": get_top_symptoms(8),
        "disease_dist": get_disease_distribution(),
        "daily":        get_daily_diagnoses(days or 7)
    })


# ── Patient — Consultation views ──────────────────────────────────────────────

@app.route("/my_consultations")
@permission_required("consultation.view_own")
def my_consultations():
    return render_template("patient_consultations.html",
                           consultations=get_patient_consultations(session["user_id"]))


@app.route("/consultation/<int:cid>")
@permission_required("consultation.view_own", "consultation.view_all", "consultation.review")
def view_consultation(cid):
    c = get_consultation(cid)
    if not can_access_consultation(c):
        flash("Not found or access denied.", "danger")
        return redirect(url_for(ROLE_HOME.get(session.get("role"), "home")))
    if session.get("role") == "doctor":
        return redirect(url_for("doctor_view_consultation", cid=cid))
    return render_template("consultation_detail.html", c=c,
                           orders=get_lab_orders_for_consultation(cid))


# ── Doctor ────────────────────────────────────────────────────────────────────

@app.route("/doctor")
@permission_required("consultation.review")
def doctor_dashboard():
    uid = session["user_id"]
    return render_template("doctor_dashboard.html",
                           doc     = get_doctor_profile(uid) or {},
                           pending = get_doctor_consultations(uid, "pending"),
                           active  = get_doctor_consultations(uid, "in_review"),
                           done    = get_doctor_consultations(uid, "completed"),
                           stats   = get_workflow_stats())


def _doctor_consultation_or_none(cid):
    c = get_consultation(cid)
    if not c or c["doctor_user_id"] != session["user_id"]:
        return None
    return c


@app.route("/doctor/consultation/<int:cid>")
@permission_required("consultation.review")
def doctor_view_consultation(cid):
    c = _doctor_consultation_or_none(cid)
    if not c:
        flash("Not found.", "danger")
        return redirect(url_for("doctor_dashboard"))
    if c["status"] == "pending":
        update_consultation(cid,
                            c["doctor_notes"] or "",
                            c["final_diagnosis"] or "",
                            c["prescription"] or "",
                            str(c["follow_up_date"]) if c["follow_up_date"] else "",
                            "in_review")
        c["status"] = "in_review"
    return render_template("doctor_consultation.html",
                           c=c,
                           orders=get_lab_orders_for_consultation(cid),
                           lab_tests=LAB_TEST_TYPES)


@app.route("/doctor/consultation/<int:cid>/save", methods=["POST"])
@permission_required("consultation.review")
def doctor_save_consultation(cid):
    c = _doctor_consultation_or_none(cid)
    if not c:
        flash("Not found.", "danger")
        return redirect(url_for("doctor_dashboard"))
    update_consultation(cid,
                        request.form.get("doctor_notes", ""),
                        request.form.get("final_diagnosis", ""),
                        request.form.get("prescription", ""),
                        request.form.get("follow_up_date", ""),
                        request.form.get("status", "in_review"))
    tests = request.form.getlist("lab_tests")
    if tests and has_permission("lab.order"):
        priority   = request.form.get("priority", "normal")
        test_notes = request.form.get("lab_test_notes", "")
        for test in tests:
            create_lab_order(cid, c["patient_user_id"], session["user_id"],
                             test, test_notes, priority)
    flash("Consultation updated.", "success")
    return redirect(url_for("doctor_view_consultation", cid=cid))


# ── Lab ───────────────────────────────────────────────────────────────────────

@app.route("/lab")
@permission_required("lab.process")
def lab_dashboard():
    uid = session["user_id"]
    return render_template("lab_dashboard.html",
                           lab     = get_lab_profile(uid) or {},
                           pending = get_lab_tech_orders(uid, "pending"),
                           done    = get_lab_tech_orders(uid, "completed"),
                           stats   = get_workflow_stats())


@app.route("/lab/order/<int:oid>", methods=["GET", "POST"])
@permission_required("lab.process")
def lab_submit_result(oid):
    order = get_lab_order_for_tech(oid, session["user_id"])
    if not order:
        flash("Order not found.", "danger")
        return redirect(url_for("lab_dashboard"))
    if request.method == "POST":
        result_text = request.form.get("result_text", "").strip()
        if not result_text:
            flash("Result summary is required.", "danger")
            return render_template("lab_result_form.html", order=order)
        submit_lab_result(
            order_id     = oid,
            lab_user_id  = session["user_id"],
            result_text  = result_text,
            result_value = request.form.get("result_value", ""),
            unit         = request.form.get("unit", ""),
            normal_range = request.form.get("normal_range", ""),
            is_abnormal  = 1 if _to_int(request.form.get("is_abnormal")) else 0,
            remarks      = request.form.get("remarks", ""),
        )
        flash("Result submitted!", "success")
        return redirect(url_for("lab_dashboard"))
    return render_template("lab_result_form.html", order=order)


# ── Admin ─────────────────────────────────────────────────────────────────────

@app.route("/admin")
@permission_required("staff.manage", "staff.verify")
def admin_dashboard():
    roles, perms, grants = get_permission_matrix()
    app_filter = request.args.get("applications", "open")
    return render_template("admin_dashboard.html",
                           doctors   = get_all_doctors(),
                           lab_techs = get_all_lab_techs(),
                           stats     = get_workflow_stats(),
                           applications = list_applications(None if app_filter == "all" else app_filter),
                           app_counts   = application_counts(),
                           app_filter   = app_filter,
                           status_labels = STATUS_LABELS,
                           rbac_roles=roles, rbac_perms=perms, rbac_grants=grants)


@app.route("/admin/applications/<int:aid>")
@permission_required("staff.verify")
def admin_application(aid):
    a = get_application(aid)
    if not a:
        flash("Application not found.", "danger")
        return redirect(url_for("admin_dashboard"))
    return render_template("admin_application.html", a=a,
                           events=get_events(aid, include_internal=True),
                           interview_modes=INTERVIEW_MODES,
                           now=datetime.datetime.now())


APPLICANT_EMAILS = {
    "start_review":       ("Your application is under review", "Application under review"),
    "request_info":       ("Action needed on your application", "More information needed"),
    "schedule_interview": ("Your verification interview", "Interview scheduled"),
    "approve":            ("Your application has been approved", "Welcome to MediAI"),
    "reject":             ("Update on your application", "Application decision"),
}


@app.route("/admin/applications/<int:aid>/action/<action>", methods=["POST"])
@permission_required("staff.verify")
def admin_application_action(aid, action):
    a, err = transition(aid, session["user_id"], action, request.form)
    if err:
        flash(err, "danger")
    else:
        flash(f"Application {a['status_label'].lower()}.", "success")
        if action in APPLICANT_EMAILS:
            subject, heading = APPLICANT_EMAILS[action]
            last = get_events(aid)[-1]["note"] or ""
            footer = ("\n\nYou can now log in and start working on MediAI." if action == "approve"
                      else "\n\nLog in to MediAI to see the details of your application.")
            notify(a["email"], f"MediAI — {subject}", heading,
                   f"Hello {a['full_name']},\n\n{last}{footer}")
    return redirect(url_for("admin_application", aid=aid))


@app.route("/admin/applications/<int:aid>/checklist", methods=["POST"])
@permission_required("staff.verify")
def admin_application_checklist(aid):
    err = save_checklist(aid, session["user_id"], set(request.form.getlist("checks")))
    flash(err or "Verification checklist saved.", "danger" if err else "success")
    return redirect(url_for("admin_application", aid=aid))


@app.route("/admin/applications/<int:aid>/notes", methods=["POST"])
@permission_required("staff.verify")
def admin_application_notes(aid):
    save_internal_notes(aid, session["user_id"], request.form.get("internal_notes", "").strip()[:5000])
    flash("Internal notes saved.", "success")
    return redirect(url_for("admin_application", aid=aid))


@app.route("/admin/staff/<int:uid>/suspend", methods=["POST"])
@permission_required("staff.manage")
def admin_suspend_staff(uid):
    msg, err = suspend_staff(uid)
    flash(err or msg, "danger" if err else "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/staff/<int:uid>/reactivate", methods=["POST"])
@permission_required("staff.manage")
def admin_reactivate_staff(uid):
    err = reactivate_staff(uid)
    flash(err or "Account reactivated.", "danger" if err else "success")
    return redirect(url_for("admin_dashboard"))


# ── Video Call (WebRTC + SocketIO signaling) ─────────────────────────────────

@app.route("/video/start/<int:cid>", methods=["POST"])
@permission_required("video.host")
def start_video_call(cid):
    if not _doctor_consultation_or_none(cid):
        return jsonify({"ok": False, "msg": "Not found."}), 404
    set_video_state(cid, True)
    return jsonify({"ok": True})


@app.route("/video/end/<int:cid>", methods=["POST"])
@permission_required("video.host")
def end_video_call(cid):
    if not _doctor_consultation_or_none(cid):
        return jsonify({"ok": False, "msg": "Not found."}), 404
    set_video_state(cid, False)
    return jsonify({"ok": True})


@app.route("/video/<int:cid>")
@permission_required("video.join")
def video_call(cid):
    c = get_consultation(cid)
    if not can_join_video(c, session["user_id"], g.permissions):
        flash("Consultation not found or access denied.", "danger")
        return redirect(url_for(ROLE_HOME.get(session.get("role"), "home")))
    started_at  = c.get("video_started_at")
    started_fmt = started_at.strftime("%d %b %Y, %I:%M %p") if hasattr(started_at, "strftime") else None
    is_host = c["doctor_user_id"] == session["user_id"]
    call = dict(kind="consult", id=cid, title="Video Consultation", label="Consultation",
                is_host=is_host, host_label="Doctor", guest_label="Patient",
                host_icon="fa-user-doctor", host_name=f"Dr. {c.get('doctor_name')}",
                guest_name=c.get("patient_name"),
                start_url=url_for("start_video_call", cid=cid) if is_host else None,
                end_url=url_for("end_video_call", cid=cid) if is_host else None,
                return_url=url_for("doctor_view_consultation", cid=cid) if is_host
                           else url_for("view_consultation", cid=cid))
    return render_template("video_call.html", call=call,
                           role=session.get("role"),
                           username=session.get("username"),
                           started_at=started_fmt)


# ── SocketIO — WebRTC signaling events ───────────────────────────────────────
# Every event is checked against the Flask session: only the assigned doctor
# and the patient of a consultation may join or signal in its room. The
# username is always taken from the session, never trusted from the client.

def _socket_room(data):
    """Return (room, is_host) for an authorised socket user, else (None, False).
    Rooms: consultation calls (doctor + patient) and verification interviews
    (applicant + verifying admins)."""
    uid  = session.get("user_id")
    data = data if isinstance(data, dict) else {}
    kind = data.get("kind", "consult")
    rid  = _to_int(data.get("id", data.get("cid")), None)
    if uid is None or rid is None:
        return None, False
    user, role, perms = get_user_access(uid)
    if not user or role is None:
        return None, False
    if kind == "consult":
        c = get_consultation(rid)
        if can_join_video(c, uid, perms):
            return f"call_{rid}", c["doctor_user_id"] == uid
    elif kind == "interview":
        if can_join_interview(get_application(rid), uid, perms):
            return f"interview_{rid}", "staff.verify" in perms
    return None, False


@socketio.on("join")
def on_join(data):
    room, is_host = _socket_room(data)
    if not room:
        disconnect()
        return
    join_room(room)
    emit("user_joined", {"username": session.get("username"), "host": is_host},
         to=room, skip_sid=request.sid)


@socketio.on("offer")
def on_offer(data):
    room, _ = _socket_room(data)
    if room:
        emit("offer", {"sdp": data.get("sdp"), "username": session.get("username")},
             to=room, skip_sid=request.sid)


@socketio.on("answer")
def on_answer(data):
    room, _ = _socket_room(data)
    if room:
        emit("answer", {"sdp": data.get("sdp")}, to=room, skip_sid=request.sid)


@socketio.on("ice_candidate")
def on_ice(data):
    room, _ = _socket_room(data)
    if room:
        emit("ice_candidate", {"candidate": data.get("candidate")},
             to=room, skip_sid=request.sid)


@socketio.on("leave")
def on_leave(data):
    room, _ = _socket_room(data)
    if room:
        leave_room(room)
        emit("user_left", {"username": session.get("username")}, to=room)


@socketio.on("chat_message")
def on_chat(data):
    room, _ = _socket_room(data)
    msg  = str((data or {}).get("msg", "")).strip()[:1000]
    if room and msg:
        emit("chat_message", {"username": session.get("username"), "msg": msg}, to=room)


# ── Health check & error pages ───────────────────────────────────────────────

@app.route("/healthz")
def healthz():
    try:
        query("SELECT 1", one=True)
        return jsonify({"status": "ok"})
    except Exception:
        log.exception("Health check failed")
        return jsonify({"status": "error"}), 503


ERROR_TEXT = {
    400: ("Bad request", "The request could not be understood."),
    403: ("Access denied", "You do not have permission to view this page."),
    404: ("Page not found", "The page you are looking for does not exist."),
    405: ("Method not allowed", "That action is not supported here."),
    413: ("Request too large", "The submitted data is too large."),
    429: ("Too many requests", "Please slow down and try again shortly."),
    500: ("Something went wrong", "An unexpected error occurred. It has been logged."),
}


@app.errorhandler(HTTPException)
def handle_http_error(e):
    title, text = ERROR_TEXT.get(e.code, (e.name, e.description))
    if _wants_json():
        return jsonify({"ok": False, "msg": title}), e.code
    return render_template("error.html", code=e.code, title=title, text=text), e.code


@app.errorhandler(Exception)
def handle_unexpected_error(e):
    log.exception("Unhandled error on %s %s", request.method, request.path)
    title, text = ERROR_TEXT[500]
    if _wants_json():
        return jsonify({"ok": False, "msg": title}), 500
    return render_template("error.html", code=500, title=title, text=text), 500


if __name__ == "__main__":
    # Development server. For production run:  python serve.py
    if config.PRODUCTION:
        log.warning("MEDIAI_ENV=production - use `python serve.py` instead of app.py")
    log.info("MediAI running on http://%s:%s", config.HOST, config.PORT)
    socketio.run(app, host=config.HOST, port=config.PORT, debug=config.DEBUG,
                 use_reloader=config.DEBUG, allow_unsafe_werkzeug=True)
