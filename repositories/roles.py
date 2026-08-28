"""SQL access for roles + role_permissions. All raw SQL for this entity
lives here so blueprints never build queries themselves."""


def list_roles(conn):
    return conn.execute("SELECT * FROM roles ORDER BY is_system DESC, name").fetchall()


def get_role(conn, role_id):
    return conn.execute("SELECT * FROM roles WHERE id = ?", (role_id,)).fetchone()


def get_role_by_name(conn, name):
    return conn.execute("SELECT * FROM roles WHERE name = ?", (name,)).fetchone()


def create_role(conn, name):
    cur = conn.execute("INSERT INTO roles (name, is_system) VALUES (?, 0)", (name,))
    return cur.lastrowid


def rename_role(conn, role_id, name):
    conn.execute("UPDATE roles SET name = ? WHERE id = ?", (name, role_id))


def delete_role(conn, role_id):
    conn.execute("DELETE FROM roles WHERE id = ?", (role_id,))


def role_permission_codes(conn, role_id):
    rows = conn.execute(
        """SELECT p.code FROM permissions p
           JOIN role_permissions rp ON rp.permission_id = p.id
           WHERE rp.role_id = ?""",
        (role_id,),
    ).fetchall()
    return {r["code"] for r in rows}


def set_role_permissions(conn, role_id, permission_codes):
    """Replaces the full permission set for a role."""
    conn.execute("DELETE FROM role_permissions WHERE role_id = ?", (role_id,))
    if not permission_codes:
        return
    placeholders = ",".join("?" for _ in permission_codes)
    perm_rows = conn.execute(
        f"SELECT id FROM permissions WHERE code IN ({placeholders})",
        list(permission_codes),
    ).fetchall()
    for row in perm_rows:
        conn.execute(
            "INSERT INTO role_permissions (role_id, permission_id) VALUES (?, ?)",
            (role_id, row["id"]),
        )


def list_all_permissions(conn):
    return conn.execute("SELECT * FROM permissions ORDER BY code").fetchall()


def role_in_use(conn, role_id) -> bool:
    row = conn.execute("SELECT COUNT(*) c FROM users WHERE role_id = ?", (role_id,)).fetchone()
    return row["c"] > 0


def find_role_with_exact_permissions(conn, permission_codes):
    """Returns the first role row whose permission set is EXACTLY
    `permission_codes` (a set), or None. Self-service precondition for a
    "quick-create login" action: e.g. Item 5's vendor login button is only
    enabled once the owner has created a role holding just
    requisitions.vendor_fill and nothing else -- same idea as the Tracker's
    'create a Customer role first' messaging, generalized to any exact
    single-purpose permission set."""
    target = set(permission_codes)
    for role in conn.execute("SELECT * FROM roles").fetchall():
        if role_permission_codes(conn, role["id"]) == target:
            return role
    return None
