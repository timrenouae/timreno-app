from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, Response

import auth
import db
import repositories.purchase_orders as po_repo
import repositories.suppliers as suppliers_repo
import repositories.products as products_repo
import repositories.audit as audit_repo
from pdf.purchase_order import build_purchase_order_pdf
import pdf.theme as pdf_theme

bp = Blueprint("purchases", __name__, url_prefix="/purchases")


@bp.route("/")
@auth.require_permission("purchases.manage")
def list_view():
    with db.connect() as conn:
        orders = po_repo.list_purchase_orders(conn)
    return render_template("purchases/list.html", orders=orders)


@bp.route("/new", methods=["GET", "POST"])
@auth.require_permission("purchases.manage")
def new_view():
    with db.connect() as conn:
        suppliers = suppliers_repo.list_suppliers(conn)

    if not suppliers:
        flash("Add a supplier first.", "error")

    if request.method == "GET":
        return render_template("purchases/new.html", suppliers=suppliers, csrf_token=auth.generate_csrf_token())

    auth.csrf_protect()
    supplier_id = request.form.get("supplier_id")
    notes = (request.form.get("notes") or "").strip() or None
    if not supplier_id:
        flash("Choose a supplier.", "error")
        return render_template("purchases/new.html", suppliers=suppliers, csrf_token=auth.generate_csrf_token()), 400

    with db.connect() as conn:
        po_id = po_repo.create_purchase_order(conn, int(supplier_id), notes, auth.current_user()["id"], items=[])
        audit_repo.log(conn, auth.current_user()["id"], "create", "purchase_order", po_id)

    return redirect(url_for("purchases.detail_view", po_id=po_id))


@bp.route("/<int:po_id>")
@auth.require_permission("purchases.manage")
def detail_view(po_id):
    with db.connect() as conn:
        po = po_repo.get_purchase_order(conn, po_id)
        if po is None:
            abort(404)
        items = po_repo.list_po_items(conn, po_id)
        products = products_repo.search_products(conn, limit=1000)
    total = sum(i["quantity"] * i["unit_price"] for i in items)
    return render_template(
        "purchases/detail.html", po=po, items=items, products=products, total=total,
        csrf_token=auth.generate_csrf_token(),
    )


