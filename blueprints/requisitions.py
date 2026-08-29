"""
Staff-facing Material Requisitions (Item 5): create a requisition (an item
list, no prices), invite multiple vendors, watch their pricing come in
(Pending / In progress / Submitted per vendor), then award to whichever
vendor submitted and convert straight into a real Purchase Order via the
existing repositories/purchase_orders.py: create_purchase_order.
"""
import json

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, jsonify

import auth
import db
import repositories.requisitions as requisitions_repo
import repositories.suppliers as suppliers_repo
import repositories.products as products_repo
import repositories.purchase_orders as po_repo
import repositories.audit as audit_repo

bp = Blueprint("requisitions", __name__, url_prefix="/requisitions")


@bp.route("/")
@auth.require_permission("requisitions.manage")
def list_view():
    with db.connect() as conn:
        rows = requisitions_repo.list_requisitions(conn)
    return render_template("requisitions/list.html", rows=rows)


def _parse_posted_items(raw_json):
    """Shared by new_view and save_items -- validates the client-built
    items_json array (see static/js/requisitions.js) into a clean list of
    {product_id?, description, unit, quantity}, silently dropping any row
    missing a description/unit or without a positive quantity. Returns
    (items, was_malformed) -- was_malformed distinguishes "posted JSON that
    didn't parse at all" from "posted an empty/all-invalid list", since the
    two deserve different flash messages."""
    try:
        posted = json.loads(raw_json or "[]")
    except ValueError:
        return [], True

    items = []
    for it in posted if isinstance(posted, list) else []:
        description = (it.get("description") or "").strip()
        unit = (it.get("unit") or "").strip()
        try:
            quantity = float(it.get("quantity"))
        except (TypeError, ValueError):
            quantity = 0
        if not description or not unit or quantity <= 0:
            continue
        raw_product_id = it.get("product_id")
        product_id = int(raw_product_id) if raw_product_id not in (None, "") else None
        items.append({"product_id": product_id, "description": description, "unit": unit, "quantity": quantity})
    return items, False


@bp.route("/new", methods=["GET", "POST"])
@auth.require_permission("requisitions.manage")
def new_view():
    with db.connect() as conn:
        categories = products_repo.list_categories(conn)
        brands = products_repo.list_brands(conn)

    if request.method == "GET":
        return render_template(
            "requisitions/new.html", categories=categories, brands=brands, csrf_token=auth.generate_csrf_token(),
        )

    auth.csrf_protect()
    notes = (request.form.get("notes") or "").strip() or None
    receiver_name = (request.form.get("receiver_name") or "").strip() or None
    receiver_phone = (request.form.get("receiver_phone") or "").strip() or None
    location_url = (request.form.get("location_url") or "").strip() or None
    # Items are optional at creation -- the search/add panel is right here
    # on this same page now (no separate "add items" step to miss), but a
    # requisition with notes/receiver info and no items yet is still valid;
    # more can be added on the detail page below while it's still open.
    items, malformed = _parse_posted_items(request.form.get("items_json"))
    if malformed:
        flash("Item list was corrupted — try again.", "error")
        return render_template(
            "requisitions/new.html", categories=categories, brands=brands, csrf_token=auth.generate_csrf_token(),
        ), 400

    with db.connect_immediate() as conn:
        requisition_id = requisitions_repo.create_requisition(
            conn, notes, auth.current_user()["id"], receiver_name, receiver_phone, location_url,
        )
        if items:
            requisitions_repo.replace_items(conn, requisition_id, items)
        audit_repo.log(conn, auth.current_user()["id"], "create", "material_requisition", requisition_id,
                        f"items={len(items)}")

    flash("Requisition created.", "success")
    return redirect(url_for("requisitions.detail_view", requisition_id=requisition_id))


@bp.route("/<int:requisition_id>")
@auth.require_permission("requisitions.manage")
def detail_view(requisition_id):
    with db.connect() as conn:
        full = requisitions_repo.get_requisition_full(conn, requisition_id)
        if full is None:
            abort(404)
        suppliers = suppliers_repo.list_suppliers(conn)
        categories = products_repo.list_categories(conn)
        brands = products_repo.list_brands(conn)

    invited_ids = {v["supplier_id"] for v in full["vendors"]}
    available_suppliers = [s for s in suppliers if s["id"] not in invited_ids]
    any_submitted = any(v["status"] == "submitted" for v in full["vendors"])
    items_locked = full["requisition"]["status"] != "open" or any_submitted

    return render_template(
        "requisitions/detail.html",
        requisition=full["requisition"], items=full["items"], vendors=full["vendors"],
        available_suppliers=available_suppliers, categories=categories, brands=brands,
        items_locked=items_locked, any_submitted=any_submitted,
        csrf_token=auth.generate_csrf_token(),
    )


@bp.route("/products/search")
@auth.require_permission("requisitions.manage")
def products_search():
    """Mirrors blueprints/purchases.py / blueprints/quotes.py: products_search
    exactly (same params, same response shape) -- kept as its own route,
    matching how every other blueprint here owns its own endpoints."""
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


@bp.route("/<int:requisition_id>/items", methods=["POST"])
@auth.require_permission("requisitions.manage")
def save_items(requisition_id):
    auth.csrf_protect()
    items, malformed = _parse_posted_items(request.form.get("items_json"))
    if malformed:
        flash("Item list was corrupted — try again.", "error")
        return redirect(url_for("requisitions.detail_view", requisition_id=requisition_id))

    if not items:
        flash("Add at least one valid item (description, unit, and a positive quantity).", "error")
        return redirect(url_for("requisitions.detail_view", requisition_id=requisition_id))

    try:
        with db.connect_immediate() as conn:
            requisitions_repo.replace_items(conn, requisition_id, items)
            audit_repo.log(conn, auth.current_user()["id"], "save_items", "material_requisition", requisition_id,
                            f"count={len(items)}")
        flash("Item list saved.", "success")
    except requisitions_repo.RequisitionNotFoundError:
        abort(404)
    except requisitions_repo.RequisitionLockedError as e:
        flash(str(e), "error")
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for("requisitions.detail_view", requisition_id=requisition_id))


