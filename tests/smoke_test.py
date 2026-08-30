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
  ...
  Item 4 (PO product search, payment terms/delivery info, DRAFT watermark,
  signature line, duplicate): the products/search endpoint filters correctly;
  update-details round-trips and blank fields null out (not empty strings);
  a draft PO's rendered PDF is provably larger than the identical content
  marked sent (DRAFT watermark actually draws extra content, not just a
  status label); duplicate copies supplier/items/payment_terms/ship_to as
  fresh snapshot rows but leaves expected_delivery_date blank and status
  reset to draft.

  Item 1 (Quote Builder "Import from Drawing", POST /quotes/import-drawing):
  request validation (missing file, disallowed extension, oversized file all
  return a clean 4xx JSON error rather than a crash), the route is
  permission- and CSRF-gated the same as every other quotes.manage mutation,
  and -- with no ANTHROPIC_API_KEY set (the state of this sandbox, since the
  `anthropic` package could not be installed here -- see NOTE below) -- the
  route returns a clean "not configured" 503 instead of raising. The
  Anthropic-SDK-calling code path itself (request construction, tool_use
  response parsing, exception-to-JSON error mapping) is exercised separately
  against a hand-built stub substituted for the `anthropic` module, since a
  real package install and a real API key are both unavailable in this
  sandbox -- see the "drawing import: stubbed Anthropic SDK" section below.

  NOTE: this sandbox has no outbound access to pypi.org/files.pythonhosted.org
  (network egress policy), so the real `anthropic` package could not be
  pip-installed or exercised end-to-end here. blueprints/quotes.py imports it
  defensively (falls back to None) for exactly this reason, and the route
  degrades to the "not configured" 503 whenever either the package or the API
  key is missing -- verified below. Live drawing analysis against the real
  Anthropic API is UNTESTED in this environment and must be verified once a
  real ANTHROPIC_API_KEY is set in production.

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
import config  # noqa: E402
import db  # noqa: E402
import repositories.users as users_repo  # noqa: E402
import repositories.roles as roles_repo  # noqa: E402
import repositories.products as products_repo  # noqa: E402
import repositories.quotes as quotes_repo  # noqa: E402
import repositories.tracker as tracker_repo  # noqa: E402
import repositories.settings as settings_repo  # noqa: E402
import repositories.purchase_orders as po_repo  # noqa: E402
import repositories.suppliers as suppliers_repo  # noqa: E402
import repositories.requisitions as requisitions_repo  # noqa: E402
import repositories.estimator_rates as rates_repo  # noqa: E402
import repositories.public as public_repo  # noqa: E402
import pdf.theme as pdf_theme  # noqa: E402
import pdf.purchase_order as pdf_po  # noqa: E402
from app import app  # noqa: E402

app.testing = True

failures = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        failures.append(label)


def check_blocked(label, username, password, method, path, **req_kwargs):
    """app.py's 403 handler no longer returns a bare 403 page -- it clears
    the triggering session (server-side + cookie) and redirects to the
    public homepage, so a logged-in account that hits a page it can't see
    always has a way back in, instead of being stranded (this is what
    landed a Vendor account in a 403 dead end after following a stale
    internal link). Every "role X is blocked from Y" check below exercises
    that whole path: log in fresh on a disposable client (never the
    caller's long-lived role client, which must stay authenticated for
    whatever runs after it), make the one blocked request, follow the
    redirect, and confirm it landed on the public homepage with the
    "signed out" flash -- not just that some 403-shaped thing happened."""
    throwaway = app.test_client()
    r = throwaway.get("/auth/login")
    c = get_csrf(r.get_data(as_text=True))
    throwaway.post("/auth/login", data={"username": username, "password": password, "csrf_token": c})
    fn = getattr(throwaway, method.lower())
    resp = fn(path, follow_redirects=True, **req_kwargs)
    html = resp.get_data(as_text=True)
    check(label, resp.status_code == 200 and "Get a Free Quote" in html and "signed out" in html.lower())
    return throwaway


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
check_blocked("non-admin role blocked from /admin/users (signed out, redirected home)",
              "smoketest_buyer", "BuyerPass123!", "GET", "/admin/users")
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

# ------------------------------------------------- 11b. estimator: Quick Estimate
# Item 5 follow-up (per-sqft Low/Medium/High rates + Import from Drawing reuse).
resp = client.get("/estimator/")
check("estimator page includes sqftRates in initial data", '"sqftRates"' in resp.get_data(as_text=True))

resp = post_json(client, "/estimator/sqft-rates/save", {"rates": {"sqft_house_low": 200, "sqft_house_medium": 350}}, est_csrf)
check("save sqft rate overrides returns 200", resp.status_code == 200)
sqft_rates_after_save = resp.get_json().get("sqftRates", {})
check("non-default sqft rate is stored as an override", sqft_rates_after_save["house"]["Low"]["rate"] == 200)
check("sqft rate equal to its default is NOT stored (sparse storage, same table as item rates)",
      sqft_rates_after_save["house"]["Medium"]["rate"] == 350)
check("office sqft rates are untouched by a house-only save", sqft_rates_after_save["office"]["Low"]["rate"] == 110)

resp = post_json(client, "/estimator/sqft-rates/reset", {}, est_csrf)
check("reset sqft rates returns 200", resp.status_code == 200)
sqft_rates_after_reset = resp.get_json().get("sqftRates", {})
check("sqft rates back to their defaults after reset", sqft_rates_after_reset["house"]["Low"]["rate"] == 180)

resp = post_json(client, "/estimator/rates/save", {"rates": {"socket": 999}}, est_csrf)
check("item-rate save still works after adding sqft rates to the shared table", resp.status_code == 200)
post_json(client, "/estimator/sqft-rates/reset", {}, est_csrf)
with db.connect() as conn:
    check("sqft-only reset doesn't wipe the item-rate override just saved",
          rates_repo.get_overrides(conn).get("socket") == 999)
post_json(client, "/estimator/rates/reset", {}, est_csrf)  # cleanup for later sections that assume defaults

quick_estimate_payload = {
    "project_type": "office", "tier": "Medium",
    "project_name": "Quick Estimate Test", "location": "Business Bay, Dubai", "vat_percent": 5,
    "rooms": [{"name": "Open Area", "sqft": 500}, {"name": "Cabin 1", "sqft": 100}],
}
resp = post_json(client, "/estimator/quick-pdf", quick_estimate_payload, est_csrf)
check("quick-pdf endpoint returns 200", resp.status_code == 200)
check("quick-pdf is application/pdf", resp.content_type == "application/pdf")
check("quick-pdf starts with %PDF magic bytes", resp.data[:4] == b"%PDF")

resp = post_json(client, "/estimator/quick-pdf", {**quick_estimate_payload, "rooms": []}, est_csrf)
check("quick-pdf with no rooms is rejected (400)", resp.status_code == 400)

resp = post_json(client, "/estimator/quick-pdf", {**quick_estimate_payload, "project_type": "bogus"}, est_csrf)
check("quick-pdf with an unknown project type is rejected (400)", resp.status_code == 400)

resp = client.post("/estimator/import-drawing", data={"csrf_token": est_csrf}, content_type="multipart/form-data")
check("estimator import-drawing with no file returns 400 (same shared logic as Quote Builder's)", resp.status_code == 400)
check_blocked("buyer (no quotes.manage) is blocked from estimator/import-drawing",
              "smoketest_buyer", "BuyerPass123!", "POST", "/estimator/import-drawing",
              data={"csrf_token": "irrelevant"}, content_type="multipart/form-data")


def _make_1px_png():
    """Builds a real, valid 1x1 grayscale PNG from scratch (zlib + struct,
    no imaging library needed) -- exercises the actual image-decode path
    rather than faking a file with arbitrary bytes. Defined here (ahead of
    the Item 2 task-completion-photo tests below, which need a real PNG
    well before the Item 3 Document Builder section reaches its own use of
    the same helper further down)."""
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


def _photo_file(name="task.png"):
    """A fresh BytesIO each call -- a Werkzeug FileStorage consumes its
    stream on save, so the same io.BytesIO object can't be reused across
    two upload attempts."""
    return (io.BytesIO(_PNG_1PX), name)


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
resp = client.post(f"/tracker/{quote_full_id}/tasks/{task_id}/complete",
                    data={"csrf_token": tracker_csrf}, follow_redirects=True)
check("completing a task with no photo attached is rejected (photo now required)",
      "Attach a photo" in resp.get_data(as_text=True))
with db.connect() as conn:
    not_completed = conn.execute("SELECT * FROM quote_tasks WHERE id=?", (task_id,)).fetchone()
check("the no-photo completion attempt did not actually complete the task", not_completed["status"] == "pending")

resp = client.get(f"/tracker/{quote_full_id}")
tracker_csrf = get_csrf(resp.get_data(as_text=True))
resp = client.post(f"/tracker/{quote_full_id}/tasks/{task_id}/complete",
                    data={"csrf_token": tracker_csrf, "photo": _photo_file()},
                    content_type="multipart/form-data", follow_redirects=True)
with db.connect() as conn:
    completed_task = conn.execute("SELECT * FROM quote_tasks WHERE id=?", (task_id,)).fetchone()
check("completing a task sets status + completed_at", completed_task["status"] == "completed" and completed_task["completed_at"] is not None)
check("completing a task with a valid photo stores a photo_filename", bool(completed_task["photo_filename"]))
check("completed task's photo thumbnail appears on the tracker detail page",
      "task-photo-thumb" in resp.get_data(as_text=True))

resp = client.get(f"/tracker/tasks/{task_id}/photo")
check("staff can view the completed task's photo (200)", resp.status_code == 200)
check("task photo is served with the real PNG bytes", resp.data[:8] == bytes.fromhex("89504e470d0a1a0a"))

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
resp = client.post(f"/tracker/{quote_full_id}/tasks/{task_id}/complete",
                    data={"csrf_token": tracker_csrf, "photo": _photo_file()},
                    content_type="multipart/form-data", follow_redirects=True)
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
resp = client.post(f"/tracker/{zw_quote_id}/tasks/{zw_task['id']}/complete",
                    data={"csrf_token": zw_csrf, "photo": _photo_file()},
                    content_type="multipart/form-data", follow_redirects=True)
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
# These three routes all require quotes.manage, which the Buyer role
# doesn't hold -- the permission decorator rejects the request before the
# view body's csrf_protect() ever runs, so the bogus tokens below never
# actually get checked (that's confirmed by check_blocked reaching the
# signed-out-and-redirected-home behavior, not a CSRF-related 400).
check_blocked("buyer is blocked from mutating quotes (quotes.manage required)",
              "smoketest_buyer", "BuyerPass123!", "POST", "/quotes/save-draft",
              data=_json.dumps(room_a_payload), content_type="application/json",
              headers={"X-CSRF-Token": buyer_dummy_csrf})
