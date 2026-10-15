"""Issue-first PDF reports over persisted inspection evidence."""
from __future__ import annotations
from functools import lru_cache
from io import BytesIO
from pathlib import Path
import re
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.lib.fonts import addMapping
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import select
from sqlalchemy.orm import Session
from backend.app.models import ComplianceResult, ComplianceRule, ExtractedField, Inspection, Manufacturer, Product
from backend.app.services.risk import RiskService
from backend.app.services.quantity_verification import QuantityVerificationService

SEVERITY_RANK = {"CRITICAL": 5, "HIGH": 4, "WARNING": 3, "MEDIUM": 3, "INFO": 2, "LOW": 1}
FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
BODY_FONT, BOLD_FONT, DEVANAGARI_FONT = "NotoSans", "NotoSans-Bold", "NotoSansDevanagari"
# Devanagari, Devanagari Extended and Vedic Extensions; runs keep their inner spaces.
_DEVANAGARI = "\u0900-\u097F\uA8E0-\uA8FF\u1CD0-\u1CFF"
DEVANAGARI_RUN = re.compile(f"[{_DEVANAGARI}]+(?:\\s+[{_DEVANAGARI}]+)*")


@lru_cache
def register_fonts() -> bool:
    """Register Noto fonts once; returns whether HarfBuzz shaping is available.

    Helvetica has no Devanagari glyphs. ReportLab has no automatic per-glyph
    fallback, so Latin text uses Noto Sans and Devanagari runs are switched to
    Noto Sans Devanagari inline (see ``script_runs``). Shaping (needed for
    matras and conjuncts) requires the optional ``uharfbuzz`` package.
    """
    for family, regular, bold in ((BODY_FONT, "NotoSans-Regular.ttf", "NotoSans-Bold.ttf"), (DEVANAGARI_FONT, "NotoSansDevanagari-Regular.ttf", "NotoSansDevanagari-Bold.ttf")):
        pdfmetrics.registerFont(TTFont(family, FONT_DIR / regular))
        pdfmetrics.registerFont(TTFont(f"{family}-Bold", FONT_DIR / bold))
        for is_bold, is_italic in ((0, 0), (0, 1), (1, 0), (1, 1)):
            addMapping(family, is_bold, is_italic, f"{family}-Bold" if is_bold else family)
    try:
        import uharfbuzz  # noqa: F401
    except ImportError:
        return False
    return True


def script_runs(markup: str) -> str:
    """Wrap Devanagari runs of already-escaped text in the Devanagari font."""
    return DEVANAGARI_RUN.sub(lambda match: f'<font name="{DEVANAGARI_FONT}">{match.group(0)}</font>', markup)

