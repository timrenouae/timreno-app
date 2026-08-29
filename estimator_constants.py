"""
Rough Estimator constants -- ported VERBATIM from TIMR Rough Estimator.html
(RATE_DEFAULTS ~line 300, SPACE_TEMPLATES ~line 327, FINISH_MULTIPLIERS
~line 203, resolveQty ~line 453). These were never user-editable in the old
tool (only the rate *overrides*, stored in estimator_rate_overrides), so
they stay Python constants rather than DB tables.
"""

FINISH_MULTIPLIERS = {"Basic": 0.85, "Standard": 1.0, "Premium": 1.3}

# key -> {label, unit, rate}
RATE_DEFAULTS = {
    "socket":            {"label": "Socket point (supply + install)",              "unit": "nos", "rate": 60},
    "switch":            {"label": "Switch point (supply + install)",              "unit": "nos", "rate": 40},
    "light_point":       {"label": "Light point / fixture (supply + install)",     "unit": "nos", "rate": 90},
    "ac_point":          {"label": "AC point (electrical + drain provision)",      "unit": "nos", "rate": 250},
    "data_point":        {"label": "Data / TV point",                              "unit": "nos", "rate": 120},
    "door_standard":     {"label": "Door — standard (supply + install)",           "unit": "nos", "rate": 850},
    "door_glass":        {"label": "Door — glass/partition (supply + install)",    "unit": "nos", "rate": 1600},
    "window":            {"label": "Window (supply + install)",                    "unit": "nos", "rate": 900},
    "wall_paint":        {"label": "Wall paint — premium emulsion, 2 coats",       "unit": "sqft", "rate": 8},
    "flooring_tile":     {"label": "Flooring — tiles (supply + install)",          "unit": "sqft", "rate": 45},
    "flooring_wood":     {"label": "Flooring — wood/laminate (supply + install)",  "unit": "sqft", "rate": 65},
    "flooring_carpet":   {"label": "Flooring — carpet tile (supply + install)",    "unit": "sqft", "rate": 35},
    "false_ceiling":     {"label": "False ceiling — gypsum (supply + install)",    "unit": "sqft", "rate": 35},
    "partition_wall":    {"label": "Partition wall — gypsum (supply + install)",   "unit": "sqft", "rate": 55},
    "glass_partition":   {"label": "Glass partition — framed (supply + install)",  "unit": "sqft", "rate": 180},
    "wall_tiling":       {"label": "Wall tiling (supply + install)",               "unit": "sqft", "rate": 60},
    "water_point":       {"label": "Water supply point",                          "unit": "nos", "rate": 180},
    "drainage_point":    {"label": "Drainage point",                              "unit": "nos", "rate": 220},
    "exhaust_point":     {"label": "Exhaust fan point",                           "unit": "nos", "rate": 150},
    "sanitaryware_set":  {"label": "Sanitaryware set (WC/basin/mixer, mid-range)", "unit": "set", "rate": 2200},
    "cabinet_run":       {"label": "Cabinet run — kitchen/pantry (per linear ft)", "unit": "lft", "rate": 450},
    "reception_counter": {"label": "Reception counter (custom built)",            "unit": "lump sum", "rate": 6500},
    "skirting":          {"label": "Skirting (supply + install)",                 "unit": "lft", "rate": 15},
}

