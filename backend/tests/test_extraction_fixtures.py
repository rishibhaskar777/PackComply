"""Golden-label regression: every expected field in fixtures/labels/expected.json must pass."""
import json

import pytest

from backend.app.services.extraction import ExtractionService
from backend.scripts.eval_extraction import FIXTURE_DIR, evaluate, load_fixtures, metrics


@pytest.fixture(scope="module")
def rows():
    return evaluate()


def test_every_fixture_field_matches_ground_truth(rows) -> None:
    failures = [f"{row.fixture}.{row.field}: expected {row.expected!r}, got {row.got!r} ({row.outcome})" for row in rows if not row.ok]
    assert failures == []
    result = metrics(rows)
    assert result["precision"] == 1.0 and result["recall"] == 1.0


def test_parle_g_real_label_evidence() -> None:
    fixture = json.loads((FIXTURE_DIR / "parle_g_gluco_70g.json").read_text(encoding="utf-8"))
    fields = ExtractionService().extract(fixture["raw_text"], fixture["detections"])
    assert fields["product_name"].value.casefold() == "parle-g gluco biscuits"
    assert fields["brand_owner"].value == "PARLE PRODUCTS PVT LTD"
    assert [unit["code"] for unit in json.loads(fields["manufacturing_units"].value)] == ["HP", "MY", "H1", "BE"]
    assert fields["manufacturer"].matched_pattern == "multi_unit_label" and fields["manufacturer"].needs_review
    assert fields["net_quantity"].source_text == "NET WEIGHT: 70g"
    # The price is stamped separately on this pack: the key is found, no value is invented.
    assert fields["mrp"].value is None and fields["mrp_declaration"].value == "present_without_value"
    # Every extracted value points at real OCR boxes from the photo.
    boxes = {json.dumps(detection["bbox"]) for detection in fixture["detections"]}
    for name, value in fields.items():
        if value.value and value.source_bboxes:
            assert all(json.dumps(box) in boxes for box in value.source_bboxes), name


def test_fixture_set_covers_the_required_layouts() -> None:
    fixtures, expected = load_fixtures()
    assert len(fixtures) >= 9 and "parle_g_gluco_70g" in fixtures
    assert any(fixture["detections"] is None for fixture in fixtures.values())  # text-only fallback is measured too