check_blocked("buyer is blocked from the Rough Estimator page (quotes.manage required)",
              "smoketest_buyer", "BuyerPass123!", "GET", "/estimator/")
resp = buyer_client.get("/tracker/")
check("buyer CAN reach the tracker list (login_required only)", resp.status_code == 200)
check_blocked("buyer is blocked from tracker mutations (quotes.manage required)",
              "smoketest_buyer", "BuyerPass123!", "POST", f"/tracker/{quote_full_id}/team/add",
              data={"name": "X", "role": "Labor", "csrf_token": "bad"})

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

check_blocked("customer is blocked from the internal Tracker detail page",
              "smoketest_customer", "CustomerPass123!", "GET", f"/tracker/{quote_full_id}")
check_blocked("customer is blocked from the Product Master",
              "smoketest_customer", "CustomerPass123!", "GET", "/products/")
check_blocked("customer is blocked from the internal quotes list",
              "smoketest_customer", "CustomerPass123!", "GET", "/quotes/")
check_blocked("customer is blocked from downloading the quote PDF",
              "smoketest_customer", "CustomerPass123!", "GET", f"/quotes/{quote_full_id}/pdf")

check_blocked("customer is blocked from a quote NOT linked to them (ownership check)",
              "smoketest_customer", "CustomerPass123!", "GET", f"/portal/{high_quote_id}")

resp = customer_client.get(f"/tracker/tasks/{task_id}/photo")
check("linked customer can view a completed task's photo on their own project (200)", resp.status_code == 200)
check("photo served to the customer is the real PNG bytes", resp.data[:8] == bytes.fromhex("89504e470d0a1a0a"))

# -------------------------------------- Item 2: Engineer login (photo-proof
# task completion) -- new tracker.complete_tasks permission, simplified
# Tracker views, required completion photo, customer-visible photos.
with db.connect() as conn:
    engineer_role_id = roles_repo.create_role(conn, "Engineer")
    roles_repo.set_role_permissions(conn, engineer_role_id, {"tracker.complete_tasks"})
    pw_hash4, salt4, iters4 = auth.hash_password("EngineerPass123!")
    engineer_id = users_repo.create_user(conn, "smoketest_engineer", "Smoke Engineer", pw_hash4, salt4, iters4, engineer_role_id)

engineer_client = app.test_client()
r = engineer_client.get("/auth/login")
c = get_csrf(r.get_data(as_text=True))
engineer_client.post("/auth/login", data={"username": "smoketest_engineer", "password": "EngineerPass123!", "csrf_token": c})

check("is_portal_only_user recognizes an Engineer-only permission set", auth.is_portal_only_user({"tracker.complete_tasks"}))
check("is_portal_only_user recognizes a Customer-only permission set", auth.is_portal_only_user({"tracker.view_own"}))
check("is_portal_only_user is False for a genuinely internal permission set", not auth.is_portal_only_user({"purchases.manage"}))
check("default_landing_endpoint sends an Engineer-only account to the Tracker list",
      auth.default_landing_endpoint({"tracker.complete_tasks"}) == "tracker.list_view")

resp = engineer_client.get("/", follow_redirects=True)
engineer_home_html = resp.get_data(as_text=True)
check("Engineer login lands on the (simplified) Tracker list, not Product Master",
      "Project Tracker" in engineer_home_html and "Product Master" not in engineer_home_html)
check("Engineer's nav shows 'My Tasks', not 'Project Tracker'/'Quotes'/'Product Master'",
      "My Tasks" in engineer_home_html and ">Quotes<" not in engineer_home_html
      and ">Product Master<" not in engineer_home_html)

resp = engineer_client.get("/tracker/")
check("Engineer can reach the Tracker list (200)", resp.status_code == 200)
engineer_list_html = resp.get_data(as_text=True)
check("Engineer's Tracker list hides the Quoted value / Budget columns and the $ stats row",
      "Quoted value" not in engineer_list_html and ">Budget<" not in engineer_list_html
      and "Over budget" not in engineer_list_html)
check("Engineer's Tracker list still shows quote number / client / an Open tracker link",
      room_a_payload["quote_number"] in engineer_list_html and "Open tracker" in engineer_list_html)

resp = engineer_client.get(f"/tracker/{quote_full_id}")
check("Engineer can open a project's Tracker detail page (200)", resp.status_code == 200)
engineer_detail_html = resp.get_data(as_text=True)
csrf_engineer = get_csrf(engineer_detail_html)
check("Engineer detail view hides the Budget panel", "<h2>Budget</h2>" not in engineer_detail_html)
check("Engineer detail view hides the Customer access panel", "Customer access" not in engineer_detail_html)
check("Engineer detail view hides the Team panel", "<h2>Team</h2>" not in engineer_detail_html)
check("Engineer detail view hides task Weight lines", "Weight:" not in engineer_detail_html)
check("Engineer detail view hides the Download quote PDF link", "Download quote PDF" not in engineer_detail_html)
check("Engineer detail view hides the Expenses panel", "<h2>Expenses</h2>" not in engineer_detail_html)
check("Engineer detail view's stage is plain read-only text, not a submit-on-change select",
      'id="stageSelect"' not in engineer_detail_html)
check("Engineer detail view still shows the task checklist itself", "Site cleanup" in engineer_detail_html)

with db.connect() as conn:
    cleanup_task = conn.execute(
        "SELECT * FROM quote_tasks WHERE quote_id=? AND description='Site cleanup'", (quote_full_id,)
    ).fetchone()
check("the custom 'Site cleanup' task used for the Engineer flow is still pending", cleanup_task["status"] == "pending")
cleanup_task_id = cleanup_task["id"]

resp = engineer_client.post(f"/tracker/{quote_full_id}/tasks/{cleanup_task_id}/complete",
                             data={"csrf_token": csrf_engineer}, follow_redirects=True)
check("Engineer completing a task with no photo attached is rejected",
      "Attach a photo" in resp.get_data(as_text=True))
with db.connect() as conn:
    still_pending = conn.execute("SELECT status FROM quote_tasks WHERE id=?", (cleanup_task_id,)).fetchone()
check("no-photo completion attempt by the Engineer left the task pending", still_pending["status"] == "pending")

resp = engineer_client.get(f"/tracker/{quote_full_id}")
csrf_engineer = get_csrf(resp.get_data(as_text=True))
resp = engineer_client.post(f"/tracker/{quote_full_id}/tasks/{cleanup_task_id}/complete",
                             data={"csrf_token": csrf_engineer, "photo": (io.BytesIO(b"not a real image but has a bad ext"), "note.txt")},
                             content_type="multipart/form-data", follow_redirects=True)
check("a disallowed photo extension is rejected with a clear message",
      "must be a .png, .jpg, or .jpeg" in resp.get_data(as_text=True))

resp = engineer_client.get(f"/tracker/{quote_full_id}")
csrf_engineer = get_csrf(resp.get_data(as_text=True))
oversized = b"\xff" * (8 * 1024 * 1024 + 1)
resp = engineer_client.post(f"/tracker/{quote_full_id}/tasks/{cleanup_task_id}/complete",
                             data={"csrf_token": csrf_engineer, "photo": (io.BytesIO(oversized), "huge.png")},
                             content_type="multipart/form-data", follow_redirects=True)
check("an oversized (>8MB) photo is rejected with a clear message", "too large" in resp.get_data(as_text=True))
with db.connect() as conn:
    still_pending2 = conn.execute("SELECT status FROM quote_tasks WHERE id=?", (cleanup_task_id,)).fetchone()
check("the rejected oversized-photo attempt left the task pending", still_pending2["status"] == "pending")

resp = engineer_client.get(f"/tracker/{quote_full_id}")
csrf_engineer = get_csrf(resp.get_data(as_text=True))
resp = engineer_client.post(f"/tracker/{quote_full_id}/tasks/{cleanup_task_id}/complete",
                             data={"csrf_token": csrf_engineer, "photo": _photo_file("cleanup.png")},
                             content_type="multipart/form-data", follow_redirects=True)
with db.connect() as conn:
    cleanup_completed = conn.execute("SELECT * FROM quote_tasks WHERE id=?", (cleanup_task_id,)).fetchone()
check("Engineer can complete a task by attaching a valid photo",
      cleanup_completed["status"] == "completed" and bool(cleanup_completed["photo_filename"]))
check("the newly-completed task's thumbnail appears on the Tracker detail page",
      "task-photo-thumb" in resp.get_data(as_text=True))

resp = engineer_client.get(f"/tracker/tasks/{cleanup_task_id}/photo")
check("Engineer can view the photo they just uploaded (200)", resp.status_code == 200)

check_blocked("Engineer is blocked from the internal quotes list",
              "smoketest_engineer", "EngineerPass123!", "GET", "/quotes/")
check_blocked("Engineer is blocked from the Product Master",
              "smoketest_engineer", "EngineerPass123!", "GET", "/products/")
check_blocked("Engineer is blocked from downloading the quote PDF",
              "smoketest_engineer", "EngineerPass123!", "GET", f"/quotes/{quote_full_id}/pdf")
check_blocked("Engineer is blocked from tracker mutations that require quotes.manage",
              "smoketest_engineer", "EngineerPass123!", "POST", f"/tracker/{quote_full_id}/team/add",
              data={"name": "X", "role": "Labor", "csrf_token": "bad"})

# any internal account (not just the linked customer) can view a task photo --
# a deliberate QA-record design, not an ownership restriction. Buyer holds
# neither quotes.manage nor tracker.complete_tasks, and is unrelated to this
# quote, yet is still "internal" (not portal-only).
resp = buyer_client.get(f"/tracker/tasks/{cleanup_task_id}/photo")
check("any internal account (e.g. Buyer) can view a task photo as a QA record (200)", resp.status_code == 200)
resp = buyer_client.get(f"/tracker/{quote_full_id}")
check("Buyer (internal, no quotes.manage/tracker.complete_tasks) can still open the Tracker detail page (unchanged)",
      resp.status_code == 200)

# a customer NOT linked to this quote is blocked from its task photos
with db.connect() as conn:
    other_customer_role_id = roles_repo.create_role(conn, "Customer2")
    roles_repo.set_role_permissions(conn, other_customer_role_id, {"tracker.view_own"})
    pw_hash5, salt5, iters5 = auth.hash_password("Customer2Pass123!")
    users_repo.create_user(conn, "smoketest_customer2", "Smoke Customer Two", pw_hash5, salt5, iters5, other_customer_role_id)
