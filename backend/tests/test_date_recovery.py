"""Second OCR pass for date stamps: when it runs, what it may add, and what it must never turn into a date.

Deterministic: the whole-photo OCR of the real photo and the crop reads are cached fixtures (no live OCR); the
synthetic pages feed the crop pass a fake reader.  The synthetic key rows are 40 px tall, so the crop is not
reduced and a crop pixel is the photo pixel minus the crop origin (CROP_X, CROP_Y).
"""
from dataclasses import asdict
import json
from pathlib import Path

from PIL import Image
import pytest

from backend.app.services.ocr import OcrDetection
from backend.app.services.ocr import date_recovery
from backend.app.services.ocr.date_recovery import recover_dates, six_digit_suggestion
from backend.scripts.batch_evaluate import analyse

FIXTURES = Path(__file__).parent / "fixtures" / "date_recovery"
PAGE = (1600, 900)
# Synthetic keys: x 100-300, 40 px tall.  The first key's row is y 200; the crop starts 12 px left of and 60 px above it.
CROP_X, CROP_Y = 88, 140
ROW_MFG, ROW_BATCH, ROW_EXPIRY = 200, 265, 330


def det(text: str, x: float, y: float, width: float = 200, height: float = 40, confidence: float = 0.98) -> OcrDetection:
    return OcrDetection(text, confidence, [[x, y], [x + width, y], [x + width, y + height], [x, y + height]], "en")


def stamp(text: str, row: float, confidence: float = 0.95, crop_y: float = CROP_Y) -> OcrDetection:
    """A crop-read stamp level with ``row``, in crop pixels (``crop_y``: the crop's top when the first key is not at ROW_MFG)."""
    return det(text, 500 - CROP_X, row - crop_y, 220, 40, confidence)


def page(*rows: tuple[str, int]) -> list[OcrDetection]:
    return [det(text, 100, y) for text, y in rows]


class Reader:
    """A crop reader returning the same canned detections for each variant, counting its calls."""

    def __init__(self, *found: OcrDetection) -> None:
        self.found, self.calls = list(found), 0

    def __call__(self, path: Path) -> list[OcrDetection]:
        self.calls += 1
        return list(self.found)


def recover(tmp_path: Path, detections: list[OcrDetection], reader: Reader, size: tuple[int, int] = PAGE):
    image = tmp_path / "photo.png"
    Image.new("RGB", size, "white").save(image)
    result = recover_dates(image, "\n".join(item.text for item in detections), detections, reader)
    return result, analyse({"raw_text": "\n".join(item.text for item in result.detections), "detections": [asdict(item) for item in result.detections if True]})


def rule(analysis: dict, code: str) -> dict:
    return next(item for item in analysis["findings"] if item["rule_id"] == code)


def fields(analysis: dict) -> dict:
    return {name: item["value"] for name, item in analysis["fields"].items()}


# ------------------------------------------------------------- acceptance

def test_clean_date_with_separators_is_accepted_and_passes(tmp_path: Path) -> None:
    result, analysis = recover(tmp_path, page(("Mfg. Date", ROW_MFG)), Reader(stamp("05/2026", ROW_MFG, 0.94)))
    assert result.accepted == ["05/2026"]
    assert fields(analysis)["manufacturing_date"] == "05/2026"
    assert rule(analysis, "MANUFACTURE_DATE")["status"] == "PASS"


def test_clean_date_below_the_confidence_floor_is_not_accepted(tmp_path: Path) -> None:
    result, analysis = recover(tmp_path, page(("Mfg. Date", ROW_MFG)), Reader(stamp("05/2026", ROW_MFG, 0.7)))
    assert not result.accepted and "manufacturing_date" not in fields(analysis)
    assert rule(analysis, "MANUFACTURE_DATE")["status"] == "REVIEW"


def test_six_digit_stamp_level_with_a_date_key_is_a_suggestion_never_a_pass(tmp_path: Path) -> None:
    result, analysis = recover(tmp_path, page(("Mfg. Date", ROW_MFG)), Reader(stamp("052026", ROW_MFG, 0.95)))
    finding = rule(analysis, "MANUFACTURE_DATE")
    assert finding["status"] == "REVIEW" and "please confirm: 05/2026" in finding["message"]
    assert finding["value"] is None  # the rules never see the guess
    assert analysis["fields"]["manufacturing_date"]["matched_pattern"].endswith(":recovered_guess")
    assert rule(analysis, "DATE_CONSISTENCY")["status"] == "NOT_APPLICABLE"


