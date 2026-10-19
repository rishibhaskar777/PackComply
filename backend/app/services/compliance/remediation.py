"""Plain-language remediation catalogue (rulesets/remediation_v1.yaml), one entry per rule code."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re

import yaml

from backend.app.services.compliance.ruleset import RULESET_DIR, Ruleset, default_ruleset

REMEDIATION_FILE = RULESET_DIR / "remediation_v1.yaml"
_REQUIRED = ("problem_title", "fix_steps", "example", "pass_text")
_CHIP = re.compile(r"Rule\s+\d+(?:\(\w+\))*")


@dataclass(frozen=True)
class Remediation:
    problem_title: str
    fix_steps: tuple[str, ...]
    example: str
    pass_text: str


class RemediationError(ValueError):
    """The catalogue is malformed."""


@lru_cache
def load_remediation(path: Path = REMEDIATION_FILE) -> dict[str, Remediation]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    entries: dict[str, Remediation] = {}
    for code, raw in (data or {}).get("rules", {}).items():
        missing = [key for key in _REQUIRED if not raw.get(key)]
        if missing:
            raise RemediationError(f"{code}: missing {', '.join(missing)}")
        steps = raw["fix_steps"]
        if not isinstance(steps, list) or not all(isinstance(step, str) and step.strip() for step in steps):
            raise RemediationError(f"{code}: fix_steps must be a list of sentences")
        entries[code] = Remediation(str(raw["problem_title"]).strip(), tuple(step.strip() for step in steps), str(raw["example"]).strip(), str(raw["pass_text"]).strip())
    return entries


def remediation_for(code: str) -> Remediation | None:
    return load_remediation().get(code)


def missing_remediation(ruleset: Ruleset | None = None) -> list[str]:
    """Rule codes of the ruleset that have no catalogue entry."""
    catalogue = load_remediation()
    return [rule.code for rule in (ruleset or default_ruleset()).rules if rule.code not in catalogue]


def rule_chip(legal_reference: str | None) -> str | None:
    """The short citation shown on a fix card: "LM(PC) Rules 2011, Rule 6(1)(c) and Rule 9" -> "Rule 6(1)(c)"."""
    match = _CHIP.search(legal_reference or "")
    return match.group(0) if match else None
