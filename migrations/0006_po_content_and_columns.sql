-- TIM RENO -- Item 3: Purchase Order content parity (terms & conditions +
-- payment details toggle, matching what Quotes/Estimates already have) and
-- owner-configurable PDF table columns for Quotes and Purchase Orders.
--
-- Every new column below defaults to a value that reproduces today's PDF
-- output exactly, so applying this migration changes nothing visible until
-- the owner edits Document Builder (Settings -> Document builder).
--
-- (Migration 0005 is reserved for a different item building in the same
-- pass -- this item's schema changes all live here, in 0006.)

-- Same toggle pattern as show_bank_details_on_quote/_estimate (see
-- migrations/0004_settings.sql) -- off by default, so applying this
-- migration doesn't suddenly add a payment-details block nobody asked for.
ALTER TABLE company_settings ADD COLUMN show_bank_details_on_po INTEGER NOT NULL DEFAULT 0;

-- Quote room-items table columns, stored as an ordered JSON array of
-- {key, label, enabled}. "brand" is a real field captured on every
-- quote_item (see migrations/0002_quotes.sql) but was never shown on the
-- generated PDF -- included here, disabled by default, so that existing
-- gap becomes a one-click Settings toggle instead of a code change.
-- "description" can never be disabled -- enforced defensively wherever this
-- JSON is parsed (pdf/theme.py: resolve_columns()), not just by this
-- default.
ALTER TABLE company_settings ADD COLUMN quote_table_columns TEXT NOT NULL DEFAULT
    '[{"key":"description","label":"DESCRIPTION","enabled":true},{"key":"qty","label":"QTY","enabled":true},{"key":"unit","label":"UNIT","enabled":true},{"key":"unit_price","label":"UNIT PRICE","enabled":true},{"key":"amount","label":"AMOUNT","enabled":true},{"key":"brand","label":"BRAND","enabled":false}]';

-- Purchase Order items table columns -- matches today's hardcoded PO table
-- exactly (all six columns enabled, same order/labels).
ALTER TABLE company_settings ADD COLUMN po_table_columns TEXT NOT NULL DEFAULT
    '[{"key":"description","label":"DESCRIPTION","enabled":true},{"key":"brand","label":"BRAND","enabled":true},{"key":"unit","label":"UNIT","enabled":true},{"key":"qty","label":"QTY","enabled":true},{"key":"unit_price","label":"UNIT PRICE","enabled":true},{"key":"line_total","label":"LINE TOTAL","enabled":true}]';

-- Purchase Order Terms & Conditions -- same shape as settings_default_terms
-- (migrations/0004_settings.sql), the boilerplate legal text printed as a
-- numbered list on every PO. POs had no terms block at all before this
-- migration, so it starts empty (no terms block renders on a PO until the
-- owner adds some from Settings).
CREATE TABLE settings_po_terms (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    position  INTEGER NOT NULL,
    text      TEXT NOT NULL
);
