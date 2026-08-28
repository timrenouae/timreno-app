"""
SQL access for purchase orders / lines, including the receive-and-reprice
flow. The receive functions here assume the caller has already opened the
connection with db.connect_immediate() (BEGIN IMMEDIATE), so the row lock
is held for the whole check-then-write sequence and a second concurrent
request blocks until this one commits or rolls back -- not a race.
"""
from datetime import date


def _next_po_number(conn):
    today = date.today()
    prefix = f"PO-{today.strftime('%Y%m')}-"
    row = conn.execute(
        "SELECT po_number FROM purchase_orders WHERE po_number LIKE ? ORDER BY po_number DESC LIMIT 1",
        (f"{prefix}%",),
    ).fetchone()
    if row is None:
        seq = 1
    else:
        seq = int(row["po_number"].rsplit("-", 1)[-1]) + 1
    return f"{prefix}{seq:04d}"


def list_purchase_orders(conn):
    return conn.execute(
        """SELECT po.*, s.name as supplier_name FROM purchase_orders po
           JOIN suppliers s ON s.id = po.supplier_id
           ORDER BY po.created_at DESC"""
    ).fetchall()


def get_purchase_order(conn, po_id):
    return conn.execute(
        """SELECT po.*, s.name as supplier_name, s.contact_name, s.phone, s.email, s.address
           FROM purchase_orders po JOIN suppliers s ON s.id = po.supplier_id
           WHERE po.id = ?""",
        (po_id,),
    ).fetchone()


def get_po_by_number(conn, po_number):
    return conn.execute("SELECT * FROM purchase_orders WHERE po_number = ?", (po_number,)).fetchone()


def list_po_items(conn, po_id):
    return conn.execute(
        "SELECT * FROM purchase_order_items WHERE po_id = ? ORDER BY id ASC", (po_id,)
    ).fetchall()


def create_purchase_order(conn, supplier_id, notes, created_by, items):
    """items: list of dicts with product_id, description, unit, brand,
    quantity, unit_price (snapshotted at creation time)."""
    po_number = _next_po_number(conn)
    cur = conn.execute(
        """INSERT INTO purchase_orders (po_number, supplier_id, status, notes, created_by)
           VALUES (?, ?, 'draft', ?, ?)""",
        (po_number, supplier_id, notes, created_by),
    )
    po_id = cur.lastrowid
    for item in items:
        conn.execute(
            """INSERT INTO purchase_order_items
               (po_id, product_id, description, unit, brand, quantity, unit_price)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                po_id,
                item["product_id"],
                item["description"],
                item["unit"],
                item.get("brand"),
                item["quantity"],
                item["unit_price"],
            ),
        )
    return po_id


def mark_sent(conn, po_id):
    conn.execute(
        "UPDATE purchase_orders SET status = 'sent' WHERE id = ? AND status = 'draft'", (po_id,)
    )


def _recompute_status(conn, po_id):
    rows = conn.execute(
        "SELECT quantity, received_qty FROM purchase_order_items WHERE po_id = ?", (po_id,)
    ).fetchall()
    if not rows:
        return
    if all(r["received_qty"] >= r["quantity"] for r in rows):
        new_status = "received"
    elif any(r["received_qty"] > 0 for r in rows):
        new_status = "partially_received"
    else:
        return  # still draft/sent, nothing received yet
    conn.execute("UPDATE purchase_orders SET status = ? WHERE id = ?", (new_status, po_id))


class AlreadyReceivedError(Exception):
    pass


def receive_line(conn, po_item_id, received_qty, user_id):
    """Must be called on a connection opened with db.connect_immediate().
    Re-checks the line's current received_qty against its ordered quantity
    inside the held write-lock, so two concurrent 'mark received' clicks on
    the same line cannot both apply -- the second sees the already-updated
    row and raises instead of double-crediting or double-pricing.
    """
    line = conn.execute(
        "SELECT * FROM purchase_order_items WHERE id = ?", (po_item_id,)
    ).fetchone()
    if line is None:
        raise ValueError("Purchase order line not found")
    if line["received_qty"] >= line["quantity"]:
        raise AlreadyReceivedError("This line has already been fully received")

    new_received_qty = min(line["quantity"], line["received_qty"] + received_qty)
    conn.execute(
        """UPDATE purchase_order_items SET received_qty = ?, received_at = datetime('now')
           WHERE id = ?""",
        (new_received_qty, po_item_id),
    )

    # Reprice the product to this line's unit price and log history,
    # regardless of whether other lines in this (or another) PO reference
    # the same product -- last write wins for the live price, but every
    # received line still gets its own history row.
    product = conn.execute("SELECT * FROM products WHERE id = ?", (line["product_id"],)).fetchone()
    old_price = product["cost_price"]
    new_price = line["unit_price"]
    conn.execute(
        "UPDATE products SET cost_price = ?, updated_at = datetime('now') WHERE id = ?",
        (new_price, line["product_id"]),
    )
    conn.execute(
        """INSERT INTO product_price_history
           (product_id, old_price, new_price, source_po_item_id, changed_by_user_id)
           VALUES (?, ?, ?, ?, ?)""",
        (line["product_id"], old_price, new_price, po_item_id, user_id),
    )

    _recompute_status(conn, line["po_id"])
    return new_received_qty
