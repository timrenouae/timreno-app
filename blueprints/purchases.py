import re

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, Response, jsonify

import auth
import db
import repositories.purchase_orders as po_repo
import repositories.suppliers as suppliers_repo
import repositories.products as products_repo
import repositories.audit as audit_repo
import repositories.roles as roles_repo
import repositories.users as users_repo
from pdf.purchase_order import build_purchase_order_pdf
import pdf.theme as pdf_theme

# Item 5 -- a role holding exactly this one permission is what "quick-create
# vendor login" auto-detects, same self-service precondition the Tracker's
# "Customer access" panel already uses for tracker.view_own.
VENDOR_PERMISSION_SET = {"requisitions.vendor_fill"}

bp = Blueprint("purchases", __name__, url_prefix="/purchases")


@bp.route("/")
@auth.require_permission("purchases.manage")
def list_view():
    with db.connect() as conn:
        orders = po_repo.list_purchase_orders(conn)
    return render_template("purchases/list.html", orders=orders, csrf_token=auth.generate_csrf_token())


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
        # Item 4: the product picker is now a live search (static/js/
        # purchases.js hitting GET /purchases/products/search) rather than
        # one giant <select> preloaded with up to 1000 products -- only the
        # filter-dropdown option lists are needed server-side now.
        categories = products_repo.list_categories(conn)
        brands = products_repo.list_brands(conn)
    total = sum(i["quantity"] * i["unit_price"] for i in items)
    return render_template(
        "purchases/detail.html", po=po, items=items, categories=categories, brands=brands, total=total,
        csrf_token=auth.generate_csrf_token(),
    )


@bp.route("/products/search")
@auth.require_permission("purchases.manage")
def products_search():
    """Mirrors blueprints/quotes.py: products_search exactly (same params,
    same response shape) -- kept as its own route rather than cross-calling
    the Quotes blueprint, matching how every other blueprint here owns its
    own endpoints."""
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


@bp.route("/<int:po_id>/update-details", methods=["POST"])
@auth.require_permission("purchases.manage")
def update_details(po_id):
    auth.csrf_protect()
    with db.connect() as conn:
        po = po_repo.get_purchase_order(conn, po_id)
        if po is None:
            abort(404)
        if po["status"] not in ("draft", "sent"):
            flash("Can't change order details on a purchase order that has already started receiving stock.", "error")
            return redirect(url_for("purchases.detail_view", po_id=po_id))

        payment_terms = (request.form.get("payment_terms") or "").strip() or None
        expected_delivery_date = (request.form.get("expected_delivery_date") or "").strip() or None
        ship_to_address = (request.form.get("ship_to_address") or "").strip() or None
        receiver_name = (request.form.get("receiver_name") or "").strip() or None
        receiver_phone = (request.form.get("receiver_phone") or "").strip() or None
        location_url = (request.form.get("location_url") or "").strip() or None

        po_repo.update_po_details(conn, po_id, payment_terms, expected_delivery_date, ship_to_address,
                                   receiver_name, receiver_phone, location_url)
        audit_repo.log(conn, auth.current_user()["id"], "update_details", "purchase_order", po_id)

    flash("Order details saved.", "success")
    return redirect(url_for("purchases.detail_view", po_id=po_id))


@bp.route("/<int:po_id>/duplicate", methods=["POST"])
@auth.require_permission("purchases.manage")
def duplicate(po_id):
    auth.csrf_protect()
    with db.connect() as conn:
        po = po_repo.get_purchase_order(conn, po_id)
        if po is None:
            abort(404)
        source_items = po_repo.list_po_items(conn, po_id)

        # Fresh snapshot rows on a brand-new draft PO -- not references back
        # to the old PO's rows, so later edits to either PO (or to the
        # Product Master) never retroactively change the other.
        new_items = [
            {
                "product_id": i["product_id"], "description": i["description"], "unit": i["unit"],
                "brand": i["brand"], "quantity": i["quantity"], "unit_price": i["unit_price"],
            }
            for i in source_items
        ]
        new_po_id = po_repo.create_purchase_order(
            conn, po["supplier_id"], po["notes"], auth.current_user()["id"], items=new_items,
        )
        # Payment terms, ship-to, and the delivery contact usually stay the
        # same for a repeat order with the same supplier; expected_delivery_date
        # is deliberately left blank -- a new order needs its own date.
        po_repo.update_po_details(conn, new_po_id, po["payment_terms"], None, po["ship_to_address"],
                                   po["receiver_name"], po["receiver_phone"], po["location_url"])
        audit_repo.log(conn, auth.current_user()["id"], "duplicate", "purchase_order", new_po_id,
                        f"duplicated from po_id={po_id}")

    flash("Duplicated as a new draft purchase order.", "success")
    return redirect(url_for("purchases.detail_view", po_id=new_po_id))


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
        return render_template("purchases/supplier_form.html", supplier=None, vendor_users=[],
                                vendor_role_exists=False, csrf_token=auth.generate_csrf_token())

    auth.csrf_protect()
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Supplier name is required.", "error")
        return render_template("purchases/supplier_form.html", supplier=None, vendor_users=[],
                                vendor_role_exists=False, csrf_token=auth.generate_csrf_token()), 400

    with db.connect() as conn:
        new_id = suppliers_repo.create_supplier(
            conn, name,
            (request.form.get("contact_name") or "").strip() or None,
            (request.form.get("phone") or "").strip() or None,
            (request.form.get("email") or "").strip() or None,
            (request.form.get("address") or "").strip() or None,
        )
        audit_repo.log(conn, auth.current_user()["id"], "create", "supplier", new_id, name)

    # Straight to the edit page, not the supplier list -- that page is the
    # only place the vendor code and the "Quick-create vendor login" /
    # link-existing-account panel are shown (they need a real supplier_id
    # to attach to). Landing here right after creation puts vendor-login
    # setup directly in front of the person adding the supplier instead of
    # it being a separate, easy-to-forget step reached only by navigating
    # back in through Edit later.
    flash("Supplier added — set up their vendor portal login below if they'll be pricing Material Requisitions.", "success")
    return redirect(url_for("purchases.supplier_edit", supplier_id=new_id))


