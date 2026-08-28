from flask import Blueprint, render_template, request, redirect, url_for, flash, abort

import auth
import db
import repositories.users as users_repo
import repositories.roles as roles_repo
import repositories.audit as audit_repo

bp = Blueprint("admin", __name__, url_prefix="/admin")


# ------------------------------------------------------------------ users

@bp.route("/users")
@auth.require_permission("users.manage")
def users_view():
    with db.connect() as conn:
        all_users = users_repo.list_users(conn)
        all_roles = roles_repo.list_roles(conn)
    return render_template("admin/users.html", users=all_users, roles=all_roles,
                            csrf_token=auth.generate_csrf_token())


@bp.route("/users/new", methods=["GET", "POST"])
@auth.require_permission("users.manage")
def user_new():
    with db.connect() as conn:
        all_roles = roles_repo.list_roles(conn)

    if request.method == "GET":
        return render_template("admin/user_form.html", user=None, roles=all_roles,
                                csrf_token=auth.generate_csrf_token())

    auth.csrf_protect()
    username = (request.form.get("username") or "").strip()
    display_name = (request.form.get("display_name") or "").strip()
    password = request.form.get("password") or ""
    role_id = request.form.get("role_id")

    if not username or not display_name or not password or not role_id:
        flash("All fields are required.", "error")
        return render_template("admin/user_form.html", user=None, roles=all_roles,
                                csrf_token=auth.generate_csrf_token()), 400
    if len(password) < 8:
        flash("Password must be at least 8 characters.", "error")
        return render_template("admin/user_form.html", user=None, roles=all_roles,
                                csrf_token=auth.generate_csrf_token()), 400

    with db.connect() as conn:
        if users_repo.username_exists(conn, username):
            flash(f"Username '{username}' is already taken.", "error")
            return render_template("admin/user_form.html", user=None, roles=all_roles,
                                    csrf_token=auth.generate_csrf_token()), 400
        pw_hash, salt, iterations = auth.hash_password(password)
        new_id = users_repo.create_user(conn, username, display_name, pw_hash, salt, iterations, int(role_id))
        audit_repo.log(conn, auth.current_user()["id"], "create", "user", new_id, f"username={username}")

    flash(f"User '{username}' created.", "success")
    return redirect(url_for("admin.users_view"))


@bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@auth.require_permission("users.manage")
def user_edit(user_id):
    with db.connect() as conn:
        user = users_repo.get_user(conn, user_id)
        all_roles = roles_repo.list_roles(conn)
    if user is None:
        abort(404)

    if request.method == "GET":
        return render_template("admin/user_form.html", user=user, roles=all_roles,
                                csrf_token=auth.generate_csrf_token())

    auth.csrf_protect()
    display_name = (request.form.get("display_name") or "").strip()
    role_id = int(request.form.get("role_id"))
    new_password = request.form.get("password") or ""

    if not display_name:
        flash("Display name is required.", "error")
        return render_template("admin/user_form.html", user=user, roles=all_roles,
                                csrf_token=auth.generate_csrf_token()), 400
    if new_password and len(new_password) < 8:
        flash("Password must be at least 8 characters.", "error")
        return render_template("admin/user_form.html", user=user, roles=all_roles,
                                csrf_token=auth.generate_csrf_token()), 400

    role_changing = role_id != user["role_id"]

    with db.connect() as conn:
        if role_changing:
            # If this user currently holds the admin invariant, make sure
            # someone else still would after the reassignment.
            try:
                auth.assert_admin_invariant_preserved(conn, excluding_user_id=user_id)
            except auth.LastAdminError as e:
                flash(str(e), "error")
                return redirect(url_for("admin.user_edit", user_id=user_id))

        users_repo.update_profile(conn, user_id, display_name, role_id)
        if new_password:
            pw_hash, salt, iterations = auth.hash_password(new_password)
            users_repo.update_password(conn, user_id, pw_hash, salt, iterations)
        if role_changing:
            # Force re-login everywhere so a stale session can't keep using
            # the old role's permissions (session fixation on privilege change).
            auth.revoke_all_sessions_for_user(conn, user_id)
        audit_repo.log(conn, auth.current_user()["id"], "update", "user", user_id,
                        f"role_changed={role_changing} password_changed={bool(new_password)}")

    flash("User updated.", "success")
    return redirect(url_for("admin.users_view"))


@bp.route("/users/<int:user_id>/toggle-active", methods=["POST"])
@auth.require_permission("users.manage")
def user_toggle_active(user_id):
    auth.csrf_protect()
    with db.connect() as conn:
        user = users_repo.get_user(conn, user_id)
        if user is None:
            abort(404)
        deactivating = bool(user["active"])
        if deactivating:
            try:
                auth.assert_admin_invariant_preserved(conn, excluding_user_id=user_id)
            except auth.LastAdminError as e:
                flash(str(e), "error")
                return redirect(url_for("admin.users_view"))
        users_repo.set_active(conn, user_id, not deactivating)
        if deactivating:
            auth.revoke_all_sessions_for_user(conn, user_id)
        audit_repo.log(conn, auth.current_user()["id"], "deactivate" if deactivating else "activate",
                        "user", user_id)
    flash("User " + ("deactivated." if deactivating else "reactivated."), "success")
    return redirect(url_for("admin.users_view"))


