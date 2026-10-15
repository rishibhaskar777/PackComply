"""Deterministic, explainable checks over normalized extraction evidence.

Rule metadata (title, legal reference, severity, applicability, configuration)
comes from the versioned ruleset YAML; this module holds the check logic, one
function per rule code in ``CHECKS``, and the applicability conditions in
``CONDITIONS``.  Every finding is PASS, FAIL (a violation visible on the label),
REVIEW (cannot be confirmed from this photo) or NOT_APPLICABLE.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
import logging
import re
from typing import Callable

from backend.app.services.compliance.ruleset import RuleDefinition, Ruleset, default_ruleset

logger = logging.getLogger(__name__)

PASS, FAIL, REVIEW, NOT_APPLICABLE = "PASS", "FAIL", "REVIEW", "NOT_APPLICABLE"
STATUSES = (PASS, FAIL, REVIEW, NOT_APPLICABLE)
# Only these count as issues for verdicts, risk scoring and repeat patterns.
ISSUE_STATUSES = (FAIL, REVIEW)
NON_COMPLIANT, NEEDS_REVIEW, COMPLIANT = "NON_COMPLIANT", "NEEDS_REVIEW", "COMPLIANT"


def overall_status(statuses: list[str]) -> str:
    """Backwards-compatible FAIL / REVIEW / PASS; NOT_APPLICABLE never changes it."""
    return FAIL if FAIL in statuses else REVIEW if REVIEW in statuses else PASS


def verdict(statuses: list[str]) -> str:
    return {FAIL: NON_COMPLIANT, REVIEW: NEEDS_REVIEW, PASS: COMPLIANT}[overall_status(statuses)]


@dataclass(frozen=True)
class FieldEvidence:
    value: str | None = None; confidence: float | None = None; needs_review: bool = False; evidence: str | None = None


@dataclass(frozen=True)
class ComplianceFinding:
    rule_id: str; rule_name: str; status: str; severity: str; message: str; field: str | None = None; value: str | None = None; confidence: float | None = None; evidence: str | None = None
    legal_reference: str | None = None


@dataclass(frozen=True)
class Outcome:
    """What a check decided, with the field whose evidence supports it."""
    status: str
    message: str
    field: str | None = None


class ComplianceEngine:
    def __init__(self, ruleset: Ruleset | None = None):
        self._ruleset = ruleset

    @property
    def ruleset(self) -> Ruleset:
        if self._ruleset is None:
            self._ruleset = default_ruleset()
        return self._ruleset

    @staticmethod
    def clean(value: str) -> str:
        """Remove presentation-only OCR differences without changing label evidence."""
        return re.sub(r"\s+", " ", value.replace(" ", " ").replace("​", "").replace("–", "-").replace("—", "-")).strip()
    @classmethod
    def normalize_quantity(cls, value: str | None) -> str | None:
        if not value: return None
        value = cls.clean(value).lower().replace("ℓ", "l").replace("㎖", "ml")
        value = re.sub(r"\bm\s*l\b", "ml", value)
        match = re.fullmatch(r"([0-9oil]+(?:[,.][0-9oil]+)?)\s*(mgs?|milligrams?|g(?:rams?)?|gms?|k(?:ilo)?grams?|kgs?|millilit(?:re|er)s?|ml|centilit(?:re|er)s?|cm(?:\^?3|³)|cc|cl|l(?:it(?:re|er)s?)?|ltr|n|nos?\.?|units?|pcs?\.?|pieces?)", value)
        if not match: return None
        try: amount = Decimal(match.group(1).translate(str.maketrans({"o":"0", "i":"1", "l":"1", ",":"."})))
        except InvalidOperation: return None
        if amount <= 0: return None
        unit = {"mg":"mg", "mgs":"mg", "milligram":"mg", "milligrams":"mg", "g":"g", "gm":"g", "gms":"g", "gram":"g", "grams":"g", "kg":"kg", "kgs":"kg", "kilogram":"kg", "kilograms":"kg", "ml":"ml", "millilitre":"ml", "millilitres":"ml", "milliliter":"ml", "milliliters":"ml", "cc":"ml", "cm3":"ml", "cm^3":"ml", "cm³":"ml", "cl":"cl", "centilitre":"cl", "centilitres":"cl", "centiliter":"cl", "centiliters":"cl", "l":"l", "ltr":"l", "litre":"l", "liter":"l", "litres":"l", "liters":"l", "n":"count", "no":"count", "nos":"count", "unit":"count", "units":"count", "pc":"count", "pcs":"count", "piece":"count", "pieces":"count"}.get(match.group(2).rstrip("."))
        if not unit: return None
        if unit == "kg": amount, unit = amount * 1000, "g"
        elif unit == "l": amount, unit = amount * 1000, "ml"
        elif unit == "cl": amount, unit = amount * 10, "ml"
        text = format(amount.normalize(), "f")
        if "." in text: text = text.rstrip("0").rstrip(".")
        return f"{text or '0'} {unit}"
    @classmethod
    def normalize_mrp(cls, value: str | None) -> str | None:
        if not value: return None
        match = re.fullmatch(r"(?:₹|rs\.?|inr)\s*([0-9oil]+(?:[,.][0-9oil]{1,2})?)", cls.clean(value).lower())
        if not match: return None
        try: amount = Decimal(match.group(1).translate(str.maketrans({"o":"0", "i":"1", "l":"1", ",":"."})))
        except InvalidOperation: return None
        return f"₹{amount.normalize():f}" if amount > 0 else None
    @classmethod
    def _normalized(cls, code: str, value: str | None) -> str | None:
        return cls.normalize_quantity(value) if code == "NET_QUANTITY_FORMAT" else cls.normalize_mrp(value) if code == "MRP_FORMAT" else cls.clean(value) if value else None
    @staticmethod
    def _parse_date(value):
        parsed = _parse_date(value)
        return parsed[0] if parsed else None

    def evaluate(self, fields: dict[str, FieldEvidence], today: date | None = None) -> list[ComplianceFinding]:
        """One finding per enabled rule, in ruleset order."""
        label = _Label(fields, today or date.today())
        findings = []
        for rule in self.ruleset.enabled_rules:
            reason = CONDITIONS[rule.applies_when](label, rule)
            outcome = Outcome(NOT_APPLICABLE, reason, rule.target_field) if reason else CHECKS[rule.code](label, rule)
            item = label.item(outcome.field) if outcome.field else FieldEvidence()
            logger.debug("Compliance validation rule=%s field=%s raw=%r result=%s reason=%s", rule.code, outcome.field, item.value, outcome.status, outcome.message)
            findings.append(ComplianceFinding(rule.code, rule.title, outcome.status, rule.severity, outcome.message, outcome.field, item.value, item.confidence, item.evidence, rule.legal_reference))
        # Low extraction confidence is surfaced per field via needs_review, not as a finding.
        return findings


# ------------------------------------------------------------------ helpers

_DOMESTIC = re.compile(r"(?i)\bindia\b|भारत")
_IMPORTED_VALUES = {"present"}
_USP = re.compile(r"(?i)(?:₹|rs\.?|inr)?\s*(\d+(?:[.,]\d+)?)\s*(?:per|/)\s*(\d+(?:\.\d+)?)?\s*([a-z]+)")
_USP_UNITS = {"g": ("g", 1), "gm": ("g", 1), "gram": ("g", 1), "kg": ("g", 1000), "ml": ("ml", 1), "l": ("ml", 1000), "ltr": ("ml", 1000), "litre": ("ml", 1000),
              "unit": ("count", 1), "units": ("count", 1), "pc": ("count", 1), "pcs": ("count", 1), "piece": ("count", 1), "pieces": ("count", 1), "no": ("count", 1), "nos": ("count", 1), "n": ("count", 1)}
_BASE_NAMES = {"g": "g", "kg": "kg", "ml": "ml", "l": "L", "unit": "unit"}


def _parse_date(value: str | None) -> tuple[date, bool] | None:
    """(date, month_only) for MM/YYYY or DD/MM/YY(YY); None when unreadable."""
    if not value:
        return None
    value = re.sub(r"\s*([/.-])\s*", r"\1", ComplianceEngine.clean(value))
    month = re.fullmatch(r"(\d{1,2})[/.-](\d{4})", value)
    full = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})", value)
    try:
        if month:
            return date(int(month.group(2)), int(month.group(1)), 1), True
        if full:
            return date(int(full.group(3)) + (2000 if int(full.group(3)) < 100 else 0), int(full.group(2)), int(full.group(1))), False
    except ValueError:
        pass
    return None


def _add_months(day: date, months: int) -> date:
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, 28))


def _amount(value: str | None) -> Decimal | None:
    normalized = ComplianceEngine.normalize_mrp(value)
    return Decimal(normalized[1:]) if normalized else None


def _money(value: Decimal) -> str:
    # Unit sale prices are declared rounded to two decimals (half up, as printed on packs).
    return f"₹{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}"


def _quote(text: str | None) -> str:
    return f" (label: \"{text}\")" if text else ""


class _Label:
    """Read-only view of the extracted fields shared by all checks."""

    def __init__(self, fields: dict[str, FieldEvidence], today: date):
        self.fields, self.today = fields, today

    def item(self, name: str) -> FieldEvidence:
        return self.fields.get(name) or FieldEvidence()

    def text(self, name: str) -> str | None:
        value = self.item(name).value
        return ComplianceEngine.clean(value) or None if value else None

    def quantity(self) -> tuple[Decimal, str] | None:
        normalized = ComplianceEngine.normalize_quantity(self.text("net_quantity"))
        if not normalized:
            return None
        amount, unit = normalized.split(" ")
        return Decimal(amount), unit

    def mrp_declared(self) -> bool:
        return bool(self.text("mrp")) or self.text("mrp_declaration") in ("present_with_value", "present_without_value")

    def import_indicator(self) -> tuple[str, str] | None:
        """(field, description) of the first sign that the package is imported."""
        if importer := self.text("importer"):
            return "importer", f"importer \"{importer}\" is named"
        if self.text("import_declaration") in _IMPORTED_VALUES:
            return "import_declaration", "an \"Imported by\" declaration is printed"
        country = self.text("country_of_origin")
        if country and not _DOMESTIC.search(country):
            return "country_of_origin", f"the country of origin is {country}"
        return None

    def form(self, rule: RuleDefinition) -> str | None:
        """'liquid' or 'solid' from whole words in the product name; liquids win."""
        name = (self.text("product_name") or "").casefold()
        words = set(re.findall(r"[a-z]+", name))
        if words & {word.casefold() for word in rule.configuration.get("liquid_keywords", ())}:
            return "liquid"
        if words & {word.casefold() for word in rule.configuration.get("solid_keywords", ())}:
            return "solid"
        return None


# ------------------------------------------------------------ applicability
# A condition returns None when the rule applies, else why it does not.

def _always(label: _Label, rule: RuleDefinition) -> str | None:
    return None


def _imported(label: _Label, rule: RuleDefinition) -> str | None:
    return None if label.import_indicator() else "The label does not indicate an imported product (no importer, no \"Imported by\" and no foreign country of origin)."


def _separate_packer(label: _Label, rule: RuleDefinition) -> str | None:
    return None if label.text("packer") else "No separate packer is named on the label; packer details are covered by MANUFACTURER_INFORMATION."


def _mrp_declared(label: _Label, rule: RuleDefinition) -> str | None:
    return None if label.mrp_declared() else "No MRP declaration was found, so the tax-inclusive statement cannot be assessed (see MRP_FORMAT)."


def _mrp_and_net_quantity(label: _Label, rule: RuleDefinition) -> str | None:
    if _amount(label.text("mrp")) is None:
        why = "the MRP key is printed but the price is not readable (stamped separately)" if label.text("mrp_declaration") == "present_without_value" else "no MRP value was found"
        return f"The unit sale price cannot be assessed because {why}."
    if label.quantity() is None:
        return "The unit sale price cannot be assessed because no net quantity in a standard unit was found."
    return None


def _unit_form_detectable(label: _Label, rule: RuleDefinition) -> str | None:
    if label.quantity() is None:
        return "No net quantity in a standard unit was found, so its unit cannot be compared with the commodity."
    if label.form(rule) is None:
        return "The commodity form (liquid or solid) cannot be told reliably from the product name."
    return None


def _food_product(label: _Label, rule: RuleDefinition) -> str | None:
    return None if label.text("product_category") == "food" else "No ingredients list, nutrition panel or FSSAI text was found, so the label is not treated as a food product."


CONDITIONS: dict[str, Callable[[_Label, RuleDefinition], str | None]] = {
    "always": _always, "imported": _imported, "separate_packer": _separate_packer, "mrp_declared": _mrp_declared,
    "mrp_and_net_quantity": _mrp_and_net_quantity, "unit_form_detectable": _unit_form_detectable, "food_product": _food_product,
}


# ------------------------------------------------------------------- checks

_PARTIES = (("manufacturer", "manufacturer_address", "Manufacturer"), ("brand_owner", "brand_owner_address", "Brand owner / marketer"), ("packer", "packer_address", "Packer"))


def _manufacturer(label: _Label, rule: RuleDefinition) -> Outcome:
    parties = list(_PARTIES)
    if label.import_indicator():
        # Rule 6(1)(a): for imported packages the importer's details are the declaration.
        parties.append(("importer", "importer_address", "Importer"))
    named = [(field, address, role) for field, address, role in parties if label.text(field)]
    if not named:
        return Outcome(FAIL, "No manufacturer, packer or brand-owner name was found on the label.", "manufacturer")
    for field, address, role in named:
        if label.text(address):
            return Outcome(PASS, f"{role} \"{label.text(field)}\" is declared with address \"{label.text(address)}\".", field)
    field, _, role = named[0]
    return Outcome(REVIEW, f"{role} \"{label.text(field)}\" is named but no address was found in this photo; the complete address is required.", field)


def _importer(label: _Label, rule: RuleDefinition) -> Outcome:
    indicator_field, indicator = label.import_indicator()  # type: ignore[misc]
    importer = label.text("importer")
    if not importer:
        return Outcome(FAIL, f"The package appears imported ({indicator}) but no importer name and address were found.", indicator_field)
    if not label.text("importer_address"):
        return Outcome(FAIL, f"Importer \"{importer}\" is named but no importer address was found.", "importer")
    return Outcome(PASS, f"Importer \"{importer}\" is declared with address \"{label.text('importer_address')}\".", "importer")


def _country_of_origin(label: _Label, rule: RuleDefinition) -> Outcome:
    if country := label.text("country_of_origin"):
        return Outcome(PASS, f"Country of origin is declared: {country}.", "country_of_origin")
    indicator_field, indicator = label.import_indicator()  # type: ignore[misc]
    return Outcome(FAIL, f"The package appears imported ({indicator}) but no country of origin was found.", indicator_field)


def _packer(label: _Label, rule: RuleDefinition) -> Outcome:
    packer = label.text("packer")
    if label.text("packer_address"):
        return Outcome(PASS, f"Packer \"{packer}\" is declared with address \"{label.text('packer_address')}\".", "packer")
    return Outcome(REVIEW, f"Packer \"{packer}\" is named but no packer address was found in this photo.", "packer")


def _product_identity(label: _Label, rule: RuleDefinition) -> Outcome:
    if name := label.text("product_name"):
        return Outcome(PASS, f"Product name found: \"{name}\".", "product_name")
    return Outcome(REVIEW, "No product name was found; the front panel may not be in this photo.", "product_name")


def _net_quantity(label: _Label, rule: RuleDefinition) -> Outcome:
    declared = label.text("net_quantity")
    if not declared:
        return Outcome(FAIL, "No net-quantity declaration was found on the label.", "net_quantity")
    normalized = ComplianceEngine.normalize_quantity(declared)
    if not normalized:
        return Outcome(FAIL, f"The net quantity \"{declared}\" is not a positive value with a recognised standard unit.", "net_quantity")
    return Outcome(PASS, f"Net quantity \"{declared}\" uses a standard unit (normalised: {normalized}).", "net_quantity")


def _unit_suitability(label: _Label, rule: RuleDefinition) -> Outcome:
    _, unit = label.quantity()  # type: ignore[misc]
    form, declared = label.form(rule), label.text("net_quantity")
    if form == "liquid" and unit == "count":
        return Outcome(REVIEW, f"\"{label.text('product_name')}\" looks like a liquid but its net quantity \"{declared}\" is a count; liquids are normally declared by volume.", "net_quantity")
    if form == "solid" and unit == "ml":
        return Outcome(REVIEW, f"\"{label.text('product_name')}\" looks like a solid but its net quantity \"{declared}\" is a volume; solids are normally declared by weight.", "net_quantity")
    return Outcome(PASS, f"The unit of \"{declared}\" suits a {form} commodity.", "net_quantity")


def _mrp(label: _Label, rule: RuleDefinition) -> Outcome:
    if value := label.text("mrp"):
        if ComplianceEngine.normalize_mrp(value):
            return Outcome(PASS, f"MRP {value} is a positive amount in Indian rupees.", "mrp")
        return Outcome(FAIL, f"The MRP \"{value}\" is not a positive amount in Indian rupees.", "mrp")
    if label.text("mrp_declaration") == "present_without_value":
        return Outcome(REVIEW, f"An MRP declaration is printed{_quote(label.item('mrp_declaration').evidence)} but no price is readable; it may be stamped separately — verify the price on the physical pack.", "mrp_declaration")
    return Outcome(FAIL, "No MRP / retail sale price declaration was found on the label.", "mrp")


def _tax_inclusive(label: _Label, rule: RuleDefinition) -> Outcome:
    if label.text("tax_inclusive") == "true":
        return Outcome(PASS, "The MRP is stated to be inclusive of all taxes.", "tax_inclusive")
    return Outcome(FAIL, "An MRP is declared but no \"inclusive of all taxes\" statement was found.", "mrp_declaration" if label.text("mrp_declaration") else "mrp")


def _base_unit(rule: RuleDefinition, dimension: str, amount: Decimal) -> tuple[str, Decimal]:
    for row in rule.configuration["base_units"][dimension]:
        if row["up_to"] is None or amount <= Decimal(str(row["up_to"])):
            return row["unit"], Decimal(str(row["factor"]))
    raise ValueError(f"no base unit configured for {dimension} {amount}")


def _unit_sale_price(label: _Label, rule: RuleDefinition) -> Outcome:
    mrp = _amount(label.text("mrp"))
    amount, dimension = label.quantity()  # type: ignore[misc]
    base, factor = _base_unit(rule, dimension, amount)
    base_name = _BASE_NAMES.get(base, base)
    units = amount / factor
    if units == 1:
        return Outcome(NOT_APPLICABLE, f"The net quantity is exactly one {base_name}, so the unit sale price equals the MRP and need not be declared separately.", "unit_sale_price")
    expected = mrp / units  # type: ignore[operator]
    basis = f"MRP {_money(mrp)} ÷ {format(units.normalize(), 'f')} {base_name}{'s' if base == 'unit' else ''}"  # type: ignore[arg-type]
    declared = label.text("unit_sale_price")
    if not declared:
        return Outcome(FAIL, f"No unit sale price was found; about {_money(expected)} per {base_name} is expected ({basis}).", "unit_sale_price")
    match = _USP.search(declared)
    unit = _USP_UNITS.get(match.group(3).lower()) if match else None
    if not match or not unit:
        return Outcome(REVIEW, f"The unit sale price \"{declared}\" could not be read as an amount per unit.", "unit_sale_price")
    declared_dimension, unit_factor = unit
    per = Decimal(match.group(2) or "1") * unit_factor
    if declared_dimension != dimension:
        return Outcome(REVIEW, f"The unit sale price \"{declared}\" is per a different kind of unit than the net quantity ({label.text('net_quantity')}).", "unit_sale_price")
    if per != factor:
        return Outcome(REVIEW, f"The unit sale price \"{declared}\" is not per {base_name}; for this net quantity it is expected per {base_name} (about {_money(expected)}, {basis}).", "unit_sale_price")
    price = Decimal(match.group(1).replace(",", "."))
    allowed = max(Decimal(str(rule.configuration["tolerance"])) * expected, Decimal(str(rule.configuration["rounding_tolerance"])))
    if abs(price - expected) > allowed:
        difference = abs(price - expected) / expected * 100
        return Outcome(REVIEW, f"The declared unit sale price {_money(price)} per {base_name} differs by {difference:.1f}% from the expected {_money(expected)} per {base_name} ({basis}).", "unit_sale_price")
    return Outcome(PASS, f"Unit sale price \"{declared}\" is consistent with {basis} = {_money(expected)} per {base_name}.", "unit_sale_price")


def _manufacture_date(label: _Label, rule: RuleDefinition) -> Outcome:
    for field, name in (("manufacturing_date", "Manufacture"), ("packing_date", "Packing")):
        if value := label.text(field):
            return Outcome(PASS, f"{name} date found: {value}.", field)
    return Outcome(REVIEW, "No manufacture or packing date is visible in this photo — the date is often stamped elsewhere on the pack; capture that area.", "manufacturing_date")


_START_DATES, _END_DATES = ("manufacturing_date", "packing_date"), ("expiry_date", "use_by", "best_before")


def _date_consistency(label: _Label, rule: RuleDefinition) -> Outcome:
    supplied = [(name, label.text(name)) for name in _START_DATES + _END_DATES if label.text(name)]
    # "9 months from manufacture" is relative to the start date, so it is consistent by construction.
    supplied = [(name, value) for name, value in supplied if not (name == "best_before" and "month" in value.casefold())]
    parsed = {name: _parse_date(value) for name, value in supplied}
    if invalid := [name for name, value in parsed.items() if value is None]:
        return Outcome(REVIEW, f"The {invalid[0].replace('_', ' ')} \"{label.text(invalid[0])}\" cannot be read as a date; check it on the physical pack.", invalid[0])
    tolerance = int(rule.configuration.get("future_tolerance_months", 1))
    limit = _add_months(label.today, tolerance)
    starts = [(name, *parsed[name]) for name, _ in supplied if name in _START_DATES]  # type: ignore[misc]
    ends = [(name, *parsed[name]) for name, _ in supplied if name in _END_DATES]  # type: ignore[misc]
    for name, start, month_only in starts:
        if start > limit:
            return Outcome(FAIL, f"The {name.replace('_', ' ')} {label.text(name)} is in the future (checked on {label.today.isoformat()}).", name)
    if not starts or not ends:
        return Outcome(NOT_APPLICABLE, "Fewer than two comparable dates (a manufacture/packing date and an expiry/use-by date) were found.", starts[0][0] if starts else ends[0][0] if ends else None)
    for start_name, start, start_month in starts:
        for end_name, end, end_month in ends:
            later = (start.year, start.month) > (end.year, end.month) if start_month or end_month else start > end
            if later:
                return Outcome(FAIL, f"The {start_name.replace('_', ' ')} {label.text(start_name)} is after the {end_name.replace('_', ' ')} {label.text(end_name)}.", start_name)
    return Outcome(PASS, f"The {starts[0][0].replace('_', ' ')} {label.text(starts[0][0])} is not after the {ends[0][0].replace('_', ' ')} {label.text(ends[0][0])}.", starts[0][0])


_CARE_NORMALIZE = {
    "consumer_care_phone": lambda x: re.sub(r"[\s().-]", "", ComplianceEngine.clean(x)),
    "consumer_care_email": lambda x: re.sub(r"\s*([@.])\s*", r"\1", ComplianceEngine.clean(x)).lower(),
    "consumer_care_website": lambda x: ComplianceEngine.clean(x).lower().replace(" ", ""),
}
_CARE_VALID = {
    "consumer_care_phone": lambda x: 10 <= len(re.sub(r"\D", "", x)) <= 13,
    "consumer_care_email": lambda x: bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", x)),
    "consumer_care_website": lambda x: bool(re.fullmatch(r"(?:https?://)?(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)+/?", x)),
}


def _consumer_care(label: _Label, rule: RuleDefinition) -> Outcome:
    found = {field: label.text(field) for field in _CARE_NORMALIZE if label.text(field)}
    for field, value in found.items():
        if not _CARE_VALID[field](_CARE_NORMALIZE[field](value)):
            return Outcome(REVIEW, f"The consumer-care {field.rsplit('_', 1)[1]} \"{value}\" does not look valid; check it on the physical pack.", field)
    if "consumer_care_phone" in found or "consumer_care_email" in found:
        details = ", ".join(f"{field.rsplit('_', 1)[1]} {value}" for field, value in found.items())
        return Outcome(PASS, f"Consumer-care contact found: {details}.", next(iter(found)))
    if "consumer_care_website" in found:
        return Outcome(REVIEW, f"Only a website ({found['consumer_care_website']}) was found; a consumer-care telephone number and e-mail address are expected.", "consumer_care_website")
    if label.text("consumer_care_declaration") == "present_without_details":
        return Outcome(REVIEW, f"A consumer-care heading is printed{_quote(label.item('consumer_care_declaration').evidence)} but no phone number or e-mail could be read; the details may be cut off in this photo.", "consumer_care_declaration")
    return Outcome(FAIL, "No consumer-care contact details (phone, e-mail or website) and no consumer-care heading were found.", "consumer_care_phone")


def _fssai(label: _Label, rule: RuleDefinition) -> Outcome:
    licence = re.sub(r"\D", "", label.text("fssai_license") or "")
    if len(licence) == 14:
        return Outcome(PASS, f"FSSAI licence number {licence} found (14 digits).", "fssai_license")
    if licence:
        return Outcome(REVIEW, f"The licence number \"{label.text('fssai_license')}\" is not 14 digits; check the FSSAI licence on the physical pack.", "fssai_license")
    return Outcome(REVIEW, "This looks like a food product but no 14-digit FSSAI licence number was found.", "product_category")


CHECKS: dict[str, Callable[[_Label, RuleDefinition], Outcome]] = {
    "MANUFACTURER_INFORMATION": _manufacturer, "IMPORTER_INFORMATION": _importer, "COUNTRY_OF_ORIGIN": _country_of_origin,
    "PACKER_INFORMATION": _packer, "PRODUCT_IDENTITY": _product_identity, "NET_QUANTITY_FORMAT": _net_quantity,
    "NET_QUANTITY_UNIT_SUITABILITY": _unit_suitability, "MRP_FORMAT": _mrp, "MRP_TAX_INCLUSIVE": _tax_inclusive,
    "UNIT_SALE_PRICE": _unit_sale_price, "MANUFACTURE_DATE": _manufacture_date, "DATE_CONSISTENCY": _date_consistency,
    "CONSUMER_CARE_FORMAT": _consumer_care, "FSSAI_LICENSE_PRESENT": _fssai,
}