@bp.route("/<int:requisition_id>/receiver-info", methods=["POST"])
@auth.require_permission("requisitions.manage")
def update_receiver_info(requisition_id):
    auth.csrf_protect()
    with db.connect() as conn:
        requisition = requisitions_repo.get_requisition(conn, requisition_id)
        if requisition is None:
            abort(404)
        if requisition["status"] != "open":
            flash("This requisition is no longer open, so its delivery details can't be changed.", "error")
            return redirect(url_for("requisitions.detail_view", requisition_id=requisition_id))

        receiver_name = (request.form.get("receiver_name") or "").strip() or None
        receiver_phone = (request.form.get("receiver_phone") or "").strip() or None
        location_url = (request.form.get("location_url") or "").strip() or None
        requisitions_repo.update_receiver_info(conn, requisition_id, receiver_name, receiver_phone, location_url)
        audit_repo.log(conn, auth.current_user()["id"], "update_receiver_info", "material_requisition", requisition_id)

    flash("Delivery details saved.", "success")
    return redirect(url_for("requisitions.detail_view", requisition_id=requisition_id))


@bp.route("/<int:requisition_id>/invite", methods=["POST"])
@auth.require_permission("requisitions.manage")
def invite_vendor(requisition_id):
    auth.csrf_protect()
    supplier_id = request.form.get("supplier_id")
    if not supplier_id:
        flash("Choose a vendor to invite.", "error")
        return redirect(url_for("requisitions.detail_view", requisition_id=requisition_id))

    with db.connect() as conn:
        requisition = requisitions_repo.get_requisition(conn, requisition_id)
        if requisition is None:
            abort(404)
        if requisition["status"] != "open":
            flash("This requisition is no longer open for new vendor invitations.", "error")
            return redirect(url_for("requisitions.detail_view", requisition_id=requisition_id))
        supplier = suppliers_repo.get_supplier(conn, int(supplier_id))
        if supplier is None:
            flash("Vendor not found.", "error")
            return redirect(url_for("requisitions.detail_view", requisition_id=requisition_id))
        requisitions_repo.invite_vendor(conn, requisition_id, int(supplier_id))
        audit_repo.log(conn, auth.current_user()["id"], "invite_vendor", "material_requisition", requisition_id,
                        f"supplier_id={supplier_id}")

    flash(f"{supplier['name']} invited to price this requisition.", "success")
    return redirect(url_for("requisitions.detail_view", requisition_id=requisition_id))


@bp.route("/<int:requisition_id>/award", methods=["POST"])
@auth.require_permission("requisitions.manage")
def award(requisition_id):
    auth.csrf_protect()
    rv_id_raw = request.form.get("requisition_vendor_id")

    with db.connect_immediate() as conn:
        requisition = requisitions_repo.get_requisition(conn, requisition_id)
        if requisition is None:
            abort(404)
        if requisition["status"] != "open":
            flash("This requisition has already been awarded or cancelled.", "error")
            return redirect(url_for("requisitions.detail_view", requisition_id=requisition_id))

        rv = requisitions_repo.get_requisition_vendor(conn, int(rv_id_raw)) if (rv_id_raw or "").isdigit() else None
        if rv is None or rv["requisition_id"] != requisition_id:
            flash("Choose a vendor to award to.", "error")
            return redirect(url_for("requisitions.detail_view", requisition_id=requisition_id))
        if rv["status"] != "submitted":
            flash("You can only award to a vendor who has submitted their pricing.", "error")
            return redirect(url_for("requisitions.detail_view", requisition_id=requisition_id))

        items = requisitions_repo.list_items(conn, requisition_id)
        prices = {p["item_id"]: p for p in requisitions_repo.list_prices_for_vendor(conn, rv["id"])}
        po_items = []
        for it in items:
            price_row = prices.get(it["id"])
            unit_price = (price_row["unit_price"] if price_row else None) or 0
            product_id = it["product_id"]
            if product_id is None:
                # A free-text "custom item" requisition line has no Product
                # Master row -- but purchase_order_items.product_id is
                # NOT NULL (existing schema from Item 1's initial migration,
                # unchanged here). Rather than loosen that already-shipped
                # constraint, mint a Product Master entry for this line now
                # (same idea as any other custom item eventually needing a
                # real record once it's actually being bought), and use it.
                product_id = products_repo.create_product(
                    conn, "Custom (from requisition)", it["description"], it["unit"], None, unit_price,
                )
            po_items.append({
                "product_id": product_id, "description": it["description"], "unit": it["unit"],
                "brand": None, "quantity": it["quantity"], "unit_price": unit_price,
            })

        notes = f"Awarded from Material Requisition {requisition['requisition_no']}"
        po_id = po_repo.create_purchase_order(
            conn, rv["supplier_id"], notes, auth.current_user()["id"], items=po_items,
            receiver_name=requisition["receiver_name"], receiver_phone=requisition["receiver_phone"],
            location_url=requisition["location_url"],
        )
        requisitions_repo.award(conn, requisition_id, rv["id"], po_id)
        audit_repo.log(conn, auth.current_user()["id"], "award", "material_requisition", requisition_id,
                        f"vendor_id={rv['id']} po_id={po_id}")
        supplier_name = rv["supplier_name"]

    flash(f"Awarded to {supplier_name} — purchase order created.", "success")
    return redirect(url_for("purchases.detail_view", po_id=po_id))
