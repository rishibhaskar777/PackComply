"""Score extraction and compliance on the real label photos, per DEV / HOLD-OUT split.

    python -m backend.scripts.eval_real [--fixture-dir DIR] [--failures] [--show-holdout]

Reads the OCR fixtures written by ``batch_evaluate``, the person-written ground
truth ``expected_real.json`` and the fixed ``split.json`` in the same folder,
re-runs extraction and compliance (no OCR) and prints:

- field precision / recall for DEV, HOLD-OUT and the real-phone-photo subset;
- per-photo field correctness, OCR time and verdicts;
- rule-outcome agreement: the rules evaluated on the extracted fields against
  the same rules evaluated on the ground truth (a named party is assumed to be
  printed with its address, as it is on every labelled photo);
- verdict safety: false NON_COMPLIANT verdicts (the truth is not
  NON_COMPLIANT) and false PASS findings (a rule PASSes that the truth does
  not, or PASSes on a wrongly extracted value).

The ground-truth rules see the same photo as extraction: its OCR coverage is
taken from the extraction, and a declaration the person did not list is "not
detected" unless they recorded its key without a value (``mrp_declaration`` /
``consumer_care_declaration``).

``--failures`` lists the failing DEV rows only; HOLD-OUT failures stay hidden
unless ``--show-holdout`` is given, so nobody tunes on them by accident.
Scoring follows backend.scripts.eval_extraction, with three real-label
conventions: best_before / use_by / expiry_date are one field
(``best_before_or_expiry``), websites compare by host name, and
``product_category`` is scored as food or not food (drug, personal care,
household and other all count as "not food").
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
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
    if field == "product_category":
        return "food" if value.strip().casefold() == "food" else None
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


def _compliance_date(value: str) -> str:
    """Ground-truth ISO dates in the form extraction hands the rules ("2026-05" -> "05/2026"), as evidence.field_evidence does."""
    if match := re.fullmatch(r"(\d{4})-(\d{2})(?:-(\d{2}))?", value):
        year, month, day = match.groups()
        return f"{day}/{month}/{year}" if day else f"{month}/{year}"
    return value


def _truth_declarations(truth: dict[str, Any]) -> str:
    """declaration_status as perfect extraction would record it from the ground truth."""
    has = lambda *fields: any(truth.get(field) for field in fields)
    unread = {"mrp": truth.get("mrp_declaration") == "present_without_value", "consumer_care": truth.get("consumer_care_declaration") == "present_without_details"}
    declared = {
        "manufacturer": has("manufacturer", "brand_owner", "packer", "importer"),
        "net_quantity": has("net_quantity"),
        "mrp": has("mrp"),
        "manufacturing_date": has("manufacturing_date", "packing_date"),
        "unit_sale_price": has("unit_sale_price"),
        "tax_inclusive": truth.get("tax_inclusive") == "true",
        "consumer_care": has("consumer_care_phone", "consumer_care_email", "consumer_care_website"),
    }
    state = lambda group: "DECLARED_WITH_VALUE" if declared[group] else "KEY_FOUND_VALUE_UNREAD" if unread.get(group) else "NOT_DETECTED"
    return json.dumps({group: {"state": state(group), "evidence": None} for group in declared})


def truth_rules(truth: dict[str, Any], ocr_coverage: str | None = None) -> dict[str, str]:
    """Rule outcomes the current ruleset gives when extraction is perfect.

    ``ocr_coverage`` is the extraction's reading of the same photo, so an
    undetected declaration is judged against the same coverage on both sides.
    """
    fields: dict[str, FieldEvidence] = {}
    for field in (*FIELDS, *OPTIONAL_FIELDS):
        value = _first(truth.get(field))
        if value is None:
            continue
        if field == "best_before_or_expiry":
            # A relative shelf life is stored the way extraction stores it.
            months = re.fullmatch(r"P(\d+)M", value)
            fields["best_before"] = FieldEvidence(f"{months.group(1)} months from manufacture" if months else _compliance_date(value))
            continue
        fields[field] = FieldEvidence(_compliance_date(value) if field in ("manufacturing_date", "packing_date") else value)
        if field in ("manufacturer", "brand_owner", "packer", "importer"):
            fields[f"{field}_address"] = FieldEvidence("address printed on the label")
    if truth.get("importer"):
        fields["import_declaration"] = FieldEvidence("present")
    fields["declaration_status"] = FieldEvidence(_truth_declarations(truth))
    if ocr_coverage:
        fields["ocr_coverage"] = FieldEvidence(ocr_coverage)
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
        if truth.get("_meta", {}).get("filled") is False:
            continue  # a template nobody has filled in yet must not produce numbers
        fixture = json.loads(path.read_text(encoding="utf-8"))
        analysis = (results or {}).get(name) or analyse(fixture)
        got_rules = {finding["rule_id"]: finding["status"] for finding in analysis["findings"]}
        want_rules = truth_rules(truth, (analysis["fields"].get("ocr_coverage") or {}).get("value"))
        records.append({
            "fixture": name,
            "file": split.get(name, {}).get("file", name),
            "split": split.get(name, {}).get("split", "unsplit"),
            "real_photo": bool(truth.get("_meta", {}).get("real_photo")),
            "group": truth.get("_meta", {}).get("group"),
            "difficulty": truth.get("_meta", {}).get("difficulty"),
            "rows": score(name, analysis["fields"], truth),
            "rules": {rule: (want_rules.get(rule), status) for rule, status in got_rules.items()},
            "rule_fields": {finding["rule_id"]: finding.get("field") for finding in analysis["findings"]},
            "verdict": (verdict(list(want_rules.values())), analysis["verdict"]),
            "ocr_seconds": fixture.get("ocr_seconds"),
        })
    return records


# ------------------------------------------------------------------ printing

def _groups(records: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    groups = [(label.upper().replace("HOLDOUT", "HOLD-OUT"), [record for record in records if record["split"] == label]) for label in SPLITS]
    # A batch of photos added together (e.g. a retired hold-out) stays comparable after its split changes.
    for group in dict.fromkeys(record["group"] for record in records if record["group"]):
        groups.append((group.upper(), [record for record in records if record["group"] == group]))
    groups.append(("REAL PHOTOS", [record for record in records if record["real_photo"]]))
    groups.append(("ALL", records))
    return [(label, members) for label, members in groups if members]


# The mandatory declarations whose verdict safety is tracked.
MANDATORY_RULES = ("MANUFACTURER_INFORMATION", "NET_QUANTITY_FORMAT", "MRP_FORMAT", "MRP_TAX_INCLUSIVE", "UNIT_SALE_PRICE", "MANUFACTURE_DATE", "CONSUMER_CARE_FORMAT")
_ROW_FIELD = {"best_before": "best_before_or_expiry", "use_by": "best_before_or_expiry", "expiry_date": "best_before_or_expiry"}


def false_non_compliant(record: dict[str, Any]) -> bool:
    want, got = record["verdict"]
    return got == "NON_COMPLIANT" and want != "NON_COMPLIANT"


def false_passes(record: dict[str, Any]) -> list[str]:
    """Mandatory rules that PASS although the truth does not, or that PASS on a wrongly extracted value."""
    rows = {row.field: row for row in record["rows"]}
    found = []
    for rule in MANDATORY_RULES:
        want, got = record["rules"].get(rule, (None, None))
        if got != "PASS":
            continue
        field = record["rule_fields"].get(rule)
        row = rows.get(_ROW_FIELD.get(field, field))
        if want not in ("PASS", None) or (row is not None and row.outcome in ("WRONG", "FP")):
            found.append(rule)
    return found


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
            "false_non_compliant": sum(false_non_compliant(record) for record in members),
            "false_pass_mandatory": sum(len(false_passes(record)) for record in members),
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
              f"precision={result['precision']:.3f} recall={result['recall']:.3f} rule_agreement={result['rule_agreement']:.3f} verdicts={result['verdicts']}/{result['photos']} "
              f"false_NON_COMPLIANT={result['false_non_compliant']} false_PASS_mandatory={result['false_pass_mandatory']}")
    print("\nVERDICT SAFETY")
    for record in records:
        if false_non_compliant(record) or false_passes(record):
            failing = [rule for rule, (want, got) in record["rules"].items() if got == "FAIL" and want != "FAIL"]
            print(f"  {record['file'][:44]:44} false NON_COMPLIANT={'yes' if false_non_compliant(record) else 'no '} "
                  f"FAIL not in truth={','.join(failing) or '-'} false PASS={','.join(false_passes(record)) or '-'}")
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


# ------------------------------------------------------------------ JSON report (shown on the app's "About & accuracy" page)

REPORT_SCHEMA = 1


def _group_report(label: str, members: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [row for record in members for row in record["rows"]]
    pairs = [pair for record in members for pair in record["rules"].values()]
    confusion: dict[str, dict[str, int]] = {}
    for record in members:
        want, got = record["verdict"]
        confusion.setdefault(want, {}).setdefault(got, 0)
        confusion[want][got] += 1
    per_field = {}
    for field in FIELDS:
        result = metrics([row for row in rows if row.field == field])
        per_field[field] = {key: result[key] for key in ("tp", "fp", "fn", "precision", "recall")}
    per_rule: dict[str, dict[str, int]] = {}
    for record in members:
        for rule, (want, got) in record["rules"].items():
            entry = per_rule.setdefault(rule, {"agree": 0, "total": 0})
            entry["total"] += 1
            entry["agree"] += int(want == got)
    overall = metrics(rows)
    return {
        "name": label,
        "photos": len(members),
        "fields_scored": overall["fields"],
        "precision": overall["precision"],
        "recall": overall["recall"],
        "per_field": per_field,
        "rule_agreement": {"agree": sum(want == got for want, got in pairs), "total": len(pairs)},
        "per_rule": per_rule,
        "verdict_confusion": confusion,
        "verdicts_correct": sum(record["verdict"][0] == record["verdict"][1] for record in members),
        "false_non_compliant": sum(false_non_compliant(record) for record in members),
        "false_pass_mandatory": sum(len(false_passes(record)) for record in members),
    }


def build_run(records: list[dict[str, Any]], label: str, fixture_dir: Path, note: str = "") -> dict[str, Any]:
    """Everything the About page shows for one evaluation run. Every number is measured here; none is typed in.

    ``kind`` is "unseen" only when every scored photo is a hold-out photo nobody has tuned on; otherwise it is a
    development score and is labelled that way.
    """
    from backend.app.services.compliance.ruleset import default_ruleset

    split_path = fixture_dir / "split.json"
    split_info = json.loads(split_path.read_text(encoding="utf-8")) if split_path.exists() else {}
    retired = {name for batch in split_info.get("retired_holdouts", []) for name in batch.get("photos", [])}
    unseen = [record for record in records if record["split"] == "holdout"]
    kind = "unseen" if records and len(unseen) == len(records) else "development"
    times = sorted(record["ocr_seconds"] for record in records if record["ocr_seconds"])
    ruleset = default_ruleset()
    return {
        "label": label,
        "kind": kind,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ruleset": {"id": ruleset.ruleset_id, "version": ruleset.version, "rules": len(ruleset.enabled_rules)},
        "note": note,
        "dataset_sizes": {
            "dev": sum(record["split"] == "dev" for record in records),
            "phone_holdout_retired_to_dev": sum(record["fixture"] in retired and record["real_photo"] for record in records),
            "unseen": len(unseen),
            "total": len(records),
        },
        "processing_seconds": {"median": median(times), "max": max(times), "photos": len(times)} if times else None,
        "groups": [_group_report(group, members) for group, members in _groups(records)],
    }


def write_report(path: Path, run: dict[str, Any]) -> None:
    """Add (or replace, by label) one run in the report file, keeping the runs already there."""
    runs: list[dict[str, Any]] = []
    if path.exists():
        try:
            runs = [item for item in json.loads(path.read_text(encoding="utf-8")).get("runs", []) if item.get("label") != run["label"]]
        except (ValueError, OSError):
            runs = []
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": REPORT_SCHEMA, "runs": [run, *runs]}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"evaluation report written: {path} (run '{run['label']}', {run['kind']})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--fixture-dir", type=Path, default=REAL_FIXTURE_DIR)
    parser.add_argument("--failures", action="store_true", help="list failing DEV rows")
    parser.add_argument("--show-holdout", action="store_true", help="also list failing HOLD-OUT rows (measurement only — do not tune on them)")
    parser.add_argument("--json", type=Path, help="also write the overall numbers per group to this file")
    parser.add_argument("--report-json", type=Path, help="write/merge the evaluation report the About page shows (see batch_evaluate --report-json)")
    parser.add_argument("--run-label", default="development", help="name of this run in the report (a run with the same label is replaced)")
    parser.add_argument("--run-note", default="", help="one line about the dataset, shown beside the numbers")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    records = evaluate(args.fixture_dir)
    print_report(records, args.failures, args.show_holdout)
    if args.json:
        args.json.write_text(json.dumps(summary(records), indent=1) + "\n", encoding="utf-8")
    if args.report_json:
        write_report(args.report_json, build_run(records, args.run_label, args.fixture_dir.resolve(), args.run_note))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
