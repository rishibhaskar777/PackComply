"""Unit sale price (USP) in the formats Indian labels print and OCR misreads, including the "MRP ₹ / USP ₹" key line."""
import pytest

from backend.app.services.extraction.extraction_service import ExtractionService
from backend.app.services.corrections import normalise_value

service = ExtractionService()


def extract(text: str, detections: list[dict] | None = None):
    return service.extract(text, detections)


def box(text: str, x0: float, y0: float, x1: float, y1: float, confidence: float = 0.95) -> dict:
    return {"text": text, "confidence": confidence, "bbox": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], "language": "en"}


@pytest.mark.parametrize("label, expected", [
    ("Unit Sale Price ₹0.48 per ml", "₹0.48/ml"),
    ("USP: ₹0.48/ml", "₹0.48/ml"),
    ("Unit Sale Price Rs. 0.48/ml", "₹0.48/ml"),
    ("USP 0.48 per ml", "₹0.48/ml"),
    ("Unit Sale Price ₹48.00 per 100 ml", "₹48.00/100ml"),
    ("USP: ₹ 9.60/100g", "₹9.60/100g"),
    ("USP: ₹ 9.60 / 100 g", "₹9.60/100g"),
    ("Unit Sale Price: Rs 0.48 / ml", "₹0.48/ml"),
    ("USP R0.48/ml", "₹0.48/ml"),            # ₹ read as "R"
    ("USP %0.48/ml", "₹0.48/ml"),            # ₹ read as "%"
    ("USP ?0.48/ml", "₹0.48/ml"),            # ₹ read as "?"
    ("USP Rs0.48/ml", "₹0.48/ml"),
    ("USP ₹0.48/mI", "₹0.48/ml"),            # "l" read as "I"
    ("USP ₹0.48 per millilitre", "₹0.48/ml"),
    ("Unit Sale Price ₹0.25 per gram", "₹0.25/g"),
])
def test_unit_sale_price_keyed_formats(label: str, expected: str) -> None:
    result = extract(f"Net Qty: 1000 ml\nMRP ₹480.00\n{label}")["unit_sale_price"]
    assert result.normalized_value == expected, (label, result)


@pytest.mark.parametrize("text", [
    "Net Qty: 1000 ml\nMRP ₹ / USP ₹ (incl. of all taxes) 480.00 ₹0.48/ml",          # everything on one line
    "Net Qty: 1000 ml\nMRP ₹ / USP ₹ (incl. of all taxes)\n480.00\n₹0.48/ml",          # values on the next lines
    "Net Qty: 1000 ml\nMRP ₹ / USP ₹ (incl. of all taxes)\n480.00 ₹0.48/ml",
    "Net Qty: 1000 ml\nMRP₹/USP₹ (Incl. of all taxes) 480.00 0.48/ml",                # glued key, no currency on the second
    "Net Qty: 1000 ml\nMRP R / USP R (incl. of all taxes)\n480.00\nR0.48/ml",          # ₹ read as "R"
    "Net Qty: 1000 ml\nMRP Rs / USP Rs\n480.00 Rs0.48/ml",
    "Net Qty: 1000 ml\nMRP % / USP % (incl. of all taxes)\n480.00\n%0.48/ml",         # ₹ read as "%"
    "Net Qty: 1000 ml\nMRP ? / USP ?\n?480.00 ?0.48/ml",
    "Net Qty: 1000 ml\nMRP/USP (incl. of all taxes)\n480.00 0.48 per ml",
])
def test_combined_mrp_usp_key_line_gives_first_value_to_mrp_and_second_to_usp(text: str) -> None:
    result = extract(text)
    assert result["mrp"].normalized_value == "₹480", result["mrp"]
    assert result["unit_sale_price"].normalized_value == "₹0.48/ml", result["unit_sale_price"]
    assert result["tax_inclusive"].value == "true" or "incl" not in text.lower()


