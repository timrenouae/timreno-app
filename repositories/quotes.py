"""
SQL access for quotes/rooms/items/terms + quote-number generation + the
shared quote-stage-history function used by both the Quotes list and the
Tracker detail page.

Ported from TIMR Quote Builder.html / TIMR Dashboard.html / TIMR Project
Tracker.html. See /root/.claude/plans/iridescent-plotting-umbrella.md for
the full design rationale.
"""
import re

DEFAULT_TERMS = [
    "This quotation is valid for 30 days from the date of issue.",
    "50% advance payment is required to commence work; the balance is due on completion.",
    "Prices exclude government permits, NOC, or authority approvals unless stated otherwise.",
    "Any changes to scope after approval will be quoted and agreed separately.",
    "Delivery timelines will be confirmed on order confirmation and may vary with site conditions.",
]

STAGES = ["Quote", "Approved", "In Progress", "Completed", "Paid"]

_SEQ_RE = re.compile(r"(\d+)\s*$")


class DuplicateQuoteNumberError(Exception):
    def __init__(self, quote_number):
        super().__init__(f"Quote number '{quote_number}' is already in use.")
        self.quote_number = quote_number


class QuoteNotFoundError(Exception):
    pass


# -------------------------------------------------------------- numbering

def format_quote_number(seq: int, year: int = None) -> str:
    import datetime
    year = year or datetime.date.today().year
    return f"TIMR-{year}-{seq:04d}"


def extract_seq(quote_number: str):
    if not quote_number:
        return None
    m = _SEQ_RE.search(quote_number)
    return int(m.group(1)) if m else None


def peek_next_quote_number(conn) -> str:
    """Read-only -- call on a plain db.connect(). Used only to prefill the
    Builder's quote-number field / Estimator's implied next number. Does
    NOT reserve anything -- two people opening 'New Quote' can see the same
    suggestion; the actual reservation happens at save/finalize time."""
    row = conn.execute("SELECT next_seq FROM counters WHERE counter_key = 'quote_number'").fetchone()
    return format_quote_number(row["next_seq"])


def bump_counter_if_needed(conn, quote_number):
    """High-water-mark bump: only advances counters.next_seq, never rewinds
    it. Call inside the same db.connect_immediate() transaction as the
    quote write it belongs to."""
    seq = extract_seq(quote_number)
    if seq is None:
        return
    conn.execute(
        "UPDATE counters SET next_seq = ? WHERE counter_key = 'quote_number' AND next_seq <= ?",
        (seq + 1, seq),
    )


def reserve_next_quote_number(conn) -> str:
    """Eager, unconditional bump -- used only by the Estimator's
    'send to builder' action. Call inside db.connect_immediate()."""
    row = conn.execute("SELECT next_seq FROM counters WHERE counter_key = 'quote_number'").fetchone()
    conn.execute("UPDATE counters SET next_seq = next_seq + 1 WHERE counter_key = 'quote_number'")
    return format_quote_number(row["next_seq"])


# ------------------------------------------------------------------ totals

def compute_totals(rooms, vat_percent):
    """rooms: list of {items: [{qty, unit_price, ...}], ...}. Pure function,
    reused by the Builder page, PDF renderer, Dashboard list, and Tracker
    budget calc."""
    subtotal = sum(
        (item["qty"] or 0) * (item["unit_price"] or 0)
        for room in rooms for item in room["items"]
    )
    vat_pct = vat_percent if vat_percent is not None else 5
    vat_amt = round(subtotal * vat_pct / 100, 2)
    grand = round(subtotal + vat_amt, 2)
    return {"subtotal": round(subtotal, 2), "vat_percent": vat_pct, "vat_amt": vat_amt, "grand": grand}


# -------------------------------------------------------------------- CRUD

def get_quote(conn, quote_id):
    return conn.execute("SELECT * FROM quotes WHERE id = ?", (quote_id,)).fetchone()


def get_quote_by_number(conn, quote_number):
    return conn.execute("SELECT * FROM quotes WHERE quote_number = ?", (quote_number,)).fetchone()


