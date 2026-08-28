"""
One-time migration: import the JSON bundle produced by the "Export all
data" button added to TIMR Dashboard.html (localStorage keys
timr_saved_quotes_v1 / timr_trackers_v1 / timr_quote_counter_v1 /
timr_estimator_rates_v1) into the new quotes/quote_rooms/quote_items/
quote_terms/quote_stage_history/quote_trackers/quote_team_members/
quote_tasks/quote_task_assignments/quote_expenses/estimator_rate_overrides
tables.

Legacy quote items get product_id = NULL always -- they were matched from
the old flat PRICE_ITEMS array by value, not by id, so there's no reliable
id to link back to. The full snapshot (description/unit/brand/qty/price)
is preserved either way.

Legacy team-member/task ids are client-generated strings (e.g.
"id_kx3f8g2a1b"); this script remaps them to the new integer primary keys,
including inside each task's assignedIds list.

Safe to re-run in the sense that it refuses to run at all if `quotes`
already has any rows -- same guard as scripts/migrate_price_items.py.

Run:
    python3 scripts/import_legacy_data.py path/to/timr_export.json <admin_username>

The <admin_username> must already exist (run scripts/bootstrap_admin.py
first if you haven't) -- every imported quote needs a created_by user, and
the old tool never recorded who created what.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402
import repositories.users as users_repo  # noqa: E402
import repositories.quotes as quotes_repo  # noqa: E402
from estimator_constants import RATE_DEFAULTS  # noqa: E402

_SEQ_RE = re.compile(r"(\d+)\s*$")


def _extract_seq(quote_number):
    if not quote_number:
        return None
    m = _SEQ_RE.search(quote_number)
    return int(m.group(1)) if m else None


def load_bundle(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def import_bundle(conn, bundle, user_id):
    quotes = bundle.get("quotes") or {}
    trackers = bundle.get("trackers") or {}
    estimator_rates = bundle.get("estimatorRates") or {}
    legacy_counter = bundle.get("quoteCounter")

    max_seq_seen = 0
    imported_quotes = 0
    imported_tasks = 0
    imported_expenses = 0
    skipped = []

    for quote_number, entry in quotes.items():
        state = entry.get("state") or {}
        rooms = state.get("rooms") or []
        if not quote_number.strip():
            skipped.append((quote_number, "blank quote number"))
            continue

        status = "final" if entry.get("status") == "final" else "draft"
        stage_info = entry.get("progress") or {}
        stage = stage_info.get("stage") or "Quote"
        history = stage_info.get("history") or []
        updated_at = entry.get("updatedAt") or ""

        cur = conn.execute(
            """INSERT INTO quotes (quote_number, status, stage, client_name, project_name, project_type,
                                    location, quote_date, vat_percent, job_notes, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')), COALESCE(?, datetime('now')))""",
            (quote_number, status, stage, state.get("client_name"), state.get("project_name"),
             state.get("project_type"), state.get("location"), state.get("date"),
             state.get("vat_percent") if state.get("vat_percent") is not None else 5,
             state.get("job_notes"), user_id, updated_at or None, updated_at or None),
        )
        quote_id = cur.lastrowid
        imported_quotes += 1

        for pos, room in enumerate(rooms):
            room_cur = conn.execute(
                "INSERT INTO quote_rooms (quote_id, name, notes, position) VALUES (?, ?, ?, ?)",
                (quote_id, room.get("name") or f"Room {pos + 1}", room.get("notes") or "", pos),
            )
            room_id = room_cur.lastrowid
            for ipos, item in enumerate(room.get("items") or []):
                conn.execute(
                    """INSERT INTO quote_items (room_id, product_id, description, unit, brand, qty, unit_price, position)
                       VALUES (?, NULL, ?, ?, ?, ?, ?, ?)""",
                    (room_id, item.get("description") or "", item.get("unit") or "nos", item.get("brand"),
                     item.get("qty") or 0, item.get("unit_price") or 0, ipos),
                )

        for pos, text in enumerate(state.get("terms") or []):
            if text and text.strip():
                conn.execute(
                    "INSERT INTO quote_terms (quote_id, position, text) VALUES (?, ?, ?)",
                    (quote_id, pos, text),
                )

        if status == "final":
            if history:
                for h in history:
                    conn.execute(
                        "INSERT INTO quote_stage_history (quote_id, stage, changed_at) VALUES (?, ?, COALESCE(?, datetime('now')))",
                        (quote_id, h.get("stage") or "Quote", h.get("at")),
                    )
            else:
                conn.execute(
                    "INSERT INTO quote_stage_history (quote_id, stage, changed_at) VALUES (?, 'Quote', COALESCE(?, datetime('now')))",
                    (quote_id, updated_at or None),
                )

        seq = _extract_seq(quote_number)
        if seq is not None:
            max_seq_seen = max(max_seq_seen, seq)

        # ---- tracker (only for finalized quotes with a matching tracker entry) ----
        tracker = trackers.get(quote_number)
        if status == "final" and tracker:
            conn.execute(
                "INSERT INTO quote_trackers (quote_id, created_at, updated_at) VALUES (?, COALESCE(?, datetime('now')), COALESCE(?, datetime('now')))",
                (quote_id, tracker.get("createdAt"), tracker.get("updatedAt")),
            )

            member_id_map = {}
            for m in tracker.get("team") or []:
                mcur = conn.execute(
                    "INSERT INTO quote_team_members (quote_id, name, role, created_at) VALUES (?, ?, ?, COALESCE(?, datetime('now')))",
                    (quote_id, m.get("name") or "Unnamed", m.get("role") or "Other", None),
                )
                member_id_map[m.get("id")] = mcur.lastrowid

            for t in tracker.get("tasks") or []:
                tcur = conn.execute(
                    """INSERT INTO quote_tasks (quote_id, source, room_label, description, weight, deadline,
                                                 status, completed_at, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')))""",
                    (quote_id, t.get("source") or "custom", t.get("room") or None,
                     t.get("description") or "", t.get("weight") or 0, t.get("deadline") or None,
                     "completed" if t.get("status") == "completed" else "pending",
                     t.get("completedAt"), t.get("createdAt")),
                )
                new_task_id = tcur.lastrowid
                imported_tasks += 1
                for legacy_member_id in (t.get("assignedIds") or []):
                    new_member_id = member_id_map.get(legacy_member_id)
                    if new_member_id is not None:
                        conn.execute(
                            "INSERT OR IGNORE INTO quote_task_assignments (task_id, team_member_id) VALUES (?, ?)",
                            (new_task_id, new_member_id),
                        )

            for e in tracker.get("expenses") or []:
                conn.execute(
                    """INSERT INTO quote_expenses (quote_id, description, amount, expense_date, created_by, created_at)
                       VALUES (?, ?, ?, ?, ?, COALESCE(?, datetime('now')))""",
                    (quote_id, e.get("description") or "", e.get("amount") or 0,
                     e.get("date") or (e.get("createdAt") or "")[:10] or None, user_id, e.get("createdAt")),
                )
                imported_expenses += 1

    # ---- quote-number counter: advance to at least the highest imported
    # sequence + 1, and at least the exported localStorage counter value ----
    candidates = [max_seq_seen + 1 if max_seq_seen else 0]
    if isinstance(legacy_counter, int) and legacy_counter > 0:
        candidates.append(legacy_counter)
    new_next_seq = max(candidates) if candidates else 0
    if new_next_seq:
        conn.execute(
            "UPDATE counters SET next_seq = ? WHERE counter_key = 'quote_number' AND next_seq < ?",
            (new_next_seq, new_next_seq),
        )

    # ---- estimator rate overrides: only where they differ from the default ----
    imported_rates = 0
    for key, value in estimator_rates.items():
        if key not in RATE_DEFAULTS:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if value == RATE_DEFAULTS[key]["rate"]:
            continue
        conn.execute(
            """INSERT INTO estimator_rate_overrides (rate_key, rate, updated_by, updated_at)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(rate_key) DO UPDATE SET rate = excluded.rate, updated_by = excluded.updated_by,
                                                    updated_at = datetime('now')""",
            (key, value, user_id),
        )
        imported_rates += 1

    return {
        "quotes": imported_quotes, "tasks": imported_tasks, "expenses": imported_expenses,
        "rate_overrides": imported_rates, "skipped": skipped,
    }


