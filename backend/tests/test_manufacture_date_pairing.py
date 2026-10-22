"""Manufacture / packing date detection: stacked key pairing, extra key wordings and YYYY-MM formats.

Every case is a general pattern, invented text and boxes.  The safety line they all hold: a missing date is
never turned into a found one, and a bare key or a party line ("Packed by ...") never yields a date.
"""
import pytest

from backend.app.services.extraction import ExtractionService
from backend.scripts.batch_evaluate import analyse


def row(text: str, x: float, y: float, height: float = 16) -> dict:
    width = 0.55 * height * len(text)
    return {"text": text, "confidence": 0.98, "bbox": [[x, y], [x + width, y], [x + width, y + height], [x, y + height]]}


def extract_rows(*rows: dict) -> dict:
    return ExtractionService().extract("\n".join(item["text"] for item in rows), list(rows))


def mfg_status(text: str) -> str:
    return next(item["status"] for item in analyse({"raw_text": text})["findings"] if item["rule_id"] == "MANUFACTURE_DATE")


# A stacked key column on a tilted pack: the values sit lower than their keys, level with the next key.
KEYS = (row("Mfg. Date", 100, 300), row("Expiry Date", 100, 320))


def test_two_dates_in_a_stacked_column_are_paired_top_to_bottom() -> None:
    fields = extract_rows(*KEYS, row("04/2026", 300, 314), row("03/2028", 300, 334))
    assert fields["manufacturing_date"].normalized_value == "2026-04"
    assert fields["expiry_date"].normalized_value == "2028-03"


def test_a_pair_that_would_put_manufacture_after_expiry_is_skipped() -> None:
    fields = extract_rows(*KEYS, row("03/2028", 300, 314), row("04/2026", 300, 334))
    assert not fields["manufacturing_date"].value


def test_one_straddling_date_is_the_manufacture_date_only_when_a_later_date_is_printed() -> None:
    later = row("03/28", 600, 40)
    fields = extract_rows(*KEYS, row("04/2026", 300, 312), later)
    assert fields["manufacturing_date"].normalized_value == "2026-04"
    assert not fields["expiry_date"].value  # the wrong expiry is not stored
    alone = extract_rows(*KEYS, row("04/2026", 300, 312))
    assert not alone["manufacturing_date"].value  # ambiguous: nothing is invented


def test_a_single_straddling_date_with_an_earlier_other_date_is_not_moved() -> None:
    fields = extract_rows(*KEYS, row("04/2026", 300, 312), row("03/24", 600, 40))
    assert not fields["manufacturing_date"].value


def test_correctly_aligned_keys_and_values_are_left_alone() -> None:
    fields = extract_rows(row("Mfg. Date: 04/2026", 100, 300), row("Expiry Date: 03/2028", 100, 320))
    assert (fields["manufacturing_date"].normalized_value, fields["expiry_date"].normalized_value) == ("2026-04", "2028-03")
    assert "stacked" not in fields["manufacturing_date"].matched_pattern
    aligned = extract_rows(*KEYS, row("04/2026", 300, 300), row("03/2028", 300, 320))
    assert (aligned["manufacturing_date"].normalized_value, aligned["expiry_date"].normalized_value) == ("2026-04", "2028-03")


def test_an_expiry_that_already_has_its_own_value_is_not_overwritten() -> None:
    # The expiry key has its value on its own line; only the manufacture key lacks one.
    fields = extract_rows(*KEYS[:1], row("Expiry Date: 03/2028", 100, 320), row("04/2026", 300, 312))
    assert fields["expiry_date"].normalized_value == "2028-03"
    assert fields["expiry_date"].matched_pattern == "expiry_date:same_line"


@pytest.mark.parametrize("text", ["Mfg. Date:", "Mfg. Date\nExpiry Date", "Mfd.", "Production Date", "Packed on", "Pack Date:", "Made on"])
def test_a_bare_key_with_no_value_stays_review(text: str) -> None:
    result = analyse({"raw_text": text})
    assert not result["fields"].get("manufacturing_date") and not result["fields"].get("packing_date")
    assert mfg_status(text) == "REVIEW"


@pytest.mark.parametrize("text", [
    "Packed by X Ltd",
    "Packed by: Acme Foods Pvt. Ltd., Pune 411001",
    "Manufactured by Acme Foods Pvt. Ltd.",
    "Manufactured by: Acme Foods, 12 Industrial Area",
    "Manufactured for Acme Retail",
    "Best before 12 months from mfg",
    "Made in India",
    "Packed in a facility that handles nuts",
    "Packed 12.50",
    "Mfg Date 2026-05-12 extra",
])
def test_party_lines_and_shelf_life_produce_no_date(text: str) -> None:
    fields = ExtractionService().extract(text)
    assert not fields["manufacturing_date"].value, fields["manufacturing_date"]
    assert not fields["packing_date"].value, fields["packing_date"]
    assert mfg_status(text) == "REVIEW"


@pytest.mark.parametrize(("text", "field", "normalized"), [
    ("Production Date: 05/2026", "manufacturing_date", "2026-05"),
    ("Prod. Date 05/2026", "manufacturing_date", "2026-05"),
    ("Made on 05/2026", "manufacturing_date", "2026-05"),
    ("M.F.G. 05/2026", "manufacturing_date", "2026-05"),
    ("M.F.G.: MAY 2026", "manufacturing_date", "2026-05"),
    ("Pack Date: 05/2026", "packing_date", "2026-05"),
    ("Manufactured: 05/2026", "manufacturing_date", "2026-05"),
    ("Manufactured 05/2026", "manufacturing_date", "2026-05"),
    ("Packed: 05/2026", "packing_date", "2026-05"),
    ("Mfg Date: 2026-05", "manufacturing_date", "2026-05"),
    ("Mfg Date: 2026/05", "manufacturing_date", "2026-05"),
    ("Pkd 2026-05", "packing_date", "2026-05"),
])
def test_new_wordings_and_formats_need_a_date_beside_the_key(text: str, field: str, normalized: str) -> None:
    fields = ExtractionService().extract(text)
    assert fields[field].normalized_value == normalized
    assert mfg_status(text) == "PASS"


def test_a_year_month_date_reaches_the_rule_as_month_year() -> None:
    result = analyse({"raw_text": "Mfg Date: 2026-05\nExpiry Date: 2028-04"})
    findings = {item["rule_id"]: item for item in result["findings"]}
    assert findings["MANUFACTURE_DATE"]["status"] == "PASS" and findings["MANUFACTURE_DATE"]["value"] == "05/2026"
    assert findings["DATE_CONSISTENCY"]["status"] == "PASS"