# space type -> list of (rate_key, qty_rule). qty_rule: a number = fixed
# default count; 'sqft' = defaults to the space's size; 'sqft_x2' = size*2.
SPACE_TEMPLATES = {
    "Bedroom / Room": [
        ("socket", 6), ("switch", 3), ("light_point", 4), ("ac_point", 1), ("data_point", 1),
        ("door_standard", 1), ("window", 1), ("wall_paint", "sqft"), ("flooring_tile", "sqft"),
    ],
    "Living / Majlis": [
        ("socket", 8), ("switch", 4), ("light_point", 6), ("ac_point", 2), ("data_point", 2),
        ("door_standard", 1), ("window", 2), ("false_ceiling", 0), ("wall_paint", "sqft"), ("flooring_tile", "sqft"),
    ],
    "Kitchen": [
        ("socket", 8), ("switch", 3), ("light_point", 4), ("exhaust_point", 1), ("water_point", 2),
        ("drainage_point", 1), ("cabinet_run", 0), ("wall_tiling", "sqft"), ("flooring_tile", "sqft"),
    ],
    "Pantry": [
        ("socket", 4), ("light_point", 2), ("water_point", 1), ("drainage_point", 1),
        ("cabinet_run", 0), ("wall_tiling", "sqft"), ("flooring_tile", "sqft"), ("false_ceiling", 0),
    ],
    "Washroom / Toilet": [
        ("light_point", 2), ("switch", 1), ("exhaust_point", 1), ("water_point", 3), ("drainage_point", 2),
        ("sanitaryware_set", 1), ("wall_tiling", "sqft_x2"), ("flooring_tile", "sqft"), ("false_ceiling", "sqft"), ("door_standard", 1),
    ],
    "Office Cabin": [
        ("socket", 4), ("switch", 2), ("light_point", 4), ("ac_point", 1), ("data_point", 1),
        ("door_glass", 1), ("partition_wall", 0), ("false_ceiling", "sqft"), ("wall_paint", "sqft"), ("flooring_tile", "sqft"),
    ],
    "Reception": [
        ("socket", 6), ("switch", 3), ("light_point", 8), ("ac_point", 2), ("data_point", 2),
        ("reception_counter", 1), ("glass_partition", 0), ("false_ceiling", "sqft"), ("wall_paint", "sqft"), ("flooring_tile", "sqft"),
    ],
    "Meeting Room": [
        ("socket", 8), ("switch", 3), ("light_point", 6), ("ac_point", 2), ("data_point", 3),
        ("door_glass", 1), ("glass_partition", 0), ("false_ceiling", "sqft"), ("wall_paint", "sqft"), ("flooring_tile", "sqft"),
    ],
    "Open Workstation Area": [
        ("socket", 10), ("switch", 4), ("light_point", 8), ("ac_point", 2), ("data_point", 10),
        ("false_ceiling", "sqft"), ("wall_paint", "sqft"), ("flooring_carpet", "sqft"),
    ],
    "Corridor / Lobby": [
        ("light_point", 4), ("switch", 2), ("socket", 2), ("false_ceiling", "sqft"), ("wall_paint", "sqft"), ("flooring_tile", "sqft"),
    ],
    "Storage Room": [
        ("light_point", 2), ("switch", 1), ("socket", 2), ("wall_paint", "sqft"), ("flooring_tile", "sqft"),
    ],
    "Custom / Other Partition": [],
}


# Quick Estimate mode -- a fast "total sq ft x tier rate = rough total"
# alternative to the detailed item-by-item mode above, for when the ask is
# a ballpark number in minutes rather than a line-by-line breakdown. Rates
# are blended per-sqft figures (not a base rate x finish multiplier like
# RATE_DEFAULTS -- each tier below already bakes in its own scope), split
# by project type since villa and office renovation price very differently
# per sqft in the Dubai market. Defaults set from 2026 Dubai market research
# plus TIM RENO's own confirmed numbers; editable via the same sparse
# rate_key -> rate override table as RATE_DEFAULTS (see
# repositories/estimator_rates.py), just with these dedicated keys.
SQFT_RATE_DEFAULTS = {
    "sqft_house_low":     {"label": "Low (Basic)",      "project_type": "house",  "project_type_label": "House / Villa", "tier": "Low",    "rate": 180},
    "sqft_house_medium":  {"label": "Medium (Standard)", "project_type": "house",  "project_type_label": "House / Villa", "tier": "Medium", "rate": 350},
    "sqft_house_high":    {"label": "High (Premium)",   "project_type": "house",  "project_type_label": "House / Villa", "tier": "High",   "rate": 650},
    "sqft_office_low":    {"label": "Low (Basic)",      "project_type": "office", "project_type_label": "Office",        "tier": "Low",    "rate": 110},
    "sqft_office_medium": {"label": "Medium (Standard)", "project_type": "office", "project_type_label": "Office",        "tier": "Medium", "rate": 300},
    "sqft_office_high":   {"label": "High (Premium)",   "project_type": "office", "project_type_label": "Office",        "tier": "High",   "rate": 600},
}

# Display order for the Quick Estimate tier picker/table -- SQFT_RATE_DEFAULTS
# itself is grouped by project type above (a dict literal), not iteration order.
SQFT_TIERS = ["Low", "Medium", "High"]
SQFT_PROJECT_TYPES = [("house", "House / Villa"), ("office", "Office")]


def resolve_qty(rule, size):
    s = float(size) if size else 0.0
    if rule == "sqft":
        return s
    if rule == "sqft_x2":
        return round(s * 2, 2)
    return float(rule)
