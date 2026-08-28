"""
Hand-rolled migration runner (no Alembic available in this sandbox).

Applies every .sql file in migrations/ (sorted by filename) that isn't
already recorded in schema_migrations, in one transaction each. Also seeds
the fixed permissions catalog and the protected Admin role, idempotently,
so re-running this script after a code update (which may add new
permission codes) is always safe.

Run: python3 migrate.py
"""
import glob
import os
import sqlite3

import config

MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")

# The fixed permission catalog. Not editable from the admin UI -- new
# capabilities are added here in code and picked up next time this script
# runs. code -> human description.
PERMISSIONS = {
    "users.manage": "Create, edit, activate/deactivate user accounts",
    "roles.manage": "Create, edit roles and their permissions",
    "products.manage": "Add/edit products and prices in the Product Master",
    "suppliers.manage": "Add/edit suppliers",
    "purchases.manage": "Create purchase orders, generate supplier PDFs, mark lines received",
    "quotes.manage": "Create/edit quotes, run the Rough Estimator, and manage Project Tracker tasks/team/expenses",
    "tracker.view_own": "Customer portal: log in and view read-only progress on projects linked to this account "
                         "(no pricing, budget, or team info) -- give this to a Customer role, nothing else",
    "settings.manage": "Edit company details (name, address, TRN, bank details) and the Document Builder "
                        "(logo, colors, font, terms & conditions, disclaimers) used across the app's PDFs",
}


def _ensure_schema_migrations_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


def apply_migrations():
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    _ensure_schema_migrations_table(conn)

    applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    files = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.sql")))

    for path in files:
        version = os.path.basename(path)
        if version in applied:
            continue
        with open(path, "r") as f:
            sql = f.read()
        print(f"Applying migration {version} ...")
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_migrations (version) VALUES (?)", (version,)
        )
        conn.commit()
        print(f"  applied.")

    conn.close()


def seed_permissions_and_admin_role():
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row

    for code, description in PERMISSIONS.items():
        conn.execute(
            """INSERT INTO permissions (code, description) VALUES (?, ?)
               ON CONFLICT(code) DO UPDATE SET description = excluded.description""",
            (code, description),
        )
    conn.commit()

    admin_role = conn.execute("SELECT id FROM roles WHERE name = 'Admin'").fetchone()
    if admin_role is None:
        conn.execute("INSERT INTO roles (name, is_system) VALUES ('Admin', 1)")
        conn.commit()
        admin_role = conn.execute("SELECT id FROM roles WHERE name = 'Admin'").fetchone()
    admin_role_id = admin_role["id"]

    all_perm_ids = [row["id"] for row in conn.execute("SELECT id FROM permissions")]
    for pid in all_perm_ids:
        conn.execute(
            """INSERT OR IGNORE INTO role_permissions (role_id, permission_id)
               VALUES (?, ?)""",
            (admin_role_id, pid),
        )
    conn.commit()
    conn.close()
    print("Seeded permissions catalog and Admin role (all permissions granted).")


if __name__ == "__main__":
    apply_migrations()
    seed_permissions_and_admin_role()
    print("Migration complete.")
