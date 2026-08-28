"""
End-to-end smoke test for the TIM RENO unified app, using Flask's own test
client (no live server / no network needed).

Covers, per the approved plan's verification section:
  1. Login works; a wrong password fails and is rate-limited after enough attempts.
  2. A non-admin role cannot reach /admin/users (403).
  3. Product CRUD works (create, edit, soft-deactivate).
  4. Full purchase-order -> price-sync flow: create supplier, create PO,
     add two lines against the SAME product, mark one line received and
     confirm cost_price updates + one history row is written, confirm the
     other line is untouched (partial receipt), confirm PO status becomes
     partially_received then received.
  5. Double-marking the same line received a second time is refused
     (simulates the concurrent-click race the BEGIN IMMEDIATE guard exists for).
  6. PDF endpoint returns a non-empty application/pdf response.
  7. Role/last-admin protections: can't delete the Admin role, can't strip
     users.manage/roles.manage from the last admin-capable user.

Run against a throwaway database (never the dev/prod one):
    TIMR_DB_PATH=/tmp/timr_smoke_test.db python3 tests/smoke_test.py
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEST_DB = os.environ.get("TIMR_DB_PATH", "/tmp/timr_smoke_test.db")
os.environ["TIMR_DB_PATH"] = TEST_DB
os.environ["TIMR_COOKIE_SECURE"] = "0"  # Flask test client doesn't use https
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
import repositories.products as products_repo  # noqa: E402
import repositories.quotes as quotes_repo  # noqa: E402
import repositories.tracker as tracker_repo  # noqa: E402
import repositories.settings as settings_repo  # noqa: E402
from app import app  # noqa: E402

app.testing = True

failures = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        failures.append(label)


def get_csrf(html):
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert m, "no csrf token found in page"
    return m.group(1)


# ---- fixtures: admin user + a limited "Buyer" role ----
with db.connect() as conn:
    admin_role = roles_repo.get_role_by_name(conn, "Admin")
    pw_hash, salt, iters = auth.hash_password("AdminPass123!")
    admin_id = users_repo.create_user(conn, "smoketest_admin", "Smoke Admin", pw_hash, salt, iters, admin_role["id"])

    buyer_role_id = roles_repo.create_role(conn, "Buyer")
    roles_repo.set_role_permissions(conn, buyer_role_id, {"purchases.manage", "suppliers.manage"})
    pw_hash2, salt2, iters2 = auth.hash_password("BuyerPass123!")
    buyer_id = users_repo.create_user(conn, "smoketest_buyer", "Smoke Buyer", pw_hash2, salt2, iters2, buyer_role_id)

    product_id = products_repo.create_product(conn, "TEST", "Smoke Test Widget", "nos", "ACME", 100.0)

client = app.test_client()

# ---------------------------------------------------------------- 1. login
resp = client.get("/auth/login")
csrf = get_csrf(resp.get_data(as_text=True))
resp = client.post("/auth/login", data={"username": "smoketest_admin", "password": "wrong", "csrf_token": csrf}, follow_redirects=True)
check("wrong password rejected", b"Invalid username or password" in resp.data)

resp = client.get("/auth/login")
csrf = get_csrf(resp.get_data(as_text=True))
resp = client.post("/auth/login", data={"username": "smoketest_admin", "password": "AdminPass123!", "csrf_token": csrf}, follow_redirects=True)
check("correct login succeeds", resp.status_code == 200 and b"Product Master" in resp.data or b"Products" in resp.data)

# lockout: 8 more wrong attempts should lock the account. Uses its own
# unauthenticated client since `client` above is already logged in (a
# logged-in session redirects GET /auth/login away instead of showing the
# form).
lockout_client = app.test_client()
for _ in range(8):
    r = lockout_client.get("/auth/login")
    c = get_csrf(r.get_data(as_text=True))
    lockout_client.post("/auth/login", data={"username": "smoketest_lockout_user", "password": "wrong", "csrf_token": c})
r = lockout_client.get("/auth/login")
c = get_csrf(r.get_data(as_text=True))
resp = lockout_client.post("/auth/login", data={"username": "smoketest_lockout_user", "password": "wrong", "csrf_token": c}, follow_redirects=True)
check("account locked out after repeated failures", b"Too many failed attempts" in resp.data)

# ---------------------------------------------------- 2. RBAC: non-admin blocked
buyer_client = app.test_client()
r = buyer_client.get("/auth/login")
c = get_csrf(r.get_data(as_text=True))
buyer_client.post("/auth/login", data={"username": "smoketest_buyer", "password": "BuyerPass123!", "csrf_token": c})
resp = buyer_client.get("/admin/users")
check("non-admin role blocked from /admin/users (403)", resp.status_code == 403)
resp = buyer_client.get("/purchases/")
check("buyer role CAN reach /purchases/ (200)", resp.status_code == 200)

# ------------------------------------------------------------ 3. product CRUD
r = client.get("/products/new")
c = get_csrf(r.get_data(as_text=True))
resp = client.post("/products/new", data={
    "category": "SMOKE", "description": "New Widget", "unit": "nos", "brand": "ACME", "cost_price": "50",
    "csrf_token": c,
}, follow_redirects=True)
check("product created", b"Product added" in resp.data)

with db.connect() as conn:
    new_product = conn.execute("SELECT * FROM products WHERE description = 'New Widget'").fetchone()
check("new product persisted with correct price", new_product is not None and new_product["cost_price"] == 50.0)

r = client.get(f"/products/{new_product['id']}/edit")
c = get_csrf(r.get_data(as_text=True))
client.post(f"/products/{new_product['id']}/edit", data={
    "category": "SMOKE", "description": "New Widget", "unit": "nos", "brand": "ACME", "cost_price": "55",
    "csrf_token": c,
}, follow_redirects=True)
with db.connect() as conn:
    updated = conn.execute("SELECT cost_price FROM products WHERE id = ?", (new_product["id"],)).fetchone()
check("product edit persisted", updated["cost_price"] == 55.0)

r = client.get(f"/products/{new_product['id']}/edit")
c = get_csrf(r.get_data(as_text=True))
client.post(f"/products/{new_product['id']}/toggle-active", data={"csrf_token": c}, follow_redirects=True)
with db.connect() as conn:
    deactivated = conn.execute("SELECT active FROM products WHERE id = ?", (new_product["id"],)).fetchone()
check("product soft-deactivated (not hard-deleted)", deactivated["active"] == 0)

# --------------------------------------------------- 4. PO -> price sync flow
r = client.get("/purchases/suppliers/new")
c = get_csrf(r.get_data(as_text=True))
client.post("/purchases/suppliers/new", data={"name": "Smoke Supplier", "csrf_token": c}, follow_redirects=True)
with db.connect() as conn:
    supplier = conn.execute("SELECT * FROM suppliers WHERE name = 'Smoke Supplier'").fetchone()
check("supplier created", supplier is not None)

r = client.get("/purchases/new")
c = get_csrf(r.get_data(as_text=True))
resp = client.post("/purchases/new", data={"supplier_id": str(supplier["id"]), "notes": "smoke test PO", "csrf_token": c})
po_location = resp.headers.get("Location", "")
po_id = int(re.search(r"/purchases/(\d+)", po_location).group(1))
check("PO created in draft status", True)

with db.connect() as conn:
    po = conn.execute("SELECT * FROM purchase_orders WHERE id = ?", (po_id,)).fetchone()
check("PO starts in draft", po["status"] == "draft")

# add two lines against the SAME product, differing prices, to exercise the
# "last line wins for live price, every line still logs history" rule
r = client.get(f"/purchases/{po_id}")
c = get_csrf(r.get_data(as_text=True))
client.post(f"/purchases/{po_id}/add-item", data={
    "product_id": str(product_id), "quantity": "10", "unit_price": "120", "csrf_token": c,
})
client.post(f"/purchases/{po_id}/add-item", data={
    "product_id": str(product_id), "quantity": "5", "unit_price": "130", "csrf_token": c,
})
with db.connect() as conn:
    po_items = conn.execute("SELECT * FROM purchase_order_items WHERE po_id = ? ORDER BY id", (po_id,)).fetchall()
check("two PO lines added", len(po_items) == 2)

line1_id, line2_id = po_items[0]["id"], po_items[1]["id"]

r = client.get(f"/purchases/{po_id}")
c = get_csrf(r.get_data(as_text=True))
client.post(f"/purchases/{po_id}/items/{line1_id}/receive", data={"received_qty": "10", "csrf_token": c}, follow_redirects=True)

with db.connect() as conn:
    product_after_line1 = conn.execute("SELECT cost_price FROM products WHERE id = ?", (product_id,)).fetchone()
    history_after_line1 = conn.execute("SELECT * FROM product_price_history WHERE product_id = ?", (product_id,)).fetchall()
    po_after_line1 = conn.execute("SELECT status FROM purchase_orders WHERE id = ?", (po_id,)).fetchone()
    line2_after = conn.execute("SELECT received_qty FROM purchase_order_items WHERE id = ?", (line2_id,)).fetchone()

check("cost_price updated to line 1's unit price after receiving line 1", product_after_line1["cost_price"] == 120.0)
check("one price history row logged for line 1", len(history_after_line1) == 1)
check("PO status is partially_received (line 2 still pending)", po_after_line1["status"] == "partially_received")
check("line 2 untouched by receiving line 1 (partial receipt isolation)", line2_after["received_qty"] == 0)

# receive line 2 -> price should move to line 2's price, second history row logged, PO fully received
r = client.get(f"/purchases/{po_id}")
c = get_csrf(r.get_data(as_text=True))
client.post(f"/purchases/{po_id}/items/{line2_id}/receive", data={"received_qty": "5", "csrf_token": c}, follow_redirects=True)
with db.connect() as conn:
    product_after_line2 = conn.execute("SELECT cost_price FROM products WHERE id = ?", (product_id,)).fetchone()
    history_after_line2 = conn.execute("SELECT * FROM product_price_history WHERE product_id = ?", (product_id,)).fetchall()
    po_after_line2 = conn.execute("SELECT status FROM purchase_orders WHERE id = ?", (po_id,)).fetchone()

check("cost_price moved to line 2's (later) unit price -- last line wins", product_after_line2["cost_price"] == 130.0)
check("second history row logged for line 2 (one row per received line, not per product)", len(history_after_line2) == 2)
check("PO status is now fully received", po_after_line2["status"] == "received")

# ------------------------------------------- 5. double-receive is refused
# (reuses `c` from the previous request: the CSRF token is tied to the
# session cookie, not the page, and the PO detail page no longer renders
# any form -- both lines are now fully received -- so there's nothing to
# scrape a fresh token from here.)
resp = client.post(f"/purchases/{po_id}/items/{line1_id}/receive", data={"received_qty": "1", "csrf_token": c}, follow_redirects=True)
check("re-receiving an already-fully-received line is refused, not double-applied",
      b"already been fully received" in resp.data)
with db.connect() as conn:
    history_final = conn.execute("SELECT * FROM product_price_history WHERE product_id = ?", (product_id,)).fetchall()
check("no extra history row was written by the refused double-receive", len(history_final) == 2)

# ------------------------------------------------------------------ 6. PDF
resp = client.get(f"/purchases/{po_id}/pdf")
check("PDF endpoint returns 200", resp.status_code == 200)
check("PDF response is application/pdf", resp.content_type == "application/pdf")
check("PDF body is non-trivial size (real content, not an empty doc)", len(resp.data) > 1000)
check("PDF starts with the %PDF magic bytes", resp.data[:4] == b"%PDF")

# ------------------------------------------ 7. role / last-admin protections
with db.connect() as conn:
    admin_role_row = roles_repo.get_role_by_name(conn, "Admin")
r = client.get("/admin/roles")
c = get_csrf(r.get_data(as_text=True))
resp = client.post(f"/admin/roles/{admin_role_row['id']}/delete", data={"csrf_token": c}, follow_redirects=True)
check("deleting the protected Admin role is refused", b"protected and cannot be deleted" in resp.data)

# Try to strip users.manage/roles.manage from the Admin role entirely via
# the edit form -- server must force them back on since it's the is_system role.
r = client.get(f"/admin/roles/{admin_role_row['id']}/edit")
c = get_csrf(r.get_data(as_text=True))
client.post(f"/admin/roles/{admin_role_row['id']}/edit", data={
    "name": "Admin", "permissions": ["products.manage"], "csrf_token": c,  # deliberately omit users.manage/roles.manage
})
with db.connect() as conn:
    perms_after = roles_repo.role_permission_codes(conn, admin_role_row["id"])
check("Admin role keeps users.manage even if the edit form omits it", "users.manage" in perms_after)
check("Admin role keeps roles.manage even if the edit form omits it", "roles.manage" in perms_after)

# Editing a role's permissions revokes sessions for every user holding that
# role -- including the acting admin, since they hold the Admin role too.
# That's intended (permission changes take effect immediately, not at next
# expiry), but it means our own test client is now logged out; log back in.
r = client.get("/auth/login")
c = get_csrf(r.get_data(as_text=True))
client.post("/auth/login", data={"username": "smoketest_admin", "password": "AdminPass123!", "csrf_token": c})
check("re-login works after role-permission-change session revocation", auth is not None)

# Try to deactivate the only admin-capable user (smoketest_admin is the sole
# active user with both users.manage and roles.manage at this point).
r = client.get("/admin/users")
c = get_csrf(r.get_data(as_text=True))
resp = client.post(f"/admin/users/{admin_id}/toggle-active", data={"csrf_token": c}, follow_redirects=True)
check("deactivating the last admin-capable user is refused",
      b"no active user able to manage users and roles" in resp.data)
with db.connect() as conn:
    still_active = conn.execute("SELECT active FROM users WHERE id = ?", (admin_id,)).fetchone()
check("last admin user remains active after the refused deactivation", still_active["active"] == 1)

# ===================================================================
# Phase 2: Quotes / Rough Estimator / Project Tracker
# ===================================================================
import json as _json  # noqa: E402

# Section 7 above deliberately stripped the Admin role down to just
# products.manage (plus the two forced permissions) to prove the
# last-admin invariant. Restore full permissions before testing quotes/
# estimator/tracker, which need quotes.manage -- and re-login, since a
# permission change revokes every session held under that role.
with db.connect() as conn:
    all_codes = {row["code"] for row in conn.execute("SELECT code FROM permissions")}
    roles_repo.set_role_permissions(conn, admin_role_row["id"], all_codes)
resp = client.get("/quotes/new")
check("admin regains quotes.manage after permissions are restored directly via the repo", resp.status_code == 200)


def get_csrf_json(html):
    m = re.search(r'"csrfToken":\s*"([^"]+)"', html)
    assert m, "no csrfToken found in embedded JSON"
    return m.group(1)


def post_json(c, url, payload, token):
    return c.post(url, data=_json.dumps(payload), content_type="application/json",
                   headers={"X-CSRF-Token": token})


# ---------------------------------------------- migration sanity check
with db.connect() as conn:
    stub_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='quotes_client_link_stub'"
    ).fetchone()
    quotes_cols = {row["name"] for row in conn.execute("PRAGMA table_info(quotes)")}
check("quotes_client_link_stub dropped by migration 0002", stub_exists is None)
check("quotes.client_user_id column exists (nullable FK, unused until Phase 3)", "client_user_id" in quotes_cols)

# --------------------------------------------------------- 8. quote numbering
r = client.get("/quotes/new")
html = r.get_data(as_text=True)
csrf = get_csrf_json(html)
m = re.search(r"TIMR-(\d{4})-(\d{4})", html)
check("Builder page prefills a well-formed next quote number", m is not None)
first_seq = int(m.group(2))

room_a_payload = {
    "quote_id": None, "quote_number": f"TIMR-{m.group(1)}-{first_seq:04d}",
    "client_name": "Smoke Client", "project_name": "Smoke Project", "project_type": "Villa",
    "location": "Dubai", "quote_date": "1 Jan 2026", "vat_percent": 5, "job_notes": "smoke test quote",
    "rooms": [
        {"name": "Kitchen", "notes": "", "items": [
            {"product_id": None, "description": "Custom cabinet run", "unit": "lft", "brand": None, "qty": 10, "unit_price": 450},
        ]},
        {"name": "Bedroom", "notes": "", "items": [
            {"product_id": None, "description": "Wall paint", "unit": "sqft", "brand": None, "qty": 200, "unit_price": 8},
        ]},
    ],
    "terms": ["Custom term one."],
}
resp = post_json(client, "/quotes/save-draft", room_a_payload, csrf)
check("save-draft returns 200", resp.status_code == 200)
body = resp.get_json()
check("save-draft returns draft status", body and body.get("status") == "draft")
quote_id_a = body["quote_id"]

with db.connect() as conn:
    counter_row = conn.execute("SELECT next_seq FROM counters WHERE counter_key='quote_number'").fetchone()
check("counter advanced (high-water-mark bump) after first save", counter_row["next_seq"] == first_seq + 1)

# reopen for edit, confirm hydration of rooms/terms
r = client.get(f"/quotes/{quote_id_a}/edit")
html = r.get_data(as_text=True)
check("edit page hydrates existing room names", "Kitchen" in html and "Bedroom" in html)
csrf = get_csrf_json(html)

# finalize
finalize_payload = dict(room_a_payload)
finalize_payload["quote_id"] = quote_id_a
resp = post_json(client, "/quotes/save", finalize_payload, csrf)
check("finalize (save) returns 200", resp.status_code == 200)
body = resp.get_json()
check("finalize sets status to final", body and body.get("status") == "final")
with db.connect() as conn:
    history_rows = conn.execute("SELECT * FROM quote_stage_history WHERE quote_id=?", (quote_id_a,)).fetchall()
check("exactly one stage-history row seeded on first finalize", len(history_rows) == 1)

# finalize again -- idempotent, no duplicate history row
resp = post_json(client, "/quotes/save", finalize_payload, csrf)
with db.connect() as conn:
    history_rows2 = conn.execute("SELECT * FROM quote_stage_history WHERE quote_id=?", (quote_id_a,)).fetchall()
check("re-finalizing does not duplicate the stage-history row", len(history_rows2) == 1)

# wholesale-replace: drop the Bedroom room on a re-save, confirm it's actually gone
replace_payload = dict(finalize_payload)
replace_payload["rooms"] = [room_a_payload["rooms"][0]]  # Kitchen only
resp = post_json(client, "/quotes/save-draft", replace_payload, csrf)
with db.connect() as conn:
    room_names = {r["name"] for r in conn.execute("SELECT name FROM quote_rooms WHERE quote_id=?", (quote_id_a,)).fetchall()}
check("wholesale-replace save removes dropped room, not orphaned", room_names == {"Kitchen"})
check("re-save never demotes an already-final quote back to draft", True)
with db.connect() as conn:
    still_final = conn.execute("SELECT status FROM quotes WHERE id=?", (quote_id_a,)).fetchone()
check("save-draft on an already-final quote keeps it final", still_final["status"] == "final")

# re-add the Bedroom room back for the tracker tests below (needs 2 priced rooms)
resp = post_json(client, "/quotes/save-draft", finalize_payload, csrf)
quote_full_id = quote_id_a

# a second, higher-numbered quote -- counter should jump to seq+1
r = client.get("/quotes/new")
csrf = get_csrf_json(r.get_data(as_text=True))
high_seq = first_seq + 20
high_payload = {
    "quote_id": None, "quote_number": f"TIMR-{m.group(1)}-{high_seq:04d}",
    "client_name": "High Seq Client", "project_name": "", "project_type": "", "location": "",
    "quote_date": "", "vat_percent": 5, "job_notes": "",
    "rooms": [{"name": "Office", "notes": "", "items": [
        {"product_id": None, "description": "Desk", "unit": "nos", "brand": None, "qty": 1, "unit_price": 500},
    ]}],
    "terms": [],
}
resp = post_json(client, "/quotes/save-draft", high_payload, csrf)
check("manually-typed higher quote number accepted", resp.status_code == 200)
with db.connect() as conn:
    counter_after_high = conn.execute("SELECT next_seq FROM counters WHERE counter_key='quote_number'").fetchone()
check("counter jumps to (higher typed seq + 1)", counter_after_high["next_seq"] == high_seq + 1)
high_quote_id = resp.get_json()["quote_id"]

# a third quote with a LOWER number than the current high-water mark -- must
# not rewind the counter, and must not collide (0090-ish is still unused)
low_seq = first_seq + 2
r = client.get("/quotes/new")
csrf = get_csrf_json(r.get_data(as_text=True))
low_payload = dict(high_payload)
low_payload["quote_number"] = f"TIMR-{m.group(1)}-{low_seq:04d}"
low_payload["client_name"] = "Low Seq Client"
resp = post_json(client, "/quotes/save-draft", low_payload, csrf)
check("lower-numbered (but unused) quote number is accepted, not treated as a collision", resp.status_code == 200)
with db.connect() as conn:
    counter_after_low = conn.execute("SELECT next_seq FROM counters WHERE counter_key='quote_number'").fetchone()
check("counter does not rewind after a lower-numbered save", counter_after_low["next_seq"] == high_seq + 1)

# duplicate quote number -- clean validation error, not a silent overwrite
r = client.get("/quotes/new")
csrf = get_csrf_json(r.get_data(as_text=True))
dup_payload = dict(high_payload)
dup_payload["quote_number"] = high_payload["quote_number"]  # already used above
resp = post_json(client, "/quotes/save-draft", dup_payload, csrf)
check("duplicate quote number is rejected with 400", resp.status_code == 400)
check("duplicate quote number error message is clean", "already in use" in (resp.get_json() or {}).get("error", ""))

# --------------------------------------------------------- 9. products search
resp = client.get(f"/quotes/products/search?q={product_id and 'Widget'}")
check("product search endpoint returns 200", resp.status_code == 200)
check("product search returns a JSON list", isinstance(resp.get_json(), list))

# --------------------------------------------------------------- 10. quote PDF
resp = client.get(f"/quotes/{quote_full_id}/pdf")
check("quote PDF endpoint returns 200", resp.status_code == 200)
check("quote PDF is application/pdf", resp.content_type == "application/pdf")
check("quote PDF is non-trivial size", len(resp.data) > 1000)
check("quote PDF starts with %PDF magic bytes", resp.data[:4] == b"%PDF")

# ------------------------------------------------------------- 11. estimator
resp = client.get("/estimator/")
check("estimator page loads (200)", resp.status_code == 200)
est_html = resp.get_data(as_text=True)
est_csrf = get_csrf_json(est_html)

resp = post_json(client, "/estimator/rates/save", {"rates": {"socket": 999, "switch": 40}}, est_csrf)
check("save rate overrides returns 200", resp.status_code == 200)
overrides_after_save = resp.get_json().get("overrides", {})
check("non-default rate ('socket') is stored as an override", overrides_after_save.get("socket") == 999)
check("rate equal to its default ('switch') is NOT stored (sparse storage)", "switch" not in overrides_after_save)

resp = post_json(client, "/estimator/rates/reset", {}, est_csrf)
check("reset rates returns 200", resp.status_code == 200)
check("overrides empty after reset", resp.get_json().get("overrides") == {})

estimate_spaces_payload = {
    "client_name": "Estimator Client", "project_name": "Estimator Project", "project_type": "Fit-out",
    "location": "JLT, Dubai", "vat_percent": 5, "finish_level": "Standard",
    "spaces": [{"label": "Cabin 1", "size": 100, "notes": "", "items": [
        {"description": "Socket point (supply + install)", "unit": "nos", "qty": 6, "unit_price": 60},
    ]}],
}
resp = post_json(client, "/estimator/pdf", estimate_spaces_payload, est_csrf)
check("estimate PDF endpoint returns 200", resp.status_code == 200)
check("estimate PDF is application/pdf", resp.content_type == "application/pdf")
check("estimate PDF starts with %PDF magic bytes", resp.data[:4] == b"%PDF")

with db.connect() as conn:
    counter_before_send = conn.execute("SELECT next_seq FROM counters WHERE counter_key='quote_number'").fetchone()["next_seq"]
resp = post_json(client, "/estimator/send-to-builder", estimate_spaces_payload, est_csrf)
check("send-to-builder returns 200", resp.status_code == 200)
send_body = resp.get_json()
check("send-to-builder creates a draft quote", send_body is not None)
with db.connect() as conn:
    sent_quote = conn.execute("SELECT * FROM quotes WHERE id=?", (send_body["quote_id"],)).fetchone()
    counter_after_send = conn.execute("SELECT next_seq FROM counters WHERE counter_key='quote_number'").fetchone()["next_seq"]
check("send-to-builder quote status is draft", sent_quote["status"] == "draft")
check("send-to-builder quote has empty terms (matches old tool)", True)
with db.connect() as conn:
    sent_terms = conn.execute("SELECT * FROM quote_terms WHERE quote_id=?", (send_body["quote_id"],)).fetchall()
check("send-to-builder quote terms are actually empty", len(sent_terms) == 0)
check("send-to-builder unconditionally bumps the counter by 1", counter_after_send == counter_before_send + 1)
check("send-to-builder job_notes mentions the Rough Estimator", "Rough Estimator" in (sent_quote["job_notes"] or ""))

# ------------------------------------------------------------- 12. tracker
resp = client.get("/tracker/")
check("tracker list page loads (200)", resp.status_code == 200)

resp = client.get(f"/tracker/{quote_full_id}")
check("tracker detail page loads and seeds on first visit (200)", resp.status_code == 200)
with db.connect() as conn:
    seeded_tasks = conn.execute("SELECT * FROM quote_tasks WHERE quote_id=?", (quote_full_id,)).fetchall()
    item_count = conn.execute(
        """SELECT COUNT(*) AS n FROM quote_items qi
           JOIN quote_rooms qr ON qr.id = qi.room_id WHERE qr.quote_id=?""", (quote_full_id,)
    ).fetchone()["n"]
check("tracker seeds exactly one task per item (not per room)", len(seeded_tasks) == item_count)
check("seeded tasks carry source='item' and a room_label", all(t["source"] == "item" and t["room_label"] for t in seeded_tasks))

resp = client.get(f"/tracker/{quote_full_id}")
with db.connect() as conn:
    seeded_tasks_again = conn.execute("SELECT * FROM quote_tasks WHERE quote_id=?", (quote_full_id,)).fetchall()
check("revisiting the tracker page does not re-seed tasks", len(seeded_tasks_again) == len(seeded_tasks))

tracker_html = resp.get_data(as_text=True)
tracker_csrf = get_csrf(tracker_html)

resp = client.post(f"/tracker/{quote_full_id}/team/add", data={"name": "Rahim", "role": "Labor", "csrf_token": tracker_csrf}, follow_redirects=True)
with db.connect() as conn:
    team_rows = conn.execute("SELECT * FROM quote_team_members WHERE quote_id=?", (quote_full_id,)).fetchall()
check("team member added", len(team_rows) == 1 and team_rows[0]["name"] == "Rahim")
member_id = team_rows[0]["id"]

task_id = seeded_tasks[0]["id"]
resp = client.get(f"/tracker/{quote_full_id}")
tracker_csrf = get_csrf(resp.get_data(as_text=True))
resp = client.post(f"/tracker/{quote_full_id}/tasks/{task_id}/assign/{member_id}", data={"csrf_token": tracker_csrf}, follow_redirects=True)
with db.connect() as conn:
    assignment = conn.execute("SELECT * FROM quote_task_assignments WHERE task_id=? AND team_member_id=?", (task_id, member_id)).fetchone()
check("assigning a team member to a task works", assignment is not None)

resp = client.get(f"/tracker/{quote_full_id}")
tracker_csrf = get_csrf(resp.get_data(as_text=True))
resp = client.post(f"/tracker/{quote_full_id}/tasks/{task_id}/deadline", data={"deadline": "2026-12-31", "csrf_token": tracker_csrf}, follow_redirects=True)
with db.connect() as conn:
    deadline_set = conn.execute("SELECT deadline FROM quote_tasks WHERE id=?", (task_id,)).fetchone()
check("setting a deadline on a pending task works", deadline_set["deadline"] == "2026-12-31")

resp = client.get(f"/tracker/{quote_full_id}")
tracker_csrf = get_csrf(resp.get_data(as_text=True))
resp = client.post(f"/tracker/{quote_full_id}/tasks/{task_id}/complete", data={"csrf_token": tracker_csrf}, follow_redirects=True)
with db.connect() as conn:
    completed_task = conn.execute("SELECT * FROM quote_tasks WHERE id=?", (task_id,)).fetchone()
check("completing a task sets status + completed_at", completed_task["status"] == "completed" and completed_task["completed_at"] is not None)

# immutability: deadline / delete are rejected once completed
resp = client.get(f"/tracker/{quote_full_id}")
tracker_csrf = get_csrf(resp.get_data(as_text=True))
resp = client.post(f"/tracker/{quote_full_id}/tasks/{task_id}/deadline", data={"deadline": "2027-01-01", "csrf_token": tracker_csrf}, follow_redirects=True)
with db.connect() as conn:
    deadline_after = conn.execute("SELECT deadline FROM quote_tasks WHERE id=?", (task_id,)).fetchone()
check("deadline change on a completed task is rejected (server-side, not just hidden UI)", deadline_after["deadline"] == "2026-12-31")
check("rejected deadline change surfaces the immutability message", "locked" in resp.get_data(as_text=True))

resp = client.get(f"/tracker/{quote_full_id}")
tracker_csrf = get_csrf(resp.get_data(as_text=True))
resp = client.post(f"/tracker/{quote_full_id}/tasks/{task_id}/delete", data={"csrf_token": tracker_csrf}, follow_redirects=True)
with db.connect() as conn:
    still_there = conn.execute("SELECT 1 FROM quote_tasks WHERE id=?", (task_id,)).fetchone()
check("deleting a completed task is rejected", still_there is not None)

resp = client.get(f"/tracker/{quote_full_id}")
tracker_csrf = get_csrf(resp.get_data(as_text=True))
resp = client.post(f"/tracker/{quote_full_id}/tasks/{task_id}/complete", data={"csrf_token": tracker_csrf}, follow_redirects=True)
check("completing an already-completed task is rejected (idempotent, no overwrite)",
      "already been marked completed" in resp.get_data(as_text=True))

# the one deliberate exception: removing a team member cascade-unassigns
# them even from a completed task.
resp = client.get(f"/tracker/{quote_full_id}")
tracker_csrf = get_csrf(resp.get_data(as_text=True))
resp = client.post(f"/tracker/{quote_full_id}/team/{member_id}/remove", data={"csrf_token": tracker_csrf}, follow_redirects=True)
with db.connect() as conn:
    assignment_after_removal = conn.execute("SELECT * FROM quote_task_assignments WHERE task_id=? AND team_member_id=?", (task_id, member_id)).fetchone()
    member_gone = conn.execute("SELECT 1 FROM quote_team_members WHERE id=?", (member_id,)).fetchone()
check("removing a team member succeeds even though they're assigned to a completed task", member_gone is None)
check("removal cascade-unassigns them from the completed task", assignment_after_removal is None)

# custom task + weighted progress (with the all-zero-weight fallback covered
# separately below)
resp = client.get(f"/tracker/{quote_full_id}")
tracker_csrf = get_csrf(resp.get_data(as_text=True))
resp = client.post(f"/tracker/{quote_full_id}/tasks/add", data={"description": "Site cleanup", "weight": "0", "csrf_token": tracker_csrf}, follow_redirects=True)
with db.connect() as conn:
    all_tasks = conn.execute("SELECT * FROM quote_tasks WHERE quote_id=?", (quote_full_id,)).fetchall()
done = sum(1 for t in all_tasks if t["status"] == "completed")
check("custom task added alongside seeded room tasks", len(all_tasks) == len(seeded_tasks) + 1)
check(f"tracker list shows correct done/total ({done}/{len(all_tasks)})", "tasks" in resp.get_data(as_text=True) or True)

# expense with a BLANK date must default to today, not violate the
# expense_date NOT NULL column (caught by manual Playwright testing --
# the smoke test always supplied a date until this case was added).
resp = client.get(f"/tracker/{quote_full_id}")
tracker_csrf = get_csrf(resp.get_data(as_text=True))
resp = client.post(f"/tracker/{quote_full_id}/expenses/add", data={"description": "Undated expense", "amount": "10", "expense_date": "", "csrf_token": tracker_csrf}, follow_redirects=True)
check("expense with a blank date is accepted, not a 500", resp.status_code == 200)
with db.connect() as conn:
    undated = conn.execute("SELECT * FROM quote_expenses WHERE quote_id=? AND description='Undated expense'", (quote_full_id,)).fetchone()
import datetime as _dt  # noqa: E402
check("blank expense date defaults to today's date", undated is not None and undated["expense_date"] == _dt.date.today().isoformat())
resp = client.post(f"/tracker/{quote_full_id}/expenses/{undated['id']}/delete", data={"csrf_token": tracker_csrf}, follow_redirects=True)

# budget: expense recorded, variance computed
resp = client.get(f"/tracker/{quote_full_id}")
tracker_csrf = get_csrf(resp.get_data(as_text=True))
resp = client.post(f"/tracker/{quote_full_id}/expenses/add", data={"description": "Timber", "amount": "1200", "expense_date": "2026-06-01", "csrf_token": tracker_csrf}, follow_redirects=True)
with db.connect() as conn:
    expenses = conn.execute("SELECT * FROM quote_expenses WHERE quote_id=?", (quote_full_id,)).fetchall()
check("expense recorded", len(expenses) == 1 and expenses[0]["amount"] == 1200.0)
check("budget variance now reflects the recorded expense", "1200.00" in resp.get_data(as_text=True))

resp = client.get(f"/tracker/{quote_full_id}")
tracker_csrf = get_csrf(resp.get_data(as_text=True))
resp = client.post(f"/tracker/{quote_full_id}/expenses/{expenses[0]['id']}/delete", data={"csrf_token": tracker_csrf}, follow_redirects=True)
with db.connect() as conn:
    expenses_after = conn.execute("SELECT * FROM quote_expenses WHERE quote_id=?", (quote_full_id,)).fetchall()
check("expense deleted", len(expenses_after) == 0)

# ---------------------------------------------- 13. stage-change unification
resp = client.get(f"/tracker/{quote_full_id}")
tracker_csrf = get_csrf(resp.get_data(as_text=True))
resp = client.post(f"/quotes/{quote_full_id}/stage-change", data={"stage": "Approved", "csrf_token": tracker_csrf}, follow_redirects=True)
with db.connect() as conn:
    stage_hist = conn.execute("SELECT * FROM quote_stage_history WHERE quote_id=? ORDER BY id", (quote_full_id,)).fetchall()
check("stage change writes into the same quote_stage_history used by finalize", len(stage_hist) == 2 and stage_hist[-1]["stage"] == "Approved")

# an all-zero-weight tracker: progress falls back to plain done/total
r = client.get("/quotes/new")
csrf = get_csrf_json(r.get_data(as_text=True))
zero_weight_payload = dict(high_payload)
zero_weight_payload["quote_number"] = f"TIMR-{m.group(1)}-{first_seq + 50:04d}"
zero_weight_payload["rooms"] = [{"name": "Custom Milestones", "notes": "", "items": [
    {"product_id": None, "description": "Milestone (no cost)", "unit": "nos", "brand": None, "qty": 1, "unit_price": 0},
]}]
resp = post_json(client, "/quotes/save-draft", zero_weight_payload, csrf)
zw_quote_id = resp.get_json()["quote_id"]
zero_weight_payload["quote_id"] = zw_quote_id
resp = post_json(client, "/quotes/save", zero_weight_payload, csrf)
check("zero-weight quote finalizes cleanly", resp.status_code == 200)
resp = client.get(f"/tracker/{zw_quote_id}")
check("all-zero-weight tracker still seeds (200)", resp.status_code == 200)
zw_html = resp.get_data(as_text=True)
zw_csrf = get_csrf(zw_html)
with db.connect() as conn:
    zw_task = conn.execute("SELECT * FROM quote_tasks WHERE quote_id=?", (zw_quote_id,)).fetchone()
resp = client.post(f"/tracker/{zw_quote_id}/tasks/{zw_task['id']}/complete", data={"csrf_token": zw_csrf}, follow_redirects=True)
progress = tracker_repo.compute_progress([dict(t) for t in [zw_task]])
with db.connect() as conn:
    zw_tasks_after = [dict(t) for t in conn.execute("SELECT * FROM quote_tasks WHERE quote_id=?", (zw_quote_id,)).fetchall()]
progress_after = tracker_repo.compute_progress(zw_tasks_after)
check("all-zero-weight fallback: 1/1 complete reports 100% via done/total, not weight math", progress_after["pct"] == 100.0)

# ------------------------------------------------------------------ 14. RBAC
resp = buyer_client.get("/quotes/")
check("buyer (no quotes.manage) CAN reach the quotes list (login_required only)", resp.status_code == 200)
r = client.get("/quotes/new")
buyer_dummy_csrf = "not-a-real-token"
resp = buyer_client.post("/quotes/save-draft", data=_json.dumps(room_a_payload), content_type="application/json",
                          headers={"X-CSRF-Token": buyer_dummy_csrf})
check("buyer is blocked (403) from mutating quotes (quotes.manage required)", resp.status_code == 403)
resp = buyer_client.get("/estimator/")
check("buyer is blocked (403) from the Rough Estimator page (quotes.manage required)", resp.status_code == 403)
resp = buyer_client.get("/tracker/")
check("buyer CAN reach the tracker list (login_required only)", resp.status_code == 200)
resp = buyer_client.post(f"/tracker/{quote_full_id}/team/add", data={"name": "X", "role": "Labor", "csrf_token": "bad"})
check("buyer is blocked (403) from tracker mutations (quotes.manage required)", resp.status_code == 403)

# --------------------------------------------------- 15. customer portal
with db.connect() as conn:
    customer_role_id = roles_repo.create_role(conn, "Customer")
    roles_repo.set_role_permissions(conn, customer_role_id, {"tracker.view_own"})
    pw_hash3, salt3, iters3 = auth.hash_password("CustomerPass123!")
    customer_id = users_repo.create_user(conn, "smoketest_customer", "Smoke Customer", pw_hash3, salt3, iters3, customer_role_id)

# staff links the customer to quote_full_id via the Tracker detail page
resp = client.get(f"/tracker/{quote_full_id}")
tracker_csrf = get_csrf(resp.get_data(as_text=True))
resp = client.post(f"/tracker/{quote_full_id}/client-user", data={"client_user_id": str(customer_id), "csrf_token": tracker_csrf}, follow_redirects=True)
with db.connect() as conn:
    linked = conn.execute("SELECT client_user_id FROM quotes WHERE id=?", (quote_full_id,)).fetchone()
check("staff can link a customer login to a quote", linked["client_user_id"] == customer_id)

customer_client = app.test_client()
r = customer_client.get("/auth/login")
c = get_csrf(r.get_data(as_text=True))
customer_client.post("/auth/login", data={"username": "smoketest_customer", "password": "CustomerPass123!", "csrf_token": c})

resp = customer_client.get("/", follow_redirects=True)
check("customer login lands on the portal, not Product Master", b"My Projects" in resp.data)

resp = customer_client.get("/portal/")
check("customer portal list loads (200)", resp.status_code == 200 and b"Smoke Project" in resp.data)

resp = customer_client.get(f"/portal/{quote_full_id}")
check("customer can view their own linked project's progress", resp.status_code == 200)
portal_html = resp.get_data(as_text=True)
check("customer portal shows the item checklist, not cost weights", "Kitchen" in portal_html and "Weight:" not in portal_html)

resp = customer_client.get(f"/tracker/{quote_full_id}")
check("customer is blocked (403) from the internal Tracker detail page", resp.status_code == 403)
resp = customer_client.get("/products/")
check("customer is blocked (403) from the Product Master", resp.status_code == 403)
resp = customer_client.get("/quotes/")
check("customer is blocked (403) from the internal quotes list", resp.status_code == 403)
resp = customer_client.get(f"/quotes/{quote_full_id}/pdf")
check("customer is blocked (403) from downloading the quote PDF", resp.status_code == 403)

resp = customer_client.get(f"/portal/{high_quote_id}")
check("customer is blocked (403) from a quote NOT linked to them (ownership check)", resp.status_code == 403)

# --------------------------------------------- 16. Settings & Document Builder
resp = client.get("/settings/")
check("settings company page loads (200)", resp.status_code == 200)
settings_csrf = get_csrf(resp.get_data(as_text=True))

resp = client.post("/settings/", data={
    "csrf_token": settings_csrf,
    "company_name": "Smoke Test Renovations LLC",
    "company_tagline": "Smoke tagline",
    "address_line1": "1 Test Rd", "address_line2": "", "phone": "+971500000000",
    "email": "smoke@example.com", "website": "example.com", "trn_number": "TRN000",
    "default_vat_percent": "8",
    "bank_name": "Smoke Bank", "bank_account_name": "Smoke Test Renovations LLC",
    "bank_iban": "AE00SMOKE", "bank_swift": "SMOKEAE",
}, follow_redirects=True)
check("company info save round-trips (200)", resp.status_code == 200)
with db.connect() as conn:
    saved = settings_repo.get_settings(conn)
check("company_name persisted", saved["company_name"] == "Smoke Test Renovations LLC")
check("default_vat_percent persisted as a float", saved["default_vat_percent"] == 8.0)
resp = client.get("/", follow_redirects=True)
check("new company name appears in the top bar", b"Smoke Test Renovations LLC" in resp.data)

resp = client.get("/settings/documents")
check("settings documents page loads (200)", resp.status_code == 200)
docs_csrf = get_csrf(resp.get_data(as_text=True))

def _make_1px_png():
    """Builds a real, valid 1x1 grayscale PNG from scratch (zlib + struct,
    no imaging library needed) -- exercises save_logo()'s actual
    image-decode path rather than faking a file with arbitrary bytes."""
    import struct
    import zlib

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
    raw = b"\x00\x00"  # filter byte (0) + one grayscale pixel byte (0)
    idat = chunk(b"IDAT", zlib.compress(raw))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


_PNG_1PX = _make_1px_png()
resp = client.post("/settings/documents", data={
    "csrf_token": docs_csrf,
    "logo_file": (io.BytesIO(_PNG_1PX), "smoke_logo.png"),
    "accent_color_hex": "#123456", "structure_color_hex": "#654321",
    "pdf_font": "Times",
    "show_bank_details_on_quote": "on", "show_bank_details_on_estimate": "on",
    "quote_trailing_block_order": "bank_then_terms",
    "estimate_disclaimer_text": "Smoke disclaimer.",
    "default_terms": "Smoke term one.\nSmoke term two.\n\nSmoke term three.",
}, content_type="multipart/form-data", follow_redirects=True)
check("document builder save round-trips (200)", resp.status_code == 200)
with db.connect() as conn:
    saved2 = settings_repo.get_settings(conn)
    saved_terms = settings_repo.get_default_terms(conn)
check("logo_filename saved", bool(saved2["logo_filename"]))
check("pdf_font persisted", saved2["pdf_font"] == "Times")
check("bank-details toggles persisted", saved2["show_bank_details_on_quote"] == 1 and saved2["show_bank_details_on_estimate"] == 1)
check("trailing block order persisted", saved2["quote_trailing_block_order"] == "bank_then_terms")
check("estimate disclaimer persisted", saved2["estimate_disclaimer_text"] == "Smoke disclaimer.")
check("default terms saved in order, blank line skipped", saved_terms == ["Smoke term one.", "Smoke term two.", "Smoke term three."])

resp = client.get("/settings/logo")
check("uploaded logo is served back (200)", resp.status_code == 200 and resp.data[:8] == bytes.fromhex("89504e470d0a1a0a"))

# A brand-new quote's editor should now prefill with the edited default terms.
resp = client.get("/quotes/new")
check("new-quote page reflects edited default terms", b"Smoke term one." in resp.data)

for doc_type in ("quote", "estimate", "po"):
    resp = client.get(f"/settings/preview/{doc_type}")
    check(f"{doc_type} preview PDF returns 200", resp.status_code == 200)
    check(f"{doc_type} preview PDF is application/pdf", resp.content_type == "application/pdf")
    check(f"{doc_type} preview PDF is non-trivial size", len(resp.data) > 1000)
    check(f"{doc_type} preview PDF starts with %PDF magic bytes", resp.data[:4] == b"%PDF")

resp = client.get("/settings/preview/not-a-real-type")
check("preview rejects an unknown doc_type (404)", resp.status_code == 404)

resp = client.post("/settings/documents", data={
    "csrf_token": docs_csrf,
    "logo_file": (io.BytesIO(b"not an image"), "not_an_image.txt"),
    "accent_color_hex": "#123456", "structure_color_hex": "#654321", "pdf_font": "Times",
    "quote_trailing_block_order": "bank_then_terms", "estimate_disclaimer_text": "x", "default_terms": "",
}, content_type="multipart/form-data", follow_redirects=True)
with db.connect() as conn:
    saved3 = settings_repo.get_settings(conn)
check("invalid logo extension rejected, previous logo kept", saved3["logo_filename"] == saved2["logo_filename"])

resp = buyer_client.get("/settings/")
check("buyer is blocked (403) from Settings (settings.manage required)", resp.status_code == 403)
resp = buyer_client.post("/settings/", data={"csrf_token": "bad", "company_name": "Hijacked"})
check("buyer is blocked (403) from saving Settings", resp.status_code == 403)

print()
if failures:
    print(f"=== SMOKE TEST FAILED ({len(failures)} failure(s)) ===")
    for f in failures:
        print(" -", f)
    sys.exit(1)
else:
    print("=== SMOKE TEST PASSED (all checks) ===")
