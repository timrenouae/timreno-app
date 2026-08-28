import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, send_from_directory

import auth
import config
import db
import repositories.quotes as quotes_repo
import repositories.tracker as tracker_repo
import repositories.users as users_repo
import repositories.audit as audit_repo

bp = Blueprint("tracker", __name__, url_prefix="/tracker")


@bp.route("/")
@auth.require_internal_login_or_task_access
def list_view():
    with db.connect() as conn:
        rows = tracker_repo.list_tracked_quotes_with_stats(conn)
    total = len(rows)
    started = sum(1 for r in rows if r["has_tracker"])
    completed = sum(1 for r in rows if r["progress"] and r["progress"]["total"] > 0
                     and r["progress"]["done"] == r["progress"]["total"])
    over_budget = sum(1 for r in rows if r["budget"] and r["budget"]["variance"] < 0)
    stats = {"total": total, "started": started, "completed": completed, "over_budget": over_budget}
    return render_template("tracker/list.html", rows=rows, stats=stats)


@bp.route("/<int:quote_id>")
@auth.require_internal_login_or_task_access
def detail_view(quote_id):
    with db.connect_immediate() as conn:
        full = quotes_repo.get_quote_full(conn, quote_id)
        if full is None or full["quote"]["status"] != "final":
            abort(404)
        tracker_repo.ensure_tracker_seeded(conn, quote_id)
        team = tracker_repo.list_team(conn, quote_id)
        tasks = tracker_repo.list_tasks(conn, quote_id)
        expenses = tracker_repo.list_expenses(conn, quote_id)
        customer_users = users_repo.list_active_users_with_permission(conn, "tracker.view_own")

    totals = quotes_repo.compute_totals(full["rooms"], full["quote"]["vat_percent"])
    progress = tracker_repo.compute_progress(tasks)
    budget = tracker_repo.compute_budget(totals["grand"], expenses)
    grouped_tasks = tracker_repo.group_tasks_by_room(tasks)
    csrf_token = auth.generate_csrf_token()
    return render_template(
        "tracker/detail.html", quote=full["quote"], totals=totals, team=team, tasks=tasks,
        grouped_tasks=grouped_tasks, expenses=expenses, progress=progress, budget=budget,
        roles=tracker_repo.ROLES, stages=quotes_repo.STAGES, customer_users=customer_users,
        csrf_token=csrf_token,
    )


@bp.route("/<int:quote_id>/client-user", methods=["POST"])
@auth.require_permission("quotes.manage")
def set_client_user(quote_id):
    auth.csrf_protect()
    raw = (request.form.get("client_user_id") or "").strip()
    user_id = int(raw) if raw.isdigit() else None
    with db.connect() as conn:
        quotes_repo.set_client_user(conn, quote_id, user_id)
        audit_repo.log(conn, auth.current_user()["id"], "set_client_user", "quote", quote_id, str(user_id))
    flash("Customer access updated.", "success")
    return _back(quote_id)


def _back(quote_id):
    return redirect(url_for("tracker.detail_view", quote_id=quote_id))


# --------------------------------------------------------------------- team

@bp.route("/<int:quote_id>/team/add", methods=["POST"])
@auth.require_permission("quotes.manage")
def add_team_member(quote_id):
    auth.csrf_protect()
    name = (request.form.get("name") or "").strip()
    role = request.form.get("role") or "Labor"
    if not name:
        flash("Enter a name for the team member.", "error")
        return _back(quote_id)
    if role not in tracker_repo.ROLES:
        role = "Other"
    with db.connect() as conn:
        tracker_repo.add_team_member(conn, quote_id, name, role)
    flash(f"Added {name} to the team.", "success")
    return _back(quote_id)


@bp.route("/<int:quote_id>/team/<int:member_id>/remove", methods=["POST"])
@auth.require_permission("quotes.manage")
def remove_team_member(quote_id, member_id):
    auth.csrf_protect()
    with db.connect() as conn:
        tracker_repo.remove_team_member(conn, member_id)
        audit_repo.log(conn, auth.current_user()["id"], "remove_team_member", "quote", quote_id)
    flash("Team member removed.", "success")
    return _back(quote_id)


# --------------------------------------------------------------------- tasks

@bp.route("/<int:quote_id>/tasks/add", methods=["POST"])
@auth.require_permission("quotes.manage")
def add_task(quote_id):
    auth.csrf_protect()
    description = (request.form.get("description") or "").strip()
    weight = request.form.get("weight")
    try:
        weight = float(weight) if weight else 0
    except ValueError:
        weight = 0
    if not description:
        flash("Enter a description for the task.", "error")
        return _back(quote_id)
    with db.connect() as conn:
        tracker_repo.add_custom_task(conn, quote_id, description, weight)
    flash("Task added.", "success")
    return _back(quote_id)


@bp.route("/<int:quote_id>/tasks/<int:task_id>/complete", methods=["POST"])
@auth.require_any_permission("quotes.manage", "tracker.complete_tasks")
def complete_task(quote_id, task_id):
    auth.csrf_protect()
    photo = request.files.get("photo")
    if not photo or not photo.filename:
        flash("Attach a photo of the finished work before marking this item done.", "error")
        return _back(quote_id)
    try:
        photo_filename = tracker_repo.save_task_photo(photo)
    except ValueError as e:
        flash(str(e), "error")
        return _back(quote_id)
    try:
        with db.connect_immediate() as conn:
            tracker_repo.complete_task(conn, task_id, photo_filename)
            audit_repo.log(conn, auth.current_user()["id"], "complete_task", "quote_task", task_id)
        flash("Task marked complete.", "success")
    except (tracker_repo.TaskNotFoundError, tracker_repo.TaskAlreadyCompletedError) as e:
        flash(str(e), "error")
    return _back(quote_id)


