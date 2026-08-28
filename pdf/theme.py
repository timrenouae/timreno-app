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
import os
from types import SimpleNamespace

from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import Table, TableStyle, Paragraph, Spacer, Image

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
# style.css's --ink/--line/--paper-raised tokens.
INK = colors.HexColor("#1b2a38")
INK_SOFT = colors.HexColor("#465361")
LINE = colors.HexColor("#c9cdc5")
PAPER_RAISED = colors.HexColor("#f8f9f7")


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
        accent=colors.HexColor(s["accent_color_hex"] or "#c1752a"),
        structure=colors.HexColor(s["structure_color_hex"] or "#2e5c7a"),
        font=font, font_bold=font_bold,
        bank_lines=bank_lines,
        show_bank_on_quote=bool(s["show_bank_details_on_quote"]) and bool(bank_lines),
        show_bank_on_estimate=bool(s["show_bank_details_on_estimate"]) and bool(bank_lines),
        trailing_order=s["quote_trailing_block_order"],
        estimate_disclaimer=s["estimate_disclaimer_text"],
        default_vat_percent=s["default_vat_percent"] if s["default_vat_percent"] is not None else 5,
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
