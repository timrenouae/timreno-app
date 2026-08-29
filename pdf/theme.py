"""Shared PDF theming, read once per document build from company_settings.

Before this module existed, pdf/quote.py, pdf/purchase_order.py, and
pdf/estimate.py each hardcoded their own (inconsistent -- the PO said
"TIM RENOVATIONS", the other two said "TWO INDIAN MINDS RENOVATIONS LLC")
copy of the company name and the same 4-5 brand colors. get_pdf_context()
is now the single place that turns a company_settings row into everything
a PDF generator needs; build_header() and bank_details_block() are the
two visual pieces genuinely identical across all three documents, so they
live here once instead of three times.
"""
import json
import os
from types import SimpleNamespace

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import (
    Table, TableStyle, Paragraph, Spacer, Image, ListFlowable, ListItem,
)

import config
import repositories.settings as settings_repo

# Limited to reportlab's built-in base-14 fonts (see the CHECK constraint
# in migrations/0004_settings.sql for why) -- (regular, bold) pairs.
_FONT_MAP = {
    "Helvetica": ("Helvetica", "Helvetica-Bold"),
    "Times": ("Times-Roman", "Times-Bold"),
    "Courier": ("Courier", "Courier-Bold"),
}

# Neutral tones stay fixed (not owner-editable) -- only the two brand
# colors (accent/structure) are settings-driven. Matches static/css/
# style.css's light-mode --ink/--line/--paper-raised tokens (Olive & Beige
# -- PDFs are always printed/viewed "light mode", so there's no dark-mode
# counterpart to carry here).
INK = colors.HexColor("#2b2a1e")
INK_SOFT = colors.HexColor("#6e6b52")
LINE = colors.HexColor("#ddd3b8")
PAPER_RAISED = colors.HexColor("#faf8f0")

# ---------------------------------------------------------- table columns
#
# Both the Quote room-items table (pdf/quote.py) and the Purchase Order
# items table (pdf/purchase_order.py) are owner-configurable: which columns
# show, their header text, and their order (see migrations/
# 0006_po_content_and_columns.sql, repositories/settings.py's
# quote_table_columns/po_table_columns). These lists are each document
# type's hardcoded fallback/seed shape -- also the exact shape the
# migration seeds into company_settings, so applying that migration changes
# nothing visible until the owner edits Document Builder.
QUOTE_DEFAULT_COLUMNS = [
    {"key": "description", "label": "DESCRIPTION", "enabled": True},
    {"key": "qty", "label": "QTY", "enabled": True},
    {"key": "unit", "label": "UNIT", "enabled": True},
    {"key": "unit_price", "label": "UNIT PRICE", "enabled": True},
    {"key": "amount", "label": "AMOUNT", "enabled": True},
    {"key": "brand", "label": "BRAND", "enabled": False},
]

PO_DEFAULT_COLUMNS = [
    {"key": "description", "label": "DESCRIPTION", "enabled": True},
    {"key": "brand", "label": "BRAND", "enabled": True},
    {"key": "unit", "label": "UNIT", "enabled": True},
    {"key": "qty", "label": "QTY", "enabled": True},
    {"key": "unit_price", "label": "UNIT PRICE", "enabled": True},
    {"key": "line_total", "label": "LINE TOTAL", "enabled": True},
]

# Every recognized column key across both tables, and its relative width
# weight -- description widest, qty/unit narrowest -- used to turn however
# many columns end up enabled into colWidths that always sum to the
# printable page width (see resolve_columns()).
_COLUMN_WEIGHTS = {
    "description": 40,
    "brand": 16,
    "unit": 11,
    "qty": 9,
    "unit_price": 17,
    "amount": 17,
    "line_total": 17,
}

# Printable table width for A4 with this app's page margins (both
# pdf/quote.py and pdf/purchase_order.py use leftMargin=rightMargin=16mm on
# SimpleDocTemplate) -- computed from the actual page/margin constants
# rather than hardcoded, so it stays correct if either ever changes.
_PAGE_LEFT_RIGHT_MARGIN_MM = 16
AVAILABLE_TABLE_WIDTH_MM = (A4[0] / mm) - (2 * _PAGE_LEFT_RIGHT_MARGIN_MM)


def resolve_columns(columns_json, default_columns, available_width_mm=AVAILABLE_TABLE_WIDTH_MM):
    """Parses a stored column-config JSON string (company_settings.
    quote_table_columns / po_table_columns -- an ordered array of
    {key, label, enabled}) into [(key, label, width_mm), ...] for a PDF
    items table, filtered to enabled columns with proportional widths that
    always sum to `available_width_mm`.

    Falls back to `default_columns` if the JSON is missing/corrupt/empty,
    and "description" is always forced present and enabled -- even against
    a corrupt stored value -- so a table can never render with zero
    columns. Any column key this app doesn't recognize (weight table has no
    entry for it) is dropped rather than trusted, since it can't map to a
    real value on an item dict anyway.
    """
    try:
        parsed = json.loads(columns_json) if columns_json else None
        if not isinstance(parsed, list) or not parsed:
            raise ValueError
    except (TypeError, ValueError):
        parsed = default_columns

    cols = []
    seen_keys = set()
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key")
        if not key or key not in _COLUMN_WEIGHTS or key in seen_keys:
            continue
        seen_keys.add(key)
        label = entry.get("label")
        label = label.strip() if isinstance(label, str) and label.strip() else key.upper()
        cols.append({"key": key, "label": label, "enabled": bool(entry.get("enabled", True))})

    desc = next((c for c in cols if c["key"] == "description"), None)
    if desc is None:
        cols.insert(0, {"key": "description", "label": "DESCRIPTION", "enabled": True})
    else:
        desc["enabled"] = True

    enabled_cols = [c for c in cols if c["enabled"]]
    if not enabled_cols:
        enabled_cols = [c for c in cols if c["key"] == "description"]

    total_weight = sum(_COLUMN_WEIGHTS[c["key"]] for c in enabled_cols)
    widths = [round(available_width_mm * _COLUMN_WEIGHTS[c["key"]] / total_weight, 2) for c in enabled_cols]
    # Absorb rounding drift into the last column so widths sum exactly to
    # the printable width regardless of how many columns are enabled.
    widths[-1] = round(available_width_mm - sum(widths[:-1]), 2)

    return [(c["key"], c["label"], w) for c, w in zip(enabled_cols, widths)]


