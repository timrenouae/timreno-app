import base64
import os

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, jsonify, Response

import auth
import config
import db
import repositories.quotes as quotes_repo
import repositories.products as products_repo
import repositories.audit as audit_repo
import repositories.settings as settings_repo
import pdf.theme as pdf_theme
from pdf.quote import build_quote_pdf

# The `anthropic` SDK (see requirements.txt) powers "Import from Drawing"
# only -- everything else in this blueprint works without it. Imported
# defensively so a deploy that hasn't installed it yet (or a dev sandbox
# with no outbound package-install access) degrades to a clean "not
# configured" JSON error from the route below instead of taking down the
# whole app at import time.
try:
    import anthropic
except ImportError:  # pragma: no cover - exercised only when the optional dep is missing
    anthropic = None

bp = Blueprint("quotes", __name__, url_prefix="/quotes")


# ------------------------------------------------------------------- list

@bp.route("/")
@auth.require_internal_login
def list_view():
    search = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip() or None
    stage = request.args.get("stage", "").strip() or None

    with db.connect() as conn:
        rows = quotes_repo.list_quotes(conn, search=search or None, status=status, stage=stage)

    quotes_with_totals = []
    total_count = 0
    draft_count = 0
    final_count = 0
    in_progress_count = 0
    paid_count = 0
    finalized_value = 0.0
    for r in rows:
        # list_quotes already gives us a flat subtotal via a SQL aggregate
        # (avoiding an N+1 get_quote_full call per row), so VAT/grand are
        # derived directly here rather than through compute_totals, which
        # expects a full room/item structure.
        vat_pct = r["vat_percent"] if r["vat_percent"] is not None else 5
        vat_amt = round(r["subtotal"] * vat_pct / 100, 2)
        grand = round(r["subtotal"] + vat_amt, 2)
        quotes_with_totals.append({"row": r, "grand": grand})
        total_count += 1
        if r["status"] == "final":
            final_count += 1
            finalized_value += grand
            if r["stage"] == "In Progress":
                in_progress_count += 1
            if r["stage"] == "Paid":
                paid_count += 1
        else:
            draft_count += 1

    stats = {
        "total": total_count, "drafts": draft_count, "finalized": final_count,
        "in_progress": in_progress_count, "paid": paid_count, "finalized_value": finalized_value,
    }
    return render_template(
        "quotes/list.html", quotes=quotes_with_totals, stats=stats,
        search=search, status=status, stage=stage, stages=quotes_repo.STAGES,
        csrf_token=auth.generate_csrf_token(),
    )


# ---------------------------------------------------------------- builder

@bp.route("/new")
@auth.require_permission("quotes.manage")
def new_view():
    with db.connect() as conn:
        next_number = quotes_repo.peek_next_quote_number(conn)
        categories = products_repo.list_categories(conn)
        brands = products_repo.list_brands(conn)
        default_terms = settings_repo.get_default_terms(conn)
    csrf_token = auth.generate_csrf_token()
    initial_data = {
        "quoteId": None, "csrfToken": csrf_token,
        "defaultTerms": default_terms, "existing": None,
    }
    return render_template(
        "quotes/builder.html", quote_id=None, next_number=next_number, quote_full=None,
        categories=categories, brands=brands, csrf_token=csrf_token, initial_data=initial_data,
    )


@bp.route("/<int:quote_id>/edit")
@auth.require_permission("quotes.manage")
def edit_view(quote_id):
    with db.connect() as conn:
        full = quotes_repo.get_quote_full(conn, quote_id)
        categories = products_repo.list_categories(conn)
        brands = products_repo.list_brands(conn)
        default_terms = settings_repo.get_default_terms(conn)
    if full is None:
        abort(404)
    csrf_token = auth.generate_csrf_token()
    initial_data = {
        "quoteId": quote_id, "csrfToken": csrf_token,
        "defaultTerms": default_terms,
        "existing": {"rooms": full["rooms"], "terms": full["terms"]},
    }
    return render_template(
        "quotes/builder.html", quote_id=quote_id, next_number=None, quote_full=full,
        categories=categories, brands=brands, csrf_token=csrf_token, initial_data=initial_data,
    )


