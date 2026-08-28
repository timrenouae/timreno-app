"""SQL access for suppliers.

Item 5 (Material Requisitions) adds vendor_code (permanent, immutable once
set -- never written by update_supplier or any other path after creation)
and portal_user_id (links a supplier to a Vendor-portal login, same pattern
as quotes.client_user_id links a quote to a Customer-portal login).
"""


def _next_vendor_code(conn):
    """Global sequential VEND-NNNN, not month-scoped -- a vendor is
    onboarded once. Mirrors repositories/purchase_orders.py: _next_po_number's
    pattern but simpler (no date prefix)."""
    row = conn.execute(
        "SELECT vendor_code FROM suppliers WHERE vendor_code LIKE 'VEND-%' ORDER BY vendor_code DESC LIMIT 1"
    ).fetchone()
    if row is None or row["vendor_code"] is None:
        seq = 1
    else:
        seq = int(row["vendor_code"].rsplit("-", 1)[-1]) + 1
    return f"VEND-{seq:04d}"


def list_suppliers(conn, include_inactive=False):
    where = "" if include_inactive else "WHERE active = 1"
    return conn.execute(f"SELECT * FROM suppliers {where} ORDER BY name").fetchall()


def get_supplier(conn, supplier_id):
    return conn.execute("SELECT * FROM suppliers WHERE id = ?", (supplier_id,)).fetchone()


def create_supplier(conn, name, contact_name, phone, email, address):
    vendor_code = _next_vendor_code(conn)
    cur = conn.execute(
        """INSERT INTO suppliers (name, contact_name, phone, email, address, vendor_code)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (name, contact_name, phone, email, address, vendor_code),
    )
    return cur.lastrowid


def update_supplier(conn, supplier_id, name, contact_name, phone, email, address):
    # Deliberately never touches vendor_code -- write-once at creation only,
    # which is what actually enforces "cannot be changed" (not just hiding
    # the input on the edit form).
    conn.execute(
        """UPDATE suppliers SET name = ?, contact_name = ?, phone = ?, email = ?, address = ?
           WHERE id = ?""",
        (name, contact_name, phone, email, address, supplier_id),
    )


def set_active(conn, supplier_id, active: bool):
    conn.execute("UPDATE suppliers SET active = ? WHERE id = ?", (1 if active else 0, supplier_id))


def set_vendor_user(conn, supplier_id, user_id):
    """Links (or, with user_id=None, unlinks) a Vendor-portal login to this
    supplier -- same shape as repositories/quotes.py: set_client_user."""
    conn.execute("UPDATE suppliers SET portal_user_id = ? WHERE id = ?", (user_id, supplier_id))


def get_supplier_by_portal_user(conn, user_id):
    """For the Vendor portal's own 'which requisitions are mine' lookup."""
    return conn.execute("SELECT * FROM suppliers WHERE portal_user_id = ?", (user_id,)).fetchone()
