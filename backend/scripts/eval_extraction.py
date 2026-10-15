"""Measure field extraction against the labelled fixtures.

    python -m backend.scripts.eval_extraction [--failures] [--fixture NAME]

Every fixture in backend/tests/fixtures/labels is run through
ExtractionService; each evaluated field is compared with expected.json.
Field-level precision/recall: a correct value is a true positive, a wrong
value is both a false positive and a false negative, a missing value is a
false negative, and a value where none is expected is a false positive.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from typing import Any

from backend.app.services.compliance.rule_engine import ComplianceEngine
from backend.app.services.extraction import ExtractionService

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "labels"

# Evaluated on every fixture; omitted from expected.json means "must be absent".
CORE_FIELDS = (
    "product_name", "brand_owner", "manufacturer", "packer", "importer", "net_quantity", "mrp",
    "mrp_declaration", "tax_inclusive", "fssai_license", "manufacturing_date", "packing_date",
    "best_before", "use_by", "expiry_date", "consumer_care_phone", "consumer_care_email",
    "consumer_care_website", "country_of_origin", "unit_sale_price", "batch_number",
)
# Evaluated only where expected.json lists them.
OPTIONAL_FIELDS = ("manufacturer_address", "brand_owner_address", "packer_address", "importer_address", "manufacturing_units")
DATE_FIELDS = {"manufacturing_date", "packing_date", "best_before", "use_by", "expiry_date"}
_MONTHS = {name: index for index, name in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


@dataclass(frozen=True)
class Row:
    fixture: str
    field: str
    expected: Any
    got: str | None
    outcome: str  # TP, TN, FP, FN, WRONG

    @property
    def ok(self) -> bool:
        return self.outcome in ("TP", "TN")


def _text(value: str) -> str:
    return re.sub(r"[^0-9a-zऀ-ॿ@.]+", "", value.casefold()).strip(".")


def _date(value: str | None) -> str | None:
    """Independent canonical form, so the baseline is judged on what it found, not its normaliser."""
    if not value:
        return None
    value = value.strip().casefold()
    if re.fullmatch(r"\d{4}-\d{2}(?:-\d{2})?|p\d+m", value):
        return value.upper() if value.startswith("p") else value
    if months := re.search(r"(\d+)\s*months?", value):
        return f"P{int(months.group(1))}M"
    year = lambda text: int(text) + (2000 if len(text) == 2 else 0)
    if full := re.fullmatch(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2}|\d{4})", value):
        return f"{year(full.group(3)):04d}-{int(full.group(2)):02d}-{int(full.group(1)):02d}"
    if month := re.fullmatch(r"(\d{1,2})[/.\-](\d{2}|\d{4})", value):
        return f"{year(month.group(2)):04d}-{int(month.group(1)):02d}"
    if named := re.fullmatch(r"([a-z]{3})[a-z]*\.?[\s/\-']*(\d{2}|\d{4})", value):
        if named.group(1) in _MONTHS:
            return f"{year(named.group(2)):04d}-{_MONTHS[named.group(1)]:02d}"
    return value


def _canonical(field: str, value: str | None, normalized: str | None = None) -> str | None:
    if value is None or value == "" or (field == "mrp_declaration" and value == "absent"):
        return None
    if field == "net_quantity":
        return ComplianceEngine.normalize_quantity(normalized or value) or ComplianceEngine.normalize_quantity(value) or _text(value)
    if field == "mrp":
        return ComplianceEngine.normalize_mrp(value) or _text(value)
    if field in DATE_FIELDS:
        return _date(normalized) if normalized and re.fullmatch(r"\d{4}-\d{2}(?:-\d{2})?|P\d+M", normalized) else _date(value)
    if field == "consumer_care_phone":
        return re.sub(r"\D", "", value)
    if field == "manufacturing_units":
        try:
            return ",".join(sorted(unit["code"] for unit in json.loads(value)))
        except (ValueError, TypeError, KeyError):
            return _text(value)
    return _text(value)


def _expected_options(field: str, expected: Any) -> list[str]:
    if field == "manufacturing_units":
        return [",".join(sorted(expected))]
    options = expected if isinstance(expected, list) else [expected]
    return [_canonical(field, option) for option in options]


def load_fixtures(directory: Path = FIXTURE_DIR) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    expected = {name: value for name, value in json.loads((directory / "expected.json").read_text(encoding="utf-8")).items() if not name.startswith("_")}
    fixtures = {name: json.loads((directory / f"{name}.json").read_text(encoding="utf-8")) for name in expected}
    return fixtures, expected


def _extract(service: ExtractionService, fixture: dict[str, Any]) -> dict[str, Any]:
    try:
        return service.extract(fixture["raw_text"], detections=fixture.get("detections"))
    except TypeError:  # extractors that predate layout-aware extraction
        return service.extract(fixture["raw_text"])


def evaluate(fixture_filter: str | None = None) -> list[Row]:
    fixtures, expected = load_fixtures()
    service = ExtractionService()
    rows: list[Row] = []
    for name, fixture in fixtures.items():
        if fixture_filter and fixture_filter not in name:
            continue
        values = _extract(service, fixture)
        truth = expected[name]
        for field in (*CORE_FIELDS, *(field for field in OPTIONAL_FIELDS if field in truth)):
            item = values.get(field)
            got = getattr(item, "value", None)
            got_canonical = _canonical(field, got, getattr(item, "normalized_value", None))
            want = truth.get(field)
            want_options = _expected_options(field, want) if want is not None else []
            if not want_options or want_options == [None]:
                outcome = "TN" if got_canonical is None else "FP"
            elif got_canonical is None:
                outcome = "FN"
            else:
                outcome = "TP" if got_canonical in want_options else "WRONG"
            rows.append(Row(name, field, want, got, outcome))
    return rows


def metrics(rows: list[Row]) -> dict[str, float | int]:
    tp = sum(row.outcome == "TP" for row in rows)
    fp = sum(row.outcome in ("FP", "WRONG") for row in rows)
    fn = sum(row.outcome in ("FN", "WRONG") for row in rows)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return {"fields": len(rows), "correct": sum(row.ok for row in rows), "tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall}


def _short(value: Any, width: int = 44) -> str:
    text = "—" if value is None else (" | ".join(value) if isinstance(value, list) else str(value))
    text = text.replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--failures", action="store_true", help="only print rows that did not pass")
    parser.add_argument("--fixture", help="only evaluate fixtures whose name contains this text")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    rows = evaluate(args.fixture)
    print(f"{'fixture':40} {'field':22} {'expected':44} {'got':44} ok")
    for row in rows:
        if args.failures and row.ok:
            continue
        print(f"{row.fixture:40} {row.field:22} {_short(row.expected):44} {_short(row.got):44} {'✓' if row.ok else '✗ ' + row.outcome}")
    print()
    for name in dict.fromkeys(row.fixture for row in rows):
        result = metrics([row for row in rows if row.fixture == name])
        print(f"{name:40} {result['correct']:3}/{result['fields']:<3} precision={result['precision']:.3f} recall={result['recall']:.3f}")
    overall = metrics(rows)
    print(f"\nOVERALL fields={overall['fields']} correct={overall['correct']} TP={overall['tp']} FP={overall['fp']} FN={overall['fn']} "
          f"precision={overall['precision']:.3f} recall={overall['recall']:.3f}")


if __name__ == "__main__":
    main()