@bp.route("/<int:po_id>/add-item", methods=["POST"])
@auth.require_permission("purchases.manage")
def add_item(po_id):
    auth.csrf_protect()
    with db.connect() as conn:
        po = po_repo.get_purchase_order(conn, po_id)
        if po is None:
            abort(404)
        if po["status"] not in ("draft", "sent"):
            flash("Can't add items to a purchase order that has already started receiving stock.", "error")
            return redirect(url_for("purchases.detail_view", po_id=po_id))

        product_id = request.form.get("product_id")
        quantity_raw = request.form.get("quantity")
        unit_price_raw = request.form.get("unit_price")
        try:
            quantity = float(quantity_raw)
            unit_price = float(unit_price_raw)
        except (TypeError, ValueError):
            flash("Quantity and unit price must be numbers.", "error")
            return redirect(url_for("purchases.detail_view", po_id=po_id))
        if not product_id or quantity <= 0 or unit_price < 0:
            flash("Choose a product, and enter a positive quantity and non-negative unit price.", "error")
            return redirect(url_for("purchases.detail_view", po_id=po_id))

        product = products_repo.get_product(conn, int(product_id))
        if product is None:
            flash("Product not found.", "error")
            return redirect(url_for("purchases.detail_view", po_id=po_id))

        conn.execute(
            """INSERT INTO purchase_order_items (po_id, product_id, description, unit, brand, quantity, unit_price)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (po_id, product["id"], product["description"], product["unit"], product["brand"], quantity, unit_price),
        )
        audit_repo.log(conn, auth.current_user()["id"], "add_item", "purchase_order", po_id,
                        f"product_id={product['id']} qty={quantity}")

    return redirect(url_for("purchases.detail_view", po_id=po_id))


@bp.route("/<int:po_id>/mark-sent", methods=["POST"])
@auth.require_permission("purchases.manage")
def mark_sent(po_id):
    auth.csrf_protect()
    with db.connect() as conn:
        po_repo.mark_sent(conn, po_id)
        audit_repo.log(conn, auth.current_user()["id"], "mark_sent", "purchase_order", po_id)
    flash("Purchase order marked as sent to supplier.", "success")
    return redirect(url_for("purchases.detail_view", po_id=po_id))


@bp.route("/<int:po_id>/items/<int:item_id>/receive", methods=["POST"])
@auth.require_permission("purchases.manage")
def receive_item(po_id, item_id):
    auth.csrf_protect()
    received_qty_raw = request.form.get("received_qty")
    try:
        received_qty = float(received_qty_raw)
    except (TypeError, ValueError):
        flash("Received quantity must be a number.", "error")
        return redirect(url_for("purchases.detail_view", po_id=po_id))
    if received_qty <= 0:
        flash("Received quantity must be positive.", "error")
        return redirect(url_for("purchases.detail_view", po_id=po_id))

    try:
        # BEGIN IMMEDIATE holds the write lock for the whole check-then-write
        # sequence, so a second concurrent "mark received" click on the same
        # line blocks until this one finishes rather than racing it.
        with db.connect_immediate() as conn:
            po_repo.receive_line(conn, item_id, received_qty, auth.current_user()["id"])
            audit_repo.log(conn, auth.current_user()["id"], "receive_item", "purchase_order", po_id,
                            f"item_id={item_id} qty={received_qty}")
    except po_repo.AlreadyReceivedError:
        flash("This line has already been fully received.", "error")
    except ValueError as e:
        flash(str(e), "error")
    else:
        flash("Marked received — product cost price updated.", "success")

    return redirect(url_for("purchases.detail_view", po_id=po_id))


@bp.route("/suppliers")
@auth.require_permission("suppliers.manage")
def suppliers_view():
    with db.connect() as conn:
        suppliers = suppliers_repo.list_suppliers(conn, include_inactive=True)
    return render_template("purchases/suppliers.html", suppliers=suppliers, csrf_token=auth.generate_csrf_token())


@bp.route("/suppliers/new", methods=["GET", "POST"])
@auth.require_permission("suppliers.manage")
def supplier_new():
    if request.method == "GET":
        return render_template("purchases/supplier_form.html", supplier=None, csrf_token=auth.generate_csrf_token())

    auth.csrf_protect()
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Supplier name is required.", "error")
        return render_template("purchases/supplier_form.html", supplier=None, csrf_token=auth.generate_csrf_token()), 400

    with db.connect() as conn:
        new_id = suppliers_repo.create_supplier(
            conn, name,
            (request.form.get("contact_name") or "").strip() or None,
            (request.form.get("phone") or "").strip() or None,
            (request.form.get("email") or "").strip() or None,
            (request.form.get("address") or "").strip() or None,
        )
        audit_repo.log(conn, auth.current_user()["id"], "create", "supplier", new_id, name)

    flash("Supplier added.", "success")
    return redirect(url_for("purchases.suppliers_view"))


@bp.route("/suppliers/<int:supplier_id>/edit", methods=["GET", "POST"])
@auth.require_permission("suppliers.manage")
def supplier_edit(supplier_id):
    with db.connect() as conn:
        supplier = suppliers_repo.get_supplier(conn, supplier_id)
    if supplier is None:
        abort(404)

    if request.method == "GET":
        return render_template("purchases/supplier_form.html", supplier=supplier, csrf_token=auth.generate_csrf_token())

    auth.csrf_protect()
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Supplier name is required.", "error")
        return render_template("purchases/supplier_form.html", supplier=supplier, csrf_token=auth.generate_csrf_token()), 400

    with db.connect() as conn:
        suppliers_repo.update_supplier(
            conn, supplier_id, name,
            (request.form.get("contact_name") or "").strip() or None,
            (request.form.get("phone") or "").strip() or None,
            (request.form.get("email") or "").strip() or None,
            (request.form.get("address") or "").strip() or None,
        )
        audit_repo.log(conn, auth.current_user()["id"], "update", "supplier", supplier_id, name)

    flash("Supplier updated.", "success")
    return redirect(url_for("purchases.suppliers_view"))


@bp.route("/suppliers/<int:supplier_id>/toggle-active", methods=["POST"])
@auth.require_permission("suppliers.manage")
def supplier_toggle_active(supplier_id):
    auth.csrf_protect()
    with db.connect() as conn:
        supplier = suppliers_repo.get_supplier(conn, supplier_id)
        if supplier is None:
            abort(404)
        suppliers_repo.set_active(conn, supplier_id, not supplier["active"])
        audit_repo.log(conn, auth.current_user()["id"],
                        "deactivate" if supplier["active"] else "activate", "supplier", supplier_id)
    flash("Supplier " + ("deactivated." if supplier["active"] else "reactivated."), "success")
    return redirect(url_for("purchases.suppliers_view"))


@bp.route("/<int:po_id>/pdf")
@auth.require_permission("purchases.manage")
def pdf_view(po_id):
    with db.connect() as conn:
        po = po_repo.get_purchase_order(conn, po_id)
        if po is None:
            abort(404)
        items = po_repo.list_po_items(conn, po_id)
        ctx = pdf_theme.get_pdf_context(conn)
        audit_repo.log(conn, auth.current_user()["id"], "download_pdf", "purchase_order", po_id)

    pdf_bytes = build_purchase_order_pdf(ctx, dict(po), [dict(i) for i in items])
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{po["po_number"]}.pdf"'},
    )