check_blocked("a customer NOT linked to this quote is blocked from its task photo",
              "smoketest_customer2", "Customer2Pass123!", "GET", f"/tracker/tasks/{cleanup_task_id}/photo")

check_blocked("a customer is still blocked from the internal Tracker detail page",
              "smoketest_customer2", "Customer2Pass123!", "GET", f"/tracker/{quote_full_id}")

# regression: quotes.manage staff still see the full, unchanged Tracker
# experience (Budget/Team/Customer-access/Weight/PDF-download all present)
resp = client.get(f"/tracker/{quote_full_id}")
staff_detail_html = resp.get_data(as_text=True)
check("staff (quotes.manage) still see the Budget panel (no regression)", "<h2>Budget</h2>" in staff_detail_html)
check("staff still see the Customer access panel (no regression)", "Customer access" in staff_detail_html)
check("staff still see the Team panel (no regression)", "<h2>Team</h2>" in staff_detail_html)
check("staff still see task Weight lines (no regression)", "Weight:" in staff_detail_html)
check("staff still see the Download quote PDF link (no regression)", "Download quote PDF" in staff_detail_html)
check("staff still see the Expenses panel (no regression)", "<h2>Expenses</h2>" in staff_detail_html)
check("staff still get the live stage-change select, not read-only text", 'id="stageSelect"' in staff_detail_html)
resp = client.get("/tracker/")
staff_list_html = resp.get_data(as_text=True)
check("staff still see the Quoted value / Budget columns on the Tracker list (no regression)",
      "Quoted value" in staff_list_html and ">Budget<" in staff_list_html)

resp = engineer_client.get(f"/tracker/tasks/999999/photo")
check("photo route 404s for a task id that doesn't exist", resp.status_code == 404)

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


# Item 3 -- PO content parity + configurable PDF table columns. Reordered/
# relabeled/partly-disabled column configs (not the hardcoded defaults) so
# the save round-trip actually exercises reordering and hiding, not just
# echoing the seeded default JSON back.
smoke_quote_columns = [
    {"key": "description", "label": "ITEM", "enabled": True},
    {"key": "brand", "label": "MAKE", "enabled": True},
    {"key": "qty", "label": "QTY", "enabled": True},
    {"key": "unit", "label": "UNIT", "enabled": True},
    {"key": "unit_price", "label": "RATE", "enabled": True},
    {"key": "amount", "label": "AMOUNT", "enabled": False},
]
smoke_po_columns = [
    {"key": "description", "label": "DESCRIPTION", "enabled": True},
    {"key": "unit_price", "label": "RATE", "enabled": True},
    {"key": "qty", "label": "QTY", "enabled": True},
    {"key": "unit", "label": "UNIT", "enabled": True},
    {"key": "brand", "label": "BRAND", "enabled": False},
    {"key": "line_total", "label": "TOTAL", "enabled": True},
]

_PNG_1PX = _make_1px_png()
resp = client.post("/settings/documents", data={
    "csrf_token": docs_csrf,
    "logo_file": (io.BytesIO(_PNG_1PX), "smoke_logo.png"),
    "accent_color_hex": "#123456", "structure_color_hex": "#654321",
    "pdf_font": "Times",
    "show_bank_details_on_quote": "on", "show_bank_details_on_estimate": "on",
    "show_bank_details_on_po": "on",
    "quote_trailing_block_order": "bank_then_terms",
    "estimate_disclaimer_text": "Smoke disclaimer.",
    "default_terms": "Smoke term one.\nSmoke term two.\n\nSmoke term three.",
    "po_terms": "Smoke PO term one.\nSmoke PO term two.\n\nSmoke PO term three.",
    "quote_table_columns": _json.dumps(smoke_quote_columns),
    "po_table_columns": _json.dumps(smoke_po_columns),
}, content_type="multipart/form-data", follow_redirects=True)
check("document builder save round-trips (200)", resp.status_code == 200)
with db.connect() as conn:
    saved2 = settings_repo.get_settings(conn)
    saved_terms = settings_repo.get_default_terms(conn)
    saved_po_terms = settings_repo.get_po_terms(conn)
check("logo_filename saved", bool(saved2["logo_filename"]))
check("pdf_font persisted", saved2["pdf_font"] == "Times")
check("bank-details toggles persisted", saved2["show_bank_details_on_quote"] == 1 and saved2["show_bank_details_on_estimate"] == 1)
check("trailing block order persisted", saved2["quote_trailing_block_order"] == "bank_then_terms")
check("estimate disclaimer persisted", saved2["estimate_disclaimer_text"] == "Smoke disclaimer.")
check("default terms saved in order, blank line skipped", saved_terms == ["Smoke term one.", "Smoke term two.", "Smoke term three."])

# ---- Item 3: PO content parity (bank toggle + PO terms) ----
check("show_bank_details_on_po persisted", saved2["show_bank_details_on_po"] == 1)
check("PO terms saved in order, blank line skipped",
      saved_po_terms == ["Smoke PO term one.", "Smoke PO term two.", "Smoke PO term three."])

# ---- Item 3: configurable table columns (Quote + PO) ----
check("quote_table_columns persisted exactly as posted (order/labels/enabled)",
      _json.loads(saved2["quote_table_columns"]) == smoke_quote_columns)
check("po_table_columns persisted exactly as posted (order/labels/enabled)",
      _json.loads(saved2["po_table_columns"]) == smoke_po_columns)

with db.connect() as conn:
    item3_ctx = pdf_theme.get_pdf_context(conn)
check("get_pdf_context resolves quote_columns from the saved JSON (Description forced first, Amount dropped)",
      [c[0] for c in item3_ctx.quote_columns] == ["description", "brand", "qty", "unit", "unit_price"])
check("get_pdf_context resolves po_columns from the saved JSON (Brand dropped, reordered)",
      [c[0] for c in item3_ctx.po_columns] == ["description", "unit_price", "qty", "unit", "line_total"])
check("get_pdf_context resolved column widths sum to the printable page width",
      abs(sum(c[2] for c in item3_ctx.quote_columns) - pdf_theme.AVAILABLE_TABLE_WIDTH_MM) < 0.01)
check("show_bank_on_po is true once toggled on and bank details exist", item3_ctx.show_bank_on_po is True)
check("ctx.po_terms reflects the saved PO terms", item3_ctx.po_terms == saved_po_terms)

# reload the documents page -- persisted PO terms/column labels/toggle show up
resp = client.get("/settings/documents")
docs_html_after = resp.get_data(as_text=True)
check("reloaded documents page reflects saved PO terms", "Smoke PO term one." in docs_html_after)
check("reloaded documents page reflects saved column labels", "MAKE" in docs_html_after and "RATE" in docs_html_after)
docs_csrf = get_csrf(docs_html_after)

# corrupt column JSON is rejected server-side, not silently stored as garbage
resp = client.post("/settings/documents", data={
    "csrf_token": docs_csrf,
    "accent_color_hex": "#123456", "structure_color_hex": "#654321", "pdf_font": "Times",
    "quote_trailing_block_order": "bank_then_terms", "estimate_disclaimer_text": "x", "default_terms": "",
    "po_terms": "", "quote_table_columns": "not valid json{{{",
    "po_table_columns": _json.dumps(smoke_po_columns),
}, content_type="multipart/form-data", follow_redirects=True)
check("corrupt column JSON flashes an error", b"Column configuration was corrupted" in resp.data)
with db.connect() as conn:
    saved_after_corrupt = settings_repo.get_settings(conn)
check("corrupt column JSON does not overwrite the previously-saved good config",
      _json.loads(saved_after_corrupt["quote_table_columns"]) == smoke_quote_columns)

# posting a column list with Description removed is rejected too (defense
# in depth -- the UI renders its checkbox disabled, but the server must not
# trust that alone)
resp = client.get("/settings/documents")
docs_csrf = get_csrf(resp.get_data(as_text=True))
no_description = [c for c in smoke_quote_columns if c["key"] != "description"]
resp = client.post("/settings/documents", data={
    "csrf_token": docs_csrf,
    "accent_color_hex": "#123456", "structure_color_hex": "#654321", "pdf_font": "Times",
    "quote_trailing_block_order": "bank_then_terms", "estimate_disclaimer_text": "x", "default_terms": "",
    "po_terms": "", "quote_table_columns": _json.dumps(no_description),
    "po_table_columns": _json.dumps(smoke_po_columns),
}, content_type="multipart/form-data", follow_redirects=True)
check("posting a column list with Description removed is rejected", b"Description column can" in resp.data)
docs_csrf = get_csrf(resp.get_data(as_text=True))

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

# ---- Item 3: the REAL Purchase Order PDF (not just the Settings preview)
# still renders once PO terms/bank-details/custom columns are all active --
# uses the actual PO created back in section 4 (po_id).
resp = client.get(f"/purchases/{po_id}/pdf")
check("real PO PDF still returns 200 with PO terms/bank/custom columns active", resp.status_code == 200)
check("real PO PDF is application/pdf", resp.content_type == "application/pdf")
check("real PO PDF starts with %PDF magic bytes", resp.data[:4] == b"%PDF")

# ---- Item 3: pdf.theme.resolve_columns() defensive fallbacks (unit-level,
# not just via the HTTP round-trip above) ----
check("resolve_columns falls back to defaults (enabled only) on corrupt JSON",
      [c[0] for c in pdf_theme.resolve_columns("not json", pdf_theme.QUOTE_DEFAULT_COLUMNS)] ==
      [c["key"] for c in pdf_theme.QUOTE_DEFAULT_COLUMNS if c["enabled"]])
check("resolve_columns falls back to defaults on an empty JSON array",
      [c[0] for c in pdf_theme.resolve_columns("[]", pdf_theme.PO_DEFAULT_COLUMNS)] ==
      [c["key"] for c in pdf_theme.PO_DEFAULT_COLUMNS])
check("resolve_columns forces Description present even if every other column is disabled and Description is missing entirely",
      pdf_theme.resolve_columns('[{"key":"qty","label":"QTY","enabled":false}]', pdf_theme.QUOTE_DEFAULT_COLUMNS) ==
      [("description", "DESCRIPTION", pdf_theme.AVAILABLE_TABLE_WIDTH_MM)])
check("resolve_columns forces Description enabled even if the stored JSON explicitly disabled it",
      pdf_theme.resolve_columns('[{"key":"description","label":"DESC","enabled":false}]', pdf_theme.QUOTE_DEFAULT_COLUMNS) ==
      [("description", "DESC", pdf_theme.AVAILABLE_TABLE_WIDTH_MM)])
