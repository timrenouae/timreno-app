"""
Vendor portal (Item 5): a login restricted to only its own linked supplier's
requisitions -- direct sibling of blueprints/portal.py (the Customer
portal). requisitions.vendor_fill is a _PORTAL_ONLY_CODES entry (see
auth.py), so a vendor-only account is already blocked from every internal
view by require_internal_login / require_internal_login_or_task_access;
this blueprint is its one dashboard.
"""
from types import SimpleNamespace

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort

import auth
import db
import repositories.requisitions as requisitions_repo
import repositories.suppliers as suppliers_repo
import repositories.purchase_orders as po_repo
import repositories.audit as audit_repo

bp = Blueprint("vendor_portal", __name__, url_prefix="/vendor-portal")


def _requisition_vendor_owner_resource(rv_id, **kwargs):
    """Mirrors blueprints/portal.py: _quote_owner_resource exactly, just
    resolving ownership through supplier.portal_user_id instead of
    quote.client_user_id."""
    with db.connect() as conn:
        rv = requisitions_repo.get_requisition_vendor(conn, rv_id)
        if rv is None:
            return None
        supplier = suppliers_repo.get_supplier(conn, rv["supplier_id"])
    return SimpleNamespace(owner_user_id=supplier["portal_user_id"] if supplier else None)


def _own_supplier_id(conn):
    supplier = suppliers_repo.get_supplier_by_portal_user(conn, auth.current_user()["id"])
    return supplier["id"] if supplier else None


def _parse_price_form(form, conn, requisition_id):
    """Plain per-line price_<item_id> / note_<item_id> form fields -- no JS
    needed for this simple numeric-inputs-per-row page, consistent with the
    rest of this app's "no heavy JS unless truly needed" convention."""
    items = []
    for item in requisitions_repo.list_items(conn, requisition_id):
        raw_price = (form.get(f"price_{item['id']}") or "").strip()
        note = (form.get(f"note_{item['id']}") or "").strip() or None
        unit_price = None
        if raw_price:
            try:
                unit_price = float(raw_price)
            except ValueError:
                unit_price = None
        items.append({"item_id": item["id"], "unit_price": unit_price, "vendor_notes": note})
    return items


@bp.route("/")
@auth.login_required
def list_view():
    with db.connect() as conn:
        supplier = suppliers_repo.get_supplier_by_portal_user(conn, auth.current_user()["id"])
        if supplier is None:
            abort(403)
        rows = requisitions_repo.list_requisitions_for_vendor(conn, supplier["id"])
    return render_template("vendor_portal/list.html", rows=rows, supplier=supplier)


@bp.route("/<int:rv_id>")
@auth.require_owner_or_permission("requisitions.manage", _requisition_vendor_owner_resource)
def detail_view(rv_id):
    with db.connect() as conn:
        rv = requisitions_repo.get_requisition_vendor(conn, rv_id)
        if rv is None:
            abort(404)
        requisition = requisitions_repo.get_requisition(conn, rv["requisition_id"])
        items = requisitions_repo.list_items(conn, rv["requisition_id"])
        prices_by_item = {p["item_id"]: dict(p) for p in requisitions_repo.list_prices_for_vendor(conn, rv_id)}
        # Once this requisition is awarded, and it was awarded to THIS
        # vendor, load the resulting PO -- this is the vendor's one place to
        # see "you got the order" plus who/where to deliver to, and to
        # acknowledge it. There's no email here (Item 6 is on hold), so this
        # page (reached from the "You got this order" badge on the list) is
        # the notification.
        won_po = None
        if requisition["status"] == "awarded" and requisition["awarded_vendor_id"] == rv["id"]:
            won_po = po_repo.get_purchase_order(conn, requisition["resulting_po_id"])
    return render_template(
        "vendor_portal/detail.html", rv=rv, requisition=requisition, items=items, prices_by_item=prices_by_item,
        won_po=won_po, csrf_token=auth.generate_csrf_token(),
    )


@bp.route("/<int:rv_id>/accept-delivery", methods=["POST"])
@auth.login_required
def accept_delivery(rv_id):
    auth.csrf_protect()
    with db.connect() as conn:
        rv = requisitions_repo.get_requisition_vendor(conn, rv_id)
        if rv is None:
            abort(404)
        own_supplier_id = _own_supplier_id(conn)
        # Same "only the actual vendor, never staff on their behalf" rule
        # as save_draft/submit above.
        if own_supplier_id is None or own_supplier_id != rv["supplier_id"]:
            abort(403)
        requisition = requisitions_repo.get_requisition(conn, rv["requisition_id"])
        if requisition is None or requisition["status"] != "awarded" or requisition["awarded_vendor_id"] != rv["id"]:
            flash("This order isn't awarded to you.", "error")
            return redirect(url_for("vendor_portal.detail_view", rv_id=rv_id))

        po_repo.accept_delivery(conn, requisition["resulting_po_id"])
        audit_repo.log(conn, auth.current_user()["id"], "accept_delivery", "purchase_order",
                        requisition["resulting_po_id"])

    flash("Delivery acknowledged — thanks for confirming.", "success")
    return redirect(url_for("vendor_portal.detail_view", rv_id=rv_id))


@bp.route("/<int:rv_id>/save-draft", methods=["POST"])
@auth.login_required
def save_draft(rv_id):
    auth.csrf_protect()
    with db.connect() as conn:
        rv = requisitions_repo.get_requisition_vendor(conn, rv_id)
        if rv is None:
            abort(404)
        own_supplier_id = _own_supplier_id(conn)
        # Deliberately NOT require_owner_or_permission here -- this action
        # must never be usable by staff on a vendor's behalf, even though
        # staff can VIEW this page (detail_view above). Only the actual
        # linked vendor may write their own pricing.
        if own_supplier_id is None or own_supplier_id != rv["supplier_id"]:
            abort(403)
        if rv["status"] != "pending":
            flash("This requisition has already been submitted and can no longer be edited.", "error")
            return redirect(url_for("vendor_portal.detail_view", rv_id=rv_id))
        items = _parse_price_form(request.form, conn, rv["requisition_id"])
        requisitions_repo.save_vendor_prices(conn, rv_id, items)
        audit_repo.log(conn, auth.current_user()["id"], "save_draft", "material_requisition_vendor", rv_id)
    flash("Draft saved.", "success")
    return redirect(url_for("vendor_portal.detail_view", rv_id=rv_id))


@bp.route("/<int:rv_id>/submit", methods=["POST"])
@auth.login_required
def submit(rv_id):
    auth.csrf_protect()
    with db.connect() as conn:
        rv = requisitions_repo.get_requisition_vendor(conn, rv_id)
        if rv is None:
            abort(404)
        own_supplier_id = _own_supplier_id(conn)
        if own_supplier_id is None or own_supplier_id != rv["supplier_id"]:
            abort(403)
        items = _parse_price_form(request.form, conn, rv["requisition_id"])

    try:
        with db.connect_immediate() as conn:
            requisitions_repo.submit_vendor_prices(conn, rv_id, items)
            audit_repo.log(conn, auth.current_user()["id"], "submit", "material_requisition_vendor", rv_id)
        flash("Prices submitted — you can no longer edit this requisition.", "success")
    except requisitions_repo.RequisitionVendorNotFoundError:
        abort(404)
    except requisitions_repo.VendorAlreadySubmittedError as e:
        flash(str(e), "error")
    except requisitions_repo.IncompletePricingError as e:
        flash(str(e), "error")
    return redirect(url_for("vendor_portal.detail_view", rv_id=rv_id))
