-- Rebrand: the app's default brand colors move from orange/blue to an
-- Olive & Beige palette (accent = antique gold/bronze, structure = deep
-- olive green). Every other neutral tone (backgrounds, borders, text) is
-- fixed in code (static/css/style.css, pdf/theme.py) and needs no data
-- migration -- only these two owner-editable columns live in the
-- database.
--
-- Each UPDATE is guarded independently and only fires if that specific
-- column still holds its original default value, so an owner who already
-- customized just one of the two colors in Document Builder keeps their
-- choice untouched.
UPDATE company_settings SET accent_color_hex = '#8f5f22'
  WHERE id = 1 AND accent_color_hex = '#c1752a';

UPDATE company_settings SET structure_color_hex = '#4a5732'
  WHERE id = 1 AND structure_color_hex = '#2e5c7a';
