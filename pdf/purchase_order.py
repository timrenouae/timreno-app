"""
Purchase-order PDF, built with reportlab's Platypus flowables
(SimpleDocTemplate / Table / Paragraph) rather than raw canvas.drawString
coordinates -- Platypus handles text wrapping and row height itself, which
is what avoids the overlap/alignment bugs the manual-coordinate approach
produced elsewhere in this project earlier.

Company identity, brand colors/font, and logo come from a
pdf.theme.PdfContext (see pdf/theme.py) built from the live
company_settings row, via the same shared header used by the quote and
estimate PDFs -- previously this file hardcoded a different, inconsistent
company name ("TIM RENOVATIONS") than the other two documents.
"""
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

import pdf.theme as theme


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

    header_row = [
        Paragraph("DESCRIPTION", styles["header_cell"]),
        Paragraph("BRAND", styles["header_cell"]),
        Paragraph("UNIT", styles["header_cell"]),
        Paragraph("QTY", styles["header_cell"]),
        Paragraph("UNIT PRICE", styles["header_cell"]),
        Paragraph("LINE TOTAL", styles["header_cell"]),
    ]
    rows = [header_row]
    grand_total = 0.0
    for item in items:
        line_total = float(item["quantity"]) * float(item["unit_price"])
        grand_total += line_total
        rows.append([
            Paragraph(item["description"], styles["cell"]),
            Paragraph(item.get("brand") or "—", styles["cell"]),
            Paragraph(item["unit"], styles["cell"]),
            Paragraph(f"{item['quantity']:g}", styles["cell"]),
            Paragraph(f"{item['unit_price']:.2f}", styles["cell"]),
            Paragraph(f"{line_total:.2f}", styles["cell"]),
        ])

    items_table = Table(rows, colWidths=[62 * mm, 26 * mm, 18 * mm, 16 * mm, 26 * mm, 26 * mm], repeatRows=1)
    items_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), ctx.structure),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.5, ctx.line),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ctx.paper_raised]),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
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

    doc.build(story)
    return buf.getvalue()
