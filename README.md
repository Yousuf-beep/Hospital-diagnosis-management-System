# MediAI — Health Diagnostic System

Flask web app: ML symptom diagnosis (Random Forest + Naive Bayes + SVM), chatbot,
symptom diary, doctor/lab workflow, video consultations, and role-based access control.
Data is stored in a single SQLite file (`mediai.db`), created automatically.

## Run it (development)

Double-click **`run.bat`**, or from a terminal in this folder:

```powershell
.venv\Scripts\python app.py
```

Open http://127.0.0.1:5000 and log in as `admin` / `admin123`.

First-time setup (only if `.venv` doesn't exist; `run.bat` does this for you):

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

In VS Code, the project is pre-configured to use `.venv` — if the Run button
still picks another Python, choose **Python: Select Interpreter → .venv**.

## Run it (production)

1. Copy `.env.example` to `.env` and set at least:
   - `MEDIAI_ENV=production`
   - `MEDIAI_SECRET_KEY=` — generate with `python -c "import secrets; print(secrets.token_hex(32))"`
   - `MEDIAI_ADMIN_PASSWORD=` — otherwise a random admin password is printed in the log on first start
2. Start the server: double-click **`serve.bat`** or run `.venv\Scripts\python serve.py`.

`serve.py` uses gevent's WSGI server (not the Flask dev server) and supports WebSockets for
video calls. It listens on `0.0.0.0:5000` by default.

For a public deployment, put it behind HTTPS (nginx, Caddy or IIS), then set
`MEDIAI_HTTPS=1` and `MEDIAI_TRUST_PROXY=1`. Browsers only allow camera/microphone access
for video calls over HTTPS (or on localhost).

Health check endpoint: `GET /healthz`.

## Roles

| Role      | Can do |
|-----------|--------|
| patient   | Self-registers at `/register`. Runs diagnoses, keeps a diary, views own consultations, joins own video calls |
| doctor    | Applies at `/apply`; active after verification. Reviews assigned consultations, orders lab tests, hosts video calls, analytics |
| lab       | Applies at `/apply?role=lab` with lab details; active after verification. Processes lab orders for the tests the lab offers |
| applicant | Effective role of any doctor/lab account not yet approved — can only view/update its application and join its interview |
| admin     | Verifies applications, suspends/reactivates staff, views any consultation, analytics, role-permission matrix |

Permissions are defined in `ROLE_PERMISSIONS` in `database.py` and synced to the database on startup.

## Staff onboarding (doctors & lab technicians)

Admins no longer create staff accounts. Doctors and lab technicians register themselves and are verified:

1. **Apply** (`/apply`) — personal details, qualifications and licence. Doctors give their specialty and
   medical registration number; lab technicians give their lab's name, address, licence, accreditation
   (e.g. NABL), opening hours and the tests it performs.
2. **Review** — in **Admin Panel → Applications**, an admin starts the review, can ask the applicant for
   more information (they edit and resubmit), and works through a verification checklist
   (identity, licence/registration, qualifications, experience or lab facility).
3. **Interview** — scheduled as an in-app video call, phone call or in-person meeting. Video interviews run
   in the same WebRTC call page as consultations. The admin records an internal assessment afterwards.
4. **Decision** — approval requires a completed interview and every checklist item ticked. Rejection
   requires a reason, which the applicant sees.

Every step is logged in an audit trail (`application_events`). If email is configured, applicants are
emailed at each stage. Approved staff can later be **suspended** (not deleted — medical records are kept,
and their open consultations / pending lab orders are reassigned to other active staff) and reactivated.

Lab orders go to an active lab that offers the ordered test, with the fewest pending orders.

## Security features

- Salted password hashing, 8-character minimum, change-password on the profile page
- CSRF protection on every form and API call
- Login throttling (5 failures per 5 minutes per username + IP)
- HttpOnly / SameSite session cookies (Secure + HSTS when `MEDIAI_HTTPS=1`), 8-hour sessions
- Security headers, no caching of authenticated pages
- Per-object access checks (patients see only their own data, doctors only assigned cases)

## Files

| File | Purpose |
|------|---------|
| `app.py` | Routes, RBAC, security, Socket.IO signalling (dev entry point) |
| `serve.py` | Production entry point (gevent) |
| `config.py` | All settings, read from environment / `.env` |
| `database.py` | SQLite schema, RBAC tables, core queries |
| `workflow.py` | Doctors, labs, consultations, lab orders, suspension |
| `staff_onboarding.py` | Staff applications, verification checklist, interviews, audit trail |
| `ml_model.py` | Ensemble model — cached in `models/` after first training |
| `chatbot_engine.py`, `symptom_diary.py`, `regional_intelligence.py`, `pdf_report.py`, `mail.py` | Features |

Back up your data by copying `mediai.db` while the server is stopped.
