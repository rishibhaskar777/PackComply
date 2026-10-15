"""Score extraction and compliance on the real label photos, per DEV / HOLD-OUT split.

    python -m backend.scripts.eval_real [--fixture-dir DIR] [--failures] [--show-holdout]

Reads the OCR fixtures written by ``batch_evaluate``, the person-written ground
truth ``expected_real.json`` and the fixed ``split.json`` in the same folder,
re-runs extraction and compliance (no OCR) and prints:

- field precision / recall for DEV, HOLD-OUT and the real-phone-photo subset;
- per-photo field correctness, OCR time and verdicts;
- rule-outcome agreement: the rules evaluated on the extracted fields against
  the same rules evaluated on the ground truth (a named party is assumed to be
  printed with its address, as it is on every labelled photo).

``--failures`` lists the failing DEV rows only; HOLD-OUT failures stay hidden
unless ``--show-holdout`` is given, so nobody tunes on them by accident.
Scoring follows backend.scripts.eval_extraction, with three real-label
conventions: best_before / use_by / expiry_date are one field
(``best_before_or_expiry``), websites compare by host name, and
``product_category`` is "food" or absent.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
from statistics import median
import sys
from typing import Any

from backend.app.services.compliance.rule_engine import ComplianceEngine, FieldEvidence, verdict
from backend.scripts.batch_evaluate import REAL_FIXTURE_DIR, analyse
from backend.scripts.eval_extraction import _canonical, metrics

FIELDS = (
    "product_name", "brand_owner", "manufacturer", "packer", "importer", "net_quantity", "mrp", "mrp_declaration",
    "tax_inclusive", "manufacturing_date", "packing_date", "best_before_or_expiry", "consumer_care_phone",
    "consumer_care_email", "consumer_care_website", "fssai_license", "country_of_origin", "unit_sale_price", "product_category",
)
# Scored only on photos whose ground truth lists them.
OPTIONAL_FIELDS = ("consumer_care_declaration",)
_EXPIRY_FIELDS = ("best_before", "use_by", "expiry_date")
SPLITS = ("dev", "holdout")


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


def _host(value: str) -> str:
    host = re.sub(r"(?i)^\s*https?://", "", value.strip()).split("/")[0].lower()
    return re.sub(r"^www\.", "", host).rstrip(".")


def canonical(field: str, value: str | None, normalized: str | None = None) -> str | None:
    if not value:
        return None
    if field == "consumer_care_website":
        return _host(value)
    if field == "best_before_or_expiry":
        return _canonical("best_before", value, normalized)
    return _canonical(field, value, normalized)


def extracted_value(fields: dict[str, Any], field: str) -> tuple[str | None, str | None]:
    names = _EXPIRY_FIELDS if field == "best_before_or_expiry" else (field,)
    for name in names:
        if item := fields.get(name):
            return item["value"], item.get("normalized_value")
    return None, None


def score(fixture: str, fields: dict[str, Any], truth: dict[str, Any]) -> list[Row]:
    rows = []
    for field in (*FIELDS, *(field for field in OPTIONAL_FIELDS if field in truth)):
        got, normalized = extracted_value(fields, field)
        got_canonical = canonical(field, got, normalized)
        want = truth.get(field)
        options = [canonical(field, option) for option in (want if isinstance(want, list) else [want])] if want is not None else []
        if not options:
            outcome = "TN" if got_canonical is None else "FP"
        elif got_canonical is None:
            outcome = "FN"
        else:
            outcome = "TP" if got_canonical in options else "WRONG"
        rows.append(Row(fixture, field, want, got, outcome))
    return rows


def _first(value: Any) -> str | None:
    return value[0] if isinstance(value, list) else value


def truth_rules(truth: dict[str, Any]) -> dict[str, str]:
    """Rule outcomes the current ruleset gives when extraction is perfect."""
    fields: dict[str, FieldEvidence] = {}
    for field in (*FIELDS, *OPTIONAL_FIELDS):
        value = _first(truth.get(field))
        if value is None:
            continue
        if field == "best_before_or_expiry":
            # A relative shelf life is stored the way extraction stores it.
            months = re.fullmatch(r"P(\d+)M", value)
            fields["best_before"] = FieldEvidence(f"{months.group(1)} months from manufacture" if months else value)
            continue
        fields[field] = FieldEvidence(value)
        if field in ("manufacturer", "brand_owner", "packer", "importer"):
            fields[f"{field}_address"] = FieldEvidence("address printed on the label")
    if truth.get("importer"):
        fields["import_declaration"] = FieldEvidence("present")
    findings = ComplianceEngine().evaluate(fields)
    return {finding.rule_id: finding.status for finding in findings}


def load(fixture_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    expected = {name: value for name, value in json.loads((fixture_dir / "expected_real.json").read_text(encoding="utf-8")).items() if not name.startswith("_")}
    split = json.loads((fixture_dir / "split.json").read_text(encoding="utf-8"))["photos"]
    return expected, split


def evaluate(fixture_dir: Path = REAL_FIXTURE_DIR, results: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """One record per labelled photo: split, real-photo flag, field rows, rule agreement and timings.

    ``results`` are batch_evaluate analyses keyed by fixture name; anything
    missing is re-analysed from the stored OCR fixture.
    """
    expected, split = load(fixture_dir)
    records = []
    for name, truth in expected.items():
        path = fixture_dir / f"{name}.json"
        if not path.exists():
            continue
        fixture = json.loads(path.read_text(encoding="utf-8"))
        analysis = (results or {}).get(name) or analyse(fixture)
        got_rules = {finding["rule_id"]: finding["status"] for finding in analysis["findings"]}
        want_rules = truth_rules(truth)
        records.append({
            "fixture": name,
            "file": split.get(name, {}).get("file", name),
            "split": split.get(name, {}).get("split", "unsplit"),
            "real_photo": bool(truth.get("_meta", {}).get("real_photo")),
            "difficulty": truth.get("_meta", {}).get("difficulty"),
            "rows": score(name, analysis["fields"], truth),
            "rules": {rule: (want_rules.get(rule), status) for rule, status in got_rules.items()},
            "verdict": (verdict(list(want_rules.values())), analysis["verdict"]),
            "ocr_seconds": fixture.get("ocr_seconds"),
        })
    return records


# ------------------------------------------------------------------ printing

def _groups(records: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    groups = [(label.upper().replace("HOLDOUT", "HOLD-OUT"), [record for record in records if record["split"] == label]) for label in SPLITS]
    groups.append(("REAL PHOTOS", [record for record in records if record["real_photo"]]))
    groups.append(("ALL", records))
    return [(label, members) for label, members in groups if members]


def summary(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Overall numbers per group, for before/after comparisons."""
    out = {}
    for label, members in _groups(records):
        rows = [row for record in members for row in record["rows"]]
        rules = [pair for record in members for pair in record["rules"].values()]
        out[label] = {
            **metrics(rows),
            "photos": len(members),
            "rule_agreement": sum(want == got for want, got in rules) / len(rules) if rules else 1.0,
            "verdicts": sum(want == got for want, got in (record["verdict"] for record in members)),
        }
    return out


