-- TIM RENO -- switch tracker seeding from one task per room to one task
-- per item, so installers tick off individual items rather than whole
-- rooms/cabins. Adds an item_id link (nullable -- custom tasks and
-- legacy-imported item tasks have none) and widens the source CHECK to
-- allow 'item' alongside the existing 'room'/'custom' values.
--
-- SQLite can't ALTER a CHECK constraint or add a REFERENCES column with a
-- constraint in place, so the table is recreated. foreign_keys is turned
-- off for the duration so dropping the old quote_tasks (still referenced
-- by quote_task_assignments) doesn't trip FK enforcement; migrate.py
-- always runs this as its own script/transaction, and turns foreign_keys
-- back on immediately after.

PRAGMA foreign_keys = OFF;

CREATE TABLE quote_tasks_new (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id       INTEGER NOT NULL REFERENCES quotes(id) ON DELETE CASCADE,
    source         TEXT NOT NULL CHECK(source IN ('room','item','custom')),
    room_label     TEXT,                            -- snapshot of the room name at seed time; NULL for source='custom'
    item_id        INTEGER REFERENCES quote_items(id) ON DELETE SET NULL,  -- NULL for source in ('room','custom')
    description    TEXT NOT NULL,                   -- immutable after creation -- no update endpoint, ever
    weight         REAL NOT NULL DEFAULT 0,          -- immutable after creation -- no update endpoint, ever
    deadline       TEXT,                             -- mutable only while status='pending'
    status         TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','completed')),
    completed_at   TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO quote_tasks_new (id, quote_id, source, room_label, description, weight, deadline,
                              status, completed_at, created_at, updated_at)
SELECT id, quote_id, source, room_label, description, weight, deadline,
       status, completed_at, created_at, updated_at
FROM quote_tasks;

DROP TABLE quote_tasks;
ALTER TABLE quote_tasks_new RENAME TO quote_tasks;

CREATE INDEX idx_quote_tasks_quote ON quote_tasks(quote_id);
CREATE INDEX idx_quote_tasks_item ON quote_tasks(item_id);

PRAGMA foreign_keys = ON;