def test_six_digit_expiry_suggestion_cannot_pass_the_date_check(tmp_path: Path) -> None:
    detections = page(("Mfg. Date", ROW_MFG), ("Expiry Date", ROW_EXPIRY))
    result, analysis = recover(tmp_path, detections, Reader(stamp("052026", ROW_MFG, 0.95), stamp("042028", ROW_EXPIRY, 0.95)))
    assert analysis["fields"]["expiry_date"]["value"] == "04/2028"
    assert analysis["fields"]["expiry_date"]["matched_pattern"].endswith(":recovered_guess")
    assert rule(analysis, "DATE_CONSISTENCY")["status"] == "NOT_APPLICABLE"
    assert rule(analysis, "MANUFACTURE_DATE")["status"] == "REVIEW"


@pytest.mark.parametrize("text", ["05026", "0520260", "05 2026x", "052O26", "05-026"])
def test_a_lost_digit_or_anything_else_is_dropped_not_guessed(tmp_path: Path, text: str) -> None:
    result, analysis = recover(tmp_path, page(("Mfg. Date", ROW_MFG)), Reader(stamp(text, ROW_MFG, 0.95)))
    assert not result.accepted
    assert "manufacturing_date" not in fields(analysis)
    assert rule(analysis, "MANUFACTURE_DATE")["status"] == "REVIEW"
    assert "please confirm" not in rule(analysis, "MANUFACTURE_DATE")["message"]


@pytest.mark.parametrize(("text", "suggestion"), [("042028", "04/2028"), ("122020", "12/2020"), ("012040", "01/2040"),
                                                    ("132028", None), ("002028", None), ("051999", None), ("052019", None), ("052041", None), ("04202", None)])
def test_six_digit_month_and_year_limits(text: str, suggestion: str | None) -> None:
    assert six_digit_suggestion(text, 0.95) == suggestion


def test_six_digit_stamp_needs_a_high_read_confidence() -> None:
    assert six_digit_suggestion("042028", 0.89) is None and six_digit_suggestion("042028", 0.9) == "04/2028" and six_digit_suggestion("042028", None) is None


@pytest.mark.parametrize("text", ["13/2028", "05/1999", "00/2028"])
def test_clean_looking_dates_with_a_bad_month_or_year_are_rejected(tmp_path: Path, text: str) -> None:
    result, analysis = recover(tmp_path, page(("Mfg. Date", ROW_MFG)), Reader(stamp(text, ROW_MFG, 0.95)))
    assert not result.accepted and "manufacturing_date" not in fields(analysis)


# ----------------------------------------------------------------- never a date

def test_a_six_digit_code_beside_a_batch_key_is_never_a_date(tmp_path: Path) -> None:
    # The date key is further up; the six digits are level with the Batch No. row, which is nearer.
    detections = page(("Mfg. Date", ROW_MFG), ("Batch No.", ROW_BATCH))
    result, analysis = recover(tmp_path, detections, Reader(stamp("042028", ROW_BATCH, 0.97)))
    assert not result.accepted
    assert "manufacturing_date" not in fields(analysis) and rule(analysis, "MANUFACTURE_DATE")["status"] == "REVIEW"


def test_a_six_digit_code_with_no_date_key_beside_it_is_never_a_date_and_the_pass_never_runs(tmp_path: Path) -> None:
    reader = Reader(stamp("042028", ROW_BATCH, 0.97))
    result, analysis = recover(tmp_path, page(("Batch No.", ROW_BATCH), ("Net Wt.", 400)), reader)
    assert reader.calls == 0 and result.crops == 0
    assert not {"manufacturing_date", "expiry_date"} & set(fields(analysis))


def test_a_six_digit_code_far_from_any_date_key_is_not_taken(tmp_path: Path) -> None:
    result, analysis = recover(tmp_path, page(("Mfg. Date", ROW_MFG)), Reader(stamp("042028", ROW_MFG + 300, 0.97)))
    assert not result.accepted and "manufacturing_date" not in fields(analysis)


# ----------------------------------------------------------------- when it runs

