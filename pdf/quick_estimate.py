"""
Quick Estimate PDF -- the Rough Estimator's fast per-sqft mode (total area x
a Low/Medium/High blended rate), as opposed to pdf/estimate.py's detailed
per-space/per-item Rough Estimate. Deliberately much shorter: a rooms table
(when the area came from "Import from Drawing" and has a room breakdown) or
just a single total-area line, then one total-area x rate = total block.

Company identity, brand colors/font, logo, the disclaimer wording, and the
optional Payment Details block all come from a pdf.theme.PdfContext (see
pdf/theme.py) built from the live company_settings row -- same as every
other PDF generator in this app.
"""
import io
import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

import pdf.theme as theme


def _styles(ctx):
    base = getSampleStyleSheet()
    return {
        "company_name": ParagraphStyle("QECompanyName", parent=base["Normal"], fontSize=14, textColor=ctx.ink,
                                        fontName=ctx.font_bold, leading=17),
        "company_sub": ParagraphStyle("QECompanySub", parent=base["Normal"], fontSize=9, textColor=colors.grey,
                                       fontName=ctx.font, leading=12),
        "doc_label": ParagraphStyle("QEDocLabel", parent=base["Normal"], fontSize=16, textColor=ctx.structure,
                                     fontName=ctx.font_bold, alignment=2, leading=19),
        "doc_meta": ParagraphStyle("QEDocMeta", parent=base["Normal"], fontSize=9, textColor=ctx.ink_soft,
                                    fontName=ctx.font, alignment=2, leading=13),
        "label": ParagraphStyle("QELabel", parent=base["Normal"], fontSize=8, textColor=colors.grey,
                                 fontName=ctx.font, leading=10),
        "value": ParagraphStyle("QEValue", parent=base["Normal"], fontSize=10, textColor=ctx.ink,
                                 fontName=ctx.font, leading=13),
        "cell": ParagraphStyle("QECell", parent=base["Normal"], fontSize=9, textColor=ctx.ink,
                                fontName=ctx.font, leading=12),
        "num_cell": ParagraphStyle("QENumCell", parent=base["Normal"], fontSize=9, textColor=ctx.ink,
                                    fontName=ctx.font, leading=12, alignment=2),
        "th": ParagraphStyle("QETh", parent=base["Normal"], fontSize=8.5, textColor=colors.white,
                              fontName=ctx.font_bold, leading=11),
        "disclaimer": ParagraphStyle("QEDisclaimer", parent=base["Normal"], fontSize=8.5, textColor=ctx.ink_soft,
                                      fontName=ctx.font, leading=12),
    }


def build_quick_estimate_pdf(ctx, meta: dict, rooms: list) -> bytes:
    """ctx: a pdf.theme PdfContext.
    meta: project_name, location, project_type_label ("House / Villa" or
        "Office"), tier ("Low"/"Medium"/"High"), rate (AED/sqft), vat_percent.
    rooms: [{name, sqft}] -- optional per-room breakdown (e.g. from Import
        from Drawing); may be empty, in which case only the total area is
        shown. Every room's sqft is summed to get the total area this
        estimate is based on -- there's no separate "total_sqft" input,
        so the story is always internally consistent with what's on the
        page."""
    styles = _styles(ctx)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=18 * mm, bottomMargin=18 * mm, leftMargin=16 * mm, rightMargin=16 * mm,
    )
    story = []

    today = datetime.date.today().strftime("%d/%m/%Y")
    doc_meta_html = f"Not a formal quotation<br/>{today}"
    story.append(theme.build_header(ctx, styles, "QUICK ESTIMATE", doc_meta_html))
    story.append(Spacer(1, 10))

    tier = meta.get("tier") or "Medium"
    project_type_label = meta.get("project_type_label") or "House / Villa"
    prepared_sub = " &middot; ".join(
        p for p in [meta.get("location"), f"{project_type_label} — {tier} quality"] if p
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

    total_sqft = round(sum((r.get("sqft") or 0) for r in rooms), 2)

    if rooms:
        rows = [[Paragraph("ROOM / SPACE", styles["th"]), Paragraph("APPROX. SQ FT", styles["th"])]]
        for r in rooms:
            sqft_text = f"{r['sqft']:,.2f}" if r.get("sqft") else "—"
            rows.append([Paragraph(r.get("name") or "—", styles["cell"]),
                         Paragraph(sqft_text, styles["num_cell"])])
        rows.append([Paragraph("<b>Total area</b>", styles["cell"]),
                     Paragraph(f"<b>{total_sqft:,.2f}</b>", styles["num_cell"])])
        rooms_table = Table(rows, colWidths=[130 * mm, 40 * mm])
        rooms_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), ctx.structure),
            ("TOPPADDING", (0, 0), (-1, 0), 6), ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
            ("GRID", (0, 0), (-1, -2), 0.5, ctx.line),
            ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, ctx.paper_raised]),
            ("LINEABOVE", (0, -1), (-1, -1), 1, ctx.ink),
            ("TOPPADDING", (0, -1), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(rooms_table)
        story.append(Spacer(1, 14))

    rate = meta.get("rate") or 0
    subtotal = round(total_sqft * rate, 2)
    vat_pct = meta.get("vat_percent")
    vat_pct = vat_pct if vat_pct is not None else 0
    vat_amt = round(subtotal * vat_pct / 100, 2)
    grand = round(subtotal + vat_amt, 2)

    totals_rows = [
        ["", Paragraph("Total area", styles["value"]), Paragraph(f"{total_sqft:,.2f} sq ft", styles["num_cell"])],
        ["", Paragraph(f"Rate ({tier})", styles["value"]), Paragraph(f"AED {rate:,.2f} / sq ft", styles["num_cell"])],
        ["", Paragraph("Subtotal", styles["value"]), Paragraph(f"{subtotal:,.2f}", styles["num_cell"])],
        ["", Paragraph(f"VAT ({vat_pct:g}%)", styles["value"]), Paragraph(f"{vat_amt:,.2f}", styles["num_cell"])],
        ["", Paragraph("<b>GRAND TOTAL</b>", ParagraphStyle("QEGrandLabel", parent=styles["value"], fontSize=12,
                                                              textColor=ctx.structure)),
         Paragraph(f"<b>AED {grand:,.2f}</b>", ParagraphStyle("QEGrandVal", parent=styles["num_cell"], fontSize=12,
                                                                textColor=ctx.structure))],
    ]
    grand_row_index = 4
    totals_table = Table(totals_rows, colWidths=[80 * mm, 60 * mm, 30 * mm])
    totals_table.setStyle(TableStyle([
        ("LINEABOVE", (1, grand_row_index), (2, grand_row_index), 1.2, ctx.ink),
        ("TOPPADDING", (0, grand_row_index), (-1, grand_row_index), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -2), 3),
    ]))
    story.append(totals_table)

    disclaimer_block = [
        Spacer(1, 14),
        Paragraph("DISCLAIMER", styles["label"]),
        Paragraph(
            (ctx.estimate_disclaimer or "") +
            " This Quick Estimate is a ballpark figure from a blended per-sq-ft rate, generated in minutes "
            "for early budgeting -- it is not a line-itemized quotation and does not reflect a site "
            "assessment or your specific material choices.",
            styles["disclaimer"],
        ),
    ]
    bank_block = theme.bank_details_block(ctx, styles) if ctx.show_bank_on_estimate else []
    story += disclaimer_block + bank_block

    doc.build(story)
    return buf.getvalue()
