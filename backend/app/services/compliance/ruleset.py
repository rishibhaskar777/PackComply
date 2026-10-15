"""Versioned, data-driven rule metadata loaded from rulesets/*.yaml.

The YAML carries what an inspector or administrator may change — title,
description, legal reference, severity, applicability, enable/disable and
configuration.  The check logic stays in Python (``rule_engine.CHECKS``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

RULESET_DIR = Path(__file__).resolve().parent / "rulesets"
DEFAULT_RULESET = RULESET_DIR / "lm_pc_2011_v2.yaml"
SEVERITIES = ("HIGH", "WARNING", "INFO")
# OCR-coverage thresholds a ruleset may set (see rule_engine._Label.coverage_adequate).
EVIDENCE_KEYS = {"min_text_lines": int, "min_mean_confidence": float, "max_low_confidence_line_share": float}


class RulesetError(ValueError):
    """The ruleset file is malformed; raised at load time, never mid-inspection."""


@dataclass(frozen=True)
class RuleDefinition:
    code: str
    title: str
    description: str
    legal_reference: str
    severity: str
    applies_when: str
    target_fields: tuple[str, ...]
    enabled: bool = True
    required_fields: tuple[str, ...] = ()
    expected: str | None = None
    actions: dict[str, str] = field(default_factory=dict)
    configuration: dict[str, Any] = field(default_factory=dict)

    @property
    def target_field(self) -> str:
        return self.target_fields[0] if self.target_fields else self.code.lower()

    def action(self, status: str) -> str | None:
        return self.actions.get(status)


@dataclass(frozen=True)
class Ruleset:
    ruleset_id: str
    version: str
    label: str
    title: str
    disclaimer: str
    rules: tuple[RuleDefinition, ...]
    # When an undetected declaration may be called absent (FAIL) rather than out of view (REVIEW).
    evidence: dict[str, Any] = field(default_factory=dict)

    def get(self, code: str) -> RuleDefinition | None:
        return next((rule for rule in self.rules if rule.code == code), None)

    @property
    def enabled_rules(self) -> tuple[RuleDefinition, ...]:
        return tuple(rule for rule in self.rules if rule.enabled)


def _require(data: dict[str, Any], key: str, where: str) -> Any:
    if data.get(key) in (None, ""):
        raise RulesetError(f"{where}: '{key}' is required")
    return data[key]


def parse_ruleset(data: dict[str, Any], *, known_conditions: frozenset[str] | None = None, known_codes: frozenset[str] | None = None) -> Ruleset:
    if not isinstance(data, dict):
        raise RulesetError("ruleset: expected a mapping at the top level")
    rules: list[RuleDefinition] = []
    for index, raw in enumerate(_require(data, "rules", "ruleset")):
        where = f"rules[{index}]"
        code = str(_require(raw, "code", where))
        where = f"rule {code}"
        severity = str(_require(raw, "severity", where)).upper()
        if severity not in SEVERITIES:
            raise RulesetError(f"{where}: severity must be one of {', '.join(SEVERITIES)}")
        applies_when = str(raw.get("applies_when") or "always")
        if known_conditions is not None and applies_when not in known_conditions:
            raise RulesetError(f"{where}: unknown applies_when '{applies_when}'")
        if known_codes is not None and code not in known_codes:
            raise RulesetError(f"{where}: no check function is registered for this code")
        if not set(raw.get("required_fields") or ()) <= set(raw.get("target_fields") or ()):
            raise RulesetError(f"{where}: required_fields must be a subset of target_fields")
        if any(rule.code == code for rule in rules):
            raise RulesetError(f"{where}: duplicate rule code")
        rules.append(RuleDefinition(
            code=code,
            title=str(_require(raw, "title", where)),
            description=" ".join(str(_require(raw, "description", where)).split()),
            legal_reference=str(_require(raw, "legal_reference", where)),
            severity=severity,
            applies_when=applies_when,
            target_fields=tuple(raw.get("target_fields") or ()),
            enabled=bool(raw.get("enabled", True)),
            required_fields=tuple(raw.get("required_fields") or ()),
            expected=raw.get("expected"),
            actions={str(status).upper(): str(text) for status, text in (raw.get("actions") or {}).items()},
            configuration=dict(raw.get("configuration") or {}),
        ))
    ruleset_id = str(_require(data, "ruleset_id", "ruleset"))
    version = str(_require(data, "version", "ruleset"))
    evidence = {}
    for key, value in (data.get("evidence") or {}).items():
        if key not in EVIDENCE_KEYS:
            raise RulesetError(f"evidence: unknown setting '{key}'")
        try:
            evidence[key] = EVIDENCE_KEYS[key](value)
        except (TypeError, ValueError) as error:
            raise RulesetError(f"evidence: '{key}' must be a number") from error
    return Ruleset(
        ruleset_id=ruleset_id,
        version=version,
        label=str(data.get("label") or f"{ruleset_id} v{version}"),
        title=str(data.get("title") or ruleset_id),
        disclaimer=" ".join(str(data.get("disclaimer") or "").split()),
        rules=tuple(rules),
        evidence=evidence,
    )


def load_ruleset(path: Path = DEFAULT_RULESET) -> Ruleset:
    # Imported here: rule_engine imports this module for RuleDefinition.
    from backend.app.services.compliance.rule_engine import CHECKS, CONDITIONS

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise RulesetError(f"cannot read ruleset {path.name}: {error}") from error
    return parse_ruleset(data, known_conditions=frozenset(CONDITIONS), known_codes=frozenset(CHECKS))


@lru_cache
def default_ruleset() -> Ruleset:
    return load_ruleset(DEFAULT_RULESET)