@pytest.mark.parametrize("rows", [
    [("Net Wt.", 200), ("MRP", 265)],
    [("Batch No.", 200), ("Ingredients", 265)],
    [("Mfd. by", 200)],                      # a party key, not a date key
    [("Best before 12 months", 200)],
])
def test_a_photo_without_a_date_key_never_runs_the_second_pass(tmp_path: Path, rows: list[tuple[str, int]]) -> None:
    reader = Reader(stamp("05/2026", 200))
    result, _ = recover(tmp_path, page(*rows), reader)
    assert reader.calls == 0 and result.crops == 0 and result.detections == page(*rows)


def test_a_date_key_that_already_has_its_value_never_runs_the_second_pass(tmp_path: Path) -> None:
    reader = Reader(stamp("05/2026", ROW_MFG))
    detections = [*page(("Mfg. Date", ROW_MFG)), det("04/2026", 500, ROW_MFG, 200)]
    result, _ = recover(tmp_path, detections, reader)
    assert reader.calls == 0 and result.crops == 0


def test_the_pass_is_capped_at_two_crops_and_two_variants(tmp_path: Path) -> None:
    # Three date keys far apart: three clusters, only two crops; each read in at most two variants.
    detections = page(("Mfg. Date", 100), ("Expiry Date", 450), ("Pkd.", 800))
    reader = Reader()
    result, _ = recover(tmp_path, detections, reader, size=(1600, 1000))
    assert result.crops == 2 and reader.calls <= 4


def test_it_stops_after_the_first_variant_when_every_key_has_a_clean_date(tmp_path: Path) -> None:
    reader = Reader(stamp("05/2026", ROW_MFG, 0.95))
    recover(tmp_path, page(("Mfg. Date", ROW_MFG)), reader)
    assert reader.calls == 1


def test_a_rotated_photo_is_left_alone(tmp_path: Path) -> None:
    detections = [OcrDetection("Mfg. Date", 0.98, [[100, 200], [300, 200], [300, 240], [100, 240]], "en", 270)]
    reader = Reader(stamp("05/2026", ROW_MFG))
    result, _ = recover(tmp_path, detections, reader)
    assert reader.calls == 0 and result.crops == 0


# ------------------------------------------------------ the real dot-matrix photo (cached OCR)

def cached_photo() -> tuple[dict, list[list[dict]]]:
    return (json.loads((FIXTURES / "real_1000089660_first_pass.json").read_text("utf-8")),
            json.loads((FIXTURES / "real_1000089660_crop_reads.json").read_text("utf-8")))


def run_cached_photo(tmp_path: Path):
    first, reads = cached_photo()
    detections = [OcrDetection(**item) for item in first["detections"]]
    queue = iter(reads)

    def read(path: Path) -> list[OcrDetection]:
        return [OcrDetection(**item) for item in next(queue)]

    image = tmp_path / "photo.png"
    Image.new("RGB", (first["image_width"], first["image_height"]), "white").save(image)
    result = recover_dates(image, first["raw_text"], detections, read)
    after = {"raw_text": "\n".join(item.text for item in result.detections), "detections": [{k: v for k, v in asdict(item).items() if k != "recovery" or v} for item in result.detections]}
    return first, result, analyse(first), analyse(after)


def test_the_dot_matrix_photo_before_and_after(tmp_path: Path) -> None:
    first, result, before, after = run_cached_photo(tmp_path)
    assert "manufacturing_date" not in fields(before) and rule(before, "MANUFACTURE_DATE")["status"] == "REVIEW"
    assert result.crops == 1 and result.reads <= 2
    assert fields(after)["manufacturing_date"] == "05/2026"
    assert rule(after, "MANUFACTURE_DATE")["status"] == "PASS"
    # The expiry stamp comes back as 042028, level with the Batch No. row: not taken as a date, so nothing is passed on it.
    assert "expiry_date" not in fields(after)
    assert rule(after, "DATE_CONSISTENCY")["status"] == "NOT_APPLICABLE"


# ----------------------------------------- a stamp the date fields hold is not also the batch number

def swapped(first: dict, old: str, new: str, confidence: float = 0.95) -> dict:
    detections = [{**item, "text": new, "confidence": confidence, "language": "en"} if item["text"] == old else item for item in first["detections"]]
    return {"raw_text": "\n".join(item["text"] for item in detections), "detections": detections}


