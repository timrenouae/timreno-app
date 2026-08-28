-- TIM RENO unified app -- initial schema
-- Applied by migrate.py, tracked in schema_migrations.

CREATE TABLE roles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    is_system   INTEGER NOT NULL DEFAULT 0,  -- 1 = protected (the seeded Admin role); cannot be deleted or stripped of core permissions
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE permissions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL UNIQUE,   -- e.g. 'users.manage'
    description TEXT NOT NULL
);

CREATE TABLE role_permissions (
    role_id       INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    permission_id INTEGER NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
    PRIMARY KEY (role_id, permission_id)
);

CREATE TABLE users (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    username            TEXT NOT NULL UNIQUE,
    display_name        TEXT NOT NULL,
    password_hash       TEXT NOT NULL,   -- hex digest
    password_salt       TEXT NOT NULL,   -- hex, unique per user
    pbkdf2_iterations   INTEGER NOT NULL,
    role_id             INTEGER NOT NULL REFERENCES roles(id),
    active              INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_users_role ON users(role_id);

-- Server-side sessions. Only the SHA-256 hash of the raw token is stored;
-- the raw token lives only in the user's cookie. Deleting a row here
-- revokes that session immediately.
CREATE TABLE sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash    TEXT NOT NULL UNIQUE,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at    TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_sessions_user ON sessions(user_id);
CREATE INDEX idx_sessions_expires ON sessions(expires_at);

-- Failed-login tracking for simple rate limiting / lockout, keyed by the
-- submitted username (not user_id, since the username may not exist).
CREATE TABLE login_attempts (
    username        TEXT PRIMARY KEY,
    failed_count    INTEGER NOT NULL DEFAULT 0,
    locked_until    TEXT
);

CREATE TABLE suppliers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    contact_name  TEXT,
    phone         TEXT,
    email         TEXT,
    address       TEXT,
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Product master. Never hard-deleted (purchase_order_items.product_id and
-- future quote_items.product_id reference it) -- use `active` instead.
CREATE TABLE products (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category    TEXT NOT NULL,
    description TEXT NOT NULL,
    unit        TEXT NOT NULL,
    brand       TEXT,
    cost_price  REAL NOT NULL DEFAULT 0,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_products_category ON products(category);
CREATE INDEX idx_products_brand ON products(brand);
CREATE INDEX idx_products_description ON products(description);

-- One row per received PO line (not per PO), so a PO with the same product
-- on two lines still logs two history entries even though only the later
-- one ends up as the product's live cost_price.
CREATE TABLE product_price_history (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id              INTEGER NOT NULL REFERENCES products(id),
    old_price               REAL NOT NULL,
    new_price               REAL NOT NULL,
    source_po_item_id       INTEGER REFERENCES purchase_order_items(id),
    changed_by_user_id      INTEGER REFERENCES users(id),
    changed_at              TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_price_history_product ON product_price_history(product_id);

CREATE TABLE purchase_orders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    po_number     TEXT NOT NULL UNIQUE,
    supplier_id   INTEGER NOT NULL REFERENCES suppliers(id),
    status        TEXT NOT NULL DEFAULT 'draft',  -- draft / sent / partially_received / received
    notes         TEXT,
    created_by    INTEGER NOT NULL REFERENCES users(id),
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE purchase_order_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    po_id         INTEGER NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
    product_id    INTEGER NOT NULL REFERENCES products(id),
    -- Snapshots taken at add-time so the PO PDF/history never silently
    -- change if the product master is edited later.
    description   TEXT NOT NULL,
    unit          TEXT NOT NULL,
    brand         TEXT,
    quantity      REAL NOT NULL,
    unit_price    REAL NOT NULL,
    received_qty  REAL NOT NULL DEFAULT 0,
    received_at   TEXT
);

CREATE INDEX idx_po_items_po ON purchase_order_items(po_id);
CREATE INDEX idx_po_items_product ON purchase_order_items(product_id);

CREATE TABLE audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER REFERENCES users(id),
    action      TEXT NOT NULL,
    entity      TEXT NOT NULL,
    entity_id   INTEGER,
    detail      TEXT,
    at          TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Stubbed now for the Phase 2 client portal: nullable FK so a quote can
-- later be tied to a client-role user. Unused by any code in this pass.
CREATE TABLE quotes_client_link_stub (
    quote_reference   TEXT PRIMARY KEY,
    client_user_id    INTEGER REFERENCES users(id)
);

-- Note: schema_migrations itself is created by migrate.py before any
-- migration file runs, so it is NOT created here (avoids a "table already
-- exists" conflict on the very first migration).
