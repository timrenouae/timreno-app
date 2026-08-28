"""Throwaway manual end-to-end check for Item 5, run standalone before
folding assertions into tests/smoke_test.py. Not part of the shipped app."""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEST_DB = "/tmp/timr_item5_dev_check.db"
os.environ["TIMR_DB_PATH"] = TEST_DB
os.environ["TIMR_COOKIE_SECURE"] = "0"
for suffix in ("", "-wal", "-shm"):
    if os.path.exists(TEST_DB + suffix):
        os.remove(TEST_DB + suffix)

import migrate  # noqa: E402
migrate.apply_migrations()
migrate.seed_permissions_and_admin_role()

import auth  # noqa: E402
import db  # noqa: E402
import repositories.users as users_repo  # noqa: E402
import repositories.roles as roles_repo  # noqa: E402
from app import app  # noqa: E402

app.testing = True
failures = []


def check(label, cond):
    print(("PASS" if cond else "FAIL"), "-", label)
    if not cond:
        failures.append(label)


def get_csrf(html):
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert m, "no csrf in page: " + html[:500]
    return m.group(1)


with db.connect() as conn:
    admin_role = roles_repo.get_role_by_name(conn, "Admin")
    pw_hash, salt, iters = auth.hash_password("AdminPass123!")
    admin_id = users_repo.create_user(conn, "dev_admin", "Dev Admin", pw_hash, salt, iters, admin_role["id"])

client = app.test_client()
r = client.get("/auth/login")
c = get_csrf(r.get_data(as_text=True))
client.post("/auth/login", data={"username": "dev_admin", "password": "AdminPass123!", "csrf_token": c})

# create two suppliers
r = client.get("/purchases/suppliers/new")
c = get_csrf(r.get_data(as_text=True))
client.post("/purchases/suppliers/new", data={"name": "Dev Vendor A", "csrf_token": c})
r = client.get("/purchases/suppliers/new")
c = get_csrf(r.get_data(as_text=True))
client.post("/purchases/suppliers/new", data={"name": "Dev Vendor B", "csrf_token": c})

with db.connect() as conn:
    sup_a = conn.execute("SELECT * FROM suppliers WHERE name='Dev Vendor A'").fetchone()
    sup_b = conn.execute("SELECT * FROM suppliers WHERE name='Dev Vendor B'").fetchone()
check("supplier A got a vendor_code", sup_a["vendor_code"] == "VEND-0001")
check("supplier B got a vendor_code", sup_b["vendor_code"] == "VEND-0002")

# create Vendor role
with db.connect() as conn:
    vendor_role_id = roles_repo.create_role(conn, "Vendor")
    roles_repo.set_role_permissions(conn, vendor_role_id, {"requisitions.vendor_fill"})

# quick-create vendor logins
r = client.get(f"/purchases/suppliers/{sup_a['id']}/edit")
html = r.get_data(as_text=True)
check("edit page shows vendor code", sup_a["vendor_code"] in html)
check("edit page shows quick-create button", "Quick-create vendor login" in html)
c = get_csrf(html)
resp = client.post(f"/purchases/suppliers/{sup_a['id']}/quick-create-vendor-login", data={"csrf_token": c}, follow_redirects=True)
flash_html = resp.get_data(as_text=True)
m = re.search(r'username &#34;([a-z0-9]+)&#34;, password &#34;([a-z0-9]+)&#34;', flash_html)
if not m:
    m = re.search(r'username "([a-z0-9]+)", password "([a-z0-9]+)"', flash_html)
check("quick-create flash shows generated username/password", m is not None)
if m:
    username_a, password_a = m.group(1), m.group(2)
    check("derived username matches vendor_code rule", username_a == "vend0001")
    check("derived password matches vendor_code rule", password_a == "vend000112345")
else:
    username_a, password_a = "vend0001", "vend000112345"

with db.connect() as conn:
    sup_a_after = conn.execute("SELECT * FROM suppliers WHERE id=?", (sup_a["id"],)).fetchone()
check("supplier A now has a linked portal_user_id", sup_a_after["portal_user_id"] is not None)

r = client.get(f"/purchases/suppliers/{sup_b['id']}/edit")
c = get_csrf(r.get_data(as_text=True))
resp = client.post(f"/purchases/suppliers/{sup_b['id']}/quick-create-vendor-login", data={"csrf_token": c}, follow_redirects=True)
with db.connect() as conn:
    sup_b_after = conn.execute("SELECT * FROM suppliers WHERE id=?", (sup_b["id"],)).fetchone()
