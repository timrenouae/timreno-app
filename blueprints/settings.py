"""Company Settings & Document Builder -- lets an Admin (or anyone with
settings.manage) edit company details and PDF branding/content directly
from the app, without a code change. See repositories/settings.py and
pdf/theme.py for where this data actually lives and gets used.
"""
import json

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, Response, send_from_directory

import auth
import config
import db
import repositories.settings as settings_repo
import repositories.audit as audit_repo
import pdf.theme as pdf_theme
from pdf.quote import build_quote_pdf
from pdf.estimate import build_estimate_pdf
from pdf.purchase_order import build_purchase_order_pdf

bp = Blueprint("settings", __name__, url_prefix="/settings")

_PDF_FONT_CHOICES = ["Helvetica", "Times", "Courier"]


def _parse_table_columns(raw_json):
    """Validates a posted column-config JSON string (the hidden field kept
    in sync by templates/settings/documents.html's column-editor JS).
    Raises ValueError with a user-facing message on anything malformed, so
    a corrupt/tampered payload is rejected rather than silently stored as
    garbage. Returns a clean list of {key, label, enabled} dicts, in
    posted order, ready for json.dumps() straight into company_settings."""
    try:
        parsed = json.loads(raw_json) if raw_json else None
    except ValueError:
        parsed = None
    if not isinstance(parsed, list) or not parsed:
        raise ValueError("Column configuration was corrupted -- please try again.")

    cleaned = []
    seen_keys = set()
    for entry in parsed:
        if not isinstance(entry, dict):
            raise ValueError("Column configuration was corrupted -- please try again.")
        key, label, enabled = entry.get("key"), entry.get("label"), entry.get("enabled")
        if not isinstance(key, str) or not key or key in seen_keys:
            raise ValueError("Column configuration was corrupted -- please try again.")
        if not isinstance(label, str) or not isinstance(enabled, bool):
            raise ValueError("Column configuration was corrupted -- please try again.")
        seen_keys.add(key)
        cleaned.append({"key": key, "label": label.strip() or key.upper(), "enabled": enabled})

    if not any(c["key"] == "description" for c in cleaned):
        raise ValueError("The Description column can't be removed.")
    return cleaned


def _load_table_columns(raw_json, defaults):
    """GET-side counterpart: best-effort read of the stored column config
    for prefilling the column editor, falling back to the same hardcoded
    defaults pdf/theme.py: resolve_columns() would -- never raises, since a
    corrupt stored value here should just show the default editor, not
    break the Settings page."""
    try:
        parsed = json.loads(raw_json) if raw_json else None
        if isinstance(parsed, list) and parsed:
            return parsed
    except (TypeError, ValueError):
        pass
    return defaults


# --------------------------------------------------------------- company info

@bp.route("/", methods=["GET", "POST"])
@auth.require_permission("settings.manage")
def company_view():
    if request.method == "POST":
        auth.csrf_protect()
        fields = {
            "company_name": (request.form.get("company_name") or "").strip(),
            "company_tagline": (request.form.get("company_tagline") or "").strip(),
            "address_line1": (request.form.get("address_line1") or "").strip(),
            "address_line2": (request.form.get("address_line2") or "").strip(),
            "phone": (request.form.get("phone") or "").strip(),
            "email": (request.form.get("email") or "").strip(),
            "website": (request.form.get("website") or "").strip(),
            "trn_number": (request.form.get("trn_number") or "").strip(),
            "bank_name": (request.form.get("bank_name") or "").strip(),
            "bank_account_name": (request.form.get("bank_account_name") or "").strip(),
            "bank_iban": (request.form.get("bank_iban") or "").strip(),
            "bank_swift": (request.form.get("bank_swift") or "").strip(),
        }
        if not fields["company_name"]:
            flash("Company name is required.", "error")
            return redirect(url_for("settings.company_view"))
        try:
            vat = float(request.form.get("default_vat_percent") or 5)
        except ValueError:
            flash("VAT % must be a number.", "error")
            return redirect(url_for("settings.company_view"))
        fields["default_vat_percent"] = vat

        with db.connect() as conn:
            settings_repo.update_settings(conn, fields, auth.current_user()["id"])
            audit_repo.log(conn, auth.current_user()["id"], "update", "company_settings", None,
                            "company info")
        flash("Company details saved.", "success")
        return redirect(url_for("settings.company_view"))

    with db.connect() as conn:
        settings_row = settings_repo.get_settings(conn)
    return render_template("settings/company.html", settings=settings_row,
                            csrf_token=auth.generate_csrf_token())


# ------------------------------------------------------------ document builder

