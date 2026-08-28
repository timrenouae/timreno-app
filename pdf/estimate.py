"""
Rough Estimate PDF -- reportlab Platypus, reproducing the old TIMR Rough
Estimator.html's buildEstimatePdfHtml() (doc label "ROUGH ESTIMATE" instead
of a quote number, per-space Rate/Amount tables with an optional per-sq-ft
line, a totals box with an extra "Approx. per sq ft" row, and a disclaimer
paragraph in place of Terms & Conditions). Generated from in-memory posted
state -- nothing here is persisted.

Company identity, brand colors/font, logo, the disclaimer wording, and the
optional Payment Details block all come from a pdf.theme.PdfContext (see
pdf/theme.py) built from the live company_settings row.
"""
import io
import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, KeepTogether

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
        "room_head": ParagraphStyle("RoomHead", parent=base["Normal"], fontSize=10.5, textColor=colors.white,
                                     fontName=ctx.font_bold, leading=13),
        "room_sub": ParagraphStyle("RoomSub", parent=base["Normal"], fontSize=10.5, textColor=colors.white,
                                    fontName=ctx.font_bold, alignment=2, leading=13),
        "cell": ParagraphStyle("Cell", parent=base["Normal"], fontSize=9, textColor=ctx.ink,
                                fontName=ctx.font, leading=12),
        "num_cell": ParagraphStyle("NumCell", parent=base["Normal"], fontSize=9, textColor=ctx.ink,
                                    fontName=ctx.font, leading=12, alignment=2),
        "header_cell": ParagraphStyle("HeaderCell", parent=base["Normal"], fontSize=8, textColor=colors.grey,
                                       fontName=ctx.font, leading=10),
        "note": ParagraphStyle("Note", parent=base["Normal"], fontSize=8.5, textColor=colors.grey,
                                fontName=ctx.font, leading=11, italic=True),
        "disclaimer": ParagraphStyle("Disclaimer", parent=base["Normal"], fontSize=8.5, textColor=ctx.ink_soft,
                                      fontName=ctx.font, leading=12),
    }


def _space_block(space, ctx, styles):
    items = space["items"]
    subtotal = sum((i["qty"] or 0) * (i["unit_price"] or 0) for i in items)
    size = space.get("size") or 0
    label = space["label"] + (f"  ({size:g} sq ft)" if size else "")

    head = Table(
        [[Paragraph(label, styles["room_head"]), Paragraph(f"AED {subtotal:,.2f}", styles["room_sub"])]],
        colWidths=[130 * mm, 40 * mm],
    )
    head.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), ctx.structure),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    rows = [[
        Paragraph("DESCRIPTION", styles["header_cell"]), Paragraph("QTY", styles["header_cell"]),
        Paragraph("UNIT", styles["header_cell"]), Paragraph("RATE", styles["header_cell"]),
        Paragraph("AMOUNT", styles["header_cell"]),
    ]]
    for item in items:
        qty = item["qty"] or 0
        price = item["unit_price"] or 0
        amount = qty * price
        rows.append([
            Paragraph(item["description"], styles["cell"]),
            Paragraph(f"{qty:g}", styles["num_cell"]),
            Paragraph(item["unit"], styles["cell"]),
            Paragraph(f"{price:,.2f}", styles["num_cell"]),
            Paragraph(f"{amount:,.2f}", styles["num_cell"]),
        ])
    items_table = Table(rows, colWidths=[75 * mm, 14 * mm, 21 * mm, 30 * mm, 30 * mm], repeatRows=1)
    items_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.5, ctx.line),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ctx.paper_raised]),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
    ]))

    block = [head, items_table]
    if size > 0:
        per_sqft = subtotal / size
        block.append(Paragraph(f"&asymp; AED {per_sqft:,.2f} / sq ft", styles["note"]))
    if space.get("notes"):
        block.append(Paragraph(f"Note: {space['notes']}", styles["note"]))
    block.append(Spacer(1, 6))
    return KeepTogether(block)


