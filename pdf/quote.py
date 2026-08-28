"""
Client quotation PDF -- reportlab Platypus, reproducing the exact section
layout of the old TIMR Quote Builder.html's buildQuotePdfHtml() (company
header, "Prepared for"/"Project" meta grid, one table per room with a
subtotal band, notes block, a 3-row VAT totals box, numbered terms), now
rendered server-side instead of via html2pdf.js.
"""
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, KeepTogether,
    ListFlowable, ListItem,
)

INK = colors.HexColor("#1b2a38")
STRUCTURE = colors.HexColor("#2e5c7a")
LINE = colors.HexColor("#c9cdc5")
PAPER_RAISED = colors.HexColor("#f8f9f7")
INK_SOFT = colors.HexColor("#465361")

styles = getSampleStyleSheet()
company_name_style = ParagraphStyle("CompanyName", parent=styles["Normal"], fontSize=14, textColor=INK,
                                     fontName="Helvetica-Bold", leading=17)
company_sub_style = ParagraphStyle("CompanySub", parent=styles["Normal"], fontSize=9, textColor=colors.grey, leading=12)
doc_label_style = ParagraphStyle("DocLabel", parent=styles["Normal"], fontSize=16, textColor=STRUCTURE,
                                  fontName="Helvetica-Bold", alignment=2, leading=19)
doc_meta_style = ParagraphStyle("DocMeta", parent=styles["Normal"], fontSize=9, textColor=INK_SOFT,
                                 alignment=2, leading=13)
label_style = ParagraphStyle("Label", parent=styles["Normal"], fontSize=8, textColor=colors.grey, leading=10)
value_style = ParagraphStyle("Value", parent=styles["Normal"], fontSize=10, textColor=INK, leading=13)
room_head_style = ParagraphStyle("RoomHead", parent=styles["Normal"], fontSize=10.5, textColor=colors.white,
                                  fontName="Helvetica-Bold", leading=13)
room_sub_style = ParagraphStyle("RoomSub", parent=styles["Normal"], fontSize=10.5, textColor=colors.white,
                                 fontName="Helvetica-Bold", alignment=2, leading=13)
cell_style = ParagraphStyle("Cell", parent=styles["Normal"], fontSize=9, textColor=INK, leading=12)
num_cell_style = ParagraphStyle("NumCell", parent=styles["Normal"], fontSize=9, textColor=INK, leading=12, alignment=2)
header_cell_style = ParagraphStyle("HeaderCell", parent=styles["Normal"], fontSize=8, textColor=colors.grey, leading=10)
note_style = ParagraphStyle("Note", parent=styles["Normal"], fontSize=8.5, textColor=colors.grey, leading=11,
                             italic=True)
term_style = ParagraphStyle("Term", parent=styles["Normal"], fontSize=9, textColor=INK_SOFT, leading=14)


def _room_block(room):
    subtotal = sum((i["qty"] or 0) * (i["unit_price"] or 0) for i in room["items"])
    head = Table(
        [[Paragraph(room["name"], room_head_style), Paragraph(f"AED {subtotal:,.2f}", room_sub_style)]],
        colWidths=[130 * mm, 40 * mm],
    )
    head.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), STRUCTURE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    rows = [[
        Paragraph("DESCRIPTION", header_cell_style), Paragraph("QTY", header_cell_style),
        Paragraph("UNIT", header_cell_style), Paragraph("UNIT PRICE", header_cell_style),
        Paragraph("AMOUNT", header_cell_style),
    ]]
    for item in room["items"]:
        qty = item["qty"] or 0
        price = item["unit_price"] or 0
        amount = qty * price
        rows.append([
            Paragraph(item["description"], cell_style),
            Paragraph(f"{qty:g}", num_cell_style),
            Paragraph(item["unit"], cell_style),
            Paragraph(f"{price:,.2f}", num_cell_style),
            Paragraph(f"{amount:,.2f}", num_cell_style),
        ])
    items_table = Table(rows, colWidths=[75 * mm, 14 * mm, 21 * mm, 30 * mm, 30 * mm], repeatRows=1)
    items_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.5, LINE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PAPER_RAISED]),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
    ]))

    block = [head, items_table]
    if room.get("notes"):
        block.append(Paragraph(f"Note: {room['notes']}", note_style))
    block.append(Spacer(1, 6))
    return KeepTogether(block)


