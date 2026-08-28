"""
Purchase-order PDF, built with reportlab's Platypus flowables
(SimpleDocTemplate / Table / Paragraph) rather than raw canvas.drawString
coordinates -- Platypus handles text wrapping and row height itself, which
is what avoids the overlap/alignment bugs the manual-coordinate approach
produced elsewhere in this project earlier.
"""
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

INK = colors.HexColor("#1b2a38")
STRUCTURE = colors.HexColor("#2e5c7a")
LINE = colors.HexColor("#c9cdc5")
PAPER_RAISED = colors.HexColor("#f8f9f7")

styles = getSampleStyleSheet()
title_style = ParagraphStyle("POTitle", parent=styles["Heading1"], fontSize=20, textColor=INK, spaceAfter=2)
eyebrow_style = ParagraphStyle("Eyebrow", parent=styles["Normal"], fontSize=9, textColor=STRUCTURE,
                                spaceAfter=10, leading=12)
label_style = ParagraphStyle("Label", parent=styles["Normal"], fontSize=8, textColor=colors.grey, leading=10)
value_style = ParagraphStyle("Value", parent=styles["Normal"], fontSize=10, textColor=INK, leading=13)
cell_style = ParagraphStyle("Cell", parent=styles["Normal"], fontSize=9, textColor=INK, leading=12)
header_cell_style = ParagraphStyle("HeaderCell", parent=styles["Normal"], fontSize=8.5, textColor=colors.white, leading=11)


def build_purchase_order_pdf(po: dict, items: list) -> bytes:
    """po: dict with po_number, supplier_name, contact_name, phone, email,
    address, status, created_at, notes. items: list of dicts with
    description, unit, brand, quantity, unit_price."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=18 * mm, bottomMargin=18 * mm, leftMargin=16 * mm, rightMargin=16 * mm,
    )

    story = []
    story.append(Paragraph("TIM RENOVATIONS &mdash; PURCHASE ORDER", eyebrow_style))
    story.append(Paragraph(po["po_number"], title_style))
    story.append(Spacer(1, 10))

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
        [
            [Paragraph("SUPPLIER", label_style), Paragraph("ORDER DATE", label_style)],
            [
                Paragraph("<br/>".join(supplier_lines), value_style),
                Paragraph(str(po.get("created_at", ""))[:10], value_style),
            ],
        ],
        colWidths=[110 * mm, 60 * mm],
    )
    meta_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 14),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 14))

    header_row = [
        Paragraph("DESCRIPTION", header_cell_style),
        Paragraph("BRAND", header_cell_style),
        Paragraph("UNIT", header_cell_style),
        Paragraph("QTY", header_cell_style),
        Paragraph("UNIT PRICE", header_cell_style),
        Paragraph("LINE TOTAL", header_cell_style),
    ]
    rows = [header_row]
    grand_total = 0.0
    for item in items:
        line_total = float(item["quantity"]) * float(item["unit_price"])
        grand_total += line_total
        rows.append([
            Paragraph(item["description"], cell_style),
            Paragraph(item.get("brand") or "—", cell_style),
            Paragraph(item["unit"], cell_style),
            Paragraph(f"{item['quantity']:g}", cell_style),
            Paragraph(f"{item['unit_price']:.2f}", cell_style),
            Paragraph(f"{line_total:.2f}", cell_style),
        ])

    items_table = Table(rows, colWidths=[62 * mm, 26 * mm, 18 * mm, 16 * mm, 26 * mm, 26 * mm], repeatRows=1)
    items_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), STRUCTURE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.5, LINE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PAPER_RAISED]),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(items_table)
    story.append(Spacer(1, 10))

    total_table = Table(
        [["", Paragraph(f"<b>TOTAL &nbsp; {grand_total:,.2f}</b>", value_style)]],
        colWidths=[144 * mm, 30 * mm],
    )
    total_table.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LINEABOVE", (0, 0), (-1, 0), 1, INK),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(total_table)

    if po.get("notes"):
        story.append(Spacer(1, 16))
        story.append(Paragraph("NOTES", label_style))
        story.append(Paragraph(po["notes"], value_style))

    doc.build(story)
    return buf.getvalue()
