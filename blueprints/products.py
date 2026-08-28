from flask import Blueprint, render_template, request, redirect, url_for, flash, abort

import auth
import db
import repositories.products as products_repo
import repositories.audit as audit_repo

bp = Blueprint("products", __name__, url_prefix="/products")

PAGE_SIZE = 50


@bp.route("/")
@auth.require_internal_login
def list_view():
    query = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip() or None
    brand = request.args.get("brand", "").strip() or None
    page = max(1, request.args.get("page", 1, type=int))

    with db.connect() as conn:
        items = products_repo.search_products(
            conn, query=query or None, category=category, brand=brand,
            limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE,
        )
        total = products_repo.count_products(conn, query=query or None, category=category, brand=brand)
        categories = products_repo.list_categories(conn)
        brands = products_repo.list_brands(conn)

    return render_template(
        "products/list.html", items=items, total=total, page=page, page_size=PAGE_SIZE,
        query=query, category=category, brand=brand, categories=categories, brands=brands,
        csrf_token=auth.generate_csrf_token(),
    )


@bp.route("/new", methods=["GET", "POST"])
@auth.require_permission("products.manage")
def new_view():
    if request.method == "GET":
        return render_template("products/form.html", product=None, csrf_token=auth.generate_csrf_token())

    auth.csrf_protect()
    category, description, unit, brand, cost_price, error = _parse_product_form()
    if error:
        flash(error, "error")
        return render_template("products/form.html", product=None, csrf_token=auth.generate_csrf_token()), 400

    with db.connect() as conn:
        new_id = products_repo.create_product(conn, category, description, unit, brand, cost_price)
        audit_repo.log(conn, auth.current_user()["id"], "create", "product", new_id, description)

    flash("Product added.", "success")
    return redirect(url_for("products.list_view"))


@bp.route("/<int:product_id>/edit", methods=["GET", "POST"])
@auth.require_permission("products.manage")
def edit_view(product_id):
    with db.connect() as conn:
        product = products_repo.get_product(conn, product_id)
    if product is None:
        abort(404)

    if request.method == "GET":
        return render_template("products/form.html", product=product, csrf_token=auth.generate_csrf_token())

    auth.csrf_protect()
    category, description, unit, brand, cost_price, error = _parse_product_form()
    if error:
        flash(error, "error")
        return render_template("products/form.html", product=product, csrf_token=auth.generate_csrf_token()), 400

    with db.connect() as conn:
        products_repo.update_product(conn, product_id, category, description, unit, brand, cost_price)
        audit_repo.log(conn, auth.current_user()["id"], "update", "product", product_id, description)

    flash("Product updated.", "success")
    return redirect(url_for("products.list_view"))


@bp.route("/<int:product_id>/toggle-active", methods=["POST"])
@auth.require_permission("products.manage")
def toggle_active(product_id):
    auth.csrf_protect()
    with db.connect() as conn:
        product = products_repo.get_product(conn, product_id)
        if product is None:
            abort(404)
        products_repo.set_active(conn, product_id, not product["active"])
        audit_repo.log(conn, auth.current_user()["id"],
                        "deactivate" if product["active"] else "activate", "product", product_id)
    flash("Product " + ("deactivated." if product["active"] else "reactivated.") , "success")
    return redirect(url_for("products.list_view", **request.args))


def _parse_product_form():
    category = (request.form.get("category") or "").strip()
    description = (request.form.get("description") or "").strip()
    unit_select = (request.form.get("unit") or "").strip()
    unit = (request.form.get("unit_other") or "").strip() if unit_select == "Other" else unit_select
    brand = (request.form.get("brand") or "").strip() or None
    cost_price_raw = (request.form.get("cost_price") or "").strip()

    if not category or not description or not unit or not cost_price_raw:
        return None, None, None, None, None, "Category, description, unit, and cost price are required."
    try:
        cost_price = float(cost_price_raw)
    except ValueError:
        return None, None, None, None, None, "Cost price must be a number."
    if cost_price < 0:
        return None, None, None, None, None, "Cost price cannot be negative."

    return category, description, unit, brand, cost_price, None
