# TIM RENO — Unified App

One Flask app, one SQLite database, covering the whole business: staff
logins with roles/permissions, the Product Master, Quotes, a Rough
Estimator, Purchase Orders, the Project Tracker, a Customer Portal, an
Engineer login for on-site task completion with photo proof, Material
Requisitions (multi-vendor pricing with its own Vendor Portal), a
Settings / Document Builder for company info and PDF branding, and the
public marketing website at your domain's root.

## First-time setup (running it on your own PC)

1. Install Python 3.10+ if you don't already have it (python.org).
2. Open a terminal in this folder and install the packages the app needs:
   ```
   pip install -r requirements.txt
   ```
3. Create the database:
   ```
   python migrate.py
   ```
4. Create your own admin login (only needs to be done once per database):
   ```
   python scripts/bootstrap_admin.py admin "Your Name"
   ```
   It will ask you to type a password on screen (visible as you type —
   that's expected).
5. Start the app:
   ```
   python app.py
   ```
6. Open http://127.0.0.1:5000 in your browser and sign in.

## Deploying (Render)

This app is designed to run as-is on Render (or any host that runs a
Python web process plus a persistent disk). Set these environment
variables in the dashboard:

- `TIMR_SECRET_KEY` — a long random string, kept stable across restarts
  (changing it logs everyone out). Generate one once and never change it.
- `TIMR_DB_PATH` — path to the database file on your persistent Disk,
  e.g. `/var/data/timr.db`. `TIMR_UPLOADS_DIR` defaults to the same
  folder, so logos and task photos land there too and survive redeploys.
- `TIMR_COOKIE_SECURE` — leave unset (defaults to on, correct behind
  HTTPS).
- `ANTHROPIC_API_KEY` — only needed for the Quote Builder's "Import from
  Drawing" feature (reads a floor plan and suggests a room list). Optional
  — everything else works without it. `TIMR_DRAWING_MODEL` lets you pick
  a different Claude model for this if you ever want to (defaults to a
  sensible one).

On every redeploy, **your database is untouched** — it lives on the
persistent Disk (`TIMR_DB_PATH`), never inside the git-deployed code, so
uploading a fresh copy of this codebase to GitHub and redeploying does
not affect your live data. `migrate.py` runs automatically on startup and
only ever adds new tables/columns; it never deletes data.

### Moving your data onto a brand-new deploy

If you're ever standing up a fresh Render service from scratch (not just
redeploying this same one), `blueprints/restore.py` has a one-time
upload form for pushing a `.db` file from your desktop onto it — set the
`TIMR_RESTORE_TOKEN` environment variable temporarily, visit
`/setup/restore-db?token=<that value>`, upload the file, then remove the
environment variable again. Not needed for a normal code update.

## What's inside

- **Left sidebar navigation**: icon + label links to every module you
  have access to, with a slim top header for your name/role and Log out.
  Collapses to a hamburger-triggered drawer on narrower screens.
- **Admin → Users / Roles**: create a login per staff member and assign
  a role. Roles are self-service — tick exactly which permissions each
  one has from the full list, including the Engineer and Vendor portal
  permissions described below. The built-in **Admin** role always keeps
  full access.
- **Product Master**: search, add, and edit the materials/pricing
  catalog shared by Quotes and Purchase Orders.
- **Quotes**: room-by-room quote builder, searches the Product Master or
  add custom line items, plus "Import from Drawing" (upload a floor
  plan, photo, or sketch and get a suggested room list to price).
- **Rough Estimator**: a fast, non-binding ballpark estimate PDF.
- **Purchases**: suppliers, purchase orders with product search, payment
  terms, delivery info, a DRAFT watermark until sent, an authorized-by
  signature line, and a one-click "duplicate this PO" for repeat orders.
- **Project Tracker**: tasks per finalized quote, budget vs. actual, and
  an **Engineer** login (a simplified checklist-only view, no pricing)
  that requires a photo before marking a task complete — the photo then
  shows up on the customer's own portal dashboard.
- **Customer Portal**: a client's own login to track their project's
  progress without seeing internal pricing/budget detail.
- **Material Requisitions**: send one item list to several vendors at
  once for side-by-side price comparison, via a restricted **Vendor**
  login (auto-generated credentials, two-phase save-draft/submit), then
  award the winning vendor straight into a real Purchase Order.
- **Settings → Company info / Document Builder**: company details, bank
  details, brand colors (type or paste a hex code, or pick visually — see
  below), PDF font, optional PDF content blocks, and fully configurable
  table columns on Quote and PO PDFs.
- **Public website**: the marketing site at your domain's root (Home,
  Services, About, Our Work, Contact) — pulls company name/logo/colors
  live from Settings, and the contact form saves to **Website Leads**
  for staff to follow up on.

### Brand colors (Settings → Document Builder)

Only two colors in the whole app are editable without a code change —
**Accent color** and **Structure color** — and they control two things:
your **public website** and every **PDF**. The rest of the software (the
actual internal app staff log into) is not affected by these fields; its
palette is fixed in the code itself, currently a Jira/Atlassian-inspired
blue (`#0052cc`) and dark navy (`#172b4d`) on a light gray canvas, set in
`static/css/style.css`. Each Document Builder field has a swatch you can
click to pick visually, and a text box beside it where you can type or
paste a hex code directly (e.g. `0052cc`) — both stay in sync, and an
invalid value is flagged and won't save.

## Tests

```
python tests/smoke_test.py
```

Runs the full functional test suite end-to-end against a throwaway
database (set via `TIMR_DB_PATH` inside the script) — no effect on your
real data.
