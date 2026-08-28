-- TIM RENO -- Item 7: Public marketing website (same domain, same app).
--
-- The public site's contact form saves here rather than depending on
-- Item 6's (on-hold) email delivery -- so "Send a message" actually goes
-- somewhere useful today. Staff with the new leads.view permission (see
-- migrate.py PERMISSIONS) read/triage these from a small internal inbox.

CREATE TABLE public_inquiries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    email      TEXT,
    phone      TEXT,
    message    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    status     TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'read'))
);

CREATE INDEX idx_public_inquiries_created_at ON public_inquiries(created_at DESC);
