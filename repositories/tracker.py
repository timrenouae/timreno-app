"""
SQL access for the Project Tracker: team members, tasks (with server-side
completion immutability), expenses, weighted progress, and budget.

Ported from TIMR Project Tracker.html. See
/root/.claude/plans/iridescent-plotting-umbrella.md for design rationale.
"""
import repositories.quotes as quotes_repo

ROLES = ["Labor", "Engineer", "Supervisor", "Contractor", "Other"]


class TaskNotFoundError(Exception):
    pass


class TaskImmutableError(Exception):
    """Raised when trying to change a completed task's deadline/assignment,
    or delete it. The old tool enforces this twice client-side (a disabled
    attribute AND a defensive early-return in the click handler) -- that's
    a real requirement, not incidental UI polish, so it's enforced here."""
    pass


class TaskAlreadyCompletedError(Exception):
    pass


# ------------------------------------------------------------------ seeding

def is_tracker_seeded(conn, quote_id) -> bool:
    return conn.execute("SELECT 1 FROM quote_trackers WHERE quote_id = ?", (quote_id,)).fetchone() is not None


def ensure_tracker_seeded(conn, quote_id):
    """Must run inside db.connect_immediate(). Idempotent -- seeds exactly
    once per quote, guarded by quote_trackers row existence (NOT by
    quote_tasks row count, since a quote with zero priced rooms legitimately
    seeds zero tasks and must not re-seed later once a room gets items).

    One task per quote_items row (not per room -- installers tick off each
    item as it goes in, so progress reflects individual items, not whole
    rooms/cabins), weight = that item's pre-VAT line total. room_label is
    kept on each task purely for display grouping in the UI."""
    if is_tracker_seeded(conn, quote_id):
        return

    rooms = conn.execute(
        "SELECT id, name FROM quote_rooms WHERE quote_id = ? ORDER BY position, id", (quote_id,)
    ).fetchall()
    for room in rooms:
        items = conn.execute(
            "SELECT id, description, qty, unit_price FROM quote_items WHERE room_id = ? ORDER BY position, id",
            (room["id"],),
        ).fetchall()
        for item in items:
            weight = (item["qty"] or 0) * (item["unit_price"] or 0)
            conn.execute(
                """INSERT INTO quote_tasks (quote_id, source, room_label, item_id, description, weight, status)
                   VALUES (?, 'item', ?, ?, ?, ?, 'pending')""",
                (quote_id, room["name"], item["id"], item["description"], weight),
            )

    conn.execute("INSERT INTO quote_trackers (quote_id) VALUES (?)", (quote_id,))


# --------------------------------------------------------------------- team

def list_team(conn, quote_id):
    return conn.execute(
        "SELECT * FROM quote_team_members WHERE quote_id = ? ORDER BY name", (quote_id,)
    ).fetchall()


def add_team_member(conn, quote_id, name, role):
    cur = conn.execute(
        "INSERT INTO quote_team_members (quote_id, name, role) VALUES (?, ?, ?)",
        (quote_id, name, role),
    )
    return cur.lastrowid


def remove_team_member(conn, member_id):
    """No task-status guard here, deliberately: ON DELETE CASCADE on
    quote_task_assignments unassigns this member from every task,
    INCLUDING completed ones -- the old tool's one deliberate exception to
    task-completion immutability (a data-integrity cleanup, not a task
    edit)."""
    conn.execute("DELETE FROM quote_team_members WHERE id = ?", (member_id,))


# --------------------------------------------------------------------- tasks

def list_tasks(conn, quote_id):
    tasks = conn.execute(
        "SELECT * FROM quote_tasks WHERE quote_id = ? ORDER BY id", (quote_id,)
    ).fetchall()
    result = []
    for t in tasks:
        assigned = conn.execute(
            """SELECT tm.id, tm.name, tm.role FROM quote_task_assignments a
               JOIN quote_team_members tm ON tm.id = a.team_member_id
               WHERE a.task_id = ?""",
            (t["id"],),
        ).fetchall()
        d = dict(t)
        d["assigned"] = [dict(a) for a in assigned]
        result.append(d)
    return result


def add_custom_task(conn, quote_id, description, weight):
    cur = conn.execute(
        """INSERT INTO quote_tasks (quote_id, source, room_label, description, weight, status)
           VALUES (?, 'custom', NULL, ?, ?, 'pending')""",
        (quote_id, description, weight or 0),
    )
    return cur.lastrowid