def get_pdf_context(conn):
    """Reads company_settings and returns a plain object every PDF
    generator pulls company identity, brand colors/font, logo, and
    optional-block content from."""
    s = settings_repo.get_settings(conn)
    font, font_bold = _FONT_MAP.get(s["pdf_font"], _FONT_MAP["Helvetica"])

    logo_path = settings_repo.logo_path(s["logo_filename"])

    bank_lines = []
    if s["bank_account_name"]:
        bank_lines.append(f"Account name: {s['bank_account_name']}")
    if s["bank_name"]:
        bank_lines.append(f"Bank: {s['bank_name']}")
    if s["bank_iban"]:
        bank_lines.append(f"IBAN: {s['bank_iban']}")
    if s["bank_swift"]:
        bank_lines.append(f"SWIFT: {s['bank_swift']}")

    address_lines = [l for l in [s["address_line1"], s["address_line2"]] if l]
    contact_bits = [b for b in [s["phone"], s["email"], s["website"]] if b]

    return SimpleNamespace(
        company_name=s["company_name"] or "Your Company Name",
        company_tagline=s["company_tagline"] or "",
        address_lines=address_lines,
        contact_line=" · ".join(contact_bits),
        trn_number=s["trn_number"],
        logo_path=logo_path,
        ink=INK, ink_soft=INK_SOFT, line=LINE, paper_raised=PAPER_RAISED,
        accent=colors.HexColor(s["accent_color_hex"] or "#8f5f22"),
        structure=colors.HexColor(s["structure_color_hex"] or "#4a5732"),
        font=font, font_bold=font_bold,
        bank_lines=bank_lines,
        show_bank_on_quote=bool(s["show_bank_details_on_quote"]) and bool(bank_lines),
        show_bank_on_estimate=bool(s["show_bank_details_on_estimate"]) and bool(bank_lines),
        show_bank_on_po=bool(s["show_bank_details_on_po"]) and bool(bank_lines),
        trailing_order=s["quote_trailing_block_order"],
        estimate_disclaimer=s["estimate_disclaimer_text"],
        default_vat_percent=s["default_vat_percent"] if s["default_vat_percent"] is not None else 5,
        po_terms=settings_repo.get_po_terms(conn),
        quote_columns=resolve_columns(s["quote_table_columns"], QUOTE_DEFAULT_COLUMNS),
        po_columns=resolve_columns(s["po_table_columns"], PO_DEFAULT_COLUMNS),
    )


def build_header(ctx, styles, doc_label_text, doc_meta_html):
    """The standard 2-column PDF header: company name/tagline/address/
    contact (+ logo, if one is set) on the left; the document label
    (e.g. "QUOTATION") and its meta (number/date) on the right. Shared by
    all three PDF generators."""
    left = []
    if ctx.logo_path:
        try:
            left.append(Image(ctx.logo_path, width=26 * mm, height=26 * mm, kind="proportional"))
            left.append(Spacer(1, 4))
        except Exception:
            pass  # a corrupt/unreadable logo file should never break PDF generation
    left.append(Paragraph(ctx.company_name, styles["company_name"]))
    if ctx.company_tagline:
        left.append(Paragraph(ctx.company_tagline, styles["company_sub"]))
    for line in ctx.address_lines:
        left.append(Paragraph(line, styles["company_sub"]))
    if ctx.contact_line:
        left.append(Paragraph(ctx.contact_line, styles["company_sub"]))
    if ctx.trn_number:
        left.append(Paragraph(f"TRN: {ctx.trn_number}", styles["company_sub"]))

    header = Table(
        [[left, [Paragraph(doc_label_text, styles["doc_label"]), Paragraph(doc_meta_html, styles["doc_meta"])]]],
        colWidths=[110 * mm, 60 * mm],
    )
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 2, ctx.structure),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 10),
    ]))
    return header


def terms_flowables(terms, styles, heading="TERMS &amp; CONDITIONS"):
    """Flowables for a numbered "TERMS & CONDITIONS"-style block. Shared by
    pdf/quote.py (its own Terms & Conditions) and pdf/purchase_order.py
    (Item 3's new PO terms block) so the two don't each carry their own
    copy. `styles` must provide "label" and "term" ParagraphStyles. Returns
    [] when there's nothing to show, so callers can unconditionally
    concatenate this into their story."""
    if not terms:
        return []
    items = [ListItem(Paragraph(t, styles["term"]), leftIndent=10) for t in terms]
    return [
        Spacer(1, 14),
        Paragraph(heading, styles["label"]),
        Spacer(1, 4),
        ListFlowable(items, bulletType="1", leftIndent=14, bulletFontSize=9),
    ]


def bank_details_block(ctx, styles):
    """Flowables for the optional "PAYMENT DETAILS" block. Returns []
    when there's nothing to show, so callers can unconditionally
    concatenate this into their story."""
    if not ctx.bank_lines:
        return []
    return [
        Spacer(1, 14),
        Paragraph("PAYMENT DETAILS", styles["label"]),
        Spacer(1, 4),
        Paragraph("<br/>".join(ctx.bank_lines), styles["value"]),
    ]
