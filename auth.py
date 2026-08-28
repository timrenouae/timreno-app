"""
Authentication, session management, RBAC decorators, and CSRF protection.

Design notes (per the approved plan):
- Passwords: PBKDF2-HMAC-SHA256, >=600,000 iterations (config.PBKDF2_ITERATIONS),
  16+ byte random salt, iteration count stored per-user so it can be raised
  later without invalidating existing hashes.
- Sessions are server-side rows in `sessions`, not JWTs, so they can be
  revoked. The cookie holds only a random opaque token; the DB stores only
  its SHA-256 hash, so stealing the DB doesn't hand out live sessions.
- Session token is rotated on login and on any privilege change (role
  reassignment) to prevent session fixation.
- CSRF tokens are signed with itsdangerous and tied to the current session
  token, so they can't be replayed across sessions.
- Login attempts are rate-limited per submitted username.
"""
import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta
from functools import wraps

from flask import g, request, redirect, url_for, session as flask_session, abort, flash

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

import config
import db

SESSION_COOKIE_NAME = "timr_session"
_csrf_serializer = URLSafeTimedSerializer(config.SECRET_KEY, salt="timr-csrf")


# ---------------------------------------------------------------- passwords

def hash_password(password: str, salt_hex: str = None, iterations: int = None):
    if salt_hex is None:
        salt_hex = secrets.token_hex(16)
    if iterations is None:
        iterations = config.PBKDF2_ITERATIONS
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), iterations
    )
    return digest.hex(), salt_hex, iterations


def verify_password(password: str, stored_hash_hex: str, salt_hex: str, iterations: int) -> bool:
    computed_hex, _, _ = hash_password(password, salt_hex, iterations)
    return hmac.compare_digest(computed_hex, stored_hash_hex)


# ----------------------------------------------------------------- sessions

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(conn: sqlite3.Connection, user_id: int) -> str:
    """Creates a new session row and returns the raw token to set as a
    cookie. Caller is responsible for committing (or use db.connect())."""
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.utcnow() + timedelta(seconds=config.SESSION_LIFETIME_SECONDS)).isoformat()
    conn.execute(
        "INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (?, ?, ?)",
        (user_id, _hash_token(token), expires_at),
    )
    return token


def revoke_session(conn: sqlite3.Connection, token: str):
    conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))


def revoke_all_sessions_for_user(conn: sqlite3.Connection, user_id: int):
    """Used on privilege change (role reassignment) to force re-login
    everywhere, preventing a stale session from keeping old permissions."""
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def _load_current_user():
    """Populates g.user / g.role / g.permissions from the session cookie, or
    leaves them None if there's no valid session. Called once per request
    via before_request in app.py."""
    g.user = None
    g.role = None
    g.permissions = set()

    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return

    with db.connect() as conn:
        row = conn.execute(
            """SELECT s.id as session_id, s.expires_at, u.*
               FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token_hash = ?""",
            (_hash_token(token),),
        ).fetchone()

        if row is None:
            return
        if datetime.fromisoformat(row["expires_at"]) < datetime.utcnow():
            conn.execute("DELETE FROM sessions WHERE id = ?", (row["session_id"],))
            return
        if not row["active"]:
            return

        conn.execute(
            "UPDATE sessions SET last_seen_at = datetime('now') WHERE id = ?",
            (row["session_id"],),
        )

        g.user = dict(row)
        role_row = conn.execute("SELECT * FROM roles WHERE id = ?", (row["role_id"],)).fetchone()
        g.role = dict(role_row) if role_row else None
        perm_rows = conn.execute(
            """SELECT p.code FROM permissions p
               JOIN role_permissions rp ON rp.permission_id = p.id
               WHERE rp.role_id = ?""",
            (row["role_id"],),
        ).fetchall()
        g.permissions = {r["code"] for r in perm_rows}


def current_user():
    return getattr(g, "user", None)


def has_permission(code: str) -> bool:
    return code in getattr(g, "permissions", set())


def get_role_permissions(conn, role_id) -> set:
    rows = conn.execute(
        """SELECT p.code FROM permissions p
           JOIN role_permissions rp ON rp.permission_id = p.id
           WHERE rp.role_id = ?""",
        (role_id,),
    ).fetchall()
    return {r["code"] for r in rows}


# Permission codes that identify a "portal-only" account -- one that should
# never reach any internal view, only its own dedicated portal. A set (not a
# single code) because more than one portal type shares this shape: the
# Customer portal (tracker.view_own), the Engineer portal
# (tracker.complete_tasks), and -- Item 5 -- the Vendor portal
# (requisitions.vendor_fill), added here rather than re-deriving the concept.
_PORTAL_ONLY_CODES = {"tracker.view_own", "tracker.complete_tasks", "requisitions.vendor_fill"}