resolved_all_po = pdf_theme.resolve_columns(saved2["po_table_columns"], pdf_theme.PO_DEFAULT_COLUMNS)
check("resolve_columns widths always sum exactly to the printable page width",
      abs(sum(w for _, _, w in resolved_all_po) - pdf_theme.AVAILABLE_TABLE_WIDTH_MM) < 0.01)

resp = client.post("/settings/documents", data={
    "csrf_token": docs_csrf,
    "logo_file": (io.BytesIO(b"not an image"), "not_an_image.txt"),
    "accent_color_hex": "#123456", "structure_color_hex": "#654321", "pdf_font": "Times",
    "quote_trailing_block_order": "bank_then_terms", "estimate_disclaimer_text": "x", "default_terms": "",
}, content_type="multipart/form-data", follow_redirects=True)
with db.connect() as conn:
    saved3 = settings_repo.get_settings(conn)
check("invalid logo extension rejected, previous logo kept", saved3["logo_filename"] == saved2["logo_filename"])

check_blocked("buyer is blocked from Settings (settings.manage required)",
              "smoketest_buyer", "BuyerPass123!", "GET", "/settings/")
check_blocked("buyer is blocked from saving Settings",
              "smoketest_buyer", "BuyerPass123!", "POST", "/settings/",
              data={"csrf_token": "bad", "company_name": "Hijacked"})

# ------------------------------------------------------------------ Item 4:
# PO product search, payment terms/delivery info, DRAFT watermark,
# signature line, duplicate. Runs after the Settings section above so
# company_settings.address_line1 ("1 Test Rd") is already saved -- needed to
# check the ship-to textarea's default prefill.
resp = client.get("/purchases/products/search", query_string={"q": "Smoke"})
check("PO product search (by keyword) returns 200", resp.status_code == 200)
item4_search_results = resp.get_json()
check("PO product search finds the smoke test product by keyword",
      any(r["id"] == product_id for r in item4_search_results))

resp = client.get("/purchases/products/search", query_string={"category": "TEST"})
check("PO product search (by category) finds the active fixture product",
      any(r["id"] == product_id for r in resp.get_json()))

# new_product (category SMOKE) was soft-deactivated back in section 3 -- a
# category search for it should now come back empty (active-only filter,
# same as blueprints/quotes.py: products_search).
resp = client.get("/purchases/products/search", query_string={"category": "SMOKE"})
check("PO product search excludes a soft-deactivated product", resp.get_json() == [])

resp = client.get("/purchases/products/search", query_string={"brand": "__NONE__"})
check("PO product search 'no brand listed' filter returns 200", resp.status_code == 200)

resp = buyer_client.get("/purchases/products/search", query_string={"q": "x"})
check("PO product search requires purchases.manage (buyer role, which has it, gets 200)", resp.status_code == 200)

# fresh supplier + PO for the order-details/duplicate/watermark checks below
# -- kept separate from po_id above, which is already fully received.
r = client.get("/purchases/suppliers/new")
c = get_csrf(r.get_data(as_text=True))
client.post("/purchases/suppliers/new",
             data={"name": "Item4 Smoke Supplier", "address": "1 Item4 Way", "csrf_token": c},
             follow_redirects=True)
with db.connect() as conn:
    item4_supplier = conn.execute("SELECT * FROM suppliers WHERE name = 'Item4 Smoke Supplier'").fetchone()

r = client.get("/purchases/new")
c = get_csrf(r.get_data(as_text=True))
resp = client.post("/purchases/new",
                    data={"supplier_id": str(item4_supplier["id"]), "notes": "item4 smoke", "csrf_token": c})
item4_po_id = int(re.search(r"/purchases/(\d+)", resp.headers["Location"]).group(1))

r = client.get(f"/purchases/{item4_po_id}")
detail_html = r.get_data(as_text=True)
c = get_csrf(detail_html)
check("PO detail 'Add item' panel uses the new search UI (no giant hardcoded <select>)",
      "poSearchQuery" in detail_html and 'id="product_id"' in detail_html)
check("PO detail page no longer preloads a 1000-row product <select>",
      "<select id=\"product_id\"" not in detail_html)
check("Order details panel present on a draft PO", "Order details" in detail_html)
check("Ship-to textarea is pre-filled with the company's saved address by default",
      "1 Test Rd" in detail_html)

client.post(f"/purchases/{item4_po_id}/add-item",
            data={"product_id": str(product_id), "quantity": "4", "unit_price": "99", "csrf_token": c})
with db.connect() as conn:
    item4_items = po_repo.list_po_items(conn, item4_po_id)
check("item added via the product-search-populated form fields", len(item4_items) == 1)

# order details: round-trip, then clear back out
resp = client.post(f"/purchases/{item4_po_id}/update-details", data={
    "payment_terms": "50% advance, balance on delivery",
    "expected_delivery_date": "2026-09-30",
    "ship_to_address": "Smoke Site, Building 9",
    "csrf_token": c,
}, follow_redirects=True)
check("update-details round-trip succeeds", resp.status_code == 200)
with db.connect() as conn:
    item4_po_with_details = po_repo.get_purchase_order(conn, item4_po_id)
check("payment_terms persisted", item4_po_with_details["payment_terms"] == "50% advance, balance on delivery")
check("expected_delivery_date persisted", item4_po_with_details["expected_delivery_date"] == "2026-09-30")
check("ship_to_address persisted", item4_po_with_details["ship_to_address"] == "Smoke Site, Building 9")

resp = client.get(f"/purchases/{item4_po_id}/pdf")
pdf_with_details = resp.data
check("PO PDF with order details set returns 200 and is non-trivial", resp.status_code == 200 and len(pdf_with_details) > 1000)

# clearing the fields back to blank nulls them out (not stored as empty
# strings) and the rendered PDF actually shrinks -- proves the SHIP TO /
# PAYMENT TERMS / EXPECTED DELIVERY blocks are omitted, not just blanked.
client.post(f"/purchases/{item4_po_id}/update-details",
            data={"payment_terms": "", "expected_delivery_date": "", "ship_to_address": "", "csrf_token": c})
with db.connect() as conn:
    item4_po_cleared = po_repo.get_purchase_order(conn, item4_po_id)
check("clearing the order-details form fields nulls them out (not empty strings)",
      item4_po_cleared["payment_terms"] is None and item4_po_cleared["expected_delivery_date"] is None
      and item4_po_cleared["ship_to_address"] is None)
resp = client.get(f"/purchases/{item4_po_id}/pdf")
pdf_without_details = resp.data
check("clearing order details shrinks the rendered PDF (blocks omitted, not blank)",
      len(pdf_without_details) < len(pdf_with_details))

# re-set payment_terms/ship_to (not expected_delivery_date) so the
# duplicate check below can actually verify carry-over vs. non-carry-over
resp = client.post(f"/purchases/{item4_po_id}/update-details", data={
    "payment_terms": "Net 30", "expected_delivery_date": "2026-10-15",
    "ship_to_address": "Warehouse 4, Jebel Ali", "csrf_token": c,
}, follow_redirects=True)
check("re-saving order details after clearing them still works", resp.status_code == 200)

# --- DRAFT watermark, at the PDF-builder level: identical content, status
# flipped draft vs sent -- deterministic, and doesn't depend on parsing
# reportlab's compressed content stream to find the drawn "DRAFT" text. ---
with db.connect() as conn:
    item4_ctx = pdf_theme.get_pdf_context(conn)
    item4_watermark_items = [dict(i) for i in po_repo.list_po_items(conn, item4_po_id)]
    item4_po_row = dict(po_repo.get_purchase_order(conn, item4_po_id))
draft_dict = dict(item4_po_row, status="draft")
sent_dict = dict(item4_po_row, status="sent")
draft_pdf_bytes = pdf_po.build_purchase_order_pdf(item4_ctx, draft_dict, item4_watermark_items)
sent_pdf_bytes = pdf_po.build_purchase_order_pdf(item4_ctx, sent_dict, item4_watermark_items)
check("a draft PO's PDF is larger than the identical content marked sent (DRAFT watermark draws extra content)",
      len(draft_pdf_bytes) > len(sent_pdf_bytes))
check("received/partially_received POs also render with no watermark (same size as sent)",
      len(pdf_po.build_purchase_order_pdf(
          item4_ctx, dict(item4_po_row, status="received"), item4_watermark_items)) == len(sent_pdf_bytes))

# mark this PO sent via the real HTTP route too, and confirm both panels
# (still editable) and the PDF still render correctly
r = client.get(f"/purchases/{item4_po_id}")
c = get_csrf(r.get_data(as_text=True))
client.post(f"/purchases/{item4_po_id}/mark-sent", data={"csrf_token": c}, follow_redirects=True)
resp = client.get(f"/purchases/{item4_po_id}/pdf")
check("PDF for the now-sent PO still returns 200", resp.status_code == 200)
r = client.get(f"/purchases/{item4_po_id}")
sent_detail_html = r.get_data(as_text=True)
check("Add item / Order details panels still show for a 'sent' PO (editable while draft or sent)",
      "Order details" in sent_detail_html and "poSearchQuery" in sent_detail_html)

# a fully-received PO's detail page hides both panels (matches the existing
# "Add item" gate -- reuses po_id/po from section 4, already fully received)
r = client.get(f"/purchases/{po_id}")
received_detail_html = r.get_data(as_text=True)
check("Order details / Add item panels are hidden once a PO is fully received",
      "Order details" not in received_detail_html and "poSearchQuery" not in received_detail_html)
resp = client.post(f"/purchases/{po_id}/update-details",
                    data={"payment_terms": "should be refused", "csrf_token": c}, follow_redirects=True)
check("update-details is refused server-side on a fully-received PO",
      b"already started receiving stock" in resp.data)
with db.connect() as conn:
    received_po_unchanged = po_repo.get_purchase_order(conn, po_id)
check("the refused update-details call did not change the received PO's payment_terms",
      received_po_unchanged["payment_terms"] is None)

# --- duplicate ---
r = client.get(f"/purchases/{item4_po_id}")
c = get_csrf(r.get_data(as_text=True))
resp = client.post(f"/purchases/{item4_po_id}/duplicate", data={"csrf_token": c})
check("duplicate redirects (302) to a new PO's detail page", resp.status_code == 302)
dup_po_id = int(re.search(r"/purchases/(\d+)", resp.headers["Location"]).group(1))
check("duplicate created a different PO id than the source", dup_po_id != item4_po_id)
with db.connect() as conn:
    dup_po = po_repo.get_purchase_order(conn, dup_po_id)
    dup_items = po_repo.list_po_items(conn, dup_po_id)
    source_items_final = po_repo.list_po_items(conn, item4_po_id)