def build_quote_pdf(meta: dict, rooms: list, terms: list) -> bytes:
    """meta: quote_number, quote_date, client_name, location, project_name,
    project_type, job_notes, vat_percent. rooms: [{name, notes, items:[...]}]."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=18 * mm, bottomMargin=18 * mm, leftMargin=16 * mm, rightMargin=16 * mm,
    )
    story = []

    header = Table(
        [[
            [Paragraph("TWO INDIAN MINDS RENOVATIONS LLC", company_name_style),
             Paragraph("Villa Construction &middot; Renovations &middot; Office Fit-out", company_sub_style)],
            [Paragraph("QUOTATION", doc_label_style),
             Paragraph(f"{meta.get('quote_number', '')}<br/>{meta.get('quote_date', '') or ''}", doc_meta_style)],
        ]],
        colWidths=[110 * mm, 60 * mm],
    )
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 2, STRUCTURE),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 10),
    ]))
    story.append(header)
    story.append(Spacer(1, 10))

    meta_table = Table(
        [[
            [Paragraph("PREPARED FOR", label_style),
             Paragraph(meta.get("client_name") or "—", value_style),
             Paragraph(meta.get("location") or "", value_style)],
            [Paragraph("PROJECT", label_style),
             Paragraph(meta.get("project_name") or "—", value_style),
             Paragraph(meta.get("project_type") or "", value_style)],
        ]],
        colWidths=[100 * mm, 70 * mm],
    )
    meta_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(meta_table)
    story.append(Spacer(1, 14))

    for room in rooms:
        if room["items"]:
            story.append(_room_block(room))

    job_notes = meta.get("job_notes")
    if job_notes:
        story.append(Paragraph("NOTES", label_style))
        story.append(Paragraph(job_notes.replace("\n", "<br/>"), value_style))
        story.append(Spacer(1, 10))

    subtotal = sum((i["qty"] or 0) * (i["unit_price"] or 0) for room in rooms for i in room["items"])
    vat_pct = meta.get("vat_percent")
    vat_pct = vat_pct if vat_pct is not None else 5
    vat_amt = round(subtotal * vat_pct / 100, 2)
    grand = round(subtotal + vat_amt, 2)

    totals_table = Table(
        [
            ["", Paragraph("Subtotal", value_style), Paragraph(f"{subtotal:,.2f}", num_cell_style)],
            ["", Paragraph(f"VAT ({vat_pct:g}%)", value_style), Paragraph(f"{vat_amt:,.2f}", num_cell_style)],
            ["", Paragraph("<b>GRAND TOTAL</b>", ParagraphStyle("GrandLabel", parent=value_style, fontSize=12,
                                                                  textColor=STRUCTURE)),
             Paragraph(f"<b>AED {grand:,.2f}</b>", ParagraphStyle("GrandVal", parent=num_cell_style, fontSize=12,
                                                                    textColor=STRUCTURE))],
        ],
        colWidths=[100 * mm, 40 * mm, 30 * mm],
    )
    totals_table.setStyle(TableStyle([
        ("LINEABOVE", (1, 2), (2, 2), 1.2, INK),
        ("TOPPADDING", (0, 2), (-1, 2), 6),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 3),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 3),
    ]))
    story.append(totals_table)

    if terms:
        story.append(Spacer(1, 14))
        story.append(Paragraph("TERMS &amp; CONDITIONS", label_style))
        story.append(Spacer(1, 4))
        items = [ListItem(Paragraph(t, term_style), leftIndent=10) for t in terms]
        story.append(ListFlowable(items, bulletType="1", leftIndent=14, bulletFontSize=9))

    doc.build(story)
    return buf.getvalue()