class InspectionReportService:
    def __init__(self, db: Session, upload_dir: Path): self.db, self.upload_dir = db, upload_dir.resolve()
    @staticmethod
    def _text(value: object | None, fallback: str = "Not available") -> str: return script_runs(escape(str(value))) if value not in (None, "") else fallback
    @staticmethod
    def _footer(canvas, document) -> None:
        canvas.saveState(); canvas.setFont(BODY_FONT, 8); canvas.setFillColor(colors.HexColor("#526176")); canvas.drawString(document.leftMargin, 1.05 * cm, f"PackComply inspection report - {document.inspection_number}"); canvas.drawRightString(A4[0] - document.rightMargin, 1.05 * cm, f"Page {document.page}"); canvas.restoreState()
    def _heading(self, title: str, styles) -> list: return [Spacer(1, .28 * cm), Paragraph(title, styles["section"]), Spacer(1, .1 * cm)]
    def _table(self, rows: list[tuple[str, object | None]], styles) -> Table:
        cells = [[Paragraph(f"<b>{self._text(key)}</b>", styles["body"]), Paragraph(self._text(value), styles["body"])] for key, value in rows if value not in (None, "")]
        table = Table(cells, colWidths=[4.5 * cm, 12.3 * cm]); table.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F4F7FB")), ("GRID", (0, 0), (-1, -1), .35, colors.HexColor("#DDE4ED")), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("PADDING", (0, 0), (-1, -1), 6)])); return table
    def _issue_card(self, result: ComplianceResult, styles, sources: dict[str, str]) -> KeepTogether:
        strong = result.rule.severity in {"CRITICAL", "HIGH"} or result.result == "FAIL"; action = "Correct the label information and submit it for review." if result.result == "FAIL" else "Manually verify the label information and address the finding."
        rows = [("Issue", result.explanation), ("Rule", f"{result.rule.rule_code} - {result.rule.description.split('.', 1)[0]}"), ("Status / severity", f"{result.result} / {result.rule.severity}"), ("Extracted value", result.value), ("Expected / required", result.rule.description), ("Evidence", sources.get(result.field_name or "") or result.evidence or result.explanation), ("Recommended action", action)]
        table = self._table(rows, styles); color, border = ("#FDE9E9", "#D95C5C") if strong else ("#FFF4F4", "#E5A3A3")
        table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(color)), ("BOX", (0, 0), (-1, -1), 1 if strong else .6, colors.HexColor(border)), ("INNERGRID", (0, 0), (-1, -1), .3, colors.HexColor("#F0CACA"))])); return KeepTogether([table, Spacer(1, .16 * cm)])
    def generate(self, inspection: Inspection) -> bytes:
        product = self.db.get(Product, inspection.product_id) if inspection.product_id else None; manufacturer = self.db.get(Manufacturer, inspection.manufacturer_id) if inspection.manufacturer_id else None
        fields = self.db.scalars(select(ExtractedField).where(ExtractedField.inspection_id == inspection.id).order_by(ExtractedField.field_name)).all(); findings = self.db.scalars(select(ComplianceResult).where(ComplianceResult.inspection_id == inspection.id).join(ComplianceRule).order_by(ComplianceRule.rule_code)).all()
        values = {field.field_name: field.value for field in fields if field.value not in (None, "")}; sources = {field.field_name: field.source_text for field in fields if field.source_text}  # Evidence is the OCR line a value came from.
        counts = {state: sum(row.result == state for row in findings) for state in ("PASS", "REVIEW", "FAIL")}; overall = "FAIL" if counts["FAIL"] else "WARNING" if counts["REVIEW"] else "PASS" if findings else "Not available"; risk = RiskService(self.db).for_inspection(inspection) if inspection.status == "COMPLETED" else None
        issues = sorted((row for row in findings if row.result in {"FAIL", "REVIEW"}), key=lambda row: (-SEVERITY_RANK.get(row.rule.severity, 0), row.rule.rule_code))
        quantity_result = next((row for row in findings if row.rule.rule_code == "NET_QUANTITY_FORMAT"), None)
        quantity = QuantityVerificationService().verify(values.get("net_quantity"), status=quantity_result.result if quantity_result else None, reason=quantity_result.explanation if quantity_result else None, severity=quantity_result.rule.severity if quantity_result else None, evidence=sources.get("net_quantity") or (quantity_result.evidence if quantity_result else values.get("net_quantity")), requirement=quantity_result.rule.description if quantity_result else None)
        output = BytesIO(); document = SimpleDocTemplate(output, pagesize=A4, rightMargin=1.7 * cm, leftMargin=1.7 * cm, topMargin=1.5 * cm, bottomMargin=1.7 * cm, title=f"PackComply inspection {inspection.inspection_number}"); document.inspection_number = inspection.inspection_number
        shaping = register_fonts(); styles = getSampleStyleSheet()
        for style in styles.byName.values():
            if hasattr(style, "fontName"): style.fontName = BOLD_FONT if "Bold" in style.fontName else BODY_FONT
            if shaping: style.shaping = 1
        styles.add(ParagraphStyle(name="titleLG", parent=styles["Title"], alignment=TA_CENTER, textColor=colors.HexColor("#087F70"), fontSize=19, leading=23)); styles.add(ParagraphStyle(name="section", parent=styles["Heading2"], textColor=colors.HexColor("#087F70"), fontSize=13, leading=16)); styles.add(ParagraphStyle(name="body", parent=styles["BodyText"], fontSize=8.7, leading=11.5, wordWrap="CJK")); styles.add(ParagraphStyle(name="positive", parent=styles["BodyText"], textColor=colors.HexColor("#09685D"), fontSize=10, leading=13))
        story = [Paragraph("PACKCOMPLY", styles["titleLG"]), Paragraph("Legal Metrology Inspection Report", styles["Heading2"]), Spacer(1, .22 * cm)]
        story += self._heading("Inspection Summary", styles); story.append(self._table([("Inspection ID", inspection.inspection_number), ("Date / time", inspection.completed_at or inspection.inspected_at), ("Product", values.get("product_name") or (product.name if product else None)), ("Manufacturer / company", values.get("manufacturer") or (manufacturer.name if manufacturer else None)), ("Overall compliance", overall), ("Risk score", f"{risk.risk_score} / 100" if risk else None), ("Risk level", risk.risk_level if risk else None)], styles))
        story += self._heading("Important Issues", styles); story.extend(self._issue_card(row, styles, sources) for row in issues) if issues else story.append(Paragraph("<b>No compliance issues detected.</b> Persisted compliance findings contain no FAIL or REVIEW result.", styles["positive"]))
        story += self._heading("Quantity Verification", styles)
        quantity_rows = [("Verification", "Declared quantity verification — no physical measurement"), ("Declared quantity", quantity.declared_quantity), ("Normalized quantity", quantity.normalized_quantity), ("Status", f"{quantity.status} / {quantity.severity}"), ("Finding", quantity.reason), ("Rule", quantity.rule_code), ("Requirement", quantity.requirement), ("Evidence", quantity.evidence)]
        quantity_table = self._table(quantity_rows, styles)
        if quantity.status in {"FAIL", "REVIEW"}:
            quantity_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFF4F4")), ("BOX", (0, 0), (-1, -1), .6, colors.HexColor("#E5A3A3"))]))
        story.append(quantity_table)
        story += self._heading("Legal Metrology Compliance", styles)
        if findings:
            rows = [[Paragraph("<b>Rule code / rule</b>", styles["body"]), Paragraph("<b>Status</b>", styles["body"]), Paragraph("<b>Severity</b>", styles["body"])]] + [[Paragraph(self._text(f"{row.rule.rule_code} - {row.rule.description.split('.', 1)[0]}"), styles["body"]), Paragraph(self._text(row.result), styles["body"]), Paragraph(self._text(row.rule.severity), styles["body"])] for row in sorted(findings, key=lambda item: (-SEVERITY_RANK.get(item.rule.severity, 0), item.rule.rule_code))]
            table = Table(rows, colWidths=[10.6 * cm, 3 * cm, 3.2 * cm], repeatRows=1); table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EAF8F5")), ("GRID", (0, 0), (-1, -1), .35, colors.HexColor("#DDE4ED")), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("PADDING", (0, 0), (-1, -1), 6)])); story.append(table)
        else: story.append(Paragraph("No persisted compliance analysis is available for this inspection.", styles["body"]))
        if issues:
            story += self._heading("Recommended / Required Actions", styles); story.append(Paragraph("<br/>".join(f"- {self._text(row.rule.rule_code)}: {'Correct the label information and submit it for review.' if row.result == 'FAIL' else 'Manually verify and address this finding.'}" for row in issues), styles["body"]))
        document.build(story, onFirstPage=self._footer, onLaterPages=self._footer); return output.getvalue()
