-- TIM RENO -- Phase 4: self-service Settings & Document Builder.
--
-- company_settings is a true singleton: `CHECK (id = 1)` plus the seed row
-- below means there is always exactly one row to read/update, so
-- repositories/settings.py never has to branch on "does a row exist yet".
-- Every column defaults to the value already hardcoded (and, for
-- purchase orders, inconsistently hardcoded -- see below) across
-- pdf/quote.py, pdf/purchase_order.py, and pdf/estimate.py, so applying
-- this migration changes nothing visible until the owner edits a field
-- from the new Settings page.

CREATE TABLE company_settings (
    id                              INTEGER PRIMARY KEY CHECK (id = 1),
    company_name                    TEXT NOT NULL DEFAULT 'TWO INDIAN MINDS RENOVATIONS LLC',
    company_tagline                 TEXT NOT NULL DEFAULT 'Villa Construction · Renovations · Office Fit-out',
    address_line1                   TEXT,
    address_line2                   TEXT,
    phone                           TEXT,
    email                           TEXT,
    website                         TEXT,
    trn_number                      TEXT,
    default_vat_percent             REAL NOT NULL DEFAULT 5,
    bank_name                       TEXT,
    bank_account_name               TEXT,
    bank_iban                       TEXT,
    bank_swift                      TEXT,
    logo_filename                   TEXT,   -- filename only, stored on disk next to the database (see config.UPLOADS_DIR)
    accent_color_hex                TEXT NOT NULL DEFAULT '#c1752a',
    structure_color_hex             TEXT NOT NULL DEFAULT '#2e5c7a',
    -- Limited to reportlab's 3 built-in font families on purpose: every
    -- PDF reader already has these, so this setting can never produce a
    -- PDF with missing/broken text the way embedding an arbitrary font
    -- file could.
    pdf_font                        TEXT NOT NULL DEFAULT 'Helvetica'
                                     CHECK (pdf_font IN ('Helvetica', 'Times', 'Courier')),
    -- Off by default so applying this migration doesn't suddenly add a
    -- payment-details block nobody asked for yet.
    show_bank_details_on_quote      INTEGER NOT NULL DEFAULT 0,
    show_bank_details_on_estimate   INTEGER NOT NULL DEFAULT 0,
    estimate_disclaimer_text        TEXT NOT NULL DEFAULT
        ('This is a rough, non-binding estimate for early budgeting and planning purposes only. ' ||
         'Actual pricing may vary once a full itemized quotation is prepared.'),
    -- Only two optional trailing blocks exist on a quote (Terms and, once
    -- enabled, Payment Details), so a simple two-way switch covers
    -- "reorder sections" without a general-purpose ordering table.
    quote_trailing_block_order      TEXT NOT NULL DEFAULT 'terms_then_bank'
                                     CHECK (quote_trailing_block_order IN ('terms_then_bank', 'bank_then_terms')),
    updated_by                      INTEGER REFERENCES users(id),
    updated_at                      TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO company_settings (id) VALUES (1);

-- Replaces repositories/quotes.py's hardcoded DEFAULT_TERMS list. Same
-- shape as quote_terms (position + text), seeded with the current five
-- lines so a brand-new quote's editor prefills identically to today until
-- the owner edits these from Settings.
CREATE TABLE settings_default_terms (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    position  INTEGER NOT NULL,
    text      TEXT NOT NULL
);

INSERT INTO settings_default_terms (position, text) VALUES
    (0, 'This quotation is valid for 30 days from the date of issue.'),
    (1, '50% advance payment is required to commence work; the balance is due on completion.'),
    (2, 'Prices exclude government permits, NOC, or authority approvals unless stated otherwise.'),
    (3, 'Any changes to scope after approval will be quoted and agreed separately.'),
    (4, 'Delivery timelines will be confirmed on order confirmation and may vary with site conditions.');