def _get_task(conn, task_id):
    task = conn.execute("SELECT * FROM quote_tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise TaskNotFoundError(f"Task {task_id} not found")
    return task


def set_task_deadline(conn, task_id, new_deadline):
    """Must run inside db.connect_immediate()."""
    task = _get_task(conn, task_id)
    if task["status"] == "completed":
        raise TaskImmutableError("Deadline is locked once a task is completed.")
    conn.execute(
        "UPDATE quote_tasks SET deadline = ?, updated_at = datetime('now') WHERE id = ?",
        (new_deadline, task_id),
    )


def toggle_task_assignment(conn, task_id, member_id):
    """Must run inside db.connect_immediate() -- re-checks task status at
    write time, closing the same race the old tool's defensive
    'if task.status===completed return' click-handler guard was aimed at
    (there it was just belt-and-braces on a single-user page; here it's a
    real guard against a second browser tab)."""
    task = _get_task(conn, task_id)
    if task["status"] == "completed":
        raise TaskImmutableError("Assignments are locked once a task is completed.")
    existing = conn.execute(
        "SELECT 1 FROM quote_task_assignments WHERE task_id = ? AND team_member_id = ?",
        (task_id, member_id),
    ).fetchone()
    if existing:
        conn.execute(
            "DELETE FROM quote_task_assignments WHERE task_id = ? AND team_member_id = ?",
            (task_id, member_id),
        )
    else:
        conn.execute(
            "INSERT INTO quote_task_assignments (task_id, team_member_id) VALUES (?, ?)",
            (task_id, member_id),
        )


def complete_task(conn, task_id):
    """Must run inside db.connect_immediate(). Idempotent guard: a second
    completion attempt (double-submit) is rejected rather than overwriting
    completed_at."""
    task = _get_task(conn, task_id)
    if task["status"] == "completed":
        raise TaskAlreadyCompletedError("This task has already been marked completed.")
    conn.execute(
        """UPDATE quote_tasks SET status = 'completed', completed_at = datetime('now'),
                                   updated_at = datetime('now') WHERE id = ?""",
        (task_id,),
    )


def delete_task(conn, task_id):
    """Must run inside db.connect_immediate()."""
    task = _get_task(conn, task_id)
    if task["status"] == "completed":
        raise TaskImmutableError("A completed task can't be deleted.")
    conn.execute("DELETE FROM quote_tasks WHERE id = ?", (task_id,))


# ------------------------------------------------------------------ expenses

def list_expenses(conn, quote_id):
    return conn.execute(
        "SELECT * FROM quote_expenses WHERE quote_id = ? ORDER BY expense_date DESC, id DESC", (quote_id,)
    ).fetchall()


def add_expense(conn, quote_id, description, amount, expense_date, user_id):
    cur = conn.execute(
        """INSERT INTO quote_expenses (quote_id, description, amount, expense_date, created_by)
           VALUES (?, ?, ?, ?, ?)""",
        (quote_id, description, amount, expense_date, user_id),
    )
    return cur.lastrowid


def delete_expense(conn, expense_id):
    conn.execute("DELETE FROM quote_expenses WHERE id = ?", (expense_id,))


# ------------------------------------------------------------- calculations

def compute_progress(tasks):
    """Weighted-by-task-value % complete, falling back to a plain
    done/total percentage when every task's weight is 0 (e.g. an
    all-custom-milestone tracker with no monetary weights) -- matches the
    old tool's computeProgress exactly, including that fallback branch."""
    if not tasks:
        return {"pct": 0, "done": 0, "total": 0}
    total_weight = sum(t["weight"] or 0 for t in tasks)
    done_weight = sum(t["weight"] or 0 for t in tasks if t["status"] == "completed")
    done = sum(1 for t in tasks if t["status"] == "completed")
    if total_weight > 0:
        pct = done_weight / total_weight * 100
    else:
        pct = done / len(tasks) * 100
    return {"pct": pct, "done": done, "total": len(tasks)}


def group_tasks_by_room(tasks):
    """Groups tasks by room_label for checklist display (Tracker detail
    page and the customer portal), with custom tasks (room_label is None)
    bucketed last under a synthetic heading. Written by hand rather than
    via Jinja's `groupby` filter, which sorts with Python's sorted() and
    raises a TypeError on a mix of None and str keys."""
    grouped = {}
    room_order = []
    for t in tasks:
        label = t["room_label"]
        if label not in grouped:
            grouped[label] = []
            if label is not None:
                room_order.append(label)
        grouped[label].append(t)
    ordered_keys = room_order + ([None] if None in grouped else [])
    return [(k, grouped[k]) for k in ordered_keys]


def compute_budget(anticipated, expenses):
    actual = sum(e["amount"] or 0 for e in expenses)
    return {"anticipated": anticipated, "actual": actual, "variance": anticipated - actual}


def list_tracked_quotes_with_stats(conn):
    """For the Tracker list page: every FINAL quote (drafts never appear
    here, matching the old tool), with progress/budget columns where a
    tracker exists, or 'Not started' equivalents where it doesn't."""
    quotes = quotes_repo.list_quotes(conn, status="final")
    result = []
    for q in quotes:
        full = quotes_repo.get_quote_full(conn, q["id"])
        totals = quotes_repo.compute_totals(full["rooms"], q["vat_percent"])
        has_tracker = is_tracker_seeded(conn, q["id"])
        progress = None
        budget = None
        if has_tracker:
            tasks = list_tasks(conn, q["id"])
            progress = compute_progress(tasks)
            expenses = list_expenses(conn, q["id"])
            budget = compute_budget(totals["grand"], expenses)
        result.append({"quote": dict(q), "totals": totals, "has_tracker": has_tracker,
                        "progress": progress, "budget": budget})
    return result
