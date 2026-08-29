import datetime

from flask import Blueprint, render_template, request, jsonify, Response

import auth
import db
import repositories.quotes as quotes_repo
import repositories.estimator_rates as rates_repo
from estimator_constants import (
    RATE_DEFAULTS, SPACE_TEMPLATES, FINISH_MULTIPLIERS,
    SQFT_TIERS, SQFT_PROJECT_TYPES,
)
from units import UNIT_OPTIONS
import pdf.theme as pdf_theme
from pdf.estimate import build_estimate_pdf
from pdf.quick_estimate import build_quick_estimate_pdf
import drawing_import

bp = Blueprint("estimator", __name__, url_prefix="/estimator")


def _rate_defaults_json():
    return {k: {"label": v["label"], "unit": v["unit"], "default_rate": v["rate"]} for k, v in RATE_DEFAULTS.items()}


@bp.route("/")
@auth.require_permission("quotes.manage")
def page_view():
    with db.connect() as conn:
        overrides = rates_repo.get_overrides(conn)
        sqft_rates = rates_repo.get_effective_sqft_rates(conn)
        next_number = quotes_repo.peek_next_quote_number(conn)
    csrf_token = auth.generate_csrf_token()
    initial_data = {
        "csrfToken": csrf_token,
        "nextQuoteNumber": next_number,
        "rateDefaults": _rate_defaults_json(),
        "overrides": overrides,
        "spaceTemplates": SPACE_TEMPLATES,
        "finishMultipliers": FINISH_MULTIPLIERS,
        "unitOptions": UNIT_OPTIONS,
        # Quick Estimate mode (Item 1 reuse + per-sqft tiers): sqftRates is
        # {project_type: {tier: {key, label, rate}}}, already override-
        # resolved server-side so the page never needs to re-derive it.
        "sqftRates": sqft_rates,
        "sqftTiers": SQFT_TIERS,
        "sqftProjectTypes": SQFT_PROJECT_TYPES,
    }
    return render_template("estimator/page.html", initial_data=initial_data, csrf_token=csrf_token)


@bp.route("/rates/save", methods=["POST"])
@auth.require_permission("quotes.manage")
def save_rates():
    auth.csrf_protect()
    body = request.get_json(silent=True) or {}
    rate_values = body.get("rates") or {}
    try:
        rate_values = {k: float(v) for k, v in rate_values.items()}
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid rate values."}), 400
    with db.connect_immediate() as conn:
        rates_repo.save_overrides(conn, rate_values, auth.current_user()["id"])
        overrides = rates_repo.get_overrides(conn)
    return jsonify({"overrides": overrides})


@bp.route("/rates/reset", methods=["POST"])
@auth.require_permission("quotes.manage")
def reset_rates():
    auth.csrf_protect()
    with db.connect_immediate() as conn:
        rates_repo.reset_overrides(conn)
    return jsonify({"overrides": {}})


@bp.route("/sqft-rates/save", methods=["POST"])
@auth.require_permission("quotes.manage")
def save_sqft_rates():
    """Same shape as /rates/save above, reusing the same sparse
    rate_key -> rate override table (see repositories/estimator_rates.py) --
    a separate route only because the frontend and the response shape
    (project-type-grouped, override-resolved) are Quick-Estimate-specific."""
    auth.csrf_protect()
    body = request.get_json(silent=True) or {}
    rate_values = body.get("rates") or {}
    try:
        rate_values = {k: float(v) for k, v in rate_values.items()}
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid rate values."}), 400
    with db.connect_immediate() as conn:
        rates_repo.save_overrides(conn, rate_values, auth.current_user()["id"])
        sqft_rates = rates_repo.get_effective_sqft_rates(conn)
    return jsonify({"sqftRates": sqft_rates})


@bp.route("/sqft-rates/reset", methods=["POST"])
@auth.require_permission("quotes.manage")
def reset_sqft_rates():
    auth.csrf_protect()
    with db.connect_immediate() as conn:
        rates_repo.reset_sqft_overrides(conn)
        sqft_rates = rates_repo.get_effective_sqft_rates(conn)
    return jsonify({"sqftRates": sqft_rates})


@bp.route("/import-drawing", methods=["POST"])
@auth.require_permission("quotes.manage")
def import_drawing():
    """Same "Import from Drawing" feature as Quote Builder (blueprints/
    quotes.py), reused here so a Quick Estimate can start from a total
    sq ft pulled straight off an uploaded floor plan instead of typed in by
    hand -- see drawing_import.py for the shared extraction logic."""
    auth.csrf_protect()
    try:
        result = drawing_import.analyze_drawing(request.files.get("drawing_file"))
    except drawing_import.DrawingImportError as e:
        return jsonify({"error": str(e)}), e.status
    return jsonify(result)