def is_portal_only_user(permissions) -> bool:
    """True if `permissions` is a subset of _PORTAL_ONLY_CODES, i.e. this
    account holds nothing but portal-only permission(s) -- so it must never
    be treated as internal staff, regardless of which portal code(s) it
    holds or in what combination."""
    return set(permissions) <= _PORTAL_ONLY_CODES


def default_landing_endpoint(permissions) -> str:
    """Where a user should land after login (and at '/', see app.py's index
    route): the customer portal for a customer-portal-only account
    (permission set empty or exactly {'tracker.view_own'}), the (now
    Engineer-simplified) Tracker list for a tasks-only account (permission
    set exactly {'tracker.complete_tasks'}), the Vendor portal for a
    vendor-only account (permission set exactly
    {'requisitions.vendor_fill'} -- Item 5), Product Master otherwise."""
    perms = set(permissions)
    if perms <= {"tracker.view_own"}:
        return "portal.list_view"
    if perms <= {"tracker.complete_tasks"}:
        return "tracker.list_view"
    if perms <= {"requisitions.vendor_fill"}:
        return "vendor_portal.list_view"
    return "products.list_view"


# -------------------------------------------------------------- decorators

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def require_internal_login(view):
    """Like login_required, but additionally blocks a portal-only account
    (one whose permission set is a subset of _PORTAL_ONLY_CODES -- e.g.
    empty, exactly {'tracker.view_own'}, or exactly
    {'tracker.complete_tasks'}) from internal read views -- product/quote
    lists and the quote PDF route -- that were historically just
    login_required so any internal staff member could browse them
    regardless of role. Any other permission at all (e.g. purchases.manage
    on the Buyer role) still passes through unchanged, matching existing
    behavior exactly. NOTE: the Tracker's own list/detail routes do NOT use
    this decorator -- see require_internal_login_or_task_access below,
    since an Engineer-only account's one dashboard IS the (simplified)
    Tracker."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            return redirect(url_for("auth.login", next=request.path))
        perms = getattr(g, "permissions", set())
        if is_portal_only_user(perms):
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def require_internal_login_or_task_access(view):
    """Like require_internal_login, but additionally admits an
    Engineer-only account (permission set exactly {'tracker.complete_tasks'}
    with nothing else) -- used only by the Tracker's own list/detail routes,
    which double as that account's one dashboard (simplified by the
    templates based on current_permissions). Still blocks a
    customer-portal-only account, and (once Item 5 adds its vendor code to
    _PORTAL_ONLY_CODES) would still block a vendor-only account too, since
    neither holds tracker.complete_tasks."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            return redirect(url_for("auth.login", next=request.path))
        perms = getattr(g, "permissions", set())
        if is_portal_only_user(perms) and "tracker.complete_tasks" not in perms:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def require_permission(code: str):
    """Role-scoped authorization: the current user's role must carry this
    permission code. Use for admin/product-master/purchase actions."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if current_user() is None:
                return redirect(url_for("auth.login", next=request.path))
            if not has_permission(code):
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def require_any_permission(*codes):
    """Like require_permission, but grants access if the current user's role
    carries ANY of the given codes -- e.g. completing a Tracker task should
    work for either quotes.manage (office staff, unchanged) or the new
    tracker.complete_tasks (Engineer)."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if current_user() is None:
                return redirect(url_for("auth.login", next=request.path))
            if not any(has_permission(c) for c in codes):
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def require_owner_or_permission(code: str, resource_fn):
    """Row-level authorization primitive, stubbed now for the Phase 2
    client portal (e.g. a client viewing only their own quote) and unused
    by any Phase 1 route. `resource_fn(**kwargs)` should return an object
    with an owner user id (e.g. `.client_user_id`); access is granted if
    the current user owns the resource OR holds `code`."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if user is None:
                return redirect(url_for("auth.login", next=request.path))
            if has_permission(code):
                return view(*args, **kwargs)
            resource = resource_fn(**kwargs)
            owner_id = getattr(resource, "owner_user_id", None) if resource is not None else None
            if owner_id is not None and owner_id == user["id"]:
                return view(*args, **kwargs)
            abort(403)
        return wrapped
    return decorator


# -------------------------------------------------------------- rate limit

def check_login_allowed(conn: sqlite3.Connection, username: str):
    """Returns True if this username may attempt a login right now."""
    row = conn.execute(
        "SELECT failed_count, locked_until FROM login_attempts WHERE username = ?",
        (username,),
    ).fetchone()
    if row is None:
        return True
    if row["locked_until"] and datetime.fromisoformat(row["locked_until"]) > datetime.utcnow():
        return False
    return True


def record_login_failure(conn: sqlite3.Connection, username: str):
    row = conn.execute(
        "SELECT failed_count FROM login_attempts WHERE username = ?", (username,)
    ).fetchone()
    failed_count = (row["failed_count"] if row else 0) + 1
    locked_until = None
    if failed_count >= config.LOGIN_MAX_ATTEMPTS:
        locked_until = (datetime.utcnow() + timedelta(seconds=config.LOGIN_LOCKOUT_SECONDS)).isoformat()
        failed_count = 0  # reset the counter once locked, so the next window starts clean
    conn.execute(
        """INSERT INTO login_attempts (username, failed_count, locked_until) VALUES (?, ?, ?)
           ON CONFLICT(username) DO UPDATE SET failed_count = excluded.failed_count,
                                                locked_until = excluded.locked_until""",
        (username, failed_count, locked_until),
    )


def record_login_success(conn: sqlite3.Connection, username: str):
    conn.execute("DELETE FROM login_attempts WHERE username = ?", (username,))


# -------------------------------------------------------------------- csrf

def generate_csrf_token() -> str:
    """Ties the token to the current session cookie value so it can't be
    replayed from a different session."""
    token = request.cookies.get(SESSION_COOKIE_NAME, "anonymous")
    return _csrf_serializer.dumps(token)


def validate_csrf_token(submitted: str) -> bool:
    if not submitted:
        return False
    current_token = request.cookies.get(SESSION_COOKIE_NAME, "anonymous")
    try:
        value = _csrf_serializer.loads(submitted, max_age=6 * 3600)
    except (BadSignature, SignatureExpired):
        return False
    return hmac.compare_digest(value, current_token)


def csrf_protect():
    """Call at the top of any state-changing view (or wire as a
    before_request check restricted to POST/PUT/DELETE)."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        if not validate_csrf_token(token):
            abort(400, description="Invalid or missing CSRF token")


