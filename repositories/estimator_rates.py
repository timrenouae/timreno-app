"""
SQL access for the Rough Estimator's rate-sheet overrides. RATE_DEFAULTS /
SPACE_TEMPLATES / FINISH_MULTIPLIERS live in estimator_constants.py (never
user-editable in the old tool); only overrides are stored here, and stored
genuinely sparse -- a deliberate small improvement over the old tool, which
wrote all 23 values as overrides on every save regardless of whether they
differed from the default.
"""
from estimator_constants import RATE_DEFAULTS, FINISH_MULTIPLIERS


def get_overrides(conn):
    rows = conn.execute("SELECT rate_key, rate FROM estimator_rate_overrides").fetchall()
    return {r["rate_key"]: r["rate"] for r in rows}


def get_effective_rates(conn, finish_level):
    """Returns {key: {label, unit, base_rate, effective_rate}} for every
    rate in RATE_DEFAULTS, applying any override and the finish-level
    multiplier."""
    overrides = get_overrides(conn)
    mult = FINISH_MULTIPLIERS[finish_level]
    result = {}
    for key, d in RATE_DEFAULTS.items():
        base = overrides.get(key, d["rate"])
        result[key] = {
            "label": d["label"], "unit": d["unit"],
            "base_rate": base,
            "effective_rate": round(base * mult, 2),
        }
    return result


def save_overrides(conn, rate_values: dict, user_id):
    """rate_values: {rate_key: float}. Only upserts a row when the value
    differs from RATE_DEFAULTS[key]['rate']; deletes any existing override
    that a save brings back to the default."""
    for key, value in rate_values.items():
        if key not in RATE_DEFAULTS:
            continue
        default = RATE_DEFAULTS[key]["rate"]
        if value == default:
            conn.execute("DELETE FROM estimator_rate_overrides WHERE rate_key = ?", (key,))
        else:
            conn.execute(
                """INSERT INTO estimator_rate_overrides (rate_key, rate, updated_by, updated_at)
                   VALUES (?, ?, ?, datetime('now'))
                   ON CONFLICT(rate_key) DO UPDATE SET rate = excluded.rate,
                                                        updated_by = excluded.updated_by,
                                                        updated_at = datetime('now')""",
                (key, value, user_id),
            )


def reset_overrides(conn):
    conn.execute("DELETE FROM estimator_rate_overrides")