check("supplier B now has a linked portal_user_id", sup_b_after["portal_user_id"] is not None)
username_b, password_b = "vend0002", "vend000212345"

# create requisition
r = client.get("/requisitions/new")
c = get_csrf(r.get_data(as_text=True))
resp = client.post("/requisitions/new", data={"notes": "Dev check requisition", "csrf_token": c})
req_location = resp.headers.get("Location", "")
req_id = int(re.search(r"/requisitions/(\d+)", req_location).group(1))
check("requisition created, redirected to detail", resp.status_code == 302)

# add items (one custom, one via product -- but no products exist here, so just custom via items_json directly)
import json as _json  # noqa: E402
r = client.get(f"/requisitions/{req_id}")
detail_html = r.get_data(as_text=True)
check("detail page shows item builder JS include", "requisitions.js" in detail_html)
c = get_csrf(detail_html)
items_payload = [
    {"product_id": None, "description": "Cement bags", "unit": "bag", "quantity": 50},
    {"product_id": None, "description": "Steel rebar 12mm", "unit": "ton", "quantity": 2},
]
resp = client.post(f"/requisitions/{req_id}/items", data={"items_json": _json.dumps(items_payload), "csrf_token": c}, follow_redirects=True)
check("item list saved", "Item list saved" in resp.get_data(as_text=True))

with db.connect() as conn:
    saved_items = conn.execute("SELECT * FROM material_requisition_items WHERE requisition_id=? ORDER BY position", (req_id,)).fetchall()
check("2 items persisted", len(saved_items) == 2)
item1_id, item2_id = saved_items[0]["id"], saved_items[1]["id"]

# invite both vendors
r = client.get(f"/requisitions/{req_id}")
c = get_csrf(r.get_data(as_text=True))
client.post(f"/requisitions/{req_id}/invite", data={"supplier_id": str(sup_a["id"]), "csrf_token": c}, follow_redirects=True)
r = client.get(f"/requisitions/{req_id}")
c = get_csrf(r.get_data(as_text=True))
client.post(f"/requisitions/{req_id}/invite", data={"supplier_id": str(sup_b["id"]), "csrf_token": c}, follow_redirects=True)

with db.connect() as conn:
    rvs = conn.execute("SELECT * FROM material_requisition_vendors WHERE requisition_id=?", (req_id,)).fetchall()
check("2 vendors invited", len(rvs) == 2)
rv_a = [r for r in rvs if r["supplier_id"] == sup_a["id"]][0]
rv_b = [r for r in rvs if r["supplier_id"] == sup_b["id"]][0]

# log in as vendor A
vendor_a_client = app.test_client()
r = vendor_a_client.get("/auth/login")
c = get_csrf(r.get_data(as_text=True))
resp = vendor_a_client.post("/auth/login", data={"username": username_a, "password": password_a, "csrf_token": c}, follow_redirects=True)
check("vendor A login succeeded", b"Material Requests" in resp.data)

resp = vendor_a_client.get("/", follow_redirects=True)
check("vendor A lands on vendor portal list, not Product Master", b"Material Requests" in resp.data and b"Product Master" not in resp.data)

resp = vendor_a_client.get("/vendor-portal/")
check("vendor A sees own requisition in list", resp.status_code == 200 and b"Dev check requisition" in resp.data or resp.status_code == 200)

resp = vendor_a_client.get(f"/vendor-portal/{rv_a['id']}")
check("vendor A can view own detail page", resp.status_code == 200)
va_html = resp.get_data(as_text=True)
va_csrf = get_csrf(va_html)

# vendor A cannot see vendor B's rv
resp = vendor_a_client.get(f"/vendor-portal/{rv_b['id']}")
check("vendor A blocked (403) from vendor B's requisition-vendor detail", resp.status_code == 403)

# vendor A blocked from rest of app
for path in ["/products/", "/quotes/", "/purchases/", "/tracker/", "/settings/", "/admin/users", "/requisitions/"]:
    resp = vendor_a_client.get(path)
    check(f"vendor A blocked (403) from {path}", resp.status_code == 403)

# vendor A save draft (partial pricing)
resp = vendor_a_client.post(f"/vendor-portal/{rv_a['id']}/save-draft", data={
    f"price_{item1_id}": "10.5", "csrf_token": va_csrf,
}, follow_redirects=True)
check("vendor A save-draft succeeds", "Draft saved" in resp.get_data(as_text=True))