def _save_common(finalize):
    auth.csrf_protect()
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "Invalid or missing JSON body."}), 400

    quote_number = (body.get("quote_number") or "").strip()
    if not quote_number:
        return jsonify({"error": "A quote number is required."}), 400
    rooms = body.get("rooms") or []
    if finalize and not any(r.get("items") for r in rooms):
        return jsonify({"error": "Add at least one priced room before saving."}), 400

    meta = {
        "client_name": body.get("client_name"), "project_name": body.get("project_name"),
        "project_type": body.get("project_type"), "location": body.get("location"),
        "quote_date": body.get("quote_date"), "vat_percent": body.get("vat_percent"),
        "job_notes": body.get("job_notes"),
    }
    quote_id = body.get("quote_id")

    try:
        with db.connect_immediate() as conn:
            new_id, number, was_newly_finalized = quotes_repo.save_quote(
                conn, quote_id, quote_number, meta, rooms, body.get("terms") or [],
                auth.current_user()["id"], finalize=finalize,
            )
            audit_repo.log(conn, auth.current_user()["id"],
                            "finalize" if finalize else "save_draft", "quote", new_id, number)
    except quotes_repo.DuplicateQuoteNumberError as e:
        return jsonify({"error": str(e)}), 400
    except quotes_repo.QuoteNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({"quote_id": new_id, "quote_number": number, "status": "final" if finalize else "draft"})


@bp.route("/save-draft", methods=["POST"])
@auth.require_permission("quotes.manage")
def save_draft():
    return _save_common(finalize=False)


@bp.route("/save", methods=["POST"])
@auth.require_permission("quotes.manage")
def save_finalize():
    return _save_common(finalize=True)


@bp.route("/products/search")
@auth.require_permission("quotes.manage")
def products_search():
    query = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip() or None
    brand = request.args.get("brand", "").strip() or None
    brand_is_none = brand == "__NONE__"
    with db.connect() as conn:
        items = products_repo.search_products(
            conn, query=query or None, category=category,
            brand=None if brand_is_none else brand, brand_is_none=brand_is_none, limit=100,
        )
    return jsonify([dict(i) for i in items])


# ---------------------------------------------------------- import drawing

ALLOWED_DRAWING_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "pdf"}
MAX_DRAWING_BYTES = 15 * 1024 * 1024  # 15 MB
_DRAWING_MEDIA_TYPES = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "webp": "image/webp", "pdf": "application/pdf",
}

_ROOM_TOOL = {
    "name": "record_rooms",
    "description": (
        "Record every distinct room/cabin/space identified in the floor-plan drawing."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "rooms": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "approx_sqft": {"type": ["number", "null"]},
                        "source_note": {"type": ["string", "null"]},
                    },
                    "required": ["name"],
                },
            },
        },
        "required": ["rooms"],
    },
}

_DRAWING_PROMPT = """You are looking at a floor-plan drawing for a villa or \
office renovation/construction project. It could be a formal architect PDF, \
a photo or scan of a printed plan, OR a rough, unlabeled hand sketch.

Identify every distinct room, cabin, or enclosed space shown in the drawing \
and call the record_rooms tool with the full list. For each space:

- name: use the room's on-drawing label if one is present (e.g. "Kitchen", \
"Manager's Cabin", "Master Bedroom"). If the drawing is an unlabeled hand \
sketch, do NOT give up -- still separate every distinct enclosed space you \
can see and give each a sensible generic name such as "Room 1", "Room 2", \
"Cabin 1", in a consistent reading order (left-to-right, top-to-bottom).
- approx_sqft: if a dimension is written near the space, convert it to an \
approximate square-foot number (a plain number, not a string). Handle:
  * a single area already in sq ft -> use as-is.
  * a single area in sq m -> multiply by 10.7639 and round to a sensible \
whole number.
  * a length x width given in feet (e.g. "12' x 10'") -> multiply them.
  * a length x width given in metres (e.g. "3.6m x 3.0m") -> multiply them, \
then multiply the result by 10.7639.
  If no dimension is shown or legible for a space, leave approx_sqft null -- \
never guess a size that isn't indicated somewhere on the drawing.
- source_note: optionally include the raw dimension text you read (e.g. \
"12' x 10'" or "10.5 sq m"), the room-type/use if noted, or any other short \
useful text next to that space. Leave null if there's nothing worth noting.

Only report spaces you can actually see distinguished in the drawing -- do \
not invent rooms that aren't shown. This is purely a room-list extraction \
step; do not suggest materials, quantities, or pricing.
"""


def _validate_drawing_upload(file_storage):
    """Validates an uploaded drawing file entirely in memory -- no disk
    write, matching the scope decision to never persist a client's drawing.
    Returns (extension, media_type, raw_bytes). Raises ValueError on any
    invalid input, mirroring the shape of repositories/settings.py: save_logo().
    """
    if not file_storage or not file_storage.filename:
        raise ValueError("Choose a drawing file first.")

    ext = file_storage.filename.rsplit(".", 1)[-1].lower() if "." in file_storage.filename else ""
    if ext not in ALLOWED_DRAWING_EXTENSIONS:
        raise ValueError("Drawing must be a .png, .jpg, .jpeg, .webp, or .pdf file.")

    file_storage.seek(0, os.SEEK_END)
    size = file_storage.tell()
    file_storage.seek(0)
    if size == 0:
        raise ValueError("That file appears to be empty.")
    if size > MAX_DRAWING_BYTES:
        raise ValueError("Drawing file is too large (max 15 MB).")

    raw_bytes = file_storage.read()
    return ext, _DRAWING_MEDIA_TYPES[ext], raw_bytes


