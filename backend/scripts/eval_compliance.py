"""Measure rule outcomes against the labelled fixtures.

    python -m backend.scripts.eval_compliance [--failures] [--fixture NAME]

Every fixture in backend/tests/fixtures/labels is extracted, stored the way an
inspection stores it (so dates reach the rules exactly as in production) and
checked against the current ruleset.  Each rule outcome (PASS / FAIL / REVIEW /
NOT_APPLICABLE) and the overall verdict are compared with the "rules" and
"verdict" entries in expected.json.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
import sys
from uuid import uuid4

from backend.app.models import OcrResult
from backend.app.services.compliance.rule_engine import ComplianceEngine, verdict
from backend.app.services.extraction import ExtractionService
from backend.app.services.extraction.evidence import build_extracted_fields, field_evidence
from backend.scripts.eval_extraction import _extract, load_fixtures


@dataclass(frozen=True)
class RuleRow:
    fixture: str
    rule: str
    expected: str | None
    got: str
    message: str

    @property
    def ok(self) -> bool:
        return self.expected == self.got


def evaluate(fixture_filter: str | None = None, today: date | None = None) -> tuple[list[RuleRow], dict[str, tuple[str | None, str]]]:
    """Rule rows, and (expected, actual) verdict per fixture."""
    fixtures, expected = load_fixtures()
    extraction, engine = ExtractionService(), ComplianceEngine()
    rows: list[RuleRow] = []
    verdicts: dict[str, tuple[str | None, str]] = {}
    for name, fixture in fixtures.items():
        if fixture_filter and fixture_filter not in name:
            continue
        ocr = OcrResult(id=uuid4(), inspection_id=uuid4(), raw_text=fixture["raw_text"], detections=fixture.get("detections") or [], languages=["en"])
        stored = build_extracted_fields(ocr.inspection_id, ocr, _extract(extraction, fixture))
        findings = engine.evaluate({field.field_name: field_evidence(field) for field in stored}, today=today)
        truth = expected[name].get("rules", {})
        rows.extend(RuleRow(name, finding.rule_id, truth.get(finding.rule_id), finding.status, finding.message) for finding in findings)
        verdicts[name] = (expected[name].get("verdict"), verdict([finding.status for finding in findings]))
    return rows, verdicts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--failures", action="store_true", help="only print rule outcomes that differ")
    parser.add_argument("--fixture", help="only evaluate fixtures whose name contains this text")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    rows, verdicts = evaluate(args.fixture)
    print(f"{'fixture':40} {'rule':30} {'expected':15} {'got':15} ok")
    for row in rows:
        if args.failures and row.ok:
            continue
        print(f"{row.fixture:40} {row.rule:30} {row.expected or '—':15} {row.got:15} {'✓' if row.ok else '✗  ' + row.message}")
    print(f"\n{'fixture':40} {'rules':>7}  {'verdict expected':18} {'verdict got':18}")
    for name, (want, got) in verdicts.items():
        own = [row for row in rows if row.fixture == name]
        print(f"{name:40} {sum(row.ok for row in own):3}/{len(own):<3}  {want or '—':18} {got:18} {'✓' if want == got else '✗'}")
    correct = sum(row.ok for row in rows)
    verdicts_ok = sum(want == got for want, got in verdicts.values())
    print(f"\nOVERALL rule outcomes={len(rows)} correct={correct} accuracy={correct / len(rows) if rows else 1.0:.3f} "
          f"verdicts={verdicts_ok}/{len(verdicts)}")


if __name__ == "__main__":
    main()
