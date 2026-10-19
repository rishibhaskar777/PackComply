"""Pre-market label self-check PDF for manufacturers.

Not an official inspection report: it lists what to fix on the artwork, with guidance and a compliant example per item, and
states plainly that it is a self-assessment aid. Built from the same persisted results, fonts and Devanagari handling as the
official report, which stays unchanged.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import ComplianceResult, ComplianceRule, ExtractedField, Inspection, User
from backend.app.services.compliance.registry import results_ruleset, rule_legal_reference, rule_title
from backend.app.services.compliance.remediation import remediation_for
from backend.app.services.compliance.rule_engine import FAIL, NOT_APPLICABLE, PASS, REVIEW
from backend.app.services.corrections import display_name
from backend.app.services.report import BODY_FONT, BOLD_FONT, SEVERITY_RANK, InspectionReportService, register_fonts
from backend.app.services.workflow import primary_image, resolve_upload_path

SELF_CHECK_DISCLAIMER = "Self-assessment aid, not an official certificate or approval by the Legal Metrology department."
TEAL, TEAL_DARK = "#0F766E", "#115E59"
VERDICT = {
    "READY": ("Ready to print", "Every applicable declaration was found on this label.", "#047857"),
    "FIX": ("Fix {count} item{plural} before printing", "Change the items below on the artwork, then run the self-check again.", "#B91C1C"),
    "CHECK": ("Verify {count} item{plural} on the pack", "Nothing is missing, but these declarations could not be confirmed from this photo.", "#B45309"),
}


class SelfCheckReportService:
    def __init__(self, db: Session, upload_dir: Path, user: User | None = None):
        self.db, self.upload_dir, self.user = db, upload_dir.resolve(), user
        self._text = InspectionReportService._text

    def _rows(self, rows: list[tuple[str, object | None]], styles, widths: tuple[float, float] = (4.2 * cm, 12.6 * cm)) -> Table:
        cells = [[Paragraph(f"<b>{self._text(key)}</b>", styles["body"]), Paragraph(self._text(value), styles["body"])] for key, value in rows if value not in (None, "")]
        table = Table(cells, colWidths=list(widths))
        table.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F0FDFA")), ("GRID", (0, 0), (-1, -1), .35, colors.HexColor("#CCEBE6")), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("PADDING", (0, 0), (-1, -1), 6)]))
        return table

    def _fix_card(self, number: int, result: ComplianceResult, styles) -> KeepTogether:
        entry = remediation_for(result.rule.rule_code)
        steps = "<br/>".join(f"• {self._text(step)}" for step in entry.fix_steps) if entry else self._text(result.explanation)
        rows = [
            [Paragraph(f"<b>{number}. {self._text(entry.problem_title if entry else rule_title(result.rule))}</b>", styles["body"])],
            [Paragraph(f"{self._text(rule_legal_reference(result.rule))} · {'Fix needed' if result.result == FAIL else 'Check on pack'}", styles["note"])],
            [Paragraph(f"<b>What we found:</b> {self._text(result.explanation)}", styles["body"])],
            [Paragraph(f"<b>How to fix:</b><br/>{steps}", styles["body"])],
        ]
        if entry:
            rows.append([Paragraph(f"<b>Example:</b> {self._text(entry.example)}", styles["example"])])
        table = Table(rows, colWidths=[16.8 * cm])
        red = result.result == FAIL
        table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FEF2F2" if red else "#FFFBEB")), ("BOX", (0, 0), (-1, -1), .8, colors.HexColor("#DC2626" if red else "#D97706")), ("PADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 1), (-1, -1), 2)]))
        return KeepTogether([table, Spacer(1, .18 * cm)])

    def generate(self, inspection: Inspection) -> bytes:
        results = self.db.scalars(select(ComplianceResult).where(ComplianceResult.inspection_id == inspection.id).join(ComplianceRule).order_by(ComplianceRule.rule_code)).all()
        fields = {field.field_name: field.value for field in self.db.scalars(select(ExtractedField).where(ExtractedField.inspection_id == inspection.id)).all() if field.value}
        by_severity = lambda row: (-SEVERITY_RANK.get(row.rule.severity, 0), row.rule.rule_code)
        groups = {status: sorted((row for row in results if row.result == status), key=by_severity) for status in (FAIL, REVIEW, PASS, NOT_APPLICABLE)}
        fixes, review = len(groups[FAIL]), len(groups[REVIEW])
        state = "FIX" if fixes else "CHECK" if review else "READY"
        count = fixes or review
        headline, detail, tone = VERDICT[state]
        headline = headline.format(count=count, plural="" if count == 1 else "s")
        applicable = fixes + review + len(groups[PASS])
        _, _, ruleset_label = results_ruleset(list(results))

        output = BytesIO()
        number = inspection.inspection_number
        document = SimpleDocTemplate(output, pagesize=A4, rightMargin=1.7 * cm, leftMargin=1.7 * cm, topMargin=1.5 * cm, bottomMargin=1.7 * cm, title=f"PackComply label self-check {number}")
        shaping = register_fonts()
        styles = getSampleStyleSheet()
        for style in styles.byName.values():
            if hasattr(style, "fontName"):
                style.fontName = BOLD_FONT if "Bold" in style.fontName else BODY_FONT
            if shaping:
                style.shaping = 1
        styles.add(ParagraphStyle(name="titleLG", parent=styles["Title"], alignment=TA_CENTER, textColor=colors.HexColor(TEAL), fontSize=19, leading=23))
        styles.add(ParagraphStyle(name="section", parent=styles["Heading2"], textColor=colors.HexColor(TEAL), fontSize=13, leading=16))
        styles.add(ParagraphStyle(name="body", parent=styles["BodyText"], fontSize=8.7, leading=11.5, wordWrap="CJK"))
        styles.add(ParagraphStyle(name="note", parent=styles["BodyText"], textColor=colors.HexColor("#526176"), fontSize=8, leading=10.5, wordWrap="CJK"))
        styles.add(ParagraphStyle(name="example", parent=styles["BodyText"], fontSize=8.7, leading=11.5, backColor=colors.HexColor("#FFFFFF"), borderPadding=3, wordWrap="CJK"))
        styles.add(ParagraphStyle(name="verdict", parent=styles["Heading2"], textColor=colors.HexColor(tone), fontSize=15, leading=19))

        def footer(canvas, doc) -> None:
            canvas.saveState()
            canvas.setFont(BODY_FONT, 8)
            canvas.setFillColor(colors.HexColor("#526176"))
            canvas.drawString(doc.leftMargin, 1.05 * cm, f"PackComply label self-check - {number}")
            canvas.drawRightString(A4[0] - doc.rightMargin, 1.05 * cm, f"Page {doc.page}")
            canvas.restoreState()

        company = (self.user.organization or self.user.full_name) if self.user else None
        when = inspection.completed_at or inspection.created_at
        story: list = [Paragraph("PACKCOMPLY", styles["titleLG"]), Paragraph("Pre-market label self-check", styles["Heading2"]), Spacer(1, .2 * cm)]
        story += [Paragraph(self._text(headline), styles["verdict"]), Paragraph(self._text(detail), styles["body"]), Spacer(1, .2 * cm)]
        summary_rows = [("Company", company), ("Product", display_name(inspection)), ("Net quantity", fields.get("net_quantity")), ("Self-check date", when.strftime("%d %b %Y %H:%M UTC") if when else None),
                        ("Self-check number", number), ("Readiness", f"{headline} · {len(groups[PASS])} of {applicable} applicable rules already pass" if applicable else headline), ("Checked against", ruleset_label)]
        photo = primary_image(self.db, inspection.id)
        path = resolve_upload_path(self.upload_dir, photo.storage_key) if photo else None
        table = self._rows(summary_rows, styles, (3.4 * cm, 8.6 * cm) if path is not None else (4.2 * cm, 12.6 * cm))
        if path is not None:
            try:
                width, height = (photo.width or 3), (photo.height or 4)
                box_w, box_h = 4.2 * cm, 5.6 * cm
                scale = min(box_w / width, box_h / height)
                thumb = Image(str(path), width=width * scale, height=height * scale)
                story.append(Table([[table, thumb]], colWidths=[12.2 * cm, 4.6 * cm], style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)])))
            except Exception:  # noqa: BLE001 - an unreadable thumbnail must not stop the report
                story.append(table)
        else:
            story.append(table)

        if not results:
            story += [Spacer(1, .3 * cm), Paragraph("No label check has been completed for this upload yet.", styles["body"])]
        if fixes:
            story += [Spacer(1, .28 * cm), Paragraph("Fix list: what to change on the artwork", styles["section"]), Spacer(1, .1 * cm)]
            story += [self._fix_card(index, row, styles) for index, row in enumerate(groups[FAIL], start=1)]
        if review:
            story += [Spacer(1, .2 * cm), Paragraph("Check on the pack", styles["section"]), Spacer(1, .1 * cm)]
            story += [self._fix_card(fixes + index, row, styles) for index, row in enumerate(groups[REVIEW], start=1)]
        if groups[PASS]:
            story += [Spacer(1, .2 * cm), Paragraph(f"Already correct ({len(groups[PASS])})", styles["section"]), Spacer(1, .1 * cm)]
            passed = []
            for row in groups[PASS]:
                entry = remediation_for(row.rule.rule_code)
                passed.append([Paragraph(self._text(entry.pass_text if entry else rule_title(row.rule)), styles["body"]), Paragraph(self._text(rule_legal_reference(row.rule)), styles["note"])])
            table = Table(passed, colWidths=[10.4 * cm, 6.4 * cm])
            table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#ECFDF5")), ("GRID", (0, 0), (-1, -1), .35, colors.HexColor("#A7F3D0")), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("PADDING", (0, 0), (-1, -1), 5)]))
            story.append(table)
        story += [Spacer(1, .4 * cm), Paragraph(f"<b>{self._text(SELF_CHECK_DISCLAIMER)}</b>", styles["note"]),
                  Paragraph("Findings are based on the uploaded label image only. Confirm every declaration on the final printed pack.", styles["note"])]
        document.build(story, onFirstPage=footer, onLaterPages=footer)
        return output.getvalue()