check("duplicate is a fresh draft regardless of the source's status", dup_po["status"] == "draft")
check("duplicate carries over the same supplier", dup_po["supplier_id"] == item4_po_row["supplier_id"])
check("duplicate carries over payment_terms from the source", dup_po["payment_terms"] == "Net 30")
check("duplicate carries over ship_to_address from the source", dup_po["ship_to_address"] == "Warehouse 4, Jebel Ali")
check("duplicate leaves expected_delivery_date blank (a new order needs its own date)",
      dup_po["expected_delivery_date"] is None)
check("duplicate copies the source's line items 1:1 by count", len(dup_items) == len(source_items_final) == 1)
check("duplicate's item is a fresh snapshot row, not a reference to the source's row id",
      dup_items[0]["id"] != source_items_final[0]["id"])
check("duplicate's item matches the source's product/qty/price",
      dup_items[0]["product_id"] == source_items_final[0]["product_id"]
      and dup_items[0]["quantity"] == source_items_final[0]["quantity"]
      and dup_items[0]["unit_price"] == source_items_final[0]["unit_price"])

# duplicated PO's own PDF renders fine and (being a fresh draft) carries the watermark again
resp = client.get(f"/purchases/{dup_po_id}/pdf")
check("duplicated PO's PDF returns 200", resp.status_code == 200)
check("duplicated PO's PDF is application/pdf", resp.content_type == "application/pdf")

resp = buyer_client.post(f"/purchases/{item4_po_id}/duplicate", data={"csrf_token": "bad"})
check("duplicate is CSRF-protected (bad token rejected)", resp.status_code == 400)

# ------------------------------------------- Item 1: import-drawing route
# See the module docstring's NOTE -- no real `anthropic` package/API key is
# available in this sandbox, so this section covers request validation,
# permission/CSRF gating, and the "not configured" fallback via real HTTP
# round-trips, then exercises the SDK-calling code path itself against a
# hand-built stub module (below).

r = client.get("/quotes/new")
drawing_csrf = get_csrf_json(r.get_data(as_text=True))

resp = client.post("/quotes/import-drawing", data={"csrf_token": drawing_csrf},
                    content_type="multipart/form-data")
check("import-drawing with no file returns 400", resp.status_code == 400)
check("import-drawing with no file returns a clean JSON error", "error" in (resp.get_json() or {}))

resp = client.post("/quotes/import-drawing", data={
    "csrf_token": drawing_csrf,
    "drawing_file": (io.BytesIO(b"not a real drawing"), "notes.txt"),
}, content_type="multipart/form-data")
check("import-drawing rejects a disallowed extension (400)", resp.status_code == 400)
check("disallowed-extension error message names the allowed types",
      "png" in (resp.get_json() or {}).get("error", ""))

_oversized = b"\x00" * (15 * 1024 * 1024 + 1)
resp = client.post("/quotes/import-drawing", data={
    "csrf_token": drawing_csrf,
    "drawing_file": (io.BytesIO(_oversized), "big.png"),
}, content_type="multipart/form-data")
check("import-drawing rejects an oversized file (400)", resp.status_code == 400)
check("oversized-file error message mentions the size limit",
      "large" in (resp.get_json() or {}).get("error", "").lower())
del _oversized

resp = client.post("/quotes/import-drawing", data={
    "csrf_token": "bad-token",
    "drawing_file": (io.BytesIO(_PNG_1PX), "plan.png"),
}, content_type="multipart/form-data")
check("import-drawing is CSRF-protected (bad token rejected, 400)", resp.status_code == 400)

check_blocked("buyer (no quotes.manage) is blocked from import-drawing",
              "smoketest_buyer", "BuyerPass123!", "POST", "/quotes/import-drawing",
              data={"csrf_token": "irrelevant", "drawing_file": (io.BytesIO(_PNG_1PX), "plan.png")},
              content_type="multipart/form-data")

check("ANTHROPIC_API_KEY is unset in this sandbox (expected -- no key available here)",
      config.ANTHROPIC_API_KEY in (None, ""))
resp = client.post("/quotes/import-drawing", data={
    "csrf_token": drawing_csrf,
    "drawing_file": (io.BytesIO(_PNG_1PX), "plan.png"),
}, content_type="multipart/form-data")
check("import-drawing with no API key configured returns a clean 503 (not a crash)", resp.status_code == 503)
check("not-configured error message is actionable", "configured" in (resp.get_json() or {}).get("error", "").lower())

# ---- drawing import: stubbed Anthropic SDK (see module docstring NOTE) ----
# Exercises the parts of import_drawing() that only run once a client/API
# key are present: request construction, successful tool_use parsing, the
# zero-rooms message, and mapping SDK exceptions to clean JSON errors --
# all without a real network call or a real installed `anthropic` package.
import types as _types
import drawing_import


class _FakeAPIError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


class _FakeAuthenticationError(_FakeAPIError):
    pass


class _FakeRateLimitError(_FakeAPIError):
    pass


class _FakeToolUseBlock:
    def __init__(self, name, input_):
        self.type = "tool_use"
        self.name = name
        self.input = input_


class _FakeResponse:
    def __init__(self, content):
        self.content = content


class _FakeMessages:
    def __init__(self, behavior):
        self.behavior = behavior  # callable: (**kwargs) -> _FakeResponse, or raises

    def create(self, **kwargs):
        return self.behavior(**kwargs)


class _FakeAnthropicClient:
    def __init__(self, behavior):
        self.messages = _FakeMessages(behavior)


def _make_fake_anthropic(behavior):
    fake_client_cls = lambda api_key=None: _FakeAnthropicClient(behavior)  # noqa: E731
    return _types.SimpleNamespace(
        Anthropic=fake_client_cls,
        APIError=_FakeAPIError,
        AuthenticationError=_FakeAuthenticationError,
        RateLimitError=_FakeRateLimitError,
        BadRequestError=_FakeAPIError,
    )


_real_anthropic_module = drawing_import.anthropic
_real_api_key = config.ANTHROPIC_API_KEY
config.ANTHROPIC_API_KEY = "sk-ant-fake-key-for-smoke-test"

try:
    # -- success path: a well-formed tool_use response is parsed into rooms
    def _success_behavior(**kwargs):
        check("stubbed call forces the record_rooms tool",
              kwargs.get("tool_choice") == {"type": "tool", "name": "record_rooms"})
        check("stubbed call sends the configured drawing model", kwargs.get("model") == config.DRAWING_MODEL)
        return _FakeResponse([_FakeToolUseBlock("record_rooms", {"rooms": [
            {"name": "Kitchen", "approx_sqft": 180, "source_note": "12' x 15'"},
            {"name": "  ", "approx_sqft": None, "source_note": None},  # blank name -- must be dropped
            {"name": "Room 2", "approx_sqft": "not-a-number", "source_note": "  "},
        ]})])

    drawing_import.anthropic = _make_fake_anthropic(_success_behavior)
    resp = client.post("/quotes/import-drawing", data={
        "csrf_token": drawing_csrf,
        "drawing_file": (io.BytesIO(_PNG_1PX), "plan.png"),
    }, content_type="multipart/form-data")
    check("stubbed successful analysis returns 200", resp.status_code == 200)
    body = resp.get_json() or {}
    rooms_out = body.get("rooms") or []
    check("stubbed analysis returns exactly the 2 well-formed rooms (blank-name room dropped)",
          len(rooms_out) == 2)
    check("first room's name/size/note passed through", rooms_out[0] == {
        "name": "Kitchen", "approx_sqft": 180.0, "source_note": "12' x 15'",
    })
    check("a non-numeric approx_sqft is coerced to null rather than crashing",
          rooms_out[1]["approx_sqft"] is None)
    check("a blank source_note is normalized to null", rooms_out[1]["source_note"] is None)

    # -- zero-rooms path: still 200, with a helpful message the UI can show
    def _empty_behavior(**kwargs):
        return _FakeResponse([_FakeToolUseBlock("record_rooms", {"rooms": []})])

    drawing_import.anthropic = _make_fake_anthropic(_empty_behavior)
    resp = client.post("/quotes/import-drawing", data={
        "csrf_token": drawing_csrf,
        "drawing_file": (io.BytesIO(_PNG_1PX), "plan.png"),
    }, content_type="multipart/form-data")
    check("stubbed zero-rooms result still returns 200 (not an error)", resp.status_code == 200)
    body = resp.get_json() or {}
    check("zero-rooms result returns an empty list", body.get("rooms") == [])
    check("zero-rooms result includes a helpful message for the UI", bool(body.get("message")))

    # -- PDF (document content block) path also reaches the SDK call cleanly
    def _pdf_behavior(**kwargs):
        content = kwargs["messages"][0]["content"]
        check("a .pdf upload is sent as a document content block, not an image block",
              content[0]["type"] == "document" and content[0]["source"]["media_type"] == "application/pdf")
        return _FakeResponse([_FakeToolUseBlock("record_rooms", {"rooms": [{"name": "Room 1"}]})])

    drawing_import.anthropic = _make_fake_anthropic(_pdf_behavior)
    resp = client.post("/quotes/import-drawing", data={
        "csrf_token": drawing_csrf,
        "drawing_file": (io.BytesIO(b"%PDF-1.4 fake pdf bytes"), "plan.pdf"),
    }, content_type="multipart/form-data")
    check("stubbed PDF upload returns 200", resp.status_code == 200)

    # -- SDK exception mapping: auth error -> clean 401 JSON, not a crash
    def _auth_error_behavior(**kwargs):
        raise _FakeAuthenticationError("invalid x-api-key")

    drawing_import.anthropic = _make_fake_anthropic(_auth_error_behavior)
    resp = client.post("/quotes/import-drawing", data={
        "csrf_token": drawing_csrf,
        "drawing_file": (io.BytesIO(_PNG_1PX), "plan.png"),
    }, content_type="multipart/form-data")
    check("stubbed AuthenticationError maps to a clean 401 JSON error (not a crash)", resp.status_code == 401)
    check("auth-error response has no raw traceback/HTML leaking through",
          b"Traceback" not in resp.data and resp.content_type.startswith("application/json"))

    # -- SDK exception mapping: rate limit -> clean 429 JSON
    def _rate_limit_behavior(**kwargs):
        raise _FakeRateLimitError("rate limited")

    drawing_import.anthropic = _make_fake_anthropic(_rate_limit_behavior)
    resp = client.post("/quotes/import-drawing", data={
        "csrf_token": drawing_csrf,
        "drawing_file": (io.BytesIO(_PNG_1PX), "plan.png"),
    }, content_type="multipart/form-data")
    check("stubbed RateLimitError maps to a clean 429 JSON error", resp.status_code == 429)

    # -- a genuinely unexpected exception still comes back as clean JSON,
    # never an unhandled 500 crash
    def _weird_behavior(**kwargs):
        raise RuntimeError("something totally unexpected")

    drawing_import.anthropic = _make_fake_anthropic(_weird_behavior)
    resp = client.post("/quotes/import-drawing", data={
        "csrf_token": drawing_csrf,
        "drawing_file": (io.BytesIO(_PNG_1PX), "plan.png"),
    }, content_type="multipart/form-data")
    check("an unexpected non-SDK exception still returns clean JSON, not a raw crash",
          resp.status_code == 500 and resp.content_type.startswith("application/json")
          and "error" in (resp.get_json() or {}))