# ------------------------------------------------------------------ roles

@bp.route("/roles")
@auth.require_permission("roles.manage")
def roles_view():
    with db.connect() as conn:
        all_roles = roles_repo.list_roles(conn)
    return render_template("admin/roles.html", roles=all_roles, csrf_token=auth.generate_csrf_token())


@bp.route("/roles/new", methods=["GET", "POST"])
@auth.require_permission("roles.manage")
def role_new():
    with db.connect() as conn:
        all_permissions = roles_repo.list_all_permissions(conn)

    if request.method == "GET":
        return render_template("admin/role_form.html", role=None, all_permissions=all_permissions,
                                role_permissions=set(), csrf_token=auth.generate_csrf_token())

    auth.csrf_protect()
    name = (request.form.get("name") or "").strip()
    selected = request.form.getlist("permissions")

    if not name:
        flash("Role name is required.", "error")
        return render_template("admin/role_form.html", role=None, all_permissions=all_permissions,
                                role_permissions=set(selected), csrf_token=auth.generate_csrf_token()), 400

    with db.connect() as conn:
        if roles_repo.get_role_by_name(conn, name):
            flash(f"A role named '{name}' already exists.", "error")
            return render_template("admin/role_form.html", role=None, all_permissions=all_permissions,
                                    role_permissions=set(selected), csrf_token=auth.generate_csrf_token()), 400
        role_id = roles_repo.create_role(conn, name)
        roles_repo.set_role_permissions(conn, role_id, selected)
        audit_repo.log(conn, auth.current_user()["id"], "create", "role", role_id, f"name={name}")

    flash(f"Role '{name}' created.", "success")
    return redirect(url_for("admin.roles_view"))


@bp.route("/roles/<int:role_id>/edit", methods=["GET", "POST"])
@auth.require_permission("roles.manage")
def role_edit(role_id):
    with db.connect() as conn:
        role = roles_repo.get_role(conn, role_id)
        all_permissions = roles_repo.list_all_permissions(conn)
        current_perms = roles_repo.role_permission_codes(conn, role_id)
    if role is None:
        abort(404)

    if request.method == "GET":
        return render_template("admin/role_form.html", role=role, all_permissions=all_permissions,
                                role_permissions=current_perms, csrf_token=auth.generate_csrf_token())

    auth.csrf_protect()
    name = (request.form.get("name") or "").strip()
    selected = set(request.form.getlist("permissions"))

    if role["is_system"]:
        # The protected Admin role can be renamed but must always retain
        # the two permissions that guarantee at least one full admin exists.
        selected |= {"users.manage", "roles.manage"}

    if not name:
        flash("Role name is required.", "error")
        return render_template("admin/role_form.html", role=role, all_permissions=all_permissions,
                                role_permissions=selected, csrf_token=auth.generate_csrf_token()), 400

    with db.connect() as conn:
        # If this role is losing users.manage/roles.manage, make sure some
        # OTHER active admin-capable user still exists after the change.
        try:
            auth.assert_role_update_preserves_admin_invariant(conn, role_id, selected)
        except auth.LastAdminError as e:
            flash(str(e), "error")
            return redirect(url_for("admin.role_edit", role_id=role_id))
        roles_repo.rename_role(conn, role_id, name)
        roles_repo.set_role_permissions(conn, role_id, selected)
        # Revoke sessions for everyone with this role so permission changes
        # take effect immediately rather than at next natural expiry.
        for u in users_repo.list_users(conn):
            if u["role_id"] == role_id:
                auth.revoke_all_sessions_for_user(conn, u["id"])
        audit_repo.log(conn, auth.current_user()["id"], "update", "role", role_id, f"name={name}")

    flash("Role updated.", "success")
    return redirect(url_for("admin.roles_view"))


@bp.route("/roles/<int:role_id>/delete", methods=["POST"])
@auth.require_permission("roles.manage")
def role_delete(role_id):
    auth.csrf_protect()
    with db.connect() as conn:
        role = roles_repo.get_role(conn, role_id)
        if role is None:
            abort(404)
        if role["is_system"]:
            flash("The Admin role is protected and cannot be deleted.", "error")
            return redirect(url_for("admin.roles_view"))
        if roles_repo.role_in_use(conn, role_id):
            flash("This role is still assigned to one or more users and cannot be deleted.", "error")
            return redirect(url_for("admin.roles_view"))
        roles_repo.delete_role(conn, role_id)
        audit_repo.log(conn, auth.current_user()["id"], "delete", "role", role_id)
    flash("Role deleted.", "success")
    return redirect(url_for("admin.roles_view"))