@bp.route("/import-drawing", methods=["POST"])
@auth.require_permission("quotes.manage")
def import_drawing():
    auth.csrf_protect()

    try:
        ext, media_type, raw_bytes = _validate_drawing_upload(request.files.get("drawing_file"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    if anthropic is None or not config.ANTHROPIC_API_KEY:
        return jsonify({
            "error": "Drawing import isn't configured yet -- ask an admin to set the "
                     "ANTHROPIC_API_KEY environment variable to enable this feature.",
        }), 503

    b64_data = base64.standard_b64encode(raw_bytes).decode("ascii")
    if media_type == "application/pdf":
        content_block = {"type": "document", "source": {"type": "base64", "media_type": media_type, "data": b64_data}}
    else:
        content_block = {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64_data}}

    try:
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=config.DRAWING_MODEL,
            max_tokens=4096,
            tools=[_ROOM_TOOL],
            tool_choice={"type": "tool", "name": "record_rooms"},
            messages=[{
                "role": "user",
                "content": [content_block, {"type": "text", "text": _DRAWING_PROMPT}],
            }],
        )
    except anthropic.APIError as e:
        message = getattr(e, "message", None) or str(e) or "Anthropic API request failed."
        if isinstance(e, anthropic.AuthenticationError):
            status, message = 401, "Drawing import is misconfigured (invalid Anthropic API key)."
        elif isinstance(e, getattr(anthropic, "RateLimitError", ())):
            status, message = 429, "Anthropic API rate limit reached -- try again shortly."
        elif isinstance(e, getattr(anthropic, "BadRequestError", ())):
            status = 400
        else:
            status = 502
        return jsonify({"error": message}), status
    except Exception as e:  # never let an unhandled exception 500 out as raw HTML
        return jsonify({"error": f"Unexpected error analyzing the drawing: {e}"}), 500

    rooms = []
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "record_rooms":
            rooms = (block.input or {}).get("rooms") or []
            break

    clean_rooms = []
    for r in rooms:
        name = (r.get("name") or "").strip() if isinstance(r, dict) else ""
        if not name:
            continue
        sqft = r.get("approx_sqft")
        try:
            sqft = float(sqft) if sqft is not None else None
        except (TypeError, ValueError):
            sqft = None
        note = r.get("source_note")
        note = note.strip() or None if isinstance(note, str) else None
        clean_rooms.append({"name": name, "approx_sqft": sqft, "source_note": note})

    if not clean_rooms:
        return jsonify({
            "rooms": [],
            "message": "Couldn't make out distinct rooms — try a clearer photo or add rooms manually.",
        })

    return jsonify({"rooms": clean_rooms})


# --------------------------------------------------------------- actions

@bp.route("/<int:quote_id>/stage-change", methods=["POST"])
@auth.require_permission("quotes.manage")
def stage_change(quote_id):
    auth.csrf_protect()
    new_stage = request.form.get("stage")
    if new_stage not in quotes_repo.STAGES:
        abort(400, description="Invalid stage")
    with db.connect() as conn:
        quotes_repo.set_stage(conn, quote_id, new_stage, auth.current_user()["id"])
        audit_repo.log(conn, auth.current_user()["id"], "stage_change", "quote", quote_id, new_stage)
    return redirect(request.referrer or url_for("quotes.list_view"))


@bp.route("/<int:quote_id>/delete", methods=["POST"])
@auth.require_permission("quotes.manage")
def delete_view(quote_id):
    auth.csrf_protect()
    with db.connect() as conn:
        quotes_repo.delete_quote(conn, quote_id)
        audit_repo.log(conn, auth.current_user()["id"], "delete", "quote", quote_id)
    flash("Quote deleted.", "success")
    return redirect(url_for("quotes.list_view"))


@bp.route("/<int:quote_id>/pdf")
@auth.require_internal_login
def pdf_view(quote_id):
    with db.connect() as conn:
        full = quotes_repo.get_quote_full(conn, quote_id)
        if full is None:
            abort(404)
        ctx = pdf_theme.get_pdf_context(conn)
        audit_repo.log(conn, auth.current_user()["id"], "download_pdf", "quote", quote_id)

    meta = dict(full["quote"])
    pdf_bytes = build_quote_pdf(ctx, meta, full["rooms"], full["terms"])
    safe_name = "".join(c for c in meta["quote_number"] if c.isalnum() or c in "-_") or "quote"
    return Response(
        pdf_bytes, mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.pdf"'},
    )