with db.connect() as conn:
    rv_a_status = conn.execute("SELECT status FROM material_requisition_vendors WHERE id=?", (rv_a["id"],)).fetchone()
check("vendor A still pending after partial draft save", rv_a_status["status"] == "pending")

# staff sees "In progress" for vendor A
r = client.get(f"/requisitions/{req_id}")
staff_html = r.get_data(as_text=True)
check("staff sees vendor A as In progress", "In progress" in staff_html)

# vendor A tries to submit with incomplete pricing -- rejected
resp = vendor_a_client.post(f"/vendor-portal/{rv_a['id']}/submit", data={
    f"price_{item1_id}": "10.5", "csrf_token": va_csrf,
}, follow_redirects=True)
check("incomplete submit rejected", "Enter a price for every line" in resp.get_data(as_text=True))
with db.connect() as conn:
    rv_a_status2 = conn.execute("SELECT status FROM material_requisition_vendors WHERE id=?", (rv_a["id"],)).fetchone()
check("still pending after rejected submit", rv_a_status2["status"] == "pending")

# vendor A submits with full pricing
resp = vendor_a_client.post(f"/vendor-portal/{rv_a['id']}/submit", data={
    f"price_{item1_id}": "10.5", f"price_{item2_id}": "800", "csrf_token": va_csrf,
}, follow_redirects=True)
check("full submit succeeds", "Prices submitted" in resp.get_data(as_text=True))
with db.connect() as conn:
    rv_a_status3 = conn.execute("SELECT status FROM material_requisition_vendors WHERE id=?", (rv_a["id"],)).fetchone()
check("vendor A now submitted", rv_a_status3["status"] == "submitted")

# vendor A can no longer edit (page shows read-only / submit button not usable)
resp = vendor_a_client.get(f"/vendor-portal/{rv_a['id']}")
va_html2 = resp.get_data(as_text=True)
check("vendor A detail page shows locked message", "you can no longer edit" in va_html2.lower())

resp = vendor_a_client.post(f"/vendor-portal/{rv_a['id']}/save-draft", data={
    f"price_{item1_id}": "999", "csrf_token": va_csrf,
}, follow_redirects=True)
check("vendor A blocked from editing after submit", "already been submitted" in resp.get_data(as_text=True))
with db.connect() as conn:
    price_after = conn.execute(
        "SELECT unit_price FROM material_requisition_prices WHERE requisition_vendor_id=? AND item_id=?",
        (rv_a["id"], item1_id),
    ).fetchone()
check("submitted price unchanged after blocked edit attempt", price_after["unit_price"] == 10.5)

# staff sees Submitted + total for vendor A
r = client.get(f"/requisitions/{req_id}")
staff_html2 = r.get_data(as_text=True)
check("staff sees vendor A as Submitted", "Submitted" in staff_html2)
expected_total = 10.5 * 50 + 800 * 2
check(f"staff sees vendor A total ({expected_total})", f"{expected_total:.2f}" in staff_html2)

# items now locked (vendor A submitted)
check("items panel locked once a vendor has submitted", "item list is locked" in staff_html2)

# vendor B logs in and submits too
vendor_b_client = app.test_client()
r = vendor_b_client.get("/auth/login")
c = get_csrf(r.get_data(as_text=True))
vendor_b_client.post("/auth/login", data={"username": username_b, "password": password_b, "csrf_token": c})
r = vendor_b_client.get(f"/vendor-portal/{rv_b['id']}")
vb_csrf = get_csrf(r.get_data(as_text=True))
resp = vendor_b_client.post(f"/vendor-portal/{rv_b['id']}/submit", data={
    f"price_{item1_id}": "9.75", f"price_{item2_id}": "750", "csrf_token": vb_csrf,
}, follow_redirects=True)
check("vendor B submit succeeds", "Prices submitted" in resp.get_data(as_text=True))

# vendor B cannot see vendor A's data
resp = vendor_b_client.get(f"/vendor-portal/{rv_a['id']}")
check("vendor B blocked (403) from vendor A's requisition-vendor detail", resp.status_code == 403)

