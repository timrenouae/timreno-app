"""SQL access for the product master."""


def _build_where(query, category, brand, brand_is_none, include_inactive):
    """Shared by search_products/count_products. `query` is split on
    whitespace and AND-matched (every keyword must appear somewhere in the
    description, in any order) -- ported from the old Quote Builder's
    multi-keyword search so "socket schneider" and "schneider socket"
    return the same results, not just an exact-phrase substring match.
    `brand_is_none=True` matches the old tool's "no brand listed" filter
    option (a few hundred catalog items have no trailing "(BRAND)" tag)."""
    clauses = []
    params = []
    if not include_inactive:
        clauses.append("active = 1")
    if query:
        for keyword in query.split():
            clauses.append("description LIKE ?")
            params.append(f"%{keyword}%")
    if category:
        clauses.append("category = ?")
        params.append(category)
    if brand_is_none:
        clauses.append("(brand IS NULL OR brand = '')")
    elif brand:
        clauses.append("brand = ?")
        params.append(brand)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def search_products(conn, query=None, category=None, brand=None, brand_is_none=False,
                     include_inactive=False, limit=200, offset=0):
    where, params = _build_where(query, category, brand, brand_is_none, include_inactive)
    sql = f"""SELECT * FROM products {where}
              ORDER BY category, description LIMIT ? OFFSET ?"""
    params = params + [limit, offset]
    return conn.execute(sql, params).fetchall()


def count_products(conn, query=None, category=None, brand=None, brand_is_none=False, include_inactive=False):
    where, params = _build_where(query, category, brand, brand_is_none, include_inactive)
    row = conn.execute(f"SELECT COUNT(*) c FROM products {where}", params).fetchone()
    return row["c"]


def list_categories(conn):
    rows = conn.execute("SELECT DISTINCT category FROM products WHERE active = 1 ORDER BY category").fetchall()
    return [r["category"] for r in rows]


def list_brands(conn):
    rows = conn.execute(
        "SELECT DISTINCT brand FROM products WHERE active = 1 AND brand IS NOT NULL AND brand != '' ORDER BY brand"
    ).fetchall()
    return [r["brand"] for r in rows]


def get_product(conn, product_id):
    return conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()


def create_product(conn, category, description, unit, brand, cost_price):
    cur = conn.execute(
        """INSERT INTO products (category, description, unit, brand, cost_price)
           VALUES (?, ?, ?, ?, ?)""",
        (category, description, unit, brand, cost_price),
    )
    return cur.lastrowid


def update_product(conn, product_id, category, description, unit, brand, cost_price):
    conn.execute(
        """UPDATE products SET category = ?, description = ?, unit = ?, brand = ?,
                                cost_price = ?, updated_at = datetime('now')
           WHERE id = ?""",
        (category, description, unit, brand, cost_price, product_id),
    )


def set_active(conn, product_id, active: bool):
    conn.execute(
        "UPDATE products SET active = ?, updated_at = datetime('now') WHERE id = ?",
        (1 if active else 0, product_id),
    )


def update_cost_price(conn, product_id, new_price):
    """Used only from the PO-receive flow; logs no history itself -- caller
    (repositories/purchase_orders.py) writes the product_price_history row
    in the same transaction."""
    conn.execute(
        "UPDATE products SET cost_price = ?, updated_at = datetime('now') WHERE id = ?",
        (new_price, product_id),
    )


def bulk_insert(conn, rows):
    """rows: iterable of (category, description, unit, brand, cost_price).
    Used by the one-time PRICE_ITEMS migration script."""
    conn.executemany(
        """INSERT INTO products (category, description, unit, brand, cost_price)
           VALUES (?, ?, ?, ?, ?)""",
        rows,
    )
