"""Deterministic, explainable checks over normalized extraction evidence."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import logging
import re

logger = logging.getLogger(__name__)
RULES = (("PRODUCT_IDENTITY", "Product identity", "product_name", "WARNING"), ("MANUFACTURER_INFORMATION", "Manufacturer information", "manufacturer", "INFO"), ("PACKER_INFORMATION", "Packer information", "packer", "INFO"), ("IMPORTER_INFORMATION", "Importer information", "importer", "INFO"), ("MRP_FORMAT", "MRP format", "mrp", "WARNING"), ("NET_QUANTITY_FORMAT", "Net quantity format", "net_quantity", "WARNING"), ("DATE_CONSISTENCY", "Date consistency", "dates", "WARNING"), ("CONSUMER_CARE_FORMAT", "Consumer-care format", "consumer_care", "INFO"))

@dataclass(frozen=True)
class FieldEvidence:
    value: str | None = None; confidence: float | None = None; needs_review: bool = False; evidence: str | None = None
@dataclass(frozen=True)
class ComplianceFinding:
    rule_id: str; rule_name: str; status: str; severity: str; message: str; field: str | None = None; value: str | None = None; confidence: float | None = None; evidence: str | None = None

class ComplianceEngine:
    @staticmethod
    def clean(value: str) -> str:
        """Remove presentation-only OCR differences without changing label evidence."""
        return re.sub(r"\s+", " ", value.replace("\u00a0", " ").replace("\u200b", "").replace("–", "-").replace("—", "-")).strip()
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
    @classmethod
    def _finding(cls, code, name, status, severity, message, field=None, item=None):
        logger.debug("Compliance validation rule=%s field=%s raw=%r normalized=%r result=%s reason=%s", code, field, item.value if item else None, cls._normalized(code, item.value if item else None), status, message)
        return ComplianceFinding(code, name, status, severity, message, field, item.value if item else None, item.confidence if item else None, item.evidence if item else None)
    def evaluate(self, fields: dict[str, FieldEvidence]) -> list[ComplianceFinding]:
        findings = [self._identity(fields), *(self._role(fields, field) for field in ("manufacturer", "packer", "importer")), self._mrp(fields), self._quantity(fields), self._dates(fields), self._consumer_care(fields)]
        # Low extraction confidence is surfaced per field via needs_review, not as a finding.
        return findings
    def _identity(self, fields):
        item = fields.get("product_name", FieldEvidence()); return self._finding("PRODUCT_IDENTITY", "Product identity", "PASS" if item.value and self.clean(item.value) else "REVIEW", "WARNING", "A product identity was extracted." if item.value and self.clean(item.value) else "No product identity was extracted; manual review is required.", "product_name", item)
    def _role(self, fields, field):
        item = fields.get(field, FieldEvidence()); name = field.capitalize() + " information"
        if not item.value: return self._finding(field.upper()+"_INFORMATION", name, "REVIEW", "INFO", f"No {field} value was extracted; applicability is not configured and requires review.", field, item)
        return self._finding(field.upper()+"_INFORMATION", name, "PASS" if self.clean(item.value) else "FAIL", "INFO", f"A non-empty {field} value was extracted." if self.clean(item.value) else f"The extracted {field} value is empty.", field, item)
    def _mrp(self, fields):
        item = fields.get("mrp", FieldEvidence())
        if not item.value: return self._finding("MRP_FORMAT", "MRP format", "REVIEW", "WARNING", "No MRP was extracted; any requirement for it must be configured and reviewed.", "mrp", item)
        return self._finding("MRP_FORMAT", "MRP format", "PASS" if self.normalize_mrp(item.value) else "FAIL", "WARNING", "The extracted MRP has a recognized positive monetary representation." if self.normalize_mrp(item.value) else "The extracted MRP does not have a positive recognized monetary representation.", "mrp", item)
    def _quantity(self, fields):
        item = fields.get("net_quantity", FieldEvidence())
        if not item.value: return self._finding("NET_QUANTITY_FORMAT", "Net quantity format", "REVIEW", "WARNING", "No net quantity was extracted; applicability requires review.", "net_quantity", item)
        normalized = self.normalize_quantity(item.value)
        return self._finding("NET_QUANTITY_FORMAT", "Net quantity format", "PASS" if normalized else "FAIL", "WARNING", "The extracted net quantity has a positive recognized unit after normalization." if normalized else "The extracted net quantity is not a positive value with a recognized unit.", "net_quantity", item)
    @staticmethod
    def _parse_date(value):
        value = re.sub(r"\s*([/.-])\s*", r"\1", ComplianceEngine.clean(value)); month = re.fullmatch(r"(\d{1,2})[/.-](\d{4})", value); full = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})", value)
        try:
            if month: return date(int(month.group(2)), int(month.group(1)), 1)
            if full: return date(int(full.group(3)) + (2000 if int(full.group(3)) < 100 else 0), int(full.group(2)), int(full.group(1)))
        except ValueError: pass
        return None
    def _dates(self, fields):
        supplied = [(name, fields[name]) for name in ("manufacturing_date", "packing_date", "expiry_date", "use_by") if fields.get(name) and fields[name].value]; invalid = [name for name, item in supplied if not self._parse_date(item.value)]
        if invalid: return self._finding("DATE_CONSISTENCY", "Date consistency", "REVIEW", "WARNING", f"The date value for {', '.join(invalid)} is ambiguous or cannot be compared.", invalid[0], fields[invalid[0]])
        starts = [(name, self._parse_date(item.value)) for name, item in supplied if name in ("manufacturing_date", "packing_date")]; ends = [(name, self._parse_date(item.value)) for name, item in supplied if name in ("expiry_date", "use_by")]
        for name, start in starts:
            for _, end in ends:
                if start > end: return self._finding("DATE_CONSISTENCY", "Date consistency", "FAIL", "WARNING", f"{name} is later than the expiry/use-by date.", name, fields[name])
        return self._finding("DATE_CONSISTENCY", "Date consistency", "PASS", "WARNING", "Available comparable date values are internally consistent.")
    def _consumer_care(self, fields):
        values = {"consumer_care_email": lambda x: re.sub(r"\s*([@.])\s*", r"\1", self.clean(x)).lower(), "consumer_care_phone": lambda x: re.sub(r"[\s().-]", "", self.clean(x)), "consumer_care_website": lambda x: self.clean(x).lower().replace(" ", "")}; validators = {"consumer_care_email": lambda x: bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", x)), "consumer_care_phone": lambda x: 10 <= len(re.sub(r"\D", "", x)) <= 13, "consumer_care_website": lambda x: bool(re.fullmatch(r"(?:https?://)?(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)+/?", x))}
        for field, normalizer in values.items():
            item = fields.get(field, FieldEvidence())
            if item.value and not validators[field](normalizer(item.value)): return self._finding("CONSUMER_CARE_FORMAT", "Consumer-care format", "REVIEW", "INFO", f"The extracted {field.replace('_', ' ')} has an unrecognized structure.", field, item)
        return self._finding("CONSUMER_CARE_FORMAT", "Consumer-care format", "PASS", "INFO", "Available consumer-care information has a recognizable basic structure." if any(fields.get(f, FieldEvidence()).value for f in values) else "No consumer-care information was extracted; no requirement is assumed.")