def main():
    if len(sys.argv) < 3:
        print("Usage: python3 scripts/import_legacy_data.py path/to/timr_export.json <admin_username>")
        sys.exit(1)
    export_path = sys.argv[1]
    username = sys.argv[2]

    if not os.path.exists(export_path):
        print(f"Export file not found: {export_path}")
        sys.exit(1)

    with db.connect() as conn:
        existing = conn.execute("SELECT COUNT(*) AS n FROM quotes").fetchone()["n"]
        if existing > 0:
            print(f"Refusing to run: 'quotes' already has {existing} row(s). "
                  "This script is a one-time import and won't double-import.")
            sys.exit(1)
        user = users_repo.get_user_by_username(conn, username)
        if user is None:
            print(f"No user found with username '{username}'. Run scripts/bootstrap_admin.py first, "
                  "or pass the username of an existing user.")
            sys.exit(1)
        user_id = user["id"]

    bundle = load_bundle(export_path)
    print(f"Loaded export from {export_path} (exported {bundle.get('exportedAt', 'unknown time')}).")
    print(f"  {len(bundle.get('quotes') or {})} quote(s), {len(bundle.get('trackers') or {})} tracker(s) in the bundle.")

    with db.connect_immediate() as conn:
        result = import_bundle(conn, bundle, user_id)

    print()
    print(f"Imported {result['quotes']} quote(s), {result['tasks']} task(s), "
          f"{result['expenses']} expense(s), {result['rate_overrides']} rate override(s).")
    if result["skipped"]:
        print(f"Skipped {len(result['skipped'])} entr(y/ies):")
        for number, reason in result["skipped"]:
            print(f"  - {number!r}: {reason}")
    with db.connect() as conn:
        next_seq = conn.execute("SELECT next_seq FROM counters WHERE counter_key='quote_number'").fetchone()["next_seq"]
    print(f"Quote-number counter is now at {next_seq} (next quote will be {quotes_repo.format_quote_number(next_seq)}).")
    print("Done.")


if __name__ == "__main__":
    main()
