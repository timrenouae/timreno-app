"""SQL access for suppliers."""


def list_suppliers(conn, include_inactive=False):
    where = "" if include_inactive else "WHERE active = 1"
    return conn.execute(f"SELECT * FROM suppliers {where} ORDER BY name").fetchall()


def get_supplier(conn, supplier_id):
    return conn.execute("SELECT * FROM suppliers WHERE id = ?", (supplier_id,)).fetchone()


def create_supplier(conn, name, contact_name, phone, email, address):
    cur = conn.execute(
        """INSERT INTO suppliers (name, contact_name, phone, email, address)
           VALUES (?, ?, ?, ?, ?)""",
        (name, contact_name, phone, email, address),
    )
    return cur.lastrowid


def update_supplier(conn, supplier_id, name, contact_name, phone, email, address):
    conn.execute(
        """UPDATE suppliers SET name = ?, contact_name = ?, phone = ?, email = ?, address = ?
           WHERE id = ?""",
        (name, contact_name, phone, email, address, supplier_id),
    )


def set_active(conn, supplier_id, active: bool):
    conn.execute("UPDATE suppliers SET active = ? WHERE id = ?", (1 if active else 0, supplier_id))