def build_estimate_pdf(ctx, meta: dict, spaces: list) -> bytes:
    """ctx: a pdf.theme PdfContext (see pdf/theme.py: get_pdf_context()).
    meta: project_name, location, finish_level, vat_percent.
    spaces: [{label, size, notes, items:[{description, unit, qty, unit_price}]}]."""
    styles = _styles(ctx)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=18 * mm, bottomMargin=18 * mm, leftMargin=16 * mm, rightMargin=16 * mm,
    )
    story = []

    today = datetime.date.today().strftime("%d/%m/%Y")
    doc_meta_html = f"Not a formal quotation<br/>{today}"
    story.append(theme.build_header(ctx, styles, "ROUGH ESTIMATE", doc_meta_html))
    story.append(Spacer(1, 10))

    prepared_sub = " &middot; ".join(
        p for p in [meta.get("location"), f"{meta.get('finish_level', 'Standard')} finish"] if p
    )
    project_row = Table(
        [[
            [Paragraph("PREPARED FOR", styles["label"]), Paragraph(prepared_sub or "—", styles["value"])],
            [Paragraph("PROJECT", styles["label"]), Paragraph(meta.get("project_name") or "—", styles["value"])],
        ]],
        colWidths=[100 * mm, 70 * mm],
    )
    project_row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(project_row)
    story.append(Spacer(1, 14))

    for space in spaces:
        if space["items"]:
            story.append(_space_block(space, ctx, styles))

    subtotal = sum((i["qty"] or 0) * (i["unit_price"] or 0) for s in spaces for i in s["items"])
    vat_pct = meta.get("vat_percent")
    vat_pct = vat_pct if vat_pct is not None else 0
    vat_amt = round(subtotal * vat_pct / 100, 2)
    grand = round(subtotal + vat_amt, 2)
    total_sqft = sum(s.get("size") or 0 for s in spaces)

    totals_rows = [
        ["", Paragraph("Subtotal", styles["value"]), Paragraph(f"{subtotal:,.2f}", styles["num_cell"])],
        ["", Paragraph(f"VAT ({vat_pct:g}%)", styles["value"]), Paragraph(f"{vat_amt:,.2f}", styles["num_cell"])],
        ["", Paragraph("<b>GRAND TOTAL</b>", ParagraphStyle("GrandLabel", parent=styles["value"], fontSize=12,
                                                              textColor=ctx.structure)),
         Paragraph(f"<b>AED {grand:,.2f}</b>", ParagraphStyle("GrandVal", parent=styles["num_cell"], fontSize=12,
                                                                textColor=ctx.structure))],
    ]
    grand_row_index = 2
    if total_sqft > 0:
        per_sqft_overall = subtotal / total_sqft
        totals_rows.append(["", Paragraph("Approx. per sq ft (pre-VAT)", styles["value"]),
                             Paragraph(f"{per_sqft_overall:,.2f}", styles["num_cell"])])

    totals_table = Table(totals_rows, colWidths=[100 * mm, 40 * mm, 30 * mm])
    style = [
        ("LINEABOVE", (1, grand_row_index), (2, grand_row_index), 1.2, ctx.ink),
        ("TOPPADDING", (0, grand_row_index), (-1, grand_row_index), 6),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 3),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 3),
    ]
    totals_table.setStyle(TableStyle(style))
    story.append(totals_table)

    disclaimer_block = [
        Spacer(1, 14),
        Paragraph("DISCLAIMER", styles["label"]),
        Paragraph(ctx.estimate_disclaimer, styles["disclaimer"]),
    ] if ctx.estimate_disclaimer else []
    bank_block = theme.bank_details_block(ctx, styles) if ctx.show_bank_on_estimate else []
    story += disclaimer_block + bank_block

    doc.build(story)
    return buf.getvalue()