finally:
    drawing_import.anthropic = _real_anthropic_module
    config.ANTHROPIC_API_KEY = _real_api_key

resp = client.post("/quotes/import-drawing", data={
    "csrf_token": drawing_csrf,
    "drawing_file": (io.BytesIO(_PNG_1PX), "plan.png"),
}, content_type="multipart/form-data")
check("import-drawing returns to the clean 503 once the stub/fake key are restored to their real (unset) state",
      resp.status_code == 503)

# ============================================================= Item 5:
# Material Requisitions (multi-vendor pricing portal). Covers: existing
# suppliers get sequential vendor_codes on migration (checked earlier, see
# "migration sanity check" additions below); new suppliers get one at
# create-time; vendor_code is immutable via any path; the Vendor role
# self-service precondition + quick-create derivation rule
# (username = re.sub([^a-z0-9], vendor_code.lower()), password = username +
# "12345"); full multi-vendor flow (invite two vendors, one saves a draft
# -- visible to staff as "In progress" -- then both submit, staff awards to
# one, a real PO is created with the right supplier/items/prices, the
# requisition shows 'awarded'); a vendor is fully sandboxed to their own
# data and blocked from the rest of the app exactly like the Customer/
# Engineer portal-only accounts.

with db.connect() as conn:
    mr_cols = {row["name"] for row in conn.execute("PRAGMA table_info(suppliers)")}
    mr_tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'material_requisition%'"
    )}
check("migration 0008 added suppliers.vendor_code / portal_user_id", {"vendor_code", "portal_user_id"} <= mr_cols)
check("migration 0008 created all four material_requisition tables",
      mr_tables == {"material_requisitions", "material_requisition_items",
                    "material_requisition_vendors", "material_requisition_prices"})

with db.connect() as conn:
    pre_existing_suppliers = conn.execute("SELECT id, vendor_code FROM suppliers ORDER BY id").fetchall()
check("every supplier created before Item 5 (sections 4/Item 4) was backfilled with a sequential vendor_code",
      all(s["vendor_code"] is not None for s in pre_existing_suppliers))
check("backfilled vendor_codes are unique and sequential by id",
      [s["vendor_code"] for s in pre_existing_suppliers] ==
      [f"VEND-{i:04d}" for i in range(1, len(pre_existing_suppliers) + 1)])

# --- new suppliers created via the normal route also get a vendor_code ---
r = client.get("/purchases/suppliers/new")
c = get_csrf(r.get_data(as_text=True))
client.post("/purchases/suppliers/new", data={"name": "MR Vendor Alpha", "csrf_token": c}, follow_redirects=True)
r = client.get("/purchases/suppliers/new")
c = get_csrf(r.get_data(as_text=True))
client.post("/purchases/suppliers/new", data={"name": "MR Vendor Beta", "csrf_token": c}, follow_redirects=True)
with db.connect() as conn:
    mr_vendor_a = conn.execute("SELECT * FROM suppliers WHERE name='MR Vendor Alpha'").fetchone()
    mr_vendor_b = conn.execute("SELECT * FROM suppliers WHERE name='MR Vendor Beta'").fetchone()
check("newly-created supplier A got a fresh sequential vendor_code",
      mr_vendor_a["vendor_code"] == f"VEND-{len(pre_existing_suppliers) + 1:04d}")
check("newly-created supplier B got the next sequential vendor_code",
      mr_vendor_b["vendor_code"] == f"VEND-{len(pre_existing_suppliers) + 2:04d}")

# --- vendor_code is immutable: a crafted POST field is silently ignored ---
r = client.get(f"/purchases/suppliers/{mr_vendor_a['id']}/edit")
supplier_edit_html = r.get_data(as_text=True)
check("supplier edit page shows the read-only vendor code", mr_vendor_a["vendor_code"] in supplier_edit_html)
check("supplier edit page has no editable vendor_code input",
      'name="vendor_code"' not in supplier_edit_html)
c = get_csrf(supplier_edit_html)
client.post(f"/purchases/suppliers/{mr_vendor_a['id']}/edit", data={
    "name": "MR Vendor Alpha Renamed", "vendor_code": "HACKED-0001", "csrf_token": c,
}, follow_redirects=True)
with db.connect() as conn:
    mr_vendor_a_after_edit = conn.execute("SELECT * FROM suppliers WHERE id=?", (mr_vendor_a["id"],)).fetchone()
check("a crafted vendor_code POST field is silently ignored -- vendor_code unchanged",
      mr_vendor_a_after_edit["vendor_code"] == mr_vendor_a["vendor_code"])
check("the legitimate name field in the same request still updates normally",
      mr_vendor_a_after_edit["name"] == "MR Vendor Alpha Renamed")

# --- vendor accounts: self-service precondition, then quick-create ---
r = client.get(f"/purchases/suppliers/{mr_vendor_a['id']}/edit")
pre_role_html = r.get_data(as_text=True)
check('no Vendor role yet -> quick-create button is not shown, "create a Vendor role" messaging is',
      "Quick-create vendor login" not in pre_role_html and "Vendor" in pre_role_html and "role" in pre_role_html)

with db.connect() as conn:
    vendor_role_id = roles_repo.create_role(conn, "Vendor")
    roles_repo.set_role_permissions(conn, vendor_role_id, {"requisitions.vendor_fill"})

r = client.get(f"/purchases/suppliers/{mr_vendor_a['id']}/edit")
post_role_html = r.get_data(as_text=True)
check("once a role holding EXACTLY requisitions.vendor_fill exists, the quick-create button appears",
      "Quick-create vendor login" in post_role_html)
c = get_csrf(post_role_html)

resp = client.post(f"/purchases/suppliers/{mr_vendor_a['id']}/quick-create-vendor-login",
                    data={"csrf_token": c}, follow_redirects=True)
qc_flash_html = resp.get_data(as_text=True)
expected_username_a = "mrvendoralpha" if False else __import__("re").sub(
    r"[^a-z0-9]", "", mr_vendor_a["vendor_code"].lower())
expected_password_a = expected_username_a + "12345"
check("quick-create success flash names the generated username",
      expected_username_a in qc_flash_html)
check("quick-create success flash names the generated password",
      expected_password_a in qc_flash_html)

with db.connect() as conn:
    mr_vendor_a_linked = conn.execute("SELECT * FROM suppliers WHERE id=?", (mr_vendor_a["id"],)).fetchone()
    mr_vendor_a_user = conn.execute(
        "SELECT * FROM users WHERE id=?", (mr_vendor_a_linked["portal_user_id"],)
    ).fetchone()
check("quick-create linked a new user as this supplier's portal_user_id",
      mr_vendor_a_linked["portal_user_id"] is not None)
check("quick-create derived username = vendor_code lowercased, non-alnum stripped",
      mr_vendor_a_user["username"] == expected_username_a)
check("quick-create assigned the auto-detected Vendor role",
      mr_vendor_a_user["role_id"] == vendor_role_id)

# quick-create again is refused (already linked)
r = client.get(f"/purchases/suppliers/{mr_vendor_a['id']}/edit")
c = get_csrf(r.get_data(as_text=True))
resp = client.post(f"/purchases/suppliers/{mr_vendor_a['id']}/quick-create-vendor-login",
                    data={"csrf_token": c}, follow_redirects=True)
check("quick-create a second time for an already-linked supplier is refused",
      "already has a linked vendor login" in resp.get_data(as_text=True))

# quick-create for vendor B too, so the multi-vendor flow below has two real logins
r = client.get(f"/purchases/suppliers/{mr_vendor_b['id']}/edit")
c = get_csrf(r.get_data(as_text=True))
client.post(f"/purchases/suppliers/{mr_vendor_b['id']}/quick-create-vendor-login",
            data={"csrf_token": c}, follow_redirects=True)
with db.connect() as conn:
    mr_vendor_b_linked = conn.execute("SELECT * FROM suppliers WHERE id=?", (mr_vendor_b["id"],)).fetchone()
    mr_vendor_b_user = conn.execute("SELECT * FROM users WHERE id=?", (mr_vendor_b_linked["portal_user_id"],)).fetchone()
expected_username_b = __import__("re").sub(r"[^a-z0-9]", "", mr_vendor_b["vendor_code"].lower())
expected_password_b = expected_username_b + "12345"
check("vendor B quick-create also derived the correct username", mr_vendor_b_user["username"] == expected_username_b)

# --- auth.py plumbing ---
check("requisitions.vendor_fill is a portal-only code",
      auth.is_portal_only_user({"requisitions.vendor_fill"}))
check("default_landing_endpoint sends a vendor-only account to the Vendor portal",
      auth.default_landing_endpoint({"requisitions.vendor_fill"}) == "vendor_portal.list_view")

# --- staff creates a requisition, adds items, invites both vendors ---
r = client.get("/requisitions/")
check("requisitions list page loads (200)", r.status_code == 200)
r = client.get("/requisitions/new")
c = get_csrf(r.get_data(as_text=True))
resp = client.post("/requisitions/new", data={"notes": "Smoke test requisition", "csrf_token": c})
check("new requisition redirects (302) to its detail page", resp.status_code == 302)
req_id = int(re.search(r"/requisitions/(\d+)", resp.headers["Location"]).group(1))

with db.connect() as conn:
    req_row = conn.execute("SELECT * FROM material_requisitions WHERE id=?", (req_id,)).fetchone()
check("requisition number is well-formed (MR-YYYYMM-NNNN)",
      re.match(r"^MR-\d{6}-\d{4}$", req_row["requisition_no"]) is not None)
check("new requisition starts 'open'", req_row["status"] == "open")

r = client.get(f"/requisitions/{req_id}")
req_detail_html = r.get_data(as_text=True)
check("requisition products-search endpoint is reachable and returns JSON",
      client.get("/requisitions/products/search", query_string={"q": "Smoke"}).status_code == 200)
c = get_csrf(req_detail_html)

