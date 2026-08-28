"""
Purchase-order PDF, built with reportlab's Platypus flowables
(SimpleDocTemplate / Table / Paragraph) rather than raw canvas.drawString
coordinates -- Platypus handles text wrapping and row height itself, which
is what avoids the overlap/alignment bugs the manual-coordinate approach
produced elsewhere in this project earlier.

Company identity, brand colors/font, logo, the owner-configurable items
table columns, the optional Terms & Conditions/Payment Details blocks, all
come from a pdf.theme.PdfContext (see pdf/theme.py) built from the live
company_settings row, via the same shared header/terms/bank-details helpers
the quote and estimate PDFs use -- previously this file hardcoded a
different, inconsistent company name ("TIM RENOVATIONS") than the other two
documents, and had no settings-driven content of its own at all (Item 3).
"""
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

import pdf.theme as theme

# Item 4: faint diagonal "DRAFT" overlay, active only while the PO's status
# is 'draft'. A light neutral gray (not ctx.structure/ctx.accent, which
# would clash with the owner's brand colors) drawn on the canvas layer
# underneath the normal flowable content -- pure decoration, so it carries
# none of the layout/wrapping risk a Platypus flowable would.
_WATERMARK_COLOR = colors.Color(0.6, 0.6, 0.6, alpha=0.30)


def _draft_watermark(canvas_obj, doc):
    canvas_obj.saveState()
    canvas_obj.setFont("Helvetica-Bold", 72)
    canvas_obj.setFillColor(_WATERMARK_COLOR)
    canvas_obj.translate(A4[0] / 2, A4[1] / 2)
    canvas_obj.rotate(45)
    canvas_obj.drawCentredString(0, 0, "DRAFT")
    canvas_obj.restoreState()


def _no_watermark(canvas_obj, doc):
    pass


def _styles(ctx):
    base = getSampleStyleSheet()
    return {
        "company_name": ParagraphStyle("CompanyName", parent=base["Normal"], fontSize=14, textColor=ctx.ink,
                                        fontName=ctx.font_bold, leading=17),
        "company_sub": ParagraphStyle("CompanySub", parent=base["Normal"], fontSize=9, textColor=colors.grey,
                                       fontName=ctx.font, leading=12),
        "doc_label": ParagraphStyle("DocLabel", parent=base["Normal"], fontSize=16, textColor=ctx.structure,
                                     fontName=ctx.font_bold, alignment=2, leading=19),
        "doc_meta": ParagraphStyle("DocMeta", parent=base["Normal"], fontSize=9, textColor=ctx.ink_soft,
                                    fontName=ctx.font, alignment=2, leading=13),
        "label": ParagraphStyle("Label", parent=base["Normal"], fontSize=8, textColor=colors.grey,
                                 fontName=ctx.font, leading=10),
        "value": ParagraphStyle("Value", parent=base["Normal"], fontSize=10, textColor=ctx.ink,
                                 fontName=ctx.font, leading=13),
        "cell": ParagraphStyle("Cell", parent=base["Normal"], fontSize=9, textColor=ctx.ink,
                                fontName=ctx.font, leading=12),
        "header_cell": ParagraphStyle("HeaderCell", parent=base["Normal"], fontSize=8.5, textColor=colors.white,
                                       fontName=ctx.font, leading=11),
        "term": ParagraphStyle("Term", parent=base["Normal"], fontSize=9, textColor=ctx.ink_soft,
                                fontName=ctx.font, leading=14),
    }


