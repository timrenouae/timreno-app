from flask import Blueprint, render_template, request, redirect, url_for, make_response, flash, g

import auth
import config
import db
import repositories.users as users_repo

bp = Blueprint("auth", __name__, url_prefix="/auth")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if auth.current_user() is not None:
        return redirect(url_for(auth.default_landing_endpoint(getattr(g, "permissions", set()))))

    next_url = request.values.get("next") or ""

    if request.method == "GET":
        return render_template("login.html", csrf_token=auth.generate_csrf_token(), next=next_url)

    auth.csrf_protect()
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    next_url = request.form.get("next") or ""

    error = None
    landing_perms = set()
    with db.connect() as conn:
        if not auth.check_login_allowed(conn, username):
            error = "Too many failed attempts. Try again in a few minutes."
        else:
            user = users_repo.get_user_by_username(conn, username)
            valid = user is not None and user["active"] and auth.verify_password(
                password, user["password_hash"], user["password_salt"], user["pbkdf2_iterations"]
            )
            if not valid:
                auth.record_login_failure(conn, username)
                error = "Invalid username or password."
            else:
                auth.record_login_success(conn, username)
                token = auth.create_session(conn, user["id"])
                landing_perms = auth.get_role_permissions(conn, user["role_id"])

    if error:
        flash(error, "error")
        return render_template("login.html", csrf_token=auth.generate_csrf_token(), next=next_url), 401

    default_url = url_for(auth.default_landing_endpoint(landing_perms))
    # A portal-only account (Customer/Engineer/Vendor) always lands on its
    # own dashboard -- `next` is never honored for them, even though it IS
    # honored for internal staff below. Without this, a portal-only user
    # who reached /auth/login via some internal-only page redirecting them
    # here (a stale bookmark from before the public site existed, an old
    # link, a decorator's own "please log in" redirect) would get sent
    # straight back to that same page after authenticating successfully --
    # and immediately hit a 403 there, since their role was never meant to
    # see it. Internal staff keep the normal "return me to what I was
    # trying to reach" behavior.
    if auth.is_portal_only_user(landing_perms):
        target = default_url
    else:
        target = next_url if next_url.startswith("/") else default_url
    resp = make_response(redirect(target))
    resp.set_cookie(
        auth.SESSION_COOKIE_NAME,
        token,
        max_age=config.SESSION_LIFETIME_SECONDS,
        httponly=True,
        secure=config.COOKIE_SECURE,
        samesite="Lax",
    )
    return resp


@bp.route("/logout", methods=["GET", "POST"])
def logout():
    token = request.cookies.get(auth.SESSION_COOKIE_NAME)
    if token:
        with db.connect() as conn:
            auth.revoke_session(conn, token)
    resp = make_response(redirect(url_for("auth.login")))
    resp.delete_cookie(auth.SESSION_COOKIE_NAME)
    return resp
