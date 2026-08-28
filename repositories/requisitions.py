"""
SQL access for Material Requisitions (Item 5): a multi-vendor "please quote
me these items" workflow, separate from Purchase Orders (a PO is a firm
order at an agreed price; a requisition is a request for pricing that later
converts into a PO once a vendor is chosen).

Data model recap (see migrations/0008_material_requisitions.sql):
  material_requisitions        -- one requisition, status open/awarded/cancelled
  material_requisition_items   -- the master item list (no price -- price is per-vendor)
  material_requisition_vendors -- one row per vendor invited to a requisition
  material_requisition_prices  -- one row per vendor per item, upserted only
                                   once that vendor actually prices a line
"""
from datetime import date


class RequisitionNotFoundError(Exception):
    pass


class RequisitionLockedError(Exception):
    """Raised when trying to wholesale-replace a requisition's item list
    after it's no longer open, or after a vendor has already submitted."""
    pass


class RequisitionVendorNotFoundError(Exception):
    pass


class VendorAlreadySubmittedError(Exception):
    pass


class IncompletePricingError(Exception):
    pass


# -------------------------------------------------------------- numbering

def _next_requisition_no(conn):
    """Month-scoped MR-YYYYMM-NNNN, same pattern as
    repositories/purchase_orders.py: _next_po_number."""
    today = date.today()
    prefix = f"MR-{today.strftime('%Y%m')}-"
    row = conn.execute(
        "SELECT requisition_no FROM material_requisitions WHERE requisition_no LIKE ? "
        "ORDER BY requisition_no DESC LIMIT 1",
        (f"{prefix}%",),
    ).fetchone()
    if row is None:
        seq = 1
    else:
        seq = int(row["requisition_no"].rsplit("-", 1)[-1]) + 1
    return f"{prefix}{seq:04d}"


# -------------------------------------------------------------------- CRUD

def create_requisition(conn, notes, created_by):
    requisition_no = _next_requisition_no(conn)
    cur = conn.execute(
        "INSERT INTO material_requisitions (requisition_no, notes, status, created_by) VALUES (?, ?, 'open', ?)",
        (requisition_no, notes, created_by),
    )
    return cur.lastrowid


def get_requisition(conn, requisition_id):
    return conn.execute("SELECT * FROM material_requisitions WHERE id = ?", (requisition_id,)).fetchone()


def list_requisitions(conn):
    """Staff-side list -- every requisition, newest first, with an invited-
    vendor count so the list view can show 'sent to N vendors' at a glance."""
    return conn.execute(
        """SELECT mr.*, (SELECT COUNT(*) FROM material_requisition_vendors mrv
                          WHERE mrv.requisition_id = mr.id) AS vendor_count,
                        (SELECT COUNT(*) FROM material_requisition_vendors mrv
                         WHERE mrv.requisition_id = mr.id AND mrv.status = 'submitted') AS submitted_count
           FROM material_requisitions mr ORDER BY mr.created_at DESC"""
    ).fetchall()


def list_requisitions_for_vendor(conn, supplier_id):
    """Every requisition this vendor's linked supplier was invited to, with
    their own status -- for the Vendor portal dashboard."""
    return conn.execute(
        """SELECT mrv.*, mr.requisition_no, mr.status AS requisition_status, mr.notes, mr.created_at
           FROM material_requisition_vendors mrv
           JOIN material_requisitions mr ON mr.id = mrv.requisition_id
           WHERE mrv.supplier_id = ?
           ORDER BY mr.created_at DESC""",
        (supplier_id,),
    ).fetchall()


def list_items(conn, requisition_id):
    return conn.execute(
        "SELECT * FROM material_requisition_items WHERE requisition_id = ? ORDER BY position, id",
        (requisition_id,),
    ).fetchall()


def list_vendors(conn, requisition_id):
    return conn.execute(
        """SELECT mrv.*, s.name AS supplier_name, s.vendor_code
           FROM material_requisition_vendors mrv JOIN suppliers s ON s.id = mrv.supplier_id
           WHERE mrv.requisition_id = ? ORDER BY s.name""",
        (requisition_id,),
    ).fetchall()


def get_requisition_vendor(conn, rv_id):
    return conn.execute(
        """SELECT mrv.*, s.name AS supplier_name, s.vendor_code
           FROM material_requisition_vendors mrv JOIN suppliers s ON s.id = mrv.supplier_id
           WHERE mrv.id = ?""",
        (rv_id,),
    ).fetchone()


def list_prices_for_vendor(conn, requisition_vendor_id):
    return conn.execute(
        "SELECT * FROM material_requisition_prices WHERE requisition_vendor_id = ?",
        (requisition_vendor_id,),
    ).fetchall()


def get_requisition_full(conn, requisition_id):
    """Returns {requisition, items, vendors} in one call, enough to render
    the staff comparison grid: items (description/unit/qty), and per vendor
    -- its own prices keyed by item_id (LEFT-JOIN-shaped: a missing key just
    means "not priced yet"), a running total, whether every line is priced
    (is_complete), and its own status. Returns None if the requisition
    doesn't exist."""
    requisition = get_requisition(conn, requisition_id)
    if requisition is None:
        return None
    items = [dict(i) for i in list_items(conn, requisition_id)]
    vendors = []
    for v in list_vendors(conn, requisition_id):
        prices_by_item = {p["item_id"]: dict(p) for p in list_prices_for_vendor(conn, v["id"])}
        priced_count = sum(1 for i in items if prices_by_item.get(i["id"], {}).get("unit_price") is not None)
        total = sum((prices_by_item.get(i["id"], {}).get("unit_price") or 0) * i["quantity"] for i in items)
        vendors.append({
            **dict(v),
            "prices_by_item": prices_by_item,
            "priced_count": priced_count,
            "total": total,
            "is_complete": bool(items) and priced_count == len(items),
        })
    return {"requisition": dict(requisition), "items": items, "vendors": vendors}


