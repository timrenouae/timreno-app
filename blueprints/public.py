"""
Public marketing website (Item 7) -- the only blueprint with no url_prefix,
so it owns the site root. Reachable by anyone, logged in or not:

  GET  /            -- marketing homepage for an anonymous visitor;
                        redirects a logged-in visitor to their own
                        dashboard (same behavior app.py's old index()
                        route had -- just relocated here).
  GET  /services     -- the three service lines, one section each.
  GET  /about        -- company story + the 4-step process.
  GET  /our-work      -- gallery (stock photography, honestly captioned --
                        see templates/public/our_work.html) + a
                        before/after slider.
  GET  /contact       -- the inquiry form.
  POST /contact       -- validates + saves via repositories/public.py,
                        flashes a thank-you, redirects back to /contact
                        (POST-redirect-GET so a refresh never resubmits).

None of these routes require login -- this is the public side of the
site. company_settings is already available in every template via
app.py's inject_user_context context processor, same as the internal
tool.
"""
import re

from flask import Blueprint, render_template, request, redirect, url_for, flash, g

import auth
import db
import repositories.public as public_repo

bp = Blueprint("public", __name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@bp.route("/")
def home():
    if auth.current_user() is not None:
        perms = getattr(g, "permissions", set())
        return redirect(url_for(auth.default_landing_endpoint(perms)))
    return render_template("public/home.html")


@bp.route("/services")
def services():
    return render_template("public/services.html")


@bp.route("/about")
def about():
    return render_template("public/about.html")


@bp.route("/our-work")
def our_work():
    return render_template("public/our_work.html")


@bp.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "GET":
        return render_template("public/contact.html", csrf_token=auth.generate_csrf_token(),
                                form={})

    # CSRF here is tied to the session cookie, not to being logged in --
    # auth.generate_csrf_token()/validate_csrf_token() sign against
    # request.cookies.get(SESSION_COOKIE_NAME, "anonymous"), so an
    # anonymous visitor still gets a real, verifiable token.
    auth.csrf_protect()

    name = (request.form.get("name") or "").strip()
    email = (request.form.get("email") or "").strip()
    phone = (request.form.get("phone") or "").strip()
    message = (request.form.get("message") or "").strip()

    errors = []
    if not name:
        errors.append("Please enter your name.")
    if not message:
        errors.append("Please enter a message.")
    if email and not _EMAIL_RE.match(email):
        errors.append("That email address doesn't look right.")

    if errors:
        for e in errors:
            flash(e, "error")
        return render_template(
            "public/contact.html", csrf_token=auth.generate_csrf_token(),
            form={"name": name, "email": email, "phone": phone, "message": message},
        ), 400

    with db.connect() as conn:
        public_repo.create_inquiry(conn, name, email or None, phone or None, message)

    flash("Thanks — your message has been sent. We'll get back to you shortly.", "success")
    return redirect(url_for("public.contact"))
