-- TIM RENO unified app -- Phase 2: quotes, rough estimator, project tracker.
-- Replaces the Phase-1 stub quotes_client_link_stub with a real quotes table
-- that carries client_user_id directly (nullable, unused until Phase 3).

DROP TABLE quotes_client_link_stub;

CREATE TABLE quotes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_number    TEXT NOT NULL UNIQUE,          -- TIMR-{year}-{seq:04d}
    status          TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','final')),
    stage           TEXT NOT NULL DEFAULT 'Quote'  CHECK(stage IN ('Quote','Approved','In Progress','Completed','Paid')),
    client_name     TEXT,
    client_user_id  INTEGER REFERENCES users(id),  -- nullable; Phase 3 client-portal hook, unused in Phase 2
    project_name    TEXT,
    project_type    TEXT,
    location        TEXT,
    quote_date      TEXT,                          -- free text as typed, not enforced ISO (matches old tool)
    vat_percent     REAL NOT NULL DEFAULT 5,
    job_notes       TEXT,
    created_by      INTEGER NOT NULL REFERENCES users(id),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_quotes_status ON quotes(status);
CREATE INDEX idx_quotes_stage ON quotes(stage);

CREATE TABLE quote_stage_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id     INTEGER NOT NULL REFERENCES quotes(id) ON DELETE CASCADE,
    stage        TEXT NOT NULL,
    changed_by   INTEGER REFERENCES users(id),
    changed_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_stage_history_quote ON quote_stage_history(quote_id);

CREATE TABLE quote_rooms (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id   INTEGER NOT NULL REFERENCES quotes(id) ON DELETE CASCADE,
    name       TEXT NOT NULL COLLATE NOCASE,
    notes      TEXT,
    position   INTEGER NOT NULL DEFAULT 0,
    UNIQUE(quote_id, name)
);

CREATE TABLE quote_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id      INTEGER NOT NULL REFERENCES quote_rooms(id) ON DELETE CASCADE,
    product_id   INTEGER REFERENCES products(id),   -- NULL for labor/custom items or legacy-imported items
    description  TEXT NOT NULL,                     -- snapshot, same pattern as purchase_order_items
    unit         TEXT NOT NULL,
    brand        TEXT,
    qty          REAL NOT NULL DEFAULT 0,
    unit_price   REAL NOT NULL DEFAULT 0,
    position     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_quote_items_room ON quote_items(room_id);
CREATE INDEX idx_quote_items_product ON quote_items(product_id);

CREATE TABLE quote_terms (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id  INTEGER NOT NULL REFERENCES quotes(id) ON DELETE CASCADE,
    position  INTEGER NOT NULL,
    text      TEXT NOT NULL
);
CREATE INDEX idx_quote_terms_quote ON quote_terms(quote_id);

-- High-water-mark quote numbering. counter_key leaves room for future
-- counters (e.g. a separate PO-number sequence) without a new table.
CREATE TABLE counters (
    counter_key  TEXT PRIMARY KEY,
    next_seq     INTEGER NOT NULL
);
INSERT INTO counters (counter_key, next_seq) VALUES ('quote_number', 88);

-- Marks "this quote's tracker has been seeded" -- existence of this row (not
-- quote_tasks row count) is the seed-once guard: a quote with zero priced
-- rooms legitimately seeds zero tasks and must not re-seed on a later visit
-- once a room gets items.
CREATE TABLE quote_trackers (
    quote_id     INTEGER PRIMARY KEY REFERENCES quotes(id) ON DELETE CASCADE,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE quote_team_members (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id    INTEGER NOT NULL REFERENCES quotes(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    role        TEXT NOT NULL CHECK(role IN ('Labor','Engineer','Supervisor','Contractor','Other')),
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_team_members_quote ON quote_team_members(quote_id);

CREATE TABLE quote_tasks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id       INTEGER NOT NULL REFERENCES quotes(id) ON DELETE CASCADE,
    source         TEXT NOT NULL CHECK(source IN ('room','custom')),
    room_label     TEXT,                            -- snapshot of the room name at seed time; NULL for source='custom'
    description    TEXT NOT NULL,                   -- immutable after creation -- no update endpoint, ever
    weight         REAL NOT NULL DEFAULT 0,          -- immutable after creation -- no update endpoint, ever
    deadline       TEXT,                             -- mutable only while status='pending'
    status         TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','completed')),
    completed_at   TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_quote_tasks_quote ON quote_tasks(quote_id);

-- ON DELETE CASCADE from quote_team_members is what lets removing a team
-- member unassign them from every task, INCLUDING a completed one -- the
-- old tool's one deliberate exception to task-completion immutability.
CREATE TABLE quote_task_assignments (
    task_id          INTEGER NOT NULL REFERENCES quote_tasks(id) ON DELETE CASCADE,
    team_member_id   INTEGER NOT NULL REFERENCES quote_team_members(id) ON DELETE CASCADE,
    PRIMARY KEY (task_id, team_member_id)
);

CREATE TABLE quote_expenses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id      INTEGER NOT NULL REFERENCES quotes(id) ON DELETE CASCADE,
    description   TEXT NOT NULL,
    amount        REAL NOT NULL CHECK(amount >= 0),
    expense_date  TEXT NOT NULL,                    -- ISO YYYY-MM-DD
    created_by    INTEGER REFERENCES users(id),
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_quote_expenses_quote ON quote_expenses(quote_id);

-- Rough Estimator rate sheet: sparse overrides only. RATE_DEFAULTS,
-- SPACE_TEMPLATES, and FINISH_MULTIPLIERS stay as Python constants
-- (estimator_constants.py) -- they were never user-editable in the old tool.
CREATE TABLE estimator_rate_overrides (
    rate_key     TEXT PRIMARY KEY,       -- must match a key in RATE_DEFAULTS
    rate         REAL NOT NULL,
    updated_by   INTEGER REFERENCES users(id),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
