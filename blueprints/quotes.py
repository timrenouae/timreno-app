from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, jsonify, Response

import auth
import db
import repositories.quotes as quotes_repo
import repositories.products as products_repo
import repositories.audit as audit_repo
import repositories.settings as settings_repo
import pdf.theme as pdf_theme
from pdf.quote import build_quote_pdf

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
