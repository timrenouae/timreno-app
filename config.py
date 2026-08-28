"""
Configuration for the TIM RENO unified app.

Everything that differs between a dev run (this sandbox) and the eventual
production host (e.g. PythonAnywhere) is pulled from environment variables
so no secrets or host-specific paths are hardcoded.
"""
import os
import secrets

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Where the SQLite database file lives. On PythonAnywhere this should point
# into the persistent home directory (e.g. /home/<user>/timr_app/timr.db).
DATABASE_PATH = os.environ.get("TIMR_DB_PATH", os.path.join(BASE_DIR, "timr.db"))

# Where uploaded files (currently just the company logo) are stored.
# Defaults to the same directory as the database itself so that on Render
# it automatically lands on the persistent Disk (survives redeploys) with
# no extra configuration -- static/ would NOT survive a redeploy, since
# it's part of the git-deployed code, not the persistent Disk.
UPLOADS_DIR = os.environ.get("TIMR_UPLOADS_DIR", os.path.dirname(DATABASE_PATH))

# Flask's session-signing / CSRF-signing secret. In production this MUST be
# set via the TIMR_SECRET_KEY environment variable and kept stable across
# restarts (changing it invalidates every session and CSRF token). For local
# dev we generate one at import time and persist it to a local file so
# restarts during development don't log everyone out constantly.
_SECRET_FILE = os.path.join(BASE_DIR, ".dev_secret_key")


def _load_or_create_dev_secret():
    if os.environ.get("TIMR_SECRET_KEY"):
        return os.environ["TIMR_SECRET_KEY"]
    if os.path.exists(_SECRET_FILE):
        with open(_SECRET_FILE, "r") as f:
            return f.read().strip()
    key = secrets.token_urlsafe(48)
    with open(_SECRET_FILE, "w") as f:
        f.write(key)
    return key


SECRET_KEY = _load_or_create_dev_secret()

# Session cookie lifetime (seconds). 12 hours by default.
SESSION_LIFETIME_SECONDS = int(os.environ.get("TIMR_SESSION_LIFETIME", 12 * 3600))

# PBKDF2 parameters. Iteration count follows 2023 OWASP guidance for
# PBKDF2-HMAC-SHA256 (>= 600,000). Stored per-user in the DB as well so it
# can be raised later (with rehash-on-login) without invalidating old hashes.
PBKDF2_ITERATIONS = int(os.environ.get("TIMR_PBKDF2_ITERATIONS", 600_000))

# Login lockout: after this many consecutive failed attempts for a username,
# reject further attempts until the lockout window has passed.
LOGIN_MAX_ATTEMPTS = int(os.environ.get("TIMR_LOGIN_MAX_ATTEMPTS", 8))
LOGIN_LOCKOUT_SECONDS = int(os.environ.get("TIMR_LOGIN_LOCKOUT_SECONDS", 15 * 60))

# Whether cookies require HTTPS. Set TIMR_COOKIE_SECURE=0 for local http-only
# development; production behind HTTPS should leave this at the default (1).
COOKIE_SECURE = os.environ.get("TIMR_COOKIE_SECURE", "1") != "0"

DEBUG = os.environ.get("TIMR_DEBUG", "0") == "1"