def build_purchase_order_pdf(ctx, po: dict, items: list) -> bytes:
    """ctx: a pdf.theme PdfContext (see pdf/theme.py: get_pdf_context()).
    po: dict with po_number, supplier_name, contact_name, phone, email,
    address, status, created_at, notes. items: list of dicts with
    description, unit, brand, quantity, unit_price."""
    styles = _styles(ctx)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=18 * mm, bottomMargin=18 * mm, leftMargin=16 * mm, rightMargin=16 * mm,
    )

    story = []
    doc_meta_html = f"{po['po_number']}<br/>{str(po.get('created_at', ''))[:10]}"
    story.append(theme.build_header(ctx, styles, "PURCHASE ORDER", doc_meta_html))
    story.append(Spacer(1, 14))

    supplier_lines = [po["supplier_name"]]
    if po.get("contact_name"):
        supplier_lines.append(po["contact_name"])
    if po.get("phone"):
        supplier_lines.append(po["phone"])
    if po.get("email"):
        supplier_lines.append(po["email"])
    if po.get("address"):
        supplier_lines.append(po["address"])

    meta_table = Table(
        [[Paragraph("SUPPLIER", styles["label"]), Paragraph("<br/>".join(supplier_lines), styles["value"])]],
        colWidths=[30 * mm, 140 * mm],
    )
    meta_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 14),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 4))

    # Item 4: per-PO payment terms (negotiated with this supplier for this
    # order -- separate from Item 3's settings_po_terms boilerplate legal
    # text), expected delivery date, and ship-to address. Each line/block is
    # omitted entirely when not set on this PO, with no fallback text.
    extra_meta_rows = []
    if po.get("payment_terms"):
        extra_meta_rows.append(
            [Paragraph("PAYMENT TERMS", styles["label"]), Paragraph(po["payment_terms"], styles["value"])]
        )
    if po.get("expected_delivery_date"):
        extra_meta_rows.append(
            [Paragraph("EXPECTED DELIVERY", styles["label"]),
             Paragraph(str(po["expected_delivery_date"])[:10], styles["value"])]
        )
    if po.get("ship_to_address"):
        ship_to_html = po["ship_to_address"].replace("\r\n", "\n").replace("\n", "<br/>")
        extra_meta_rows.append(
            [Paragraph("SHIP TO", styles["label"]), Paragraph(ship_to_html, styles["value"])]
        )
    if extra_meta_rows:
        extra_meta_table = Table(extra_meta_rows, colWidths=[30 * mm, 140 * mm])
        extra_meta_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(extra_meta_table)
        story.append(Spacer(1, 4))

    # Column set/order/labels/widths are owner-configurable (Settings ->
    # Document builder) -- ctx.po_columns is already the resolved
    # [(key, label, width_mm), ...] list built by pdf.theme.get_pdf_context()
    # -> resolve_columns(). "description" is always present (enforced
    # there), so this table can never end up with zero columns.
    numeric_keys = {"qty", "unit_price", "line_total"}
    columns = ctx.po_columns

    header_row = [Paragraph(label, styles["header_cell"]) for _, label, _ in columns]
    rows = [header_row]
    grand_total = 0.0
    for item in items:
        line_total = float(item["quantity"]) * float(item["unit_price"])
        grand_total += line_total
        values = {
            "description": item["description"],
            "brand": item.get("brand") or "—",
            "unit": item["unit"],
            "qty": f"{item['quantity']:g}",
            "unit_price": f"{item['unit_price']:.2f}",
            "line_total": f"{line_total:.2f}",
        }
        rows.append([Paragraph(str(values.get(key, "")), styles["cell"]) for key, _, _ in columns])

    col_widths = [w * mm for _, _, w in columns]
    items_table = Table(rows, colWidths=col_widths, repeatRows=1)
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), ctx.structure),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.5, ctx.line),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ctx.paper_raised]),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]
    for idx, (key, _, _) in enumerate(columns):
        if key in numeric_keys:
            style_cmds.append(("ALIGN", (idx, 0), (idx, -1), "RIGHT"))
    items_table.setStyle(TableStyle(style_cmds))
    story.append(items_table)
    story.append(Spacer(1, 10))

    total_table = Table(
        [["", Paragraph(f"<b>TOTAL &nbsp; {grand_total:,.2f}</b>", styles["value"])]],
        colWidths=[144 * mm, 30 * mm],
    )
    total_table.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LINEABOVE", (0, 0), (-1, 0), 1, ctx.ink),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(total_table)

    if po.get("notes"):
        story.append(Spacer(1, 16))
        story.append(Paragraph("NOTES", styles["label"]))
        story.append(Paragraph(po["notes"], styles["value"]))

    # Item 3: PO content parity with Quote/Estimate -- boilerplate Terms &
    # Conditions (settings_po_terms) and, if enabled, the same Payment
    # Details block Quote/Estimate already use. Both no-op (return []) when
    # there's nothing to show, so they're safe to unconditionally append.
    story += theme.terms_flowables(ctx.po_terms, styles)
    if ctx.show_bank_on_po:
        story += theme.bank_details_block(ctx, styles)

    # Item 4: authorized-by/signature line, always appended near the end of
    # the story (after terms/bank/ship-to blocks) -- blank lines for a
    # physical signature, no new data stored (print-and-sign, not a digital
    # signature capture).
    story.append(Spacer(1, 28))
    story.append(Paragraph(
        "Authorized by: ________________________________ &nbsp;&nbsp;&nbsp;&nbsp; "
        "Date: ________________________________",
        styles["value"],
    ))

    # Item 4: faint diagonal "DRAFT" watermark, active only while this PO is
    # still a draft -- gone the moment it's marked sent (or beyond).
    watermark_fn = _draft_watermark if po.get("status") == "draft" else _no_watermark
    doc.build(story, onFirstPage=watermark_fn, onLaterPages=watermark_fn)
    return buf.getvalue()