def replace_items(conn, requisition_id, items):
    """Must run inside db.connect_immediate(). Wholesale-replaces the item
    list (same pattern as repositories/quotes.py: save_quote's room/item
    replace), only while the requisition is still 'open' AND no invited
    vendor has submitted yet -- re-checked at write time so a staff member
    can't change the ask out from under a vendor who already responded.

    items: [{product_id?, description, unit, quantity}].
    """
    requisition = get_requisition(conn, requisition_id)
    if requisition is None:
        raise RequisitionNotFoundError(f"Requisition {requisition_id} not found")
    if requisition["status"] != "open":
        raise RequisitionLockedError("This requisition is no longer open -- the item list can't be changed.")
    already_submitted = conn.execute(
        "SELECT 1 FROM material_requisition_vendors WHERE requisition_id = ? AND status = 'submitted' LIMIT 1",
        (requisition_id,),
    ).fetchone()
    if already_submitted:
        raise RequisitionLockedError(
            "A vendor has already submitted pricing for this requisition -- the item list can no longer be changed."
        )

    conn.execute("DELETE FROM material_requisition_items WHERE requisition_id = ?", (requisition_id,))
    for pos, item in enumerate(items):
        product_id = item.get("product_id")
        if product_id is not None:
            found = conn.execute("SELECT 1 FROM products WHERE id = ?", (product_id,)).fetchone()
            if not found:
                raise ValueError(f"product_id {product_id} does not exist")
        conn.execute(
            """INSERT INTO material_requisition_items (requisition_id, product_id, description, unit, quantity, position)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (requisition_id, product_id, item["description"], item["unit"], item.get("quantity") or 0, pos),
        )


def invite_vendor(conn, requisition_id, supplier_id):
    """Idempotent -- inviting an already-invited vendor is a no-op (the
    UNIQUE(requisition_id, supplier_id) constraint would otherwise raise)."""
    conn.execute(
        """INSERT OR IGNORE INTO material_requisition_vendors (requisition_id, supplier_id, status)
           VALUES (?, ?, 'pending')""",
        (requisition_id, supplier_id),
    )


def save_vendor_prices(conn, requisition_vendor_id, items):
    """items: [{item_id, unit_price, vendor_notes}]. Upserts each line;
    does NOT change the requisition_vendor's status -- this is the
    repeatable 'Save draft' action, callable as many times as the vendor
    likes before they Submit."""
    for item in items:
        conn.execute(
            """INSERT INTO material_requisition_prices (requisition_vendor_id, item_id, unit_price, vendor_notes)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(requisition_vendor_id, item_id) DO UPDATE SET
                   unit_price = excluded.unit_price, vendor_notes = excluded.vendor_notes""",
            (requisition_vendor_id, item["item_id"], item.get("unit_price"), item.get("vendor_notes")),
        )


def submit_vendor_prices(conn, requisition_vendor_id, items):
    """Must run inside db.connect_immediate() -- re-checks status=='pending'
    at write time, same race-guard shape as repositories/tracker.py:
    complete_task. Saves the posted prices first (so a vendor's last-second
    edits are captured), then rejects if any item is left without a price;
    only once every line is priced does it flip status='submitted' and
    stamp submitted_at, locking the vendor's page to read-only from then on.
    """
    rv = conn.execute(
        "SELECT * FROM material_requisition_vendors WHERE id = ?", (requisition_vendor_id,)
    ).fetchone()
    if rv is None:
        raise RequisitionVendorNotFoundError(f"Requisition-vendor {requisition_vendor_id} not found")
    if rv["status"] != "pending":
        raise VendorAlreadySubmittedError("Pricing for this requisition has already been submitted.")

    save_vendor_prices(conn, requisition_vendor_id, items)

    all_item_ids = {
        r["id"] for r in conn.execute(
            "SELECT id FROM material_requisition_items WHERE requisition_id = ?", (rv["requisition_id"],)
        ).fetchall()
    }
    priced_item_ids = {
        r["item_id"] for r in conn.execute(
            """SELECT item_id FROM material_requisition_prices
               WHERE requisition_vendor_id = ? AND unit_price IS NOT NULL""",
            (requisition_vendor_id,),
        ).fetchall()
    }
    if all_item_ids - priced_item_ids:
        raise IncompletePricingError("Enter a price for every line before submitting.")

    conn.execute(
        "UPDATE material_requisition_vendors SET status = 'submitted', submitted_at = datetime('now') WHERE id = ?",
        (requisition_vendor_id,),
    )


def award(conn, requisition_id, requisition_vendor_id, resulting_po_id):
    conn.execute(
        """UPDATE material_requisitions
           SET status = 'awarded', awarded_vendor_id = ?, resulting_po_id = ?, awarded_at = datetime('now')
           WHERE id = ?""",
        (requisition_vendor_id, resulting_po_id, requisition_id),
    )