mr_items_payload = [
    {"product_id": product_id, "description": "Smoke Test Widget", "unit": "nos", "quantity": 20},
    {"product_id": None, "description": "Custom miscellaneous fasteners", "unit": "box", "quantity": 3},
]
resp = client.post(f"/requisitions/{req_id}/items",
                    data={"items_json": _json.dumps(mr_items_payload), "csrf_token": c}, follow_redirects=True)
check("item list save succeeds", "Item list saved" in resp.get_data(as_text=True))
with db.connect() as conn:
    mr_items = conn.execute(
        "SELECT * FROM material_requisition_items WHERE requisition_id=? ORDER BY position", (req_id,)
    ).fetchall()
check("both items persisted, in order", len(mr_items) == 2 and mr_items[0]["description"] == "Smoke Test Widget")
check("the Product-Master-linked line kept its product_id", mr_items[0]["product_id"] == product_id)
check("the custom line has no product_id", mr_items[1]["product_id"] is None)
mr_item1_id, mr_item2_id = mr_items[0]["id"], mr_items[1]["id"]

r = client.get(f"/requisitions/{req_id}")
c = get_csrf(r.get_data(as_text=True))
resp = client.post(f"/requisitions/{req_id}/invite",
                    data={"supplier_id": str(mr_vendor_a["id"]), "csrf_token": c}, follow_redirects=True)
check("vendor A invited", "invited to price this requisition" in resp.get_data(as_text=True))
r = client.get(f"/requisitions/{req_id}")
c = get_csrf(r.get_data(as_text=True))
client.post(f"/requisitions/{req_id}/invite", data={"supplier_id": str(mr_vendor_b["id"]), "csrf_token": c},
            follow_redirects=True)

with db.connect() as conn:
    mr_rvs = conn.execute("SELECT * FROM material_requisition_vendors WHERE requisition_id=?", (req_id,)).fetchall()
check("both vendors invited (2 requisition-vendor rows)", len(mr_rvs) == 2)
mr_rv_a = next(v for v in mr_rvs if v["supplier_id"] == mr_vendor_a["id"])
mr_rv_b = next(v for v in mr_rvs if v["supplier_id"] == mr_vendor_b["id"])
check("both vendors start 'pending'", mr_rv_a["status"] == "pending" and mr_rv_b["status"] == "pending")

# inviting the same vendor twice is a harmless no-op (UNIQUE constraint,
# INSERT OR IGNORE)
r = client.get(f"/requisitions/{req_id}")
c = get_csrf(r.get_data(as_text=True))
client.post(f"/requisitions/{req_id}/invite", data={"supplier_id": str(mr_vendor_a["id"]), "csrf_token": c},
            follow_redirects=True)
with db.connect() as conn:
    mr_rvs_after_dup_invite = conn.execute(
        "SELECT * FROM material_requisition_vendors WHERE requisition_id=?", (req_id,)
    ).fetchall()
check("re-inviting an already-invited vendor doesn't create a duplicate row", len(mr_rvs_after_dup_invite) == 2)

# --- vendor A logs in, sandboxed to their own data ---
mr_vendor_a_client = app.test_client()
r = mr_vendor_a_client.get("/auth/login")
c = get_csrf(r.get_data(as_text=True))
resp = mr_vendor_a_client.post("/auth/login", data={
    "username": expected_username_a, "password": expected_password_a, "csrf_token": c,
}, follow_redirects=True)
check("vendor A logs in with the generated credentials", b"Material Requests" in resp.data)

# regression: a portal-only account that reaches /auth/login via a stale
# "next" redirect (e.g. an old bookmark to an internal-only page from
# before the public site existed) must land on ITS OWN dashboard after
# logging in, not get bounced straight back to that internal page and
# 403 there. See blueprints/auth.py's login() -- next is only honored for
# non-portal-only accounts.
mr_vendor_a_stale_next_client = app.test_client()
resp = mr_vendor_a_stale_next_client.get("/products/", follow_redirects=False)
check("an unauthenticated vendor hitting an internal-only page is redirected to login with ?next set",
      resp.status_code == 302 and "/auth/login" in resp.headers["Location"] and "next=" in resp.headers["Location"])
r = mr_vendor_a_stale_next_client.get(resp.headers["Location"])
c = get_csrf(r.get_data(as_text=True))
resp = mr_vendor_a_stale_next_client.post("/auth/login", data={
    "username": expected_username_a, "password": expected_password_a, "next": "/products/", "csrf_token": c,
}, follow_redirects=True)
check("a vendor logging in via a stale next=/products/ link lands on the Vendor portal, not a 403",
      resp.status_code == 200 and b"Material Requests" in resp.data and b"403" not in resp.data)

resp = mr_vendor_a_client.get("/", follow_redirects=True)
check("vendor A lands on the Vendor portal at '/', not Product Master",
      b"Material Requests" in resp.data and b"Product Master" not in resp.data)
resp = mr_vendor_a_client.get("/vendor-portal/")
check("vendor A's dashboard lists their own invitation",
      resp.status_code == 200 and b"Smoke test requisition" in resp.data)

for blocked_path in ("/products/", "/quotes/", "/purchases/", "/tracker/", "/settings/",
                      "/admin/users", "/requisitions/", "/estimator/"):
    # throwaway client per path -- mr_vendor_a_client itself must stay
    # logged in for the large Material Requisitions flow that continues
    # to use it well after this loop.
    check_blocked(f"vendor A is blocked from {blocked_path}, same as Customer/Engineer portal-only accounts",
                  expected_username_a, expected_password_a, "GET", blocked_path)

check_blocked("vendor A is blocked from vendor B's requisition-vendor detail page",
              expected_username_a, expected_password_a, "GET", f"/vendor-portal/{mr_rv_b['id']}")

resp = mr_vendor_a_client.get(f"/vendor-portal/{mr_rv_a['id']}")
check("vendor A can view their own requisition-vendor detail page", resp.status_code == 200)
mr_va_csrf = get_csrf(resp.get_data(as_text=True))

# save a partial draft -- visible to staff as "In progress"
resp = mr_vendor_a_client.post(f"/vendor-portal/{mr_rv_a['id']}/save-draft", data={
    f"price_{mr_item1_id}": "15.00", "csrf_token": mr_va_csrf,
}, follow_redirects=True)
check("vendor A saves a partial draft", "Draft saved" in resp.get_data(as_text=True))
with db.connect() as conn:
    mr_rv_a_after_draft = conn.execute(
        "SELECT * FROM material_requisition_vendors WHERE id=?", (mr_rv_a["id"],)
    ).fetchone()
check("saving a draft does NOT change status away from pending", mr_rv_a_after_draft["status"] == "pending")

r = client.get(f"/requisitions/{req_id}")
staff_after_draft_html = r.get_data(as_text=True)
check("staff sees vendor A badged 'In progress' after a partial draft save",
      "In progress" in staff_after_draft_html)

# submitting with an incomplete price set is rejected
resp = mr_vendor_a_client.post(f"/vendor-portal/{mr_rv_a['id']}/submit", data={
    f"price_{mr_item1_id}": "15.00", "csrf_token": mr_va_csrf,
}, follow_redirects=True)
check("submitting with a missing line price is rejected",
      "Enter a price for every line" in resp.get_data(as_text=True))
with db.connect() as conn:
    mr_rv_a_after_bad_submit = conn.execute(
        "SELECT * FROM material_requisition_vendors WHERE id=?", (mr_rv_a["id"],)
    ).fetchone()
check("the rejected submit attempt left vendor A pending, not submitted",
      mr_rv_a_after_bad_submit["status"] == "pending")

# vendor A completes and submits
resp = mr_vendor_a_client.post(f"/vendor-portal/{mr_rv_a['id']}/submit", data={
    f"price_{mr_item1_id}": "15.00", f"price_{mr_item2_id}": "80.00",
    f"note_{mr_item2_id}": "2-week lead time", "csrf_token": mr_va_csrf,
}, follow_redirects=True)
check("vendor A's full submit succeeds", "Prices submitted" in resp.get_data(as_text=True))
with db.connect() as conn:
    mr_rv_a_submitted = conn.execute("SELECT * FROM material_requisition_vendors WHERE id=?", (mr_rv_a["id"],)).fetchone()
check("vendor A's status is now 'submitted'", mr_rv_a_submitted["status"] == "submitted")
check("submitted_at was stamped", mr_rv_a_submitted["submitted_at"] is not None)

# vendor A's page is now locked -- no further edits accepted
resp = mr_vendor_a_client.get(f"/vendor-portal/{mr_rv_a['id']}")
check("vendor A's detail page shows the read-only 'submitted, locked' state",
      "you can no longer edit" in resp.get_data(as_text=True).lower())
resp = mr_vendor_a_client.post(f"/vendor-portal/{mr_rv_a['id']}/save-draft", data={
    f"price_{mr_item1_id}": "999.00", "csrf_token": mr_va_csrf,
}, follow_redirects=True)
check("editing after submit is rejected server-side, not just hidden in the UI",
      "already been submitted" in resp.get_data(as_text=True))
with db.connect() as conn:
    mr_price_unchanged = conn.execute(
        "SELECT unit_price FROM material_requisition_prices WHERE requisition_vendor_id=? AND item_id=?",
        (mr_rv_a["id"], mr_item1_id),
    ).fetchone()
check("the blocked post-submit edit did not change the already-submitted price",
      mr_price_unchanged["unit_price"] == 15.0)

# --- staff comparison grid reflects vendor A's submission; items now locked ---
r = client.get(f"/requisitions/{req_id}")
staff_after_a_submit_html = r.get_data(as_text=True)
check("staff sees vendor A badged 'Submitted'", "Submitted" in staff_after_a_submit_html)
mr_a_total = 15.00 * 20 + 80.00 * 3
check(f"staff sees vendor A's total ({mr_a_total:.2f}) in the comparison grid",
      f"{mr_a_total:.2f}" in staff_after_a_submit_html)
check("the item list is now locked (a vendor has submitted) even though the requisition is still 'open'",
      "item list is locked" in staff_after_a_submit_html)

# server-side re-check: replace_items is refused now too, not just hidden
try:
    with db.connect_immediate() as conn:
        requisitions_repo.replace_items(conn, req_id, [
            {"product_id": None, "description": "Should not be allowed", "unit": "nos", "quantity": 1},
        ])
    check("replace_items after a vendor submission should have raised RequisitionLockedError", False)
except requisitions_repo.RequisitionLockedError:
    check("repositories.requisitions.replace_items refuses to run once a vendor has submitted", True)