@bp.route("/tasks/<int:task_id>/photo")
@auth.login_required
def task_photo(task_id):
    """Serves a completed task's photo. Deliberately NOT public like the
    company logo route -- these are photos of a client's home/site, so
    access is checked explicitly rather than reusing
    require_owner_or_permission (whose resource_fn shape doesn't fit a
    task-id-keyed route cleanly): allowed if the requester is internal
    (anything other than a portal-only account -- an Engineer completing
    tasks needs to see photos too) OR is the customer this task's quote is
    linked to. Uses the shared auth.is_portal_only_user() helper (not an
    ad-hoc {'tracker.view_own'} subset check) so a Vendor-only account
    (Item 5's requisitions.vendor_fill) is correctly excluded too, not
    misclassified as "internal"."""
    with db.connect() as conn:
        row = conn.execute(
            """SELECT t.photo_filename, q.client_user_id
               FROM quote_tasks t JOIN quotes q ON q.id = t.quote_id
               WHERE t.id = ?""",
            (task_id,),
        ).fetchone()
    if row is None or not row["photo_filename"]:
        abort(404)

    user = auth.current_user()
    perms = getattr(auth.g, "permissions", set())
    # is_portal_only_user() now also flags an Engineer-only account
    # (tracker.complete_tasks was added to _PORTAL_ONLY_CODES so Engineers
    # are correctly blocked from every OTHER internal area) -- but for
    # THIS route specifically, an Engineer completing tasks still needs to
    # see the photos, so that one permission is allowed back in explicitly
    # rather than treating "portal-only" as a blanket exclusion here.
    is_internal = (not auth.is_portal_only_user(perms)) or ("tracker.complete_tasks" in perms)
    is_linked_customer = row["client_user_id"] is not None and row["client_user_id"] == user["id"]
    if not (is_internal or is_linked_customer):
        abort(403)

    path = tracker_repo.task_photo_path(row["photo_filename"])
    if not path:
        abort(404)
    return send_from_directory(config.UPLOADS_DIR, row["photo_filename"])


@bp.route("/<int:quote_id>/tasks/<int:task_id>/deadline", methods=["POST"])
@auth.require_permission("quotes.manage")
def set_task_deadline(quote_id, task_id):
    auth.csrf_protect()
    deadline = (request.form.get("deadline") or "").strip() or None
    try:
        with db.connect_immediate() as conn:
            tracker_repo.set_task_deadline(conn, task_id, deadline)
    except (tracker_repo.TaskNotFoundError, tracker_repo.TaskImmutableError) as e:
        flash(str(e), "error")
    return _back(quote_id)


@bp.route("/<int:quote_id>/tasks/<int:task_id>/assign/<int:member_id>", methods=["POST"])
@auth.require_permission("quotes.manage")
def toggle_assignment(quote_id, task_id, member_id):
    auth.csrf_protect()
    try:
        with db.connect_immediate() as conn:
            tracker_repo.toggle_task_assignment(conn, task_id, member_id)
    except (tracker_repo.TaskNotFoundError, tracker_repo.TaskImmutableError) as e:
        flash(str(e), "error")
    return _back(quote_id)


@bp.route("/<int:quote_id>/tasks/<int:task_id>/delete", methods=["POST"])
@auth.require_permission("quotes.manage")
def delete_task(quote_id, task_id):
    auth.csrf_protect()
    try:
        with db.connect_immediate() as conn:
            tracker_repo.delete_task(conn, task_id)
            audit_repo.log(conn, auth.current_user()["id"], "delete_task", "quote_task", task_id)
        flash("Task deleted.", "success")
    except (tracker_repo.TaskNotFoundError, tracker_repo.TaskImmutableError) as e:
        flash(str(e), "error")
    return _back(quote_id)


# ------------------------------------------------------------------ expenses

@bp.route("/<int:quote_id>/expenses/add", methods=["POST"])
@auth.require_permission("quotes.manage")
def add_expense(quote_id):
    auth.csrf_protect()
    description = (request.form.get("description") or "").strip()
    amount = request.form.get("amount")
    expense_date = (request.form.get("expense_date") or "").strip() or datetime.date.today().isoformat()
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        flash("Enter a valid amount.", "error")
        return _back(quote_id)
    if not description:
        flash("Enter a description for the expense.", "error")
        return _back(quote_id)
    with db.connect() as conn:
        tracker_repo.add_expense(conn, quote_id, description, amount, expense_date, auth.current_user()["id"])
        audit_repo.log(conn, auth.current_user()["id"], "add_expense", "quote", quote_id, description)
    flash("Expense recorded.", "success")
    return _back(quote_id)


@bp.route("/<int:quote_id>/expenses/<int:expense_id>/delete", methods=["POST"])
@auth.require_permission("quotes.manage")
def delete_expense(quote_id, expense_id):
    auth.csrf_protect()
    with db.connect() as conn:
        tracker_repo.delete_expense(conn, expense_id)
        audit_repo.log(conn, auth.current_user()["id"], "delete_expense", "quote", quote_id)
    flash("Expense removed.", "success")
    return _back(quote_id)
