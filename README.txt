TIM RENO — Midnight & Brass theme (Option 3)
=============================================

WHAT'S IN THIS PACKAGE
-----------------------
15 changed/new files, same relative paths as your app folder — copy each
one over its counterpart in your deployed app:

  templates/base.html
  static/css/style.css
  templates/public/_layout.html
  static/css/public.css
  templates/settings/documents.html
  templates/leads/list.html
  templates/tracker/detail.html
  templates/tracker/list.html
  templates/requisitions/detail.html
  templates/quotes/builder.html
  templates/quotes/list.html
  templates/vendor_portal/detail.html
  templates/purchases/detail.html
  pdf/theme.py
  migrations/0012_midnight_brass_theme.sql   <- NEW FILE

WHAT CHANGED
------------
- Palette: near-black "obsidian" structure color + brass/bronze accent on
  a soft cream background (light mode), inverted for dark mode. Applied
  to the internal app, the public marketing site, and every PDF.
- Fonts: Libre Baskerville (headings) + Source Sans 3 (everything else),
  replacing the old three-font system. Loaded from Google Fonts on the
  web app and public site.
- Headline case: h1 headings now render in natural mixed case (more
  elegant for a serif) instead of forced uppercase. Brand wordmark and
  section-label "spaced caps" styling is kept for structure.
- PDF font: the two owner-editable PDF colors move to the new brass/
  near-black defaults, and the default PDF font moves from Helvetica to
  Times — reportlab's built-in serif, the closest available match to
  Libre Baskerville (see "PDF font note" below).

ONE THING TO KNOW: PDF FONT
----------------------------
Your web app and public website use the real Libre Baskerville font from
Google Fonts. PDFs (Quote / Purchase Order / Rough Estimate) can only use
fonts that are built into every PDF reader, so a PDF can never come out
with missing text — that's a deliberate safeguard already in your app
(see the "PDF font" field in Settings → Document Builder). Libre
Baskerville isn't one of those built-in fonts, so PDFs use "Times"
instead — reportlab's built-in classical serif, and the closest visual
match available. Everything else (the new colors, layout, header) is
identical between the web app and the PDFs.

DEPLOY STEPS
------------
1. Copy the 14 changed files over their existing counterparts in your
   deployed app (GitHub upload, same as previous updates).
2. Add the new file migrations/0012_midnight_brass_theme.sql.
3. Deploy (Render → "Deploy latest commit"). The migration runs
   automatically on startup and updates your two brand colors + PDF font
   default to the new palette — UNLESS you already customized either of
   those in Document Builder, in which case your customization is left
   untouched (same safe pattern as the earlier Olive & Beige update).
4. Open Settings → Document Builder and confirm the colors/font look
   right, then use the "Preview" buttons there to check a live PDF.

VERIFICATION DONE THIS PASS
----------------------------
- Full smoke-test suite: all checks passed against a fresh database with
  this migration applied.
- Visual pass (Playwright, 16 screenshots): public site (home, services,
  about, our work, contact), login, and internal app pages (Product
  Master, Quotes, Quote Builder, Purchases, Tracker, Requisitions,
  Leads, Settings — Company/Document Builder, Admin Users).
- Generated and inspected actual Quote, Purchase Order, and Rough
  Estimate PDFs — new colors and Times serif render correctly.
- Grep-verified zero leftover references to the old font names (Big
  Shoulders Display / IBM Plex) anywhere in the codebase.
