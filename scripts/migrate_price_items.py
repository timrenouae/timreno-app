"""
One-time migration: extract the 20,482-row PRICE_ITEMS array embedded in
TIMR Quote Builder.html and load it into the `products` table.

Each source row is [category, description, unit, cost_price]. Brand is not
a separate field in the source -- it's a trailing "(BRAND)" tag on the
description, e.g. "...150MM (3M)" -> "3M", exactly as already parsed by
the Quote Builder's own BRAND_RE (see TIMR Quote Builder.html, ~line 387).
We reuse that identical pattern here so the imported brand values match
what the existing brand filter already showed users.

Run:
    python3 scripts/migrate_price_items.py [path/to/TIMR Quote Builder.html]

Safe to re-run: it refuses to run if `products` already has rows, so it
can't accidentally double-import.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402
import repositories.products as products_repo  # noqa: E402

BRAND_RE = re.compile(r"\(([^()]+)\)\s*$")

DEFAULT_SOURCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "TIMR Quote Builder.html",
)


def extract_price_items(html_path: str):
    with open(html_path, "r", encoding="utf-8") as f:
        text = f.read()

    match = re.search(r"const PRICE_ITEMS\s*=\s*(\[.*?\]);", text, re.DOTALL)
    if not match:
        raise RuntimeError("Could not find 'const PRICE_ITEMS = [...]' in the source file.")

    array_literal = match.group(1)
    items = json.loads(array_literal)  # the array literal is valid JSON
    return items


def to_product_rows(items):
    rows = []
    for category, description, unit, cost_price in items:
        brand_match = BRAND_RE.search(description)
        brand = brand_match.group(1).strip() if brand_match else None
        rows.append((category, description, unit, brand, float(cost_price)))
    return rows


def main():
    source_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SOURCE
    if not os.path.exists(source_path):
        print(f"Source file not found: {source_path}")
        sys.exit(1)

    print(f"Reading {source_path} ...")
    items = extract_price_items(source_path)
    print(f"Extracted {len(items)} rows.")

    rows = to_product_rows(items)

    with db.connect() as conn:
        existing = conn.execute("SELECT COUNT(*) c FROM products").fetchone()["c"]
        if existing > 0:
            print(f"Refusing to import: products table already has {existing} row(s). "
                  f"Delete them first if you really want to re-import.")
            sys.exit(1)
        products_repo.bulk_insert(conn, rows)
        inserted = conn.execute("SELECT COUNT(*) c FROM products").fetchone()["c"]

    print(f"Inserted {inserted} products.")
    if inserted != len(items):
        print("WARNING: inserted count does not match source row count -- investigate before trusting this import.")
    else:
        print("Row count matches source. Import verified.")


if __name__ == "__main__":
    main()
