"""SQL access for the public marketing site's contact form (Item 7).

Submissions are stored here rather than emailed (Item 6, PO email delivery,
is on hold, and a mailto: link is unreliable) -- a staff user holding the
new leads.view permission reads them from blueprints/leads.py's small
inbox.
"""


def create_inquiry(conn, name, email, phone, message):
    cur = conn.execute(
        """INSERT INTO public_inquiries (name, email, phone, message) VALUES (?, ?, ?, ?)""",
        (name, email, phone, message),
    )
    return cur.lastrowid


def list_inquiries(conn):
    return conn.execute(
        "SELECT * FROM public_inquiries ORDER BY created_at DESC"
    ).fetchall()


def get_inquiry(conn, inquiry_id):
    return conn.execute(
        "SELECT * FROM public_inquiries WHERE id = ?", (inquiry_id,)
    ).fetchone()


def mark_read(conn, inquiry_id):
    conn.execute(
        "UPDATE public_inquiries SET status = 'read' WHERE id = ?", (inquiry_id,)
    )


def count_new(conn):
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM public_inquiries WHERE status = 'new'"
    ).fetchone()
    return row["n"]