# ------------------------------------------------------- admin-lockout guard

class LastAdminError(Exception):
    pass


def assert_admin_invariant_preserved(conn: sqlite3.Connection, excluding_user_id: int = None):
    """Raises LastAdminError if, after excluding `excluding_user_id` (the
    user about to be deactivated or reassigned), no other active user would
    hold both users.manage and roles.manage. Call this BEFORE committing a
    deactivation or role-reassignment that could remove the last admin."""
    rows = conn.execute(
        """SELECT DISTINCT u.id
           FROM users u
           JOIN role_permissions rp1 ON rp1.role_id = u.role_id
           JOIN permissions p1 ON p1.id = rp1.permission_id AND p1.code = 'users.manage'
           JOIN role_permissions rp2 ON rp2.role_id = u.role_id
           JOIN permissions p2 ON p2.id = rp2.permission_id AND p2.code = 'roles.manage'
           WHERE u.active = 1"""
    ).fetchall()
    remaining = {r["id"] for r in rows} - ({excluding_user_id} if excluding_user_id else set())
    if not remaining:
        raise LastAdminError(
            "Refused: this would leave no active user able to manage users and roles."
        )


def assert_role_update_preserves_admin_invariant(conn: sqlite3.Connection, role_id: int, new_permission_codes):
    """Same invariant as assert_admin_invariant_preserved, but for editing a
    role's permission set in place (which assert_admin_invariant_preserved
    can't see, since it reads permissions as currently stored). Call BEFORE
    writing the new permission set for `role_id`."""
    if {"users.manage", "roles.manage"} <= set(new_permission_codes):
        return  # this role still grants full admin -- invariant trivially holds
    rows = conn.execute(
        """SELECT DISTINCT u.id
           FROM users u
           JOIN role_permissions rp1 ON rp1.role_id = u.role_id
           JOIN permissions p1 ON p1.id = rp1.permission_id AND p1.code = 'users.manage'
           JOIN role_permissions rp2 ON rp2.role_id = u.role_id
           JOIN permissions p2 ON p2.id = rp2.permission_id AND p2.code = 'roles.manage'
           WHERE u.active = 1 AND u.role_id != ?""",
        (role_id,),
    ).fetchall()
    if not rows:
        raise LastAdminError(
            "Refused: removing these permissions would leave no active user able to manage users and roles."
        )
