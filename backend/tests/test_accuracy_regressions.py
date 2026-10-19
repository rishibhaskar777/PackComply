"""Regression tests for the two false PASS findings of the dev evaluation, and the root causes behind them.

1. Patanjali photo: CONSUMER_CARE_FORMAT passed on a lone, misread e-mail ("wecere@..." for "wecare@..."), the telephone
   number unread. Root cause: the rule accepted either contact detail as a complete declaration.
2. Godrej photo: MANUFACTURER_INFORMATION passed on "S. Godrej Consumer Products LTD." - OCR made an "S." of a logo that
   precedes the name. Root cause: a stray leading initial was kept as part of the company name.
3. Found while fixing them: a line that is tilted or curved overlaps the line above it by a third, and the geometric
   duplicate filter dropped it as a "re-reading", losing the "incl. of all taxes" statement; and a stray "016" beside an
   "MRP ... see below" key was read as a price of 16 rupees.
"""
import pytest

from backend.app.services.compliance.rule_engine import ComplianceEngine, FieldEvidence
from backend.app.services.extraction import ExtractionService
from backend.app.services.extraction.layout import Layout, LayoutBox, suppress_duplicates


def care_status(declaration: str = "present_with_details", **fields: str) -> tuple[str, str]:
    evidence = {name: FieldEvidence(value, 0.9, False, value) for name, value in {"consumer_care_declaration": declaration, **fields}.items()}
    finding = next(item for item in ComplianceEngine().evaluate(evidence) if item.rule_id == "CONSUMER_CARE_FORMAT")
    return finding.status, finding.message


def test_a_lone_contact_detail_without_a_consumer_care_heading_is_not_a_pass() -> None:
    # Patanjali photo: heading unreadable ("absent"), phone unread, e-mail misread ("wecere" for "wecare").
    status, message = care_status("absent", consumer_care_email="wecere@patanjalifoods.co.in")
    assert status == "REVIEW" and "no consumer-care heading" in message
    assert care_status("absent", consumer_care_phone="18002660007")[0] == "REVIEW"
    # With the heading found, or with both details, the declaration stands.
    assert care_status("present_with_details", consumer_care_email="care@godrejcp.com")[0] == "PASS"
    assert care_status("absent", consumer_care_phone="18002660007", consumer_care_email="care@godrejcp.com")[0] == "PASS"
    # A partly read declaration is never a violation.
    assert care_status("absent", consumer_care_email="a@b.in")[0] != "FAIL"


GODREJ = """Marketed by**/Registered
#Godrej Consumer Products
Limited Godrej one, Vikhroli (East)
Mumbai 400 079, India
Manufactured by:
S. Godrej Consumer Products
LTD. Plot No.. 85-88, EPIP Phase-II
Thana, Baddi, Tehsil Nalagarh,
DISTT. SOLAN, (H.P.)-173205. India
"""


def test_a_stray_initial_made_of_a_logo_is_dropped_when_the_name_is_printed_without_it_elsewhere() -> None:
    manufacturer = ExtractionService().extract(GODREJ)["manufacturer"]
    assert manufacturer.value is not None and manufacturer.value.startswith("Godrej Consumer Products"), manufacturer.value
    assert not manufacturer.value.startswith("S.")


def test_a_genuine_initial_is_kept() -> None:
    text = "Manufactured by:\nM. K. Foods Pvt Ltd, Plot 4, Sector 7, Noida 201301\n"
    assert ExtractionService().extract(text)["manufacturer"].value.startswith("M. K. Foods")
    # Nothing else on the label prints the name without the initial, so there is no evidence it is noise.
    alone = "Manufactured by:\nS. Kumar Industries Pvt Ltd, Plot 4, Sector 7, Noida 201301\n"
    assert ExtractionService().extract(alone)["manufacturer"].value.startswith("S. Kumar")


def box(text: str, x0: float, y0: float, x1: float, y1: float, language: str | None = None, tilt: float = 0.0) -> LayoutBox:
    return LayoutBox(text, 0.95, [[x0, y0 + tilt], [x1, y0], [x1, y1], [x0, y1 + tilt]], language)


def test_tilted_neighbouring_lines_of_one_pass_are_both_kept() -> None:
    upper = box("For Batch No., Mfg. Date-Use before Date,", 272, 810, 499, 826, "en", tilt=-5)
    lower = box("MRP Rs. Incl. of all taxes and USP, see below:", 272, 823, 518, 841, "en", tilt=-6)
    assert [item.text for item in suppress_duplicates([upper, lower])] == [upper.text, lower.text]


def test_a_second_language_rereading_the_same_region_is_still_a_duplicate() -> None:
    english = box("Net Wt: 70 g", 100, 100, 300, 130, "en")
    reread = box("Net Wt: 70 g", 101, 101, 301, 131, "hi")
    assert [item.text for item in suppress_duplicates([english, reread])] == ["Net Wt: 70 g"]
    # Even within one pass, a box that is (almost) the same region is a duplicate.
    assert len(suppress_duplicates([english, box("Net Wt: 70 g", 100, 101, 300, 131, "en")])) == 1


def test_the_tax_statement_on_a_tilted_line_below_another_line_is_extracted() -> None:
    detections = [
        {"text": "For Batch No., Mfg. Date-Use before Date,", "confidence": 0.95, "bbox": [[272, 810], [499, 805], [499, 826], [272, 831]], "language": "en"},
        {"text": "MRP Rs. IncL. of all taxes and USP, see below:", "confidence": 0.9, "bbox": [[272, 823], [518, 817], [518, 841], [272, 847]], "language": "en"},
    ]
    layout = Layout.build("", detections)
    assert any("all taxes" in line.text for line in layout.lines)


@pytest.mark.parametrize("stray", ["016", "007", "05"])
def test_a_number_with_a_leading_zero_is_not_a_price(stray: str) -> None:
    result = ExtractionService().extract(f"MRP Rs. incl. of all taxes see below: {stray}\nNet Wt: 70 g\n")
    assert result["mrp"].value is None


def test_a_decimal_price_below_one_rupee_is_still_a_price() -> None:
    assert ExtractionService().extract("MRP: ₹0.50\n")["mrp"].value == "₹0.50"
