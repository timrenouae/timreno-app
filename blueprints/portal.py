"""Customer portal: a read-only, progress-only view for a customer-role
login (tracker.view_own permission) linked to one or more quotes via
quotes.client_user_id. Deliberately shows nothing about pricing, budget, or
team -- just project identity, stage, overall progress, and the item
checklist. See auth.require_owner_or_permission for the ownership check.
"""
from types import SimpleNamespace

from flask import Blueprint, render_template, abort

import auth
import db
import repositories.quotes as quotes_repo
import repositories.tracker as tracker_repo

bp = Blueprint("portal", __name__, url_prefix="/portal")


def _quote_owner_resource(quote_id, **kwargs):
    with db.connect() as conn:
        q = quotes_repo.get_quote(conn, quote_id)
    if q is None:
        return None
    return SimpleNamespace(owner_user_id=q["client_user_id"])


@bp.route("/")
@auth.login_required
def list_view():
    with db.connect() as conn:
        quotes = quotes_repo.list_quotes_for_client(conn, auth.current_user()["id"])
        rows = []
        for q in quotes:
            tasks = tracker_repo.list_tasks(conn, q["id"])
            rows.append({"quote": dict(q), "progress": tracker_repo.compute_progress(tasks)})
    return render_template("portal/list.html", rows=rows)


@bp.route("/<int:quote_id>")
@auth.require_owner_or_permission("quotes.manage", _quote_owner_resource)
def detail_view(quote_id):
    with db.connect() as conn:
        full = quotes_repo.get_quote_full(conn, quote_id)
        if full is None or full["quote"]["status"] != "final":
            abort(404)
        tasks = tracker_repo.list_tasks(conn, quote_id)
    progress = tracker_repo.compute_progress(tasks)
    grouped_tasks = tracker_repo.group_tasks_by_room(tasks)
    return render_template(
        "portal/detail.html", quote=full["quote"], progress=progress, grouped_tasks=grouped_tasks,
    )