def print_report(records: list[dict[str, Any]], failures: bool = False, show_holdout: bool = False) -> None:
    if not records:
        print("no labelled photos (add ground truth to expected_real.json)")
        return
    groups = _groups(records)
    print("\nFIELD PRECISION / RECALL (TP / FP / FN per field; WRONG counts as FP and FN)")
    print(f"{'field':24}" + "".join(f"{label:>22}" for label, _ in groups))
    for field in FIELDS:
        cells = []
        for _, members in groups:
            result = metrics([row for record in members for row in record["rows"] if row.field == field])
            cells.append(f"P {result['precision']:.2f} R {result['recall']:.2f} ({result['tp']}/{result['fp']}/{result['fn']})")
        print(f"{field:24}" + "".join(f"{cell:>22}" for cell in cells))

    print(f"\n{'photo':38} {'split':8} {'kind':8} {'diff':9} {'fields ok':>10} {'P':>5} {'R':>5} {'rules ok':>9} {'verdict (truth -> got)':34} {'OCR s':>6}")
    for record in records:
        result = metrics(record["rows"])
        rules_ok = sum(want == got for want, got in record["rules"].values())
        want, got = record["verdict"]
        print(f"{record['file'][:38]:38} {record['split']:8} {'photo' if record['real_photo'] else 'catalog':8} {record['difficulty'] or '-':9} "
              f"{result['correct']:>4}/{result['fields']:<5} {result['precision']:5.2f} {result['recall']:5.2f} {rules_ok:>4}/{len(record['rules']):<4} "
              f"{want + ' -> ' + got:34} {record['ocr_seconds'] or 0:6.1f}{'' if want == got else '  ✗'}")

    print("\nOVERALL")
    for label, result in summary(records).items():
        print(f"{label:12} photos={result['photos']} fields={result['fields']} correct={result['correct']} TP={result['tp']} FP={result['fp']} FN={result['fn']} "
              f"precision={result['precision']:.3f} recall={result['recall']:.3f} rule_agreement={result['rule_agreement']:.3f} verdicts={result['verdicts']}/{result['photos']}")
    times = [record["ocr_seconds"] for record in records if record["ocr_seconds"]]
    if times:
        print(f"OCR seconds per photo: median={median(times):.1f} max={max(times):.1f} (the first photo of a run includes model loading)")

    if failures:
        print("\nFAILING ROWS" + ("" if show_holdout else " (DEV only; HOLD-OUT hidden — pass --show-holdout to list them)"))
        for record in records:
            if record["split"] == "holdout" and not show_holdout:
                continue
            for row in record["rows"]:
                if not row.ok:
                    print(f"  {record['fixture']:36} {row.field:24} {row.outcome:6} want={row.expected!s:.60} got={row.got!s:.60}")
            for rule, (want, got) in record["rules"].items():
                if want != got:
                    print(f"  {record['fixture']:36} rule {rule:28} truth={want} got={got}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--fixture-dir", type=Path, default=REAL_FIXTURE_DIR)
    parser.add_argument("--failures", action="store_true", help="list failing DEV rows")
    parser.add_argument("--show-holdout", action="store_true", help="also list failing HOLD-OUT rows (measurement only — do not tune on them)")
    parser.add_argument("--json", type=Path, help="also write the overall numbers per group to this file")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    records = evaluate(args.fixture_dir)
    print_report(records, args.failures, args.show_holdout)
    if args.json:
        args.json.write_text(json.dumps(summary(records), indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
