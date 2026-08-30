-- Rebrand #3: the app's default brand colors move to a Jira/Atlassian-
-- inspired palette (accent = Atlassian's own product blue, structure =
-- their dark "Nile Blue" navy). Every other neutral tone (backgrounds,
-- borders, text) is fixed in code (static/css/style.css, static/css/
-- public.css, pdf/theme.py) and needs no data migration -- only these two
-- owner-editable columns (plus pdf_font, see below) live in the database.
--
-- Each UPDATE is guarded independently and only fires if that specific
-- column still holds a *default* value this app has ever shipped -- the
-- very original defaults, the Olive & Beige defaults, or the Midnight &
-- Brass defaults -- so an owner who has customized a color in Document
-- Builder (including a value that doesn't match any of this app's own
-- defaults, e.g. picked freehand) keeps it untouched. A site whose colors
-- were already customized before this migration runs will need that
-- customization re-applied by hand in Settings -> Document Builder if the
-- owner wants the new Jira-style blue/navy instead.
UPDATE company_settings SET accent_color_hex = '#0052cc'
  WHERE id = 1 AND accent_color_hex IN ('#c1752a', '#8f5f22', '#805f22');

UPDATE company_settings SET structure_color_hex = '#172b4d'
  WHERE id = 1 AND structure_color_hex IN ('#2e5c7a', '#4a5732', '#2b2823');

-- Jira's UI is entirely sans-serif -- reportlab's built-in "Helvetica" is
-- the closest match and was this app's very first default, before
-- Midnight & Brass moved it to the serif "Times". Guarded the same way:
-- only moves an owner off a stock default this app has shipped, never off
-- a font they picked themselves.
UPDATE company_settings SET pdf_font = 'Helvetica'
  WHERE id = 1 AND pdf_font = 'Times';
