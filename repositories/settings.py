"""Company Settings & Document Builder -- the single source of truth for
company identity, PDF branding (logo/colors/font), and the optional
Payment Details / default-terms content that used to be hardcoded and
duplicated across pdf/quote.py, pdf/purchase_order.py, and
pdf/estimate.py. See /root/.claude/plans/iridescent-plotting-umbrella.md
for the full design rationale.

company_settings is a singleton (id=1, enforced by a CHECK constraint --
see migrations/0004_settings.sql), so callers never need to branch on
"does a settings row exist yet".
"""
import os
import time

from werkzeug.utils import secure_filename

import config

# Every column a caller is allowed to update via update_settings(). Kept
# as an explicit allowlist (rather than trusting whatever keys a caller
# passes) so a stray/typo'd key can never silently no-op or, worse, be
# smuggled into the SQL.
_EDITABLE_FIELDS = {
    "company_name", "company_tagline", "address_line1", "address_line2",
    "phone", "email", "website", "trn_number", "default_vat_percent",
    "bank_name", "bank_account_name", "bank_iban", "bank_swift",
    "accent_color_hex", "structure_color_hex", "pdf_font",
    "show_bank_details_on_quote", "show_bank_details_on_estimate",
    "estimate_disclaimer_text", "quote_trailing_block_order",
    "logo_filename",
}

ALLOWED_LOGO_EXTENSIONS = {"png", "jpg", "jpeg"}
MAX_LOGO_BYTES = 2 * 1024 * 1024  # 2 MB -- plenty for a PDF header logo


def get_settings(conn):
    row = conn.execute("SELECT * FROM company_settings WHERE id = 1").fetchone()
    if row is None:
        # Should be unreachable once migrate.py has run (the migration
        # seeds the row), but fall back to inserting it rather than
        # crashing every page load if something's out of sync.
        conn.execute("INSERT INTO company_settings (id) VALUES (1)")
        conn.commit()
        row = conn.execute("SELECT * FROM company_settings WHERE id = 1").fetchone()
    return row


def update_settings(conn, fields: dict, user_id):
    updates = {k: v for k, v in fields.items() if k in _EDITABLE_FIELDS}
    if not updates:
        return
    set_clause = ", ".join(f"{col} = ?" for col in updates)
    params = list(updates.values()) + [user_id]
    conn.execute(
        f"UPDATE company_settings SET {set_clause}, updated_by = ?, updated_at = datetime('now') WHERE id = 1",
        params,
    )


# ------------------------------------------------------------ default terms

def get_default_terms(conn):
    rows = conn.execute(
        "SELECT text FROM settings_default_terms ORDER BY position"
    ).fetchall()
    return [r["text"] for r in rows]


def save_default_terms(conn, terms: list):
    conn.execute("DELETE FROM settings_default_terms")
    for pos, text in enumerate(terms or []):
        text = (text or "").strip()
        if not text:
            continue
        conn.execute(
            "INSERT INTO settings_default_terms (position, text) VALUES (?, ?)",
            (pos, text),
        )


# ------------------------------------------------------------------- logo

def save_logo(file_storage, old_filename=None):
    """Validates and saves an uploaded logo image next to the database
    (config.UPLOADS_DIR -- NOT static/, which is wiped on every redeploy
    on Render since it's part of the git-deployed code, not the
    persistent Disk). Returns the new stored filename. Raises ValueError
    on an invalid file. Removes the previous logo file, if any, once the
    new one is safely written.
    """
    if not file_storage or not file_storage.filename:
        raise ValueError("Choose an image file first.")

    ext = file_storage.filename.rsplit(".", 1)[-1].lower() if "." in file_storage.filename else ""
    if ext not in ALLOWED_LOGO_EXTENSIONS:
        raise ValueError("Logo must be a .png, .jpg, or .jpeg file.")

    file_storage.seek(0, os.SEEK_END)
    size = file_storage.tell()
    file_storage.seek(0)
    if size > MAX_LOGO_BYTES:
        raise ValueError("Logo file is too large (max 2 MB).")

    os.makedirs(config.UPLOADS_DIR, exist_ok=True)
    # Unique-ish filename (timestamp prefix) so an old cached copy of the
    # previous logo never lingers in a browser/PDF-viewer cache under the
    # same name.
    safe_name = secure_filename(file_storage.filename) or "logo"
    stored_name = f"logo_{int(time.time())}_{safe_name}"
    dest_path = os.path.join(config.UPLOADS_DIR, stored_name)
    file_storage.save(dest_path)

    if old_filename:
        old_path = os.path.join(config.UPLOADS_DIR, old_filename)
        if os.path.exists(old_path) and os.path.abspath(old_path) != os.path.abspath(dest_path):
            try:
                os.remove(old_path)
            except OSError:
                pass  # non-fatal -- an orphaned old logo file is harmless

    return stored_name


def logo_path(filename):
    if not filename:
        return None
    path = os.path.join(config.UPLOADS_DIR, filename)
    return path if os.path.exists(path) else None
