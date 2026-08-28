-- TIM RENO -- Item 5: Material Requisitions (multi-vendor pricing portal).
--
-- "Vendor" reuses the existing suppliers entity (same pattern as the
-- Customer portal's quotes.client_user_id link) rather than a new table --
-- a company you buy materials from is the same real-world thing whether
-- you're requesting a quote or placing an order. vendor_code is permanent
-- and immutable once set -- enforced in code (repositories/suppliers.py
-- never writes it after creation), not by a DB trigger.

ALTER TABLE suppliers ADD COLUMN vendor_code TEXT;
ALTER TABLE suppliers ADD COLUMN portal_user_id INTEGER REFERENCES users(id);

-- Backfill every existing supplier with a sequential VEND-0001, VEND-0002,
-- ... code, ordered by id, via a correlated COUNT(*) subquery -- no per-row
-- Python loop needed, stays a plain .sql file like every other migration.
UPDATE suppliers
SET vendor_code = 'VEND-' || substr('0000' || (
    SELECT COUNT(*) FROM suppliers s2 WHERE s2.id <= suppliers.id
), -4, 4);

-- Guarantees uniqueness going forward too (repositories/suppliers.py:
-- _next_vendor_code() only ever proposes the next free number, but this
-- index is the real guarantee).
CREATE UNIQUE INDEX idx_suppliers_vendor_code ON suppliers(vendor_code);

-- Master requisition record. awarded_vendor_id references
-- material_requisition_vendors, defined further below in this same script.
-- SQLite resolves a foreign key's target table lazily -- it's only
-- consulted at DML time (once PRAGMA foreign_keys enforcement kicks in),
-- not at CREATE TABLE time -- so this forward reference within one
-- migration file is safe.
CREATE TABLE material_requisitions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    requisition_no    TEXT NOT NULL UNIQUE,           -- MR-YYYYMM-NNNN
    notes             TEXT,                           -- staff instructions
    status            TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','awarded','cancelled')),
    created_by        INTEGER NOT NULL REFERENCES users(id),
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    awarded_vendor_id INTEGER REFERENCES material_requisition_vendors(id),
    resulting_po_id   INTEGER REFERENCES purchase_orders(id),
    awarded_at        TEXT
);

-- The master item list -- no price here (price is per-vendor, see
-- material_requisition_prices below). product_id is nullable: set when a
-- line was picked from the Product Master via search (the common case),
-- left NULL for a free-text "custom item" line (matches quote_items'
-- product_id, which is nullable for exactly the same reason). Editable by
-- staff (add/remove/reorder, wholesale-replace) only while the requisition
-- is still 'open' AND no invited vendor has submitted yet -- enforced in
-- repositories/requisitions.py: replace_items(), not here.
CREATE TABLE material_requisition_items (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    requisition_id INTEGER NOT NULL REFERENCES material_requisitions(id) ON DELETE CASCADE,
    product_id     INTEGER REFERENCES products(id),
    description    TEXT NOT NULL,
    unit           TEXT NOT NULL,
    quantity       REAL NOT NULL,
    position       INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_mr_items_requisition ON material_requisition_items(requisition_id);

-- One row per vendor invited to a requisition. Staff can invite more
-- vendors to an 'open' requisition at any time.
CREATE TABLE material_requisition_vendors (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    requisition_id INTEGER NOT NULL REFERENCES material_requisitions(id) ON DELETE CASCADE,
    supplier_id    INTEGER NOT NULL REFERENCES suppliers(id),
    status         TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','submitted')),
    submitted_at   TEXT,
    UNIQUE (requisition_id, supplier_id)
);

CREATE INDEX idx_mr_vendors_requisition ON material_requisition_vendors(requisition_id);
CREATE INDEX idx_mr_vendors_supplier ON material_requisition_vendors(supplier_id);

-- One row per vendor per item, upserted only when that vendor actually
-- saves a price for that line -- no pre-created empty placeholder rows.
-- The staff comparison grid LEFT JOINs this, so a missing row just renders
-- blank (not yet priced).
CREATE TABLE material_requisition_prices (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    requisition_vendor_id INTEGER NOT NULL REFERENCES material_requisition_vendors(id) ON DELETE CASCADE,
    item_id               INTEGER NOT NULL REFERENCES material_requisition_items(id) ON DELETE CASCADE,
    unit_price            REAL,
    vendor_notes          TEXT,
    UNIQUE (requisition_vendor_id, item_id)
);

CREATE INDEX idx_mr_prices_vendor ON material_requisition_prices(requisition_vendor_id);
CREATE INDEX idx_mr_prices_item ON material_requisition_prices(item_id);