@bp.route("/quick-pdf", methods=["POST"])
@auth.require_permission("quotes.manage")
def quick_pdf():
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "Invalid or missing JSON body."}), 400

    project_type = body.get("project_type")
    tier = body.get("tier")
    with db.connect() as conn:
        sqft_rates = rates_repo.get_effective_sqft_rates(conn)
        ctx = pdf_theme.get_pdf_context(conn)

    # The rate is always resolved server-side from the saved rate sheet --
    # never trusts a client-posted number -- so a Quick Estimate PDF can
    # never be generated at a rate nobody actually configured.
    tier_entry = (sqft_rates.get(project_type) or {}).get(tier)
    if tier_entry is None:
        return jsonify({"error": "Choose a project type and quality tier."}), 400

    rooms = []
    for r in (body.get("rooms") or []):
        name = (r.get("name") or "").strip()
        try:
            sqft = float(r.get("sqft") or 0)
        except (TypeError, ValueError):
            sqft = 0
        if name and sqft > 0:
            rooms.append({"name": name, "sqft": sqft})
    if not rooms:
        return jsonify({"error": "Add at least one room/space with a sq ft value."}), 400

    project_type_label = dict(SQFT_PROJECT_TYPES).get(project_type, project_type)
    meta = {
        "project_name": body.get("project_name"), "location": body.get("location"),
        "project_type_label": project_type_label, "tier": tier, "rate": tier_entry["rate"],
        "vat_percent": body.get("vat_percent"),
    }
    pdf_bytes = build_quick_estimate_pdf(ctx, meta, rooms)
    safe_name = "".join(c for c in (meta.get("project_name") or "estimate") if c.isalnum() or c in "-_") or "estimate"
    return Response(
        pdf_bytes, mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="Quick_Estimate_{safe_name}.pdf"'},
    )


def _spaces_from_body(body):
    spaces = body.get("spaces") or []
    cleaned = []
    for sp in spaces:
        items = [
            {
                "description": i.get("description") or "",
                "unit": i.get("unit") or "nos",
                "qty": float(i.get("qty") or 0),
                "unit_price": float(i.get("unit_price") or 0),
            }
            for i in (sp.get("items") or [])
        ]
        cleaned.append({
            "label": sp.get("label") or sp.get("type") or "Space",
            "size": float(sp.get("size") or 0),
            "notes": sp.get("notes") or "",
            "items": items,
        })
    return cleaned


@bp.route("/pdf", methods=["POST"])
@auth.require_permission("quotes.manage")
def pdf_view():
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "Invalid or missing JSON body."}), 400
    meta = {
        "project_name": body.get("project_name"),
        "location": body.get("location"),
        "finish_level": body.get("finish_level") or "Standard",
        "vat_percent": body.get("vat_percent"),
    }
    spaces = _spaces_from_body(body)
    if not spaces:
        return jsonify({"error": "Add at least one space before exporting."}), 400
    with db.connect() as conn:
        ctx = pdf_theme.get_pdf_context(conn)
    pdf_bytes = build_estimate_pdf(ctx, meta, spaces)
    safe_name = "".join(c for c in (meta.get("project_name") or "estimate") if c.isalnum() or c in "-_") or "estimate"
    return Response(
        pdf_bytes, mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="Rough_Estimate_{safe_name}.pdf"'},
    )


@bp.route("/send-to-builder", methods=["POST"])
@auth.require_permission("quotes.manage")
def send_to_builder():
    auth.csrf_protect()
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "Invalid or missing JSON body."}), 400
    spaces = _spaces_from_body(body)
    if not spaces:
        return jsonify({"error": "Add at least one space before sending this to Quote Builder."}), 400

    rooms = [
        {
            "name": sp["label"],
            "notes": sp["notes"],
            "items": [
                {"product_id": None, "description": i["description"], "unit": i["unit"],
                 "brand": None, "qty": i["qty"], "unit_price": i["unit_price"]}
                for i in sp["items"]
            ],
        }
        for sp in spaces
    ]
    finish_level = body.get("finish_level") or "Standard"
    job_notes = (
        f"Created from the Rough Estimator ({finish_level} finish level) on "
        f"{datetime.datetime.now().strftime('%d/%m/%Y %H:%M')}. Review every line before sending this to a client."
    )
    meta = {
        "client_name": body.get("client_name"), "project_name": body.get("project_name"),
        "project_type": body.get("project_type"), "location": body.get("location"),
        "quote_date": None, "vat_percent": body.get("vat_percent"), "job_notes": job_notes,
    }

    with db.connect_immediate() as conn:
        quote_number = quotes_repo.reserve_next_quote_number(conn)
        quote_id, quote_number, _ = quotes_repo.save_quote(
            conn, None, quote_number, meta, rooms, [], auth.current_user()["id"], finalize=False,
        )

    return jsonify({"quote_id": quote_id, "quote_number": quote_number})
