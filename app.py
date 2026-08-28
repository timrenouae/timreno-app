"""
TIM RENO unified app -- Flask application factory.

Run locally:
    python3 migrate.py                 # create/upgrade the database
    python3 scripts/bootstrap_admin.py # create the first Admin user (first run only)
    python3 app.py                     # start the dev server
"""
from flask import Flask, redirect, url_for, g

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
        }

    @app.route("/")
    def index():
        if auth.current_user() is None:
            return redirect(url_for("auth.login"))
        perms = getattr(g, "permissions", set())
        if perms <= {"tracker.view_own"}:
            # A customer-portal-only account has nothing to see under
            # Product Master -- send them straight to their own portal.
            return redirect(url_for("portal.list_view"))
        return redirect(url_for("products.list_view"))

    @app.errorhandler(403)
    def forbidden(e):
        return "403 Forbidden — you don't have permission to view this page.", 403

    @app.errorhandler(400)
    def bad_request(e):
        return f"400 Bad Request — {e.description}", 400

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=config.DEBUG)
