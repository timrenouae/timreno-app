"""SQL access for users."""


def list_users(conn):
    return conn.execute(
        """SELECT u.*, r.name as role_name FROM users u
           JOIN roles r ON r.id = u.role_id
           ORDER BY u.active DESC, u.username"""
    ).fetchall()


def get_user(conn, user_id):
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def get_user_by_username(conn, username):
    return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


def username_exists(conn, username, excluding_user_id=None):
    if excluding_user_id:
        row = conn.execute(
            "SELECT 1 FROM users WHERE username = ? AND id != ?", (username, excluding_user_id)
        ).fetchone()
    else:
        row = conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
    return row is not None


def create_user(conn, username, display_name, password_hash, password_salt, iterations, role_id):
    cur = conn.execute(
        """INSERT INTO users (username, display_name, password_hash, password_salt,
                               pbkdf2_iterations, role_id, active)
           VALUES (?, ?, ?, ?, ?, ?, 1)""",
        (username, display_name, password_hash, password_salt, iterations, role_id),
    )
    return cur.lastrowid


def update_profile(conn, user_id, display_name, role_id):
    conn.execute(
        "UPDATE users SET display_name = ?, role_id = ? WHERE id = ?",
        (display_name, role_id, user_id),
    )


def update_password(conn, user_id, password_hash, password_salt, iterations):
    conn.execute(
        """UPDATE users SET password_hash = ?, password_salt = ?, pbkdf2_iterations = ?
           WHERE id = ?""",
        (password_hash, password_salt, iterations, user_id),
    )


def set_active(conn, user_id, active: bool):
    conn.execute("UPDATE users SET active = ? WHERE id = ?", (1 if active else 0, user_id))


def list_active_users_with_permission(conn, permission_code):
    """Active users whose role carries this permission code -- used to
    populate the Tracker detail page's "link a customer login" dropdown
    (permission_code='tracker.view_own')."""
    return conn.execute(
        """SELECT DISTINCT u.* FROM users u
           JOIN role_permissions rp ON rp.role_id = u.role_id
           JOIN permissions p ON p.id = rp.permission_id AND p.code = ?
           WHERE u.active = 1
           ORDER BY u.display_name""",
        (permission_code,),
    ).fetchall()


def count_active_users_with_role(conn, role_id, excluding_user_id=None):
    if excluding_user_id:
        row = conn.execute(
            "SELECT COUNT(*) c FROM users WHERE role_id = ? AND active = 1 AND id != ?",
            (role_id, excluding_user_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) c FROM users WHERE role_id = ? AND active = 1", (role_id,)
        ).fetchone()
    return row["c"]
