"""
TIM RENO unified app -- Flask application factory.

Run locally:
    python3 migrate.py                 # create/upgrade the database
    python3 scripts/bootstrap_admin.py # create the first Admin user (first run only)
    python3 app.py                     # start the dev server
"""
import datetime

from flask import Flask, g, request, redirect, url_for, flash, make_response

import auth
import config
import db
import repositories.settings as settings_repo
from units import UNIT_OPTIONS


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = config.SECRET_KEY

    from blueprints.auth import bp as auth_bp
    from blueprints.admin import bp as admin_bp
    from blueprints.products import bp as products_bp
    from blueprints.purchases import bp as purchases_bp
    from blueprints.quotes import bp as quotes_bp
    from blueprints.estimator import bp as estimator_bp
    from blueprints.tracker import bp as tracker_bp
    from blueprints.portal import bp as portal_bp
    from blueprints.restore import bp as restore_bp
    from blueprints.settings import bp as settings_bp
    from blueprints.requisitions import bp as requisitions_bp
    from blueprints.vendor_portal import bp as vendor_portal_bp
    from blueprints.public import bp as public_bp
    from blueprints.leads import bp as leads_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(products_bp)
    app.register_blueprint(purchases_bp)
    app.register_blueprint(quotes_bp)
    app.register_blueprint(estimator_bp)
    app.register_blueprint(tracker_bp)
    app.register_blueprint(portal_bp)
    app.register_blueprint(restore_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(requisitions_bp)
    app.register_blueprint(vendor_portal_bp)
    # No url_prefix -- public.py owns the site root ('/'), including the
    # "logged-in visitor gets redirected to their dashboard" behavior the
    # old index() route below used to own directly.
    app.register_blueprint(public_bp)
    app.register_blueprint(leads_bp)

    @app.before_request
    def load_user():
        auth._load_current_user()

    @app.context_processor
    def inject_user_context():
        # company_settings is read on every request so every template --
        # the top bar, the login page, any PDF-adjacent page -- can show
        # the current company name/logo without each view fetching it
        # separately. It's a single indexed-by-PK SQLite read, cheap
        # enough not to worry about caching.
        with db.connect() as conn:
            company_settings = settings_repo.get_settings(conn)
        return {
            "current_user": getattr(g, "user", None),
            "current_role": getattr(g, "role", None),
            "current_permissions": getattr(g, "permissions", set()),
            "units": UNIT_OPTIONS,
            "company_settings": company_settings,
            # Used by the public site's footer copyright line
            # (templates/public/_layout.html) -- computed once here rather
            # than duplicated in every route.
            "now_year": datetime.date.today().year,
        }

    @app.errorhandler(403)
    def forbidden(e):
        # A 403 here always happens to a *logged-in* account whose own
        # session steered it somewhere that account was never meant to
        # see -- a stale link, a bookmark meant for a different role, a
        # portal-only account (Customer/Engineer/Vendor) whose setup isn't
        # finished yet. Those accounts have no internal nav to click back
        # out through, so a bare error page is a dead end. Instead, this
        # clears the session -- server-side (auth.revoke_session, same
        # call blueprints/auth.py's logout route makes) and the cookie --
        # and sends the visitor to the public homepage, where signing
        # back in (as the same account once it's fixed, or a different
        # one) always works, rather than immediately bouncing them into
        # the same broken page again the way a plain redirect to their
        # "own dashboard" would.
        token = request.cookies.get(auth.SESSION_COOKIE_NAME)
        if token:
            with db.connect() as conn:
                auth.revoke_session(conn, token)
        flash("That page isn't available to your account, so you've been signed out. Please sign back in.", "error")
        resp = make_response(redirect(url_for("public.home")))
        resp.delete_cookie(auth.SESSION_COOKIE_NAME)
        return resp

    @app.errorhandler(400)
    def bad_request(e):
        return f"400 Bad Request — {e.description}", 400

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=config.DEBUG)
