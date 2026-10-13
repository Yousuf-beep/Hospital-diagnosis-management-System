"""
config.py — All runtime settings, read from environment variables.
A local `.env` file (see .env.example) is loaded automatically if present.
"""

import os
import secrets
import logging

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:          # python-dotenv is optional
    pass


def _bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


ENV        = os.environ.get("MEDIAI_ENV", "development").strip().lower()
PRODUCTION = ENV == "production"
DEBUG      = _bool("MEDIAI_DEBUG", not PRODUCTION)

HOST = os.environ.get("MEDIAI_HOST", "0.0.0.0" if PRODUCTION else "127.0.0.1")
PORT = int(os.environ.get("MEDIAI_PORT", "5000"))

DB_PATH   = os.environ.get("MEDIAI_DB", os.path.join(BASE_DIR, "mediai.db"))
MODEL_DIR = os.environ.get("MEDIAI_MODEL_DIR", os.path.join(BASE_DIR, "models"))

# Socket.IO allowed origins: comma-separated list, or empty for same-origin only
CORS_ORIGINS = [o.strip() for o in os.environ.get("MEDIAI_CORS_ORIGINS", "").split(",") if o.strip()] or None

# Cookies: set MEDIAI_HTTPS=1 when served behind HTTPS so cookies are Secure-only
HTTPS = _bool("MEDIAI_HTTPS", False)

# Login throttling
LOGIN_MAX_ATTEMPTS   = int(os.environ.get("MEDIAI_LOGIN_MAX_ATTEMPTS", "5"))
LOGIN_WINDOW_SECONDS = int(os.environ.get("MEDIAI_LOGIN_WINDOW", "300"))

# Mail (diagnosis report emails). Leave unset to disable emailing.
MAIL_SERVER   = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
MAIL_PORT     = int(os.environ.get("MAIL_PORT", "587"))
MAIL_USE_TLS  = _bool("MAIL_USE_TLS", True)
MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
MAIL_SENDER   = os.environ.get("MAIL_DEFAULT_SENDER", f"MediAI <{MAIL_USERNAME}>" if MAIL_USERNAME else "")
MAIL_ENABLED  = bool(MAIL_USERNAME and MAIL_PASSWORD)

logging.basicConfig(
    level=os.environ.get("MEDIAI_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("mediai")


def _secret_key():
    key = os.environ.get("MEDIAI_SECRET_KEY", "").strip()
    if key:
        return key
    if PRODUCTION:
        raise RuntimeError(
            "MEDIAI_SECRET_KEY must be set in production. Generate one with:\n"
            "  python -c \"import secrets; print(secrets.token_hex(32))\"")
    # Development: persist a random key so sessions survive restarts
    path = os.path.join(BASE_DIR, ".dev_secret_key")
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    with open(path, "w") as f:
        f.write(key)
    return key


SECRET_KEY = _secret_key()

# Password for the auto-created `admin` account (first run only).
# In production with no value set, a random one is generated and logged once.
ADMIN_PASSWORD = os.environ.get("MEDIAI_ADMIN_PASSWORD", "").strip() or (
    None if PRODUCTION else "admin123")
