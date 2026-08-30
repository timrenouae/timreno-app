-- Rebrand #2: the app's default brand colors move from Olive & Beige to
-- Midnight & Brass (accent = brass/bronze, structure = warm near-black
-- "obsidian"). Every other neutral tone (backgrounds, borders, text) is
-- fixed in code (static/css/style.css, static/css/public.css,
-- pdf/theme.py) and needs no data migration -- only these two
-- owner-editable columns (plus pdf_font, see below) live in the database.
--
-- Each UPDATE is guarded independently and only fires if that specific
-- column still holds a *default* value this app has ever shipped -- either
-- the very original default (a site that never applied
-- 0011_olive_beige_theme.sql) or the Olive & Beige default (a site that
-- did) -- so an owner who already customized a color in Document Builder
-- keeps their choice untouched either way.
UPDATE company_settings SET accent_color_hex = '#805f22'
  WHERE id = 1 AND accent_color_hex IN ('#c1752a', '#8f5f22');

UPDATE company_settings SET structure_color_hex = '#2b2823'
  WHERE id = 1 AND structure_color_hex IN ('#2e5c7a', '#4a5732');

-- Midnight & Brass pairs a classical serif (Libre Baskerville) on the web
-- app/public site; reportlab's built-in fonts don't include it, so "Times"
-- (Times-Roman/Times-Bold) is the closest available serif for PDFs --
-- nicer alongside the new palette than the old sans-serif "Helvetica"
-- default. Guarded the same way: only moves an owner off the untouched
-- factory default, never off a font they picked themselves.
UPDATE company_settings SET pdf_font = 'Times'
  WHERE id = 1 AND pdf_font = 'Helvetica';
