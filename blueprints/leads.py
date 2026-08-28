"""
Staff-facing inbox for messages submitted through the public website's
contact form (Item 7). Kept as its own tiny blueprint (one business
concern -> one blueprint, matching the rest of this app) rather than
folded into blueprints/public.py, which is deliberately the
no-login-required surface.
"""
from flask import Blueprint, render_template, redirect, url_for, flash, abort

import auth
import db
import repositories.public as public_repo

bp = Blueprint("leads", __name__, url_prefix="/leads")


@bp.route("/")
@auth.require_permission("leads.view")
def list_view():
    with db.connect() as conn:
        inquiries = public_repo.list_inquiries(conn)
    return render_template("leads/list.html", inquiries=inquiries, csrf_token=auth.generate_csrf_token())


@bp.route("/<int:inquiry_id>/mark-read", methods=["POST"])
@auth.require_permission("leads.view")
def mark_read(inquiry_id):
    auth.csrf_protect()
    with db.connect() as conn:
        inquiry = public_repo.get_inquiry(conn, inquiry_id)
        if inquiry is None:
            abort(404)
        public_repo.mark_read(conn, inquiry_id)
    flash("Marked as read.", "success")
    return redirect(url_for("leads.list_view"))
