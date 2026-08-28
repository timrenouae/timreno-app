"""
Client quotation PDF -- reportlab Platypus, reproducing the exact section
layout of the old TIMR Quote Builder.html's buildQuotePdfHtml() (company
header, "Prepared for"/"Project" meta grid, one table per room with a
subtotal band, notes block, a 3-row VAT totals box, numbered terms), now
rendered server-side instead of via html2pdf.js.

Company identity, brand colors/font, logo, and the optional Payment
Details block all come from a pdf.theme.PdfContext (see pdf/theme.py) built
from the live company_settings row -- nothing about the business's own
details is hardcoded here.
"""
import io

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, KeepTogether,
    ListFlowable, ListItem,
)
from reportlab.lib import colors

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
        "term": ParagraphStyle("Term", parent=base["Normal"], fontSize=9, textColor=ctx.ink_soft,
                                fontName=ctx.font, leading=14),
    }


def _room_block(room, ctx, styles):
    subtotal = sum((i["qty"] or 0) * (i["unit_price"] or 0) for i in room["items"])
    head = Table(
        [[Paragraph(room["name"], styles["room_head"]), Paragraph(f"AED {subtotal:,.2f}", styles["room_sub"])]],
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
        Paragraph("UNIT", styles["header_cell"]), Paragraph("UNIT PRICE", styles["header_cell"]),
        Paragraph("AMOUNT", styles["header_cell"]),
    ]]
    for item in room["items"]:
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
    if room.get("notes"):
        block.append(Paragraph(f"Note: {room['notes']}", styles["note"]))
    block.append(Spacer(1, 6))
    return KeepTogether(block)


def _terms_flowables(terms, styles):
    if not terms:
        return []
    items = [ListItem(Paragraph(t, styles["term"]), leftIndent=10) for t in terms]
    return [
        Spacer(1, 14),
        Paragraph("TERMS &amp; CONDITIONS", styles["label"]),
        Spacer(1, 4),
        ListFlowable(items, bulletType="1", leftIndent=14, bulletFontSize=9),
    ]


def build_quote_pdf(ctx, meta: dict, rooms: list, terms: list) -> bytes:
    """ctx: a pdf.theme PdfContext (see pdf/theme.py: get_pdf_context()).
    meta: quote_number, quote_date, client_name, location, project_name,
    project_type, job_notes, vat_percent. rooms: [{name, notes, items:[...]}]."""
    styles = _styles(ctx)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=18 * mm, bottomMargin=18 * mm, leftMargin=16 * mm, rightMargin=16 * mm,
    )
    story = []

    doc_meta_html = f"{meta.get('quote_number', '')}<br/>{meta.get('quote_date', '') or ''}"
    story.append(theme.build_header(ctx, styles, "QUOTATION", doc_meta_html))
    story.append(Spacer(1, 10))

    meta_table = Table(
        [[
            [Paragraph("PREPARED FOR", styles["label"]),
             Paragraph(meta.get("client_name") or "—", styles["value"]),
             Paragraph(meta.get("location") or "", styles["value"])],
            [Paragraph("PROJECT", styles["label"]),
             Paragraph(meta.get("project_name") or "—", styles["value"]),
             Paragraph(meta.get("project_type") or "", styles["value"])],
        ]],
        colWidths=[100 * mm, 70 * mm],
    )
    meta_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(meta_table)
    story.append(Spacer(1, 14))

    for room in rooms:
        if room["items"]:
            story.append(_room_block(room, ctx, styles))

    job_notes = meta.get("job_notes")
    if job_notes:
        story.append(Paragraph("NOTES", styles["label"]))
        story.append(Paragraph(job_notes.replace("\n", "<br/>"), styles["value"]))
        story.append(Spacer(1, 10))

    subtotal = sum((i["qty"] or 0) * (i["unit_price"] or 0) for room in rooms for i in room["items"])
    vat_pct = meta.get("vat_percent")
    vat_pct = vat_pct if vat_pct is not None else ctx.default_vat_percent
    vat_amt = round(subtotal * vat_pct / 100, 2)
    grand = round(subtotal + vat_amt, 2)

    totals_table = Table(
        [
            ["", Paragraph("Subtotal", styles["value"]), Paragraph(f"{subtotal:,.2f}", styles["num_cell"])],
            ["", Paragraph(f"VAT ({vat_pct:g}%)", styles["value"]), Paragraph(f"{vat_amt:,.2f}", styles["num_cell"])],
            ["", Paragraph("<b>GRAND TOTAL</b>", ParagraphStyle("GrandLabel", parent=styles["value"], fontSize=12,
                                                                  textColor=ctx.structure)),
             Paragraph(f"<b>AED {grand:,.2f}</b>", ParagraphStyle("GrandVal", parent=styles["num_cell"], fontSize=12,
                                                                    textColor=ctx.structure))],
        ],
        colWidths=[100 * mm, 40 * mm, 30 * mm],
    )
    totals_table.setStyle(TableStyle([
        ("LINEABOVE", (1, 2), (2, 2), 1.2, ctx.ink),
        ("TOPPADDING", (0, 2), (-1, 2), 6),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 3),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 3),
    ]))
    story.append(totals_table)

    terms_block = _terms_flowables(terms, styles)
    bank_block = theme.bank_details_block(ctx, styles) if ctx.show_bank_on_quote else []
    if ctx.trailing_order == "bank_then_terms":
        story += bank_block + terms_block
    else:
        story += terms_block + bank_block

    doc.build(story)
    return buf.getvalue()