def test_an_expiry_read_cleanly_is_not_also_stored_as_the_batch_number() -> None:
    first, _ = cached_photo()
    analysis = analyse(swapped(swapped(first, "05026", "05/2026"), "042028", "04-2028"))
    assert fields(analysis)["manufacturing_date"] == "05/2026" and fields(analysis)["expiry_date"] == "04-2028"
    assert fields(analysis).get("batch_number") != "04-2028"
    assert rule(analysis, "DATE_CONSISTENCY")["status"] == "PASS"


def test_a_real_batch_number_stays_the_batch_number() -> None:
    detections = [det("Batch No.", 100, 300), det("R-DLU7-063", 500, 300, 300, confidence=0.9), det("Mfg. Date", 100, 200), det("05/2026", 500, 200)]
    analysis = analyse({"raw_text": "\n".join(item.text for item in detections), "detections": [asdict(item) for item in detections]})
    assert fields(analysis)["batch_number"] == "R-DLU7-063" and fields(analysis)["manufacturing_date"] == "05/2026"


def test_a_suggested_stamp_is_not_stored_as_the_batch_number(tmp_path: Path) -> None:
    detections = page(("Expiry Date", ROW_EXPIRY), ("Batch No.", ROW_EXPIRY + 60))
    # Level with the expiry key, nearer than the batch key below it.
    result, analysis = recover(tmp_path, detections, Reader(stamp("042028", ROW_EXPIRY, 0.95, crop_y=ROW_EXPIRY - 60)))
    assert analysis["fields"]["expiry_date"]["matched_pattern"].endswith(":recovered_guess")
    assert fields(analysis).get("batch_number") != "042028"


def test_a_date_the_first_pass_already_read_is_not_re_added(tmp_path: Path) -> None:
    # 1000088949: the expiry key has no value of its own (the date beside it is the manufacture date, paired by the
    # stacked-column step), so the pass runs; it re-reads "04/2026" and must leave the first-pass detections alone.
    base = Path(__file__).parent / "fixtures" / "labels" / "real" / "real_1000088949.json"
    first = json.loads(base.read_text("utf-8"))
    detections = [OcrDetection(**item) for item in first["detections"]]
    raw = first["raw_text"]
    keys = date_recovery.missing_date_keys(raw, detections)
    assert keys, "the pass is expected to run on this photo"
    x0, y0, _, _ = date_recovery._region(date_recovery._clusters(keys)[0], first["image_width"], first["image_height"])
    scale = min(1.0, date_recovery.TARGET_KEY_HEIGHT / max(key.box[3] - key.box[1] for key in keys))
    original = next(item for item in detections if item.text == "04/2026")
    (left, top), (right, bottom) = original.bbox[0], original.bbox[2]
    again = OcrDetection("04/2026", 1.0, [[(left - x0) * scale, (top - y0) * scale], [(right - x0) * scale, (top - y0) * scale], [(right - x0) * scale, (bottom - y0) * scale], [(left - x0) * scale, (bottom - y0) * scale]], "en")
    image = tmp_path / "photo.png"
    Image.new("RGB", (first["image_width"], first["image_height"]), "white").save(image)
    result = recover_dates(image, raw, detections, Reader(again))
    assert result.detections == detections and not result.accepted
    analysis = analyse({"raw_text": raw, "detections": [asdict(item) for item in result.detections]})
    assert fields(analysis)["manufacturing_date"] == "04/2026" and "expiry_date" not in fields(analysis)


def test_detections_without_recovery_serialise_as_before() -> None:
    from backend.app.services.ocr import OcrDocument

    document = OcrDocument("PaddleOCR", ["en"], [det("x", 0, 0)])
    assert "recovery" not in document.as_database_detections()[0]
    assert OcrDocument("PaddleOCR", ["en"], [replace_recovery(det("x", 0, 0))]).as_database_detections()[0]["recovery"] == {"kind": "date_read"}


def replace_recovery(item: OcrDetection) -> OcrDetection:
    from dataclasses import replace

    return replace(item, recovery={"kind": "date_read"})


def test_constants_match_the_documented_limits() -> None:
    assert (date_recovery.MAX_CROPS, date_recovery.MIN_CLEAN_CONFIDENCE, date_recovery.MIN_GUESS_CONFIDENCE, date_recovery.GUESS_YEARS) == (2, 0.8, 0.9, (2020, 2040))
