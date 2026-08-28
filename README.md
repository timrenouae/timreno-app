# TIM RENO — Unified App (Phase 1)

This is the foundation of the new unified TIM RENO system: one real
database, staff logins, an admin panel with roles/permissions, the Product
Master (already loaded with your 20,482-item price list), and the Purchase
Order module (create a PO, download a PDF for the supplier, mark stock
received — cost prices update automatically).

The old standalone tools (Quote Builder, Rough Estimator, Project Tracker,
Dashboard) are **not part of this app yet** — porting them so they share
this same database is the natural next step, and worth discussing once
you've had a chance to use this piece.

## First-time setup (on your PC)

1. Install Python 3.10+ if you don't already have it (python.org).
2. Open a terminal/command prompt in this folder and install the few
   packages this app needs:
   ```
   pip install -r requirements.txt
   ```
3. Create the database (this is already done for the copy you were sent —
   `timr.db` already contains your full product list — but run this again
   any time you need to reset or upgrade the schema):
   ```
   python migrate.py
   ```
4. Create your own admin login (only needs to be done once). Replace
   `admin` and `"Mustafa"` below with the username and name you actually
   want — don't type them as shown, they're just an example:
   ```
   python scripts/bootstrap_admin.py admin "Mustafa"
   ```
   It will then ask you to type a password on screen (visible as you
   type — that's expected).
5. Start the app:
   ```
   python app.py
   ```
6. Open http://127.0.0.1:5000 in your browser and sign in.

## What you can do

- **Admin → Users**: create a login for each staff member and assign them
  a role.
- **Admin → Roles**: create roles (e.g. "Purchaser", "Site Manager") and
  tick exactly which permissions each one has. The built-in **Admin** role
  always keeps full access so the account can never be locked out.
- **Product Master**: search, add, and edit products/prices. This is the
  same catalog that was hardcoded in the old Quote Builder — now editable
  from here.
- **Purchases → Suppliers**: add your suppliers.
- **Purchases**: create a purchase order against a supplier, add line
  items from the Product Master, download a PDF to send them, and mark
  lines received as stock arrives — the product's cost price updates
  automatically and every price change is logged.

## Putting this online for your team

This was built and tested without any internet access, so it currently
only runs on your own PC. When you're ready to have your team (or,
later, clients) reach it from anywhere, I'd recommend **PythonAnywhere's
free tier** — no credit card needed, and it keeps your database file
safely in place between visits. I can walk you through setting that up
whenever you're ready; just ask.

## What's next

Once you've tried this, the next piece is folding the Quote Builder,
Rough Estimator, Project Tracker, and Dashboard into this same app and
database — and after that, the client-facing portal (progress tracking,
hidden pricing, change requests) and payment links you asked about, in
that order.