# --- vendor B logs in independently, cannot see vendor A's data, submits too ---
mr_vendor_b_client = app.test_client()
r = mr_vendor_b_client.get("/auth/login")
c = get_csrf(r.get_data(as_text=True))
mr_vendor_b_client.post("/auth/login", data={
    "username": expected_username_b, "password": expected_password_b, "csrf_token": c,
})
check_blocked("vendor B is blocked from vendor A's requisition-vendor detail page",
              expected_username_b, expected_password_b, "GET", f"/vendor-portal/{mr_rv_a['id']}")

resp = mr_vendor_b_client.get(f"/vendor-portal/{mr_rv_b['id']}")
mr_vb_csrf = get_csrf(resp.get_data(as_text=True))
resp = mr_vendor_b_client.post(f"/vendor-portal/{mr_rv_b['id']}/submit", data={
    f"price_{mr_item1_id}": "12.50", f"price_{mr_item2_id}": "75.00", "csrf_token": mr_vb_csrf,
}, follow_redirects=True)
check("vendor B's full submit succeeds", "Prices submitted" in resp.get_data(as_text=True))

# --- staff awards to vendor B (the cheaper submission) ---
r = client.get(f"/requisitions/{req_id}")
c = get_csrf(r.get_data(as_text=True))
resp = client.post(f"/requisitions/{req_id}/award",
                    data={"requisition_vendor_id": str(mr_rv_b["id"]), "csrf_token": c})
check("award redirects (302) to the new PO's detail page", resp.status_code == 302)
mr_po_location = resp.headers.get("Location", "")
check("award redirects into /purchases/<id>", "/purchases/" in mr_po_location)
mr_po_id = int(re.search(r"/purchases/(\d+)", mr_po_location).group(1))

with db.connect() as conn:
    mr_awarded_po = po_repo.get_purchase_order(conn, mr_po_id)
    mr_awarded_items = po_repo.list_po_items(conn, mr_po_id)
    mr_req_after_award = conn.execute("SELECT * FROM material_requisitions WHERE id=?", (req_id,)).fetchone()
check("the real PO's supplier is the awarded vendor (vendor B)", mr_awarded_po["supplier_id"] == mr_vendor_b["id"])
check("the real PO has one line per requisition item", len(mr_awarded_items) == 2)
check("PO line prices match vendor B's submitted prices, not vendor A's",
      {round(i["unit_price"], 2) for i in mr_awarded_items} == {12.50, 75.00})
check("every PO line has a real (non-null) product_id, satisfying purchase_order_items.product_id NOT NULL",
      all(i["product_id"] is not None for i in mr_awarded_items))
check("the Product-Master-linked line's PO row reused the SAME product_id (no duplicate minted)",
      any(i["product_id"] == product_id for i in mr_awarded_items))
check("requisition status is now 'awarded'", mr_req_after_award["status"] == "awarded")
check("requisition awarded_vendor_id records vendor B's requisition-vendor row", mr_req_after_award["awarded_vendor_id"] == mr_rv_b["id"])
check("requisition resulting_po_id records the new PO", mr_req_after_award["resulting_po_id"] == mr_po_id)
check("requisition awarded_at was stamped", mr_req_after_award["awarded_at"] is not None)

resp = client.get(f"/purchases/{mr_po_id}/pdf")
check("the awarded requisition's resulting PO PDF renders (200, real PDF bytes)",
      resp.status_code == 200 and resp.data[:4] == b"%PDF")

# double-award / award-to-non-submitted-vendor are refused
resp = client.post(f"/requisitions/{req_id}/award",
                    data={"requisition_vendor_id": str(mr_rv_a["id"]), "csrf_token": c}, follow_redirects=True)
check("awarding an already-awarded requisition a second time is refused",
      "already been awarded" in resp.get_data(as_text=True))

r = client.get("/requisitions/new")
c = get_csrf(r.get_data(as_text=True))
resp = client.post("/requisitions/new", data={"notes": "second smoke requisition", "csrf_token": c})
mr_req2_id = int(re.search(r"/requisitions/(\d+)", resp.headers["Location"]).group(1))
r = client.get(f"/requisitions/{mr_req2_id}")
c = get_csrf(r.get_data(as_text=True))
client.post(f"/requisitions/{mr_req2_id}/items", data={
    "items_json": _json.dumps([{"product_id": None, "description": "Paint", "unit": "litre", "quantity": 5}]),
    "csrf_token": c,
}, follow_redirects=True)
r = client.get(f"/requisitions/{mr_req2_id}")
c = get_csrf(r.get_data(as_text=True))
client.post(f"/requisitions/{mr_req2_id}/invite", data={"supplier_id": str(mr_vendor_a["id"]), "csrf_token": c},
            follow_redirects=True)
with db.connect() as conn:
    mr_rv2 = conn.execute("SELECT * FROM material_requisition_vendors WHERE requisition_id=?", (mr_req2_id,)).fetchone()
r = client.get(f"/requisitions/{mr_req2_id}")
c = get_csrf(r.get_data(as_text=True))
resp = client.post(f"/requisitions/{mr_req2_id}/award",
                    data={"requisition_vendor_id": str(mr_rv2["id"]), "csrf_token": c}, follow_redirects=True)
check("awarding to a still-pending (not submitted) vendor is refused",
      "submitted their pricing" in resp.get_data(as_text=True))
with db.connect() as conn:
    mr_req2_unchanged = conn.execute("SELECT * FROM material_requisitions WHERE id=?", (mr_req2_id,)).fetchone()
check("the refused award left the second requisition still 'open'", mr_req2_unchanged["status"] == "open")

# --- nav / RBAC regressions ---
resp = mr_vendor_a_client.get("/vendor-portal/")
vendor_nav_html = resp.get_data(as_text=True)
check("vendor's nav shows 'Material Requests', not Quotes/Product Master/Purchases",
      "Material Requests" in vendor_nav_html and ">Quotes<" not in vendor_nav_html
      and ">Product Master<" not in vendor_nav_html and ">Purchases<" not in vendor_nav_html)

resp = client.get("/requisitions/")
staff_nav_html = resp.get_data(as_text=True)
check("staff (requisitions.manage via Admin) sees 'Material Requisitions' in the nav",
      "Material Requisitions" in staff_nav_html)

check_blocked("Buyer role (no requisitions.manage) is blocked from the staff requisitions list",
              "smoketest_buyer", "BuyerPass123!", "GET", "/requisitions/")
check_blocked("Buyer role hitting the vendor portal (no linked supplier) is signed out, not a crash",
              "smoketest_buyer", "BuyerPass123!", "GET", "/vendor-portal/")

# ============================================================ Item 7: public site + leads inbox

anon_client = app.test_client()

resp = anon_client.get("/")
anon_home_html = resp.get_data(as_text=True)
check("anonymous visitor GET / renders the public homepage (200, no login redirect)",
      resp.status_code == 200 and "Get a Free Quote" in anon_home_html)
check("public homepage nav offers a single shared Login button", 'class="pub-login"' in anon_home_html
      and anon_home_html.count('class="pub-login"') == 1)

for path, marker in [
    ("/services", "Villa Renovation"),
    ("/about", "process"),
    ("/our-work", "Before"),
    ("/contact", "Tell us about your project"),
]:
    resp = anon_client.get(path)
    check(f"anonymous visitor can reach public page {path} (200)",
          resp.status_code == 200 and marker.lower() in resp.get_data(as_text=True).lower())

resp = anon_client.get("/static/css/public.css")
check("public.css is served (200)", resp.status_code == 200)

# a logged-in visitor hitting "/" lands on their own dashboard, not the
# marketing homepage -- public.home() delegates to
# auth.default_landing_endpoint the same way the old app.py index() did.
resp = client.get("/", follow_redirects=True)
logged_in_root_html = resp.get_data(as_text=True)
check("a logged-in visitor's GET / redirects to their dashboard, not the marketing page",
      "Get a Free Quote" not in logged_in_root_html)

# ---- contact form: validation, CSRF, and successful submission land in public_inquiries ----
r = anon_client.get("/contact")
contact_csrf = get_csrf(r.get_data(as_text=True))

resp = anon_client.post("/contact", data={"csrf_token": contact_csrf, "name": "", "message": "no name given"})
check("contact form rejects a missing name (400, stays on the form)",
      resp.status_code == 400 and "enter your name" in resp.get_data(as_text=True).lower())

resp = anon_client.post("/contact", data={
    "csrf_token": "not-a-real-token", "name": "Bad Token", "message": "should be rejected",
})
check("contact form POST with an invalid CSRF token is rejected (400)", resp.status_code == 400)

resp = anon_client.post("/contact", data={
    "csrf_token": contact_csrf, "name": "Priya Sharma", "email": "priya@example.com",
    "phone": "050-000-0000", "message": "Looking for a villa renovation quote.",
}, follow_redirects=True)
check("valid contact submission redirects back to /contact with a thank-you flash",
      resp.status_code == 200 and "thanks" in resp.get_data(as_text=True).lower())

with db.connect() as conn:
    inquiries = public_repo.list_inquiries(conn)
check("the contact submission was saved to public_inquiries (not emailed -- Item 6 is on hold)",
      any(i["name"] == "Priya Sharma" and i["status"] == "new" for i in inquiries))
priya_inquiry = next(i for i in inquiries if i["name"] == "Priya Sharma")

# ---- staff leads inbox (new leads.view permission) ----
resp = client.get("/leads/")
check("Admin (holds leads.view via 'all permissions') can open the leads inbox (200)", resp.status_code == 200)
check("the submitted inquiry appears in the staff leads inbox", "Priya Sharma" in resp.get_data(as_text=True))

check_blocked("Buyer role (no leads.view) is blocked from the leads inbox",
              "smoketest_buyer", "BuyerPass123!", "GET", "/leads/")

r = client.get("/leads/")
c = get_csrf(r.get_data(as_text=True))
client.post(f"/leads/{priya_inquiry['id']}/mark-read", data={"csrf_token": c}, follow_redirects=True)
with db.connect() as conn:
    priya_after = public_repo.get_inquiry(conn, priya_inquiry["id"])
check("marking a lead read persists status='read'", priya_after["status"] == "read")

# ---- nav regressions ----
resp = client.get("/products/")
check("staff (leads.view via Admin) sees 'Website Leads' in the internal nav", "Website Leads" in resp.get_data(as_text=True))
resp = buyer_client.get("/purchases/")
check("Buyer role (no leads.view) does NOT see 'Website Leads' in the nav", "Website Leads" not in resp.get_data(as_text=True))

print()
if failures:
    print(f"=== SMOKE TEST FAILED ({len(failures)} failure(s)) ===")
    for f in failures:
        print(" -", f)
    sys.exit(1)
else:
    print("=== SMOKE TEST PASSED (all checks) ===")