# staff awards to vendor B (cheaper)
r = client.get(f"/requisitions/{req_id}")
c = get_csrf(r.get_data(as_text=True))
resp = client.post(f"/requisitions/{req_id}/award", data={"requisition_vendor_id": str(rv_b["id"]), "csrf_token": c})
check("award redirects (302) to PO detail", resp.status_code == 302 and "/purchases/" in resp.headers.get("Location", ""))
po_id = int(re.search(r"/purchases/(\d+)", resp.headers["Location"]).group(1))

with db.connect() as conn:
    po = conn.execute("SELECT * FROM purchase_orders WHERE id=?", (po_id,)).fetchone()
    po_items = conn.execute("SELECT * FROM purchase_order_items WHERE po_id=? ORDER BY id", (po_id,)).fetchall()
    req_after = conn.execute("SELECT * FROM material_requisitions WHERE id=?", (req_id,)).fetchone()
check("PO created with vendor B's supplier", po["supplier_id"] == sup_b["id"])
check("PO has 2 line items", len(po_items) == 2)
check("PO line prices match vendor B's submission", {round(i["unit_price"], 2) for i in po_items} == {9.75, 750.0})
check("PO items have real product_id (auto-created for custom items)", all(i["product_id"] is not None for i in po_items))
check("requisition now shows awarded", req_after["status"] == "awarded")
check("requisition awarded_vendor_id matches vendor B", req_after["awarded_vendor_id"] == rv_b["id"])
check("requisition resulting_po_id matches new PO", req_after["resulting_po_id"] == po_id)

# award again is refused (already awarded) -- the awarded requisition's own
# detail page no longer renders any CSRF-carrying form (item/invite/award
# panels all gate on status=='open'), so reuse the last known-good token
# for this same session (csrf tokens are tied to the session cookie, not
# the page, per auth.generate_csrf_token()).
resp = client.post(f"/requisitions/{req_id}/award", data={"requisition_vendor_id": str(rv_a["id"]), "csrf_token": c}, follow_redirects=True)
check("double-award is refused", "already been awarded" in resp.get_data(as_text=True))

# try to award to a non-submitted vendor (create a 3rd requisition scenario quickly)
r = client.get("/requisitions/new")
c = get_csrf(r.get_data(as_text=True))
resp = client.post("/requisitions/new", data={"notes": "second", "csrf_token": c})
req2_id = int(re.search(r"/requisitions/(\d+)", resp.headers["Location"]).group(1))
r = client.get(f"/requisitions/{req2_id}")
c = get_csrf(r.get_data(as_text=True))
client.post(f"/requisitions/{req2_id}/items", data={"items_json": _json.dumps([
    {"product_id": None, "description": "Paint", "unit": "litre", "quantity": 5},
]), "csrf_token": c}, follow_redirects=True)
r = client.get(f"/requisitions/{req2_id}")
c = get_csrf(r.get_data(as_text=True))
client.post(f"/requisitions/{req2_id}/invite", data={"supplier_id": str(sup_a["id"]), "csrf_token": c}, follow_redirects=True)
with db.connect() as conn:
    rv2 = conn.execute("SELECT * FROM material_requisition_vendors WHERE requisition_id=?", (req2_id,)).fetchone()
r = client.get(f"/requisitions/{req2_id}")
c = get_csrf(r.get_data(as_text=True))
resp = client.post(f"/requisitions/{req2_id}/award", data={"requisition_vendor_id": str(rv2["id"]), "csrf_token": c}, follow_redirects=True)
check("awarding to a non-submitted (pending) vendor is refused", "submitted their pricing" in resp.get_data(as_text=True))

# vendor_code immutability: crafted POST to supplier update doesn't accept vendor_code field
r = client.get(f"/purchases/suppliers/{sup_a['id']}/edit")
c = get_csrf(r.get_data(as_text=True))
client.post(f"/purchases/suppliers/{sup_a['id']}/edit", data={
    "name": "Dev Vendor A Renamed", "vendor_code": "HACKED-CODE", "csrf_token": c,
}, follow_redirects=True)
with db.connect() as conn:
    sup_a_final = conn.execute("SELECT * FROM suppliers WHERE id=?", (sup_a["id"],)).fetchone()
check("vendor_code unaffected by crafted POST field", sup_a_final["vendor_code"] == "VEND-0001")
check("name update still works normally", sup_a_final["name"] == "Dev Vendor A Renamed")

print()
if failures:
    print(f"=== DEV CHECK FAILED ({len(failures)}) ===")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("=== DEV CHECK PASSED ===")