def _supplier_form_vendor_context(conn):
    """Shared by every purchases/supplier_form.html render -- the dropdown
    of users already holding requisitions.vendor_fill (same shape as the
    Tracker's 'Customer access' panel dropdown), and whether a role holding
    EXACTLY that one permission exists yet (self-service precondition for
    the 'Quick-create vendor login' button)."""
    vendor_users = users_repo.list_active_users_with_permission(conn, "requisitions.vendor_fill")
    vendor_role = roles_repo.find_role_with_exact_permissions(conn, VENDOR_PERMISSION_SET)
    return vendor_users, vendor_role is not None


@bp.route("/suppliers/<int:supplier_id>/edit", methods=["GET", "POST"])
@auth.require_permission("suppliers.manage")
def supplier_edit(supplier_id):
    with db.connect() as conn:
        supplier = suppliers_repo.get_supplier(conn, supplier_id)
        vendor_users, vendor_role_exists = _supplier_form_vendor_context(conn)
    if supplier is None:
        abort(404)

    if request.method == "GET":
        return render_template("purchases/supplier_form.html", supplier=supplier, vendor_users=vendor_users,
                                vendor_role_exists=vendor_role_exists, csrf_token=auth.generate_csrf_token())

    auth.csrf_protect()
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Supplier name is required.", "error")
        return render_template("purchases/supplier_form.html", supplier=supplier, vendor_users=vendor_users,
                                vendor_role_exists=vendor_role_exists, csrf_token=auth.generate_csrf_token()), 400

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


@bp.route("/suppliers/<int:supplier_id>/vendor-user", methods=["POST"])
@auth.require_permission("suppliers.manage")
def set_vendor_user(supplier_id):
    """Links (or, with a blank selection, unlinks) an EXISTING user account
    to this supplier -- the manual path, e.g. staff created the login by
    hand via Admin -> Users first. Same dropdown-and-save shape as the
    Tracker's 'Customer access' panel / tracker.set_client_user."""
    auth.csrf_protect()
    with db.connect() as conn:
        supplier = suppliers_repo.get_supplier(conn, supplier_id)
        if supplier is None:
            abort(404)
        raw = (request.form.get("vendor_user_id") or "").strip()
        user_id = int(raw) if raw.isdigit() else None
        suppliers_repo.set_vendor_user(conn, supplier_id, user_id)
        audit_repo.log(conn, auth.current_user()["id"], "set_vendor_user", "supplier", supplier_id, str(user_id))
    flash("Vendor portal login updated.", "success")
    return redirect(url_for("purchases.supplier_edit", supplier_id=supplier_id))


@bp.route("/suppliers/<int:supplier_id>/quick-create-vendor-login", methods=["POST"])
@auth.require_permission("suppliers.manage")
def quick_create_vendor_login(supplier_id):
    """One-click vendor login: derives username from the supplier's
    permanent vendor_code (lowercased, stripped to a-z0-9), default
    password = that username + '12345', hashed via the same
    auth.hash_password() every other account uses, assigned the
    auto-detected Vendor role, and linked to this supplier. The generated
    credentials are shown exactly once, in this flash message, for staff to
    copy and hand to the vendor -- never stored or displayable in plain
    text again, same as any other password in this app."""
    auth.csrf_protect()
    with db.connect() as conn:
        supplier = suppliers_repo.get_supplier(conn, supplier_id)
        if supplier is None:
            abort(404)
        if supplier["portal_user_id"]:
            flash("This supplier already has a linked vendor login.", "error")
            return redirect(url_for("purchases.supplier_edit", supplier_id=supplier_id))

        vendor_role = roles_repo.find_role_with_exact_permissions(conn, VENDOR_PERMISSION_SET)
        if vendor_role is None:
            flash('No Vendor role exists yet — under Admin, create a "Vendor" role with just the '
                  '"Vendor portal" permission, then come back here to quick-create a login.', "error")
            return redirect(url_for("purchases.supplier_edit", supplier_id=supplier_id))

        username = re.sub(r"[^a-z0-9]", "", (supplier["vendor_code"] or "").lower())
        if not username:
            flash("This supplier has no vendor code yet — cannot generate a login.", "error")
            return redirect(url_for("purchases.supplier_edit", supplier_id=supplier_id))
        password = f"{username}12345"

        if users_repo.username_exists(conn, username):
            flash(f"A user named \"{username}\" already exists — link an existing account below instead.", "error")
            return redirect(url_for("purchases.supplier_edit", supplier_id=supplier_id))

        pw_hash, salt, iterations = auth.hash_password(password)
        new_user_id = users_repo.create_user(
            conn, username, f"{supplier['name']} (Vendor)", pw_hash, salt, iterations, vendor_role["id"],
        )
        suppliers_repo.set_vendor_user(conn, supplier_id, new_user_id)
        audit_repo.log(conn, auth.current_user()["id"], "quick_create_vendor_login", "supplier", supplier_id,
                        f"user_id={new_user_id}")

    flash(f'Vendor login created — username "{username}", password "{password}" '
          f"(shown once — copy it now and hand it to the vendor).", "success")
    return redirect(url_for("purchases.supplier_edit", supplier_id=supplier_id))


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