def get_quote_full(conn, quote_id):
    """Returns {quote, rooms: [{id, name, notes, items:[...]}], terms: [str]}
    or None. Used for Builder-page hydration, PDF rendering, and the
    Tracker's budget calculation."""
    quote = conn.execute("SELECT * FROM quotes WHERE id = ?", (quote_id,)).fetchone()
    if quote is None:
        return None
    rooms = conn.execute(
        "SELECT * FROM quote_rooms WHERE quote_id = ? ORDER BY position, id", (quote_id,)
    ).fetchall()
    room_dicts = []
    for room in rooms:
        items = conn.execute(
            "SELECT * FROM quote_items WHERE room_id = ? ORDER BY position, id", (room["id"],)
        ).fetchall()
        room_dicts.append({
            "id": room["id"], "name": room["name"], "notes": room["notes"],
            "items": [dict(i) for i in items],
        })
    terms = conn.execute(
        "SELECT text FROM quote_terms WHERE quote_id = ? ORDER BY position, id", (quote_id,)
    ).fetchall()
    return {"quote": dict(quote), "rooms": room_dicts, "terms": [t["text"] for t in terms]}


def list_quotes(conn, search=None, status=None, stage=None):
    """List view for the Dashboard-equivalent page. Computes subtotal via a
    SQL aggregate (not get_quote_full per row) to avoid N+1 queries; VAT/
    grand are derived by the caller via compute_totals-equivalent math since
    this is a flat SUM, not a rooms structure."""
    clauses = []
    params = []
    if status:
        clauses.append("q.status = ?")
        params.append(status)
    if stage:
        clauses.append("q.stage = ?")
        params.append(stage)
    if search:
        clauses.append("(q.quote_number LIKE ? OR q.client_name LIKE ? OR q.project_name LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT q.*, COALESCE(SUM(qi.qty * qi.unit_price), 0) AS subtotal
        FROM quotes q
        LEFT JOIN quote_rooms qr ON qr.quote_id = q.id
        LEFT JOIN quote_items qi ON qi.room_id = qr.id
        {where}
        GROUP BY q.id
        ORDER BY q.updated_at DESC
    """
    return conn.execute(sql, params).fetchall()


def save_quote(conn, quote_id, quote_number, meta, rooms, terms, user_id, finalize):
    """Must run inside db.connect_immediate(). Creates or wholesale-replaces
    a quote's rooms/items/terms from the client's posted state.

    meta: dict with client_name, project_name, project_type, location,
    quote_date, vat_percent, job_notes.
    rooms: [{name, notes, items: [{product_id?, description, unit, brand,
    qty, unit_price}]}].
    terms: [str].

    Does NOT touch quote_team_members/quote_tasks/quote_expenses --
    those key off quote_id directly, not through quote_rooms, so a Builder
    re-save never disturbs Tracker data for this quote.

    Returns (quote_id, quote_number, was_newly_finalized).
    """
    existing_by_number = conn.execute(
        "SELECT id FROM quotes WHERE quote_number = ?", (quote_number,)
    ).fetchone()

    if quote_id is not None:
        current = conn.execute("SELECT * FROM quotes WHERE id = ?", (quote_id,)).fetchone()
        if current is None:
            raise QuoteNotFoundError(f"Quote id {quote_id} not found")
        if existing_by_number is not None and existing_by_number["id"] != quote_id:
            raise DuplicateQuoteNumberError(quote_number)
    else:
        if existing_by_number is not None:
            raise DuplicateQuoteNumberError(quote_number)
        current = None

    # Save Draft never demotes an already-final quote back to draft (matches
    # the old tool's finalizeQuote/saveDraft split exactly).
    if finalize:
        status = "final"
    elif current is not None and current["status"] == "final":
        status = "final"
    else:
        status = "draft"

    if current is None:
        cur = conn.execute(
            """INSERT INTO quotes (quote_number, status, client_name, project_name, project_type,
                                    location, quote_date, vat_percent, job_notes, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (quote_number, status, meta.get("client_name"), meta.get("project_name"),
             meta.get("project_type"), meta.get("location"), meta.get("quote_date"),
             meta.get("vat_percent") or 5, meta.get("job_notes"), user_id),
        )
        quote_id = cur.lastrowid
    else:
        conn.execute(
            """UPDATE quotes SET quote_number = ?, status = ?, client_name = ?, project_name = ?,
                                  project_type = ?, location = ?, quote_date = ?, vat_percent = ?,
                                  job_notes = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (quote_number, status, meta.get("client_name"), meta.get("project_name"),
             meta.get("project_type"), meta.get("location"), meta.get("quote_date"),
             meta.get("vat_percent") or 5, meta.get("job_notes"), quote_id),
        )

    was_newly_finalized = False
    if finalize:
        has_history = conn.execute(
            "SELECT 1 FROM quote_stage_history WHERE quote_id = ?", (quote_id,)
        ).fetchone()
        if not has_history:
            conn.execute(
                "INSERT INTO quote_stage_history (quote_id, stage, changed_by) VALUES (?, 'Quote', ?)",
                (quote_id, user_id),
            )
            was_newly_finalized = True

    # Wholesale-replace rooms (cascades to quote_items) and terms.
    conn.execute("DELETE FROM quote_rooms WHERE quote_id = ?", (quote_id,))
    for pos, room in enumerate(rooms):
        cur = conn.execute(
            "INSERT INTO quote_rooms (quote_id, name, notes, position) VALUES (?, ?, ?, ?)",
            (quote_id, room["name"], room.get("notes") or "", pos),
        )
        room_id = cur.lastrowid
        for ipos, item in enumerate(room.get("items", [])):
            product_id = item.get("product_id")
            if product_id is not None:
                found = conn.execute("SELECT 1 FROM products WHERE id = ?", (product_id,)).fetchone()
                if not found:
                    raise ValueError(f"product_id {product_id} does not exist")
            conn.execute(
                """INSERT INTO quote_items (room_id, product_id, description, unit, brand, qty, unit_price, position)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (room_id, product_id, item["description"], item["unit"], item.get("brand"),
                 item.get("qty") or 0, item.get("unit_price") or 0, ipos),
            )

    conn.execute("DELETE FROM quote_terms WHERE quote_id = ?", (quote_id,))
    for pos, text in enumerate(terms or []):
        conn.execute(
            "INSERT INTO quote_terms (quote_id, position, text) VALUES (?, ?, ?)",
            (quote_id, pos, text),
        )

    bump_counter_if_needed(conn, quote_number)

    return quote_id, quote_number, was_newly_finalized


def delete_quote(conn, quote_id):
    conn.execute("DELETE FROM quotes WHERE id = ?", (quote_id,))


def set_client_user(conn, quote_id, user_id):
    """Links (or, with user_id=None, unlinks) a customer-portal login to
    this quote, via the client_user_id column that's existed since Phase 2
    but was unused until this customer-portal pass."""
    conn.execute(
        "UPDATE quotes SET client_user_id = ?, updated_at = datetime('now') WHERE id = ?",
        (user_id, quote_id),
    )


def list_quotes_for_client(conn, user_id):
    """Every finalized quote linked to this customer-portal user. Drafts
    never appear here, same as the Tracker list -- a customer shouldn't see
    a project before it's a real, finalized job."""
    return conn.execute(
        "SELECT * FROM quotes WHERE client_user_id = ? AND status = 'final' ORDER BY updated_at DESC",
        (user_id,),
    ).fetchall()


def set_stage(conn, quote_id, new_stage, user_id):
    """Shared by the Quotes list's stage dropdown and the Tracker detail
    page's stage dropdown / 'Mark as Approved' button -- the old tool
    duplicated this logic independently in both files."""
    row = conn.execute("SELECT stage FROM quotes WHERE id = ?", (quote_id,)).fetchone()
    if row is None or row["stage"] == new_stage:
        return
    conn.execute(
        "UPDATE quotes SET stage = ?, updated_at = datetime('now') WHERE id = ?",
        (new_stage, quote_id),
    )
    conn.execute(
        "INSERT INTO quote_stage_history (quote_id, stage, changed_by) VALUES (?, ?, ?)",
        (quote_id, new_stage, user_id),
    )
