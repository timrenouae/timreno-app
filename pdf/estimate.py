"""
Rough Estimate PDF -- reportlab Platypus, reproducing the old TIMR Rough
Estimator.html's buildEstimatePdfHtml() (doc label "ROUGH ESTIMATE" instead
of a quote number, per-space Rate/Amount tables with an optional per-sq-ft
line, a totals box with an extra "Approx. per sq ft" row, and a fixed
disclaimer paragraph in place of Terms & Conditions). Generated from
in-memory posted state -- nothing here is persisted.
"""
import io
import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, KeepTogether

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
disclaimer_style = ParagraphStyle("Disclaimer", parent=styles["Normal"], fontSize=8.5, textColor=INK_SOFT, leading=12)

DISCLAIMER = (
    "This is a rough, non-binding estimate for early budgeting and planning purposes only. "
    "Actual pricing may vary once a full itemized quotation is prepared."
)


def _space_block(space):
    items = space["items"]
    subtotal = sum((i["qty"] or 0) * (i["unit_price"] or 0) for i in items)
    size = space.get("size") or 0
    label = space["label"] + (f"  ({size:g} sq ft)" if size else "")

    head = Table(
        [[Paragraph(label, room_head_style), Paragraph(f"AED {subtotal:,.2f}", room_sub_style)]],
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
        Paragraph("UNIT", header_cell_style), Paragraph("RATE", header_cell_style),
        Paragraph("AMOUNT", header_cell_style),
    ]]
    for item in items:
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
    if size > 0:
        per_sqft = subtotal / size
        block.append(Paragraph(f"&asymp; AED {per_sqft:,.2f} / sq ft", note_style))
    if space.get("notes"):
        block.append(Paragraph(f"Note: {space['notes']}", note_style))
    block.append(Spacer(1, 6))
    return KeepTogether(block)


def build_estimate_pdf(meta: dict, spaces: list) -> bytes:
    """meta: project_name, location, finish_level, vat_percent.
    spaces: [{label, size, notes, items:[{description, unit, qty, unit_price}]}]."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=18 * mm, bottomMargin=18 * mm, leftMargin=16 * mm, rightMargin=16 * mm,
    )
    story = []

    today = datetime.date.today().strftime("%d/%m/%Y")
    header = Table(
        [[
            [Paragraph("TWO INDIAN MINDS RENOVATIONS LLC", company_name_style),
             Paragraph("Villa Construction &middot; Renovations &middot; Office Fit-out", company_sub_style)],
            [Paragraph("ROUGH ESTIMATE", doc_label_style),
             Paragraph(f"Not a formal quotation<br/>{today}", doc_meta_style)],
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

    prepared_sub = " &middot; ".join(
        p for p in [meta.get("location"), f"{meta.get('finish_level', 'Standard')} finish"] if p
    )
    project_row = Table(
        [[
            [Paragraph("PREPARED FOR", label_style), Paragraph(prepared_sub or "—", value_style)],
            [Paragraph("PROJECT", label_style), Paragraph(meta.get("project_name") or "—", value_style)],
        ]],
        colWidths=[100 * mm, 70 * mm],
    )
    project_row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(project_row)
    story.append(Spacer(1, 14))

    for space in spaces:
        if space["items"]:
            story.append(_space_block(space))

    subtotal = sum((i["qty"] or 0) * (i["unit_price"] or 0) for s in spaces for i in s["items"])
    vat_pct = meta.get("vat_percent")
    vat_pct = vat_pct if vat_pct is not None else 0
    vat_amt = round(subtotal * vat_pct / 100, 2)
    grand = round(subtotal + vat_amt, 2)
    total_sqft = sum(s.get("size") or 0 for s in spaces)

    totals_rows = [
        ["", Paragraph("Subtotal", value_style), Paragraph(f"{subtotal:,.2f}", num_cell_style)],
        ["", Paragraph(f"VAT ({vat_pct:g}%)", value_style), Paragraph(f"{vat_amt:,.2f}", num_cell_style)],
        ["", Paragraph("<b>GRAND TOTAL</b>", ParagraphStyle("GrandLabel", parent=value_style, fontSize=12,
                                                              textColor=STRUCTURE)),
         Paragraph(f"<b>AED {grand:,.2f}</b>", ParagraphStyle("GrandVal", parent=num_cell_style, fontSize=12,
                                                                textColor=STRUCTURE))],
    ]
    grand_row_index = 2
    if total_sqft > 0:
        per_sqft_overall = subtotal / total_sqft
        totals_rows.append(["", Paragraph("Approx. per sq ft (pre-VAT)", value_style),
                             Paragraph(f"{per_sqft_overall:,.2f}", num_cell_style)])

    totals_table = Table(totals_rows, colWidths=[100 * mm, 40 * mm, 30 * mm])
    style = [
        ("LINEABOVE", (1, grand_row_index), (2, grand_row_index), 1.2, INK),
        ("TOPPADDING", (0, grand_row_index), (-1, grand_row_index), 6),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 3),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 3),
    ]
    totals_table.setStyle(TableStyle(style))
    story.append(totals_table)

    story.append(Spacer(1, 14))
    story.append(Paragraph("DISCLAIMER", label_style))
    story.append(Paragraph(DISCLAIMER, disclaimer_style))

    doc.build(story)
    return buf.getvalue()
