"""Shared unit-of-measure catalog for the Product Master, Quote Builder
custom items, and Rough Estimator custom items.

Like RATE_DEFAULTS/SPACE_TEMPLATES in estimator_constants.py, this is a
fixed list edited in code, not a DB table. The UI always offers an "Other"
option that reveals a free-text field, so a unit that isn't in this list
(including every unit already stored on an existing product/item from
before this list existed) can still be entered -- nothing already saved is
forced to match this list.
"""

UNIT_OPTIONS = [
    "Nos", "Sq.Ft", "Sq.M", "R.Ft", "Ltr", "Kg", "Set", "Box", "Roll", "Pair", "L.S.", "Hrs",
]

OTHER_UNIT = "Other"
