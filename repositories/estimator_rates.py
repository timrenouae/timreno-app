"""
SQL access for the Rough Estimator's rate-sheet overrides. RATE_DEFAULTS /
SPACE_TEMPLATES / FINISH_MULTIPLIERS / SQFT_RATE_DEFAULTS live in
estimator_constants.py (never user-editable in the old tool); only
overrides are stored here, and stored genuinely sparse -- a deliberate
small improvement over the old tool, which wrote all 23 values as overrides
on every save regardless of whether they differed from the default.

The same estimator_rate_overrides table (a plain rate_key -> rate sheet)
backs both the detailed item-by-item mode (RATE_DEFAULTS keys, e.g.
"socket") and the Quick Estimate per-sqft mode (SQFT_RATE_DEFAULTS keys,
e.g. "sqft_house_low") -- the key namespaces never collide, so there was no
reason to stand up a second table for six more rows.
"""
from estimator_constants import RATE_DEFAULTS, FINISH_MULTIPLIERS, SQFT_RATE_DEFAULTS


def _default_rate_for_key(key):
    if key in RATE_DEFAULTS:
        return RATE_DEFAULTS[key]["rate"]
    if key in SQFT_RATE_DEFAULTS:
        return SQFT_RATE_DEFAULTS[key]["rate"]
    return None


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


def get_effective_sqft_rates(conn):
    """Returns {project_type: {tier: {key, label, rate}}} for the Quick
    Estimate mode -- no finish multiplier here, since each tier (Low/
    Medium/High) already bakes in its own scope rather than being derived
    from a single base rate."""
    overrides = get_overrides(conn)
    result = {}
    for key, d in SQFT_RATE_DEFAULTS.items():
        rate = overrides.get(key, d["rate"])
        result.setdefault(d["project_type"], {})[d["tier"]] = {"key": key, "label": d["label"], "rate": rate}
    return result


def save_overrides(conn, rate_values: dict, user_id):
    """rate_values: {rate_key: float}. Only upserts a row when the value
    differs from that key's default (RATE_DEFAULTS or SQFT_RATE_DEFAULTS,
    whichever the key belongs to); deletes any existing override that a
    save brings back to the default. Unknown keys are silently ignored."""
    for key, value in rate_values.items():
        default = _default_rate_for_key(key)
        if default is None:
            continue
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


def reset_sqft_overrides(conn):
    """Resets only the Quick Estimate per-sqft rates back to their defaults
    -- kept separate from reset_overrides() so clearing a wrong sqft rate
    can never accidentally wipe out someone's carefully tuned item-level
    rate sheet too."""
    placeholders = ",".join("?" for _ in SQFT_RATE_DEFAULTS)
    conn.execute(
        f"DELETE FROM estimator_rate_overrides WHERE rate_key IN ({placeholders})",
        tuple(SQFT_RATE_DEFAULTS.keys()),
    )