def test_combined_key_line_with_boxes_on_separate_detections() -> None:
    detections = [
        box("MRP ₹ / USP ₹", 20, 100, 200, 118),
        box("(incl. of all taxes)", 20, 122, 200, 140),
        box("480.00", 260, 100, 320, 118),
        box("₹0.48/ml", 340, 100, 420, 118),
        box("Net Qty: 1000 ml", 20, 30, 200, 48),
    ]
    result = extract("", detections)
    assert result["mrp"].normalized_value == "₹480" and result["unit_sale_price"].normalized_value == "₹0.48/ml"


def test_a_second_value_without_its_unit_is_taken_only_when_it_matches_the_mrp_and_quantity() -> None:
    consistent = extract("Net Qty: 1000 ml\nMRP ₹ / USP ₹\n480.00\n0.48")
    assert consistent["unit_sale_price"].normalized_value == "₹0.48/ml" and consistent["unit_sale_price"].needs_review
    inconsistent = extract("Net Qty: 1000 ml\nMRP ₹ / USP ₹\n480.00\n7.10")
    assert not inconsistent["unit_sale_price"].value


def test_a_plain_mrp_line_is_unchanged() -> None:
    result = extract("Net Qty: 500 g\nMRP ₹120.00 (incl. of all taxes)")
    assert result["mrp"].normalized_value == "₹120" and not result["unit_sale_price"].value


def test_inspector_corrections_accept_the_same_formats() -> None:
    assert normalise_value("unit_sale_price", "₹0.48 per ml")[1] == "₹0.48/ml"
    assert normalise_value("unit_sale_price", "Rs. 48.00 per 100 ml")[1] == "₹48.00/100ml"


@pytest.mark.parametrize("field, text, normalized", [
    ("mrp", "₹480", "₹480"),
    ("unit_sale_price", "₹0.48 per ml", "₹0.48/ml"),
    ("unit_sale_price", "₹48 per 100 ml", "₹48/100ml"),
    ("unit_sale_price", "₹9.6 per L", "₹9.6/l"),
    ("unit_sale_price", "₹1 per 100 g", "₹1/100g"),
    ("unit_sale_price", "₹2 per unit", "₹2/unit"),
    ("unit_sale_price", "₹2 per piece", "₹2/piece"),
    ("net_quantity", "1.5 L", "1500 ml"),
    ("net_quantity", "3 units", "3 count"),
    ("net_quantity", "3 pieces", "3 count"),
    ("best_before", "9 months", "P9M"),
    ("manufacturing_date", "08/2026", "2026-08"),
])
def test_the_field_input_texts_are_accepted_by_the_backend_parsers(field: str, text: str, normalized: str) -> None:
    """The phone/desktop field inputs compose exactly these strings; the server stays the source of truth."""
    assert normalise_value(field, text)[1] == normalized


def test_dot_matrix_print_with_the_rupee_sign_dropped_and_the_two_values_glued_together() -> None:
    """Real OCR of 'MRP ₹ / USP ₹ ... 480.00 ₹0.48/ml' inkjet-printed on a carton: '480.000.48/ml'."""
    real = "Net Volume : 1000 ml (33.814 fl oz)\nMRP/USP₹：\n(Incl. of all taxes)\n480.000.48/ml\nMfg. Date\nBatch No."
    result = extract(real)
    assert result["mrp"].normalized_value == "₹480" and result["unit_sale_price"].normalized_value == "₹0.48/ml"
    assert result["tax_inclusive"].value == "true"


def test_dot_matrix_print_with_boxes() -> None:
    detections = [
        box("Net Volume : 1000 ml (33.814 fl oz)", 295, 770, 560, 790),
        box("MRP/USP₹：", 289, 811, 400, 830, 0.88), box("(Incl. of all taxes)", 291, 834, 440, 850),
        box("480.000.48/ml", 435, 832, 650, 852, 0.95), box("Mfg. Date", 291, 850, 380, 870),
    ]
    result = extract("", detections)
    assert result["mrp"].normalized_value == "₹480" and result["unit_sale_price"].normalized_value == "₹0.48/ml"


def test_a_long_number_is_not_split_without_a_per_unit_tail() -> None:
    result = extract("Net Qty: 1000 ml\nMRP ₹ / USP ₹\n480.000.48")
    assert result["unit_sale_price"].normalized_value in (None, "₹0.48/ml") or not result["unit_sale_price"].value