@bp.route("/documents", methods=["GET", "POST"])
@auth.require_permission("settings.manage")
def documents_view():
    if request.method == "POST":
        auth.csrf_protect()
        with db.connect() as conn:
            settings_row = settings_repo.get_settings(conn)

            logo_file = request.files.get("logo_file")
            if logo_file and logo_file.filename:
                try:
                    new_filename = settings_repo.save_logo(logo_file, old_filename=settings_row["logo_filename"])
                except ValueError as e:
                    flash(str(e), "error")
                    return redirect(url_for("settings.documents_view"))
                settings_repo.update_settings(conn, {"logo_filename": new_filename}, auth.current_user()["id"])

            pdf_font = request.form.get("pdf_font") or "Helvetica"
            if pdf_font not in _PDF_FONT_CHOICES:
                pdf_font = "Helvetica"
            trailing_order = request.form.get("quote_trailing_block_order") or "terms_then_bank"
            if trailing_order not in ("terms_then_bank", "bank_then_terms"):
                trailing_order = "terms_then_bank"

            try:
                quote_columns = _parse_table_columns(request.form.get("quote_table_columns"))
                po_columns = _parse_table_columns(request.form.get("po_table_columns"))
            except ValueError as e:
                flash(str(e), "error")
                return redirect(url_for("settings.documents_view"))

            fields = {
                "accent_color_hex": (request.form.get("accent_color_hex") or "#c1752a").strip(),
                "structure_color_hex": (request.form.get("structure_color_hex") or "#2e5c7a").strip(),
                "pdf_font": pdf_font,
                "show_bank_details_on_quote": 1 if request.form.get("show_bank_details_on_quote") else 0,
                "show_bank_details_on_estimate": 1 if request.form.get("show_bank_details_on_estimate") else 0,
                "show_bank_details_on_po": 1 if request.form.get("show_bank_details_on_po") else 0,
                "quote_trailing_block_order": trailing_order,
                "estimate_disclaimer_text": (request.form.get("estimate_disclaimer_text") or "").strip(),
                "quote_table_columns": json.dumps(quote_columns),
                "po_table_columns": json.dumps(po_columns),
            }
            settings_repo.update_settings(conn, fields, auth.current_user()["id"])

            terms_text = request.form.get("default_terms") or ""
            terms = [line.strip() for line in terms_text.splitlines() if line.strip()]
            settings_repo.save_default_terms(conn, terms)

            po_terms_text = request.form.get("po_terms") or ""
            po_terms = [line.strip() for line in po_terms_text.splitlines() if line.strip()]
            settings_repo.save_po_terms(conn, po_terms)

            audit_repo.log(conn, auth.current_user()["id"], "update", "company_settings", None,
                            "document builder")
        flash("Document settings saved.", "success")
        return redirect(url_for("settings.documents_view"))

    with db.connect() as conn:
        settings_row = settings_repo.get_settings(conn)
        default_terms = settings_repo.get_default_terms(conn)
        po_terms = settings_repo.get_po_terms(conn)
    quote_columns = _load_table_columns(settings_row["quote_table_columns"], pdf_theme.QUOTE_DEFAULT_COLUMNS)
    po_columns = _load_table_columns(settings_row["po_table_columns"], pdf_theme.PO_DEFAULT_COLUMNS)
    return render_template(
        "settings/documents.html", settings=settings_row, default_terms=default_terms,
        po_terms=po_terms, quote_columns=quote_columns, po_columns=po_columns,
        font_choices=_PDF_FONT_CHOICES, csrf_token=auth.generate_csrf_token(),
    )


# ------------------------------------------------------------------- preview

_SAMPLE_ITEMS = [
    {"description": "Sample line item -- 600x600 porcelain tile, supply & fix", "unit": "Sq.Ft",
     "qty": 220, "unit_price": 42.5},
    {"description": "Sample line item -- MDF ceiling with cove lighting", "unit": "R.Ft",
     "qty": 38, "unit_price": 95},
]


@bp.route("/preview/<doc_type>")
@auth.require_permission("settings.manage")
def preview(doc_type):
    if doc_type not in ("quote", "estimate", "po"):
        abort(404)
    with db.connect() as conn:
        ctx = pdf_theme.get_pdf_context(conn)

    if doc_type == "quote":
        meta = {
            "quote_number": "TIMR-PREVIEW-0000", "quote_date": "01/01/2026",
            "client_name": "Sample Client", "location": "Sample Villa, Dubai",
            "project_name": "Sample Renovation Project", "project_type": "Villa Renovation",
            "job_notes": "This is a live preview generated from your current Settings -- not a real quote.",
            "vat_percent": ctx.default_vat_percent,
        }
        rooms = [{"name": "Sample Room", "notes": "", "items": _SAMPLE_ITEMS}]
        pdf_bytes = build_quote_pdf(ctx, meta, rooms, ["Sample term -- edit your real default terms in Settings."])
        filename = "settings-preview-quote.pdf"
    elif doc_type == "estimate":
        meta = {"project_name": "Sample Renovation Project", "location": "Sample Villa, Dubai",
                "finish_level": "Standard", "vat_percent": ctx.default_vat_percent}
        spaces = [{"label": "Sample Room", "size": 220, "notes": "", "items": _SAMPLE_ITEMS}]
        pdf_bytes = build_estimate_pdf(ctx, meta, spaces)
        filename = "settings-preview-estimate.pdf"
    else:
        po = {"po_number": "PO-PREVIEW-0000", "supplier_name": "Sample Supplier LLC",
              "contact_name": "Sample Contact", "phone": "+971 50 000 0000",
              "email": "supplier@example.com", "address": "Sample Address, Dubai",
              "created_at": "2026-01-01", "notes": ""}
        items = [{"description": _SAMPLE_ITEMS[0]["description"], "unit": _SAMPLE_ITEMS[0]["unit"],
                   "brand": "Sample Brand", "quantity": _SAMPLE_ITEMS[0]["qty"],
                   "unit_price": _SAMPLE_ITEMS[0]["unit_price"]}]
        pdf_bytes = build_purchase_order_pdf(ctx, po, items)
        filename = "settings-preview-po.pdf"

    return Response(pdf_bytes, mimetype="application/pdf",
                     headers={"Content-Disposition": f'inline; filename="{filename}"'})


# ----------------------------------------------------------------------- logo

@bp.route("/logo")
def logo():
    with db.connect() as conn:
        settings_row = settings_repo.get_settings(conn)
    filename = settings_row["logo_filename"]
    if not filename or not settings_repo.logo_path(filename):
        abort(404)
    return send_from_directory(config.UPLOADS_DIR, filename)
