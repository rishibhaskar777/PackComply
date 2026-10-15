"""Deterministic, evidence-preserving package-label extraction heuristics.

Values are only taken from text attached to an explicit label key ("Net Wt.",
"M.R.P.", "Marketed by", "LIC. No."), found on the key's own line or — with
OCR boxes — in the box to its right or the block directly below it.  Nothing is
inferred: a key without a readable value yields no value.  Confidence is a
heuristic for *how* the value was found, never a probability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import re
from typing import Any, Callable, Iterator

from backend.app.services.compliance.rule_engine import ComplianceEngine
from backend.app.services.extraction.layout import Layout, LayoutLine, is_substantial

logger = logging.getLogger(__name__)


CORE_FIELD_NAMES = (
    "product_name", "manufacturer", "packer", "importer", "manufacturer_address",
    "packer_address", "importer_address", "mrp", "net_quantity", "manufacturing_date",
    "packing_date", "best_before", "use_by", "expiry_date", "consumer_care_phone",
    "consumer_care_email", "consumer_care_website",
)
# Persisted only when found: an absent value here is not a missing core field,
# so risk scoring and missing-field patterns keep their existing inputs.
SUPPLEMENTARY_FIELD_NAMES = (
    "brand_owner", "brand_owner_address", "manufacturing_units", "mrp_declaration", "tax_inclusive",
    "fssai_license", "country_of_origin", "unit_sale_price", "batch_number",
    "consumer_care_declaration", "import_declaration", "product_category",
)
FIELD_NAMES = CORE_FIELD_NAMES + SUPPLEMENTARY_FIELD_NAMES

# Confidence by how a value was located: the key's own line, the box to its
# right, the block below it, or (text-only OCR) simply the next line.
CONFIDENCE = {"same_line": 0.95, "right_of_key": 0.9, "below_key": 0.88, "next_line": 0.82}
AMBIGUITY_PENALTY = 0.25
PRODUCT_NAME_MAX_CONFIDENCE = 0.8


@dataclass(frozen=True)
class ExtractedValue:
    value: str | None = None
    confidence: float = 0.0
    needs_review: bool = False
    source_text: str | None = None
    matched_pattern: str | None = None
    normalized_value: str | None = None
    # Exact boxes of the OCR lines the value came from; None for text-only OCR.
    source_bboxes: list[list[list[float]]] | None = None


@dataclass(frozen=True)
class _Found:
    value: str
    normalized: str | None
    lines: tuple[LayoutLine, ...]
    method: str


@dataclass
class _Party:
    name: str
    method: str
    lines: list[LayoutLine]
    address: str | None = None
    address_lines: list[LayoutLine] = field(default_factory=list)
    units: list[dict[str, Any]] = field(default_factory=list)


# --------------------------------------------------------------- vocabulary

_CURRENCY = r"(?:₹|rs\.?|inr|रु\.?|रू\.?)"
_AMOUNT = r"(\d{1,3}(?:,\d{2,3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)(?![\d%A-Za-z])"
_NUMBER = r"([0-9OoIl]*\d[0-9OoIl]*(?:[.,]\d+)?)"
_LATIN_UNITS = (
    r"millilit(?:re|er)s?|milligrams?|kilograms?|centilit(?:re|er)s?|grams?|gms?|gm|kgs?|mgs?|m\s*l|ml|ltrs?|litres?|liters?"
    r"|cl|cc|cm(?:\^?3|³)|nos?\.?|pcs?\.?|pieces?|units?|n|l|g"
)
_HINDI_UNITS = {
    r"किलोग्राम|किग्रा|कि\.\s*ग्रा\.?": "kg", r"ग्राम|ग्रा\.?": "g",
    r"मिलीलीटर|मि\.\s*ली\.?|मिली": "ml", r"लीटर|ली\.": "l",
}
_UNITS = rf"(?:{_LATIN_UNITS})(?![A-Za-z])|{'|'.join(_HINDI_UNITS)}"
_QUANTITY = rf"{_NUMBER}\s*({_UNITS})"

_NET_KEY = (
    r"net\s*(?:wt|weight|qty|quantity|contents?|vol(?:ume)?|mass)\.?"
    r"(?:\s*\(?\s*(?:when|at\s*the\s*time\s*of)\s*pack(?:ed|ing)\s*\)?)?"  # "Net Wt. When Packed"
    r"|contents|शुद्ध\s*मात्रा|शुद्ध\s*वजन|कुल\s*मात्रा"
)
_MRP_KEY = r"m\.?\s*r\.?\s*p\.?|maximum\s+retail\s+price|अधिकतम\s*खुदरा\s*मूल्य|एमआरपी"
_TAX_INCLUSIVE = re.compile(r"(?i)incl(?:usive|uding|\.)?\s*(?:of\s*)?all\s*tax(?:es)?|सभी\s*करों\s*सहित|सभी\s*कर\s*सहित")
_LICENCE_KEY = r"lic(?:en[cs]e)?\s*[.,]?\s*(?:no|number|num)\.?|fssai\s*(?:lic(?:en[cs]e)?\.?\s*)?(?:no\.?|number)?"
_LICENCE_NUMBER = r"(\d(?:\s?\d){13})(?!\d)"
_FSSAI_LOGO = re.compile(r"(?i)(?<![A-Za-z])[fj]?ssa[ilíì](?![A-Za-z])")
_LICENCE = re.compile(rf"(?i)(?<![A-Za-z])(?:{_LICENCE_KEY})\s*[:.\-∴]*\s*{_LICENCE_NUMBER}")
_BATCH_KEY = r"batch\s*(?:no|number|code)?\.?|b\.?\s*no\.?|lot\s*(?:no|number)?\.?"
_COUNTRY_KEY = r"country\s*of\s*origin|made\s*in|मूल\s*देश|उत्पत्ति\s*का\s*देश"
_USP_KEY = r"unit\s*sale\s*price|u\.\s*s\.\s*p\.?|usp"
_USP_UNIT = r"(kg|g|gm|ml|l|ltr|units?|pcs?|pieces?|nos?|n)(?![A-Za-z])"

_MONTHS = {name: index for index, name in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_DATE_VALUE = (
    r"(\d{1,2}\s*[/.\-]\s*\d{1,2}\s*[/.\-]\s*(?:\d{4}|\d{2})(?!\d)"
    r"|\d{1,2}\s*[/.\-]\s*(?:\d{4}|\d{2})(?!\d)"
    r"|(?:\d{1,2}\s*[\-/ ]\s*)?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s*[\-/' ]?\s*(?:\d{4}|\d{2})(?!\d)"
    r"|\d{1,2}\s*months?(?:\s*from\s*(?:the\s*date\s*of\s*)?(?:manufacture|manufacturing|mfg\.?|packing|packaging|pkd\.?))?)"
)
_DATE_KEYS = {
    "manufacturing_date": r"date\s*of\s*(?:mfg|manufacture|manufacturing)|mfg\.?\s*date|mfd\.?\s*(?:date|on)|manufactured\s*on|manufacturing\s*date|mfg\.?|mfd\.?|निर्माण\s*(?:की\s*)?तिथि",
    "packing_date": r"date\s*of\s*pack(?:ing|aging)|packing\s*date|packed\s*on|pkd\.?\s*(?:date|on)?|पैकिंग\s*(?:की\s*)?तिथि",
    "use_by": r"use\s*by(?:\s*date)?|उपयोग\s*(?:करने\s*)?की\s*अंतिम\s*तिथि",
    "expiry_date": r"expiry(?:\s*date)?|exp\.?(?:\s*date)?|expires?(?:\s*on)?|समाप्ति(?:\s*तिथि)?",
    "best_before": r"best\s*before(?:\s*date)?|use\s*before|बेस्ट\s*बिफोर|इससे\s*पहले\s*उपयोग\s*करें",
}

_ROLE_KEYS = {
    "manufacturer": r"manufactured\s*(?:&|and)\s*marketed\s*by|manufactured\s*by|manufacturer|mfd\.?\s*by|mfg\.?\s*by|mfr\.?(?:\s*by)?|निर्माणकर्ता|निर्माता|निर्मित(?:\s*द्वारा)?",
    "brand_owner": r"manufactured\s*(?:&|and)\s*marketed\s*by|manufactured\s*for|mfd\.?\s*for|mfg\.?\s*for|marketed\s*by|mktd\.?\s*by|mkt\.?\s*by|distributed\s*by|marketer|विपणनकर्ता|विपणक",
    "packer": r"packed\s*by|pkd\.?\s*by|packer|पैककर्ता",
    "importer": r"imported\s*(?:in(?:to)?\s*india\s*)?by|imported\s*(?:,|&|and)\s*(?:marketed|distributed|packed)\s*by|importer|आयातकर्ता|आयातक",
}
_ROLE_LINE = {role: re.compile(rf"(?i)^[\s*•\-–]*(?:{key})(?![A-Za-z])(?:\s*[/|]\s*(?:{key})(?![A-Za-z]))?\s*[:\-.]?\s*") for role, key in _ROLE_KEYS.items()}
_ANY_ROLE = re.compile(rf"(?i)^[\s*•\-–]*(?:{'|'.join(_ROLE_KEYS.values())})(?![A-Za-z])")
# "Manufactured, Packed and Marketed by" names one party for several roles.
_ROLE_WORDS = {
    r"manufactured|mfd\.?|mfg\.?": "manufacturer", r"packed|pkd\.?": "packer",
    r"marketed|mktd\.?|mkt\.?|distributed": "brand_owner", r"imported": "importer",
}
_ROLE_WORD = "|".join(_ROLE_WORDS)
_COMBINED_ROLE = re.compile(rf"(?i)^[\s*•\-–'\"]*((?:{_ROLE_WORD})(?:\s*(?:,|&|/|and)\s*(?:{_ROLE_WORD}))+)\s*(by|for)(?![A-Za-z])\s*[:\-.]?\s*")
# "Tide Lemon & Mint Mfg by Procter & Gamble ...": a "<role> by/for" key after other text.
_INLINE_ROLE = re.compile(rf"(?i)(?<![A-Za-z])({_ROLE_WORD})\s*(by|for)(?![A-Za-z])\s*[:\-.]?\s*")
_UNIT_CODE = re.compile(r"^\(\s*([A-Z0-9]{1,3})\s*\)(?:[A-Za-z](?=\s))?\s*[-–:]?\s*")
_COMPANY_SUFFIX = r"pvt\.?\s*lt[do0]\.?|private\s+limited|ltd\.?|limited|llp|industries|foods?|co\.?|company|corporation|corp\.?|enterprises|inc\.?"
# The name ends at its suffix before a comma, the line end, a number or a full stop ("Limited. 'Ujala House'").
_COMPANY_NAME = re.compile(rf"(?i)^(.*?(?<![A-Za-z])(?:{_COMPANY_SUFFIX}))(?=\s*(?:[,;]|$|\d)|(?<=\.)\s|\.\s|\s*\([A-Z&]{{2,6}}\))")
# A maker named without a legal suffix ends where the address begins: a number
# or an address word ("... SANGH 18/57 KM STONE", "... Traders Plot 4").
_ADDRESS_START = re.compile(
    r"(?i)\s+(?=\d|(?:plot|survey|khasra|km|road|rd|street|floor|village|vill|sector|marg|lane|post|p\.\s*o|dist|near|opp|behind)(?![A-Za-z]))"
)
# Coded units inside a paragraph: "Mfg. by, read the code ... E) Unit One Pvt. Ltd., address. I) Unit Two Ltd., ...".
_PARAGRAPH_UNIT = re.compile(r"(?:(?<=[\s.,;])|^)\(?([A-Z0-9]{1,3})\)\s*[-–]?\s*(?=[A-Z])")
# A further legal suffix after the name, possibly after a short qualifier: "& Co. (Exports) Pvt. Ltd.".
_TRAILING_SUFFIX = re.compile(r"(?i)^\s*,?\s*(\([^()]{1,20}\)\s*)?((?:pvt\.?\s*)?ltd|private\s+limited|limited|inc)\.?(?=\s*(?:[,;]|$))")
_PIN = re.compile(r"(?<!\d)\d{3}\s?\d{3}(?!\d)")
_ADDRESS_HINT = re.compile(r"(?i)\d|road|street|nagar|area|estate|sector|village|plot|dist|marg|lane|colony|phase|india|भारत|नगर")
# Food labels: an ingredients list, a nutrition panel or FSSAI text (not the generic "Lic. No.").
_FOOD_HINT = re.compile(r"(?i)(?<![A-Za-z])(?:ingredients?|nutrition(?:al)?|fssai)(?![A-Za-z])|सामग्री|पोषण")
# Cosmetics and household products also print an ingredients list, but never
# a nutrition panel or FSSAI text: there, an ingredients list alone is not food.
_STRONG_FOOD_HINT = re.compile(r"(?i)(?<![A-Za-z])(?:nutrition(?:al)?|fssai)(?![A-Za-z])|पोषण")
_NON_FOOD_HINT = re.compile(
    r"(?i)(?<![A-Za-z])(?:soaps?|shampoos?|detergents?|toothpaste|deodorants?|cosmetics?|tfm|for\s*external\s*use(?:\s*only)?|"
    r"dish\s*wash|cleaner|disinfectant|surfactants?)(?![A-Za-z])"
)
_CARE_KEY = re.compile(
    r"(?i)consumer\s*(?:care|complaints?|services?|relations?|comments?|affairs|cell|helpline)|customer\s*(?:care|services?|support|relations?|feedback)|"
    r"complaints?|help\s*line|contact|toll\s*free|ग्राहक\s*सेवा|उपभोक्ता\s*सेवा"
)
_PHONE = (
    re.compile(r"(?<!\d)(?:\+?91[\s\-]?)?(?:1800|1860)[\s\-]?\d{3}[\s\-]?\d{3,4}(?!\d)"),
    re.compile(r"(?<!\d)(?:\+?91[\s\-]?)?[6-9]\d{4}[\s\-]?\d{5}(?!\d)"),
    re.compile(r"(?<!\d)0\d{2,4}[\s\-]?\d{6,8}(?!\d)"),
    re.compile(r"(?<!\d)(?:\+?91[\s\-]?)?(?:\d[\s\-]?){9,11}\d(?!\d)"),
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_WEB_TOKEN = re.compile(r"(?i)(?<![\w@.])(?:https?://)?[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)+(?:/[^\s,;]*)?")
_TLDS = ("co.in", "gov.in", "org.in", "net.in", "ac.in", "com", "in", "net", "org", "info", "biz")
_STOP_LINE = re.compile(
    rf"(?i)(?<![A-Za-z])(?:{_MRP_KEY}|{_NET_KEY}|{_LICENCE_KEY}|best\s*before|use\s*b(?:y|efore)|expiry|batch|consumer\s*care|customer\s*care|"
    rf"country\s*of\s*origin|e-?mail|www\.|ingredients|nutrition)(?![A-Za-z])|@"
)
_BOILERPLATE = re.compile(
    r"(?i)warning|caution|ingredient|nutrition|contains|allergen|store|storage|manufactur|market|packed|packer|(?<![a-z])lic(?![a-z])|licen[cs]e|"
    r"fssai|m\.?\s*r\.?\s*p|(?<![a-z])net(?![a-z])|best\s*before|batch|consumer|customer|use\s*by|expir|(?<![a-z])exp(?![a-z])|mfg|mfd|pkd|"
    r"import|country\s*of|origin|address|e-?mail|www|http|helpline|toll\s*free|energy|protein|carbohydrate|sugars?(?![a-z])|(?<![a-z])fat(?![a-z])|"
    r"fibre|fiber|sodium|per\s*100|serving|incl|tax|prohibited|trademark|export|percent|approx|(?<![a-z])scan(?![a-z])|qr\s*code|participate|vouchers?|(?<![a-z])offers?(?![a-z])|contest|terms\s*(?:and|&)\s*conditions|सामग्री|पोषण|निर्माता|शुद्ध\s*(?:मात्रा|वजन)|अधिकतम|तिथि|ग्राहक"
)


def _key(pattern: str) -> re.Pattern[str]:
    return re.compile(rf"(?i)(?<![A-Za-z])(?:{pattern})(?![A-Za-z])")


def _source(lines: tuple[LayoutLine, ...] | list[LayoutLine]) -> str:
    return " | ".join(dict.fromkeys(line.text for line in lines))


def _boxes(lines: tuple[LayoutLine, ...] | list[LayoutLine]) -> list[list[list[float]]] | None:
    unique = list(dict.fromkeys(line.index for line in lines))
    by_index = {line.index: line for line in lines}
    return [box for index in unique for box in by_index[index].bboxes] or None


def _clean_value(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(" :-|,;")


class ExtractionService:
    """Rule-based, layout-aware extraction; confidence is a heuristic, never a probability."""

    def extract(self, ocr_text: str, detections: list[Any] | None = None) -> dict[str, ExtractedValue]:
        layout = Layout.build(ocr_text, detections)
        results = {name: ExtractedValue() for name in FIELD_NAMES}
        if not layout.lines:
            return results
        results.update(self._parties(layout))
        results["net_quantity"] = self._net_quantity(layout)
        results.update(self._mrp(layout))
        results.update(self._dates(layout))
        results.update(self._consumer_care(layout))
        results["fssai_license"] = self._fssai(layout)
        results["country_of_origin"] = self._country(layout)
        results["unit_sale_price"] = self._unit_sale_price(layout)
        results["batch_number"] = self._batch(layout)
        results["import_declaration"] = self._first_line(layout, _key(_ROLE_KEYS["importer"]), "present", "import_declaration:key")
        results["product_category"] = self._product_category(layout)
        results["product_name"] = self._product_name(layout, results)
        logger.debug("Extraction layout geometric=%s lines=%d fields=%s", layout.geometric, len(layout.lines), {name: value.value for name, value in results.items() if value.value})
        return results

    # ------------------------------------------------------------ generic

    @staticmethod
    def _neighbours(layout: Layout, line: LayoutLine) -> Iterator[tuple[LayoutLine, str]]:
        if layout.geometric:
            if (right := layout.right_of(line)) is not None:
                yield right, "right_of_key"
            if (below := layout.next_in_column(line)) is not None:
                yield below, "below_key"
        elif (following := layout.next_in_column(line)) is not None:
            yield following, "next_line"

    def _keyed(self, layout: Layout, key: str, value: str, build: Callable[[re.Match[str]], tuple[str, str | None] | None], alone: str | None = None) -> list[_Found]:
        """Values attached to ``key``: on the key's line, else a neighbouring line that is only a value."""
        key_re = _key(key)
        same_re = re.compile(rf"(?i)\s*[:\-.=∴]*\s*(?:{value})")
        alone_re = re.compile(rf"(?i)\s*(?:{alone or value})\s*[.,;]?\s*")
        found: list[_Found] = []
        for line in layout.lines:
            match = key_re.search(line.text)
            if not match:
                continue
            same = same_re.match(line.text, match.end())
            if same and (built := build(same)):
                found.append(_Found(*built, (line,), "same_line"))
                continue
            for neighbour, method in self._neighbours(layout, line):
                single = alone_re.fullmatch(neighbour.text)
                if single and (built := build(single)):
                    found.append(_Found(*built, (line, neighbour), method))
                    break
        return found

    @staticmethod
    def _choose(name: str, found: list[_Found], review: bool = False) -> ExtractedValue:
        if not found:
            return ExtractedValue()
        first = found[0]
        ambiguous = len(dict.fromkeys(item.normalized or item.value for item in found)) > 1
        confidence = CONFIDENCE[first.method] - (AMBIGUITY_PENALTY if ambiguous else 0.0)
        return ExtractedValue(first.value, round(confidence, 2), ambiguous or review, _source(first.lines), f"{name}:{first.method}", first.normalized or first.value, _boxes(first.lines))

    @staticmethod
    def _first_line(layout: Layout, pattern: re.Pattern[str], value: str, method: str, confidence: float = 0.85) -> ExtractedValue:
        """A fixed value when ``pattern`` occurs on any line; the first such line is the evidence."""
        line = next((line for line in layout.lines if pattern.search(line.text)), None)
        return ExtractedValue(value, confidence, False, line.text, method, value, _boxes([line])) if line else ExtractedValue()

    # ----------------------------------------------------------- quantity

    @staticmethod
    def _quantity(match: re.Match[str]) -> tuple[str, str | None] | None:
        amount, unit = match.group(1), re.sub(r"\s+", "", match.group(2))
        for pattern, latin in _HINDI_UNITS.items():
            if re.fullmatch(pattern, unit):
                unit = latin
        declared = f"{amount} {unit}"
        normalized = ComplianceEngine.normalize_quantity(declared)
        return (declared, normalized) if normalized else None

    def _net_quantity(self, layout: Layout) -> ExtractedValue:
        # Only quantities attached to a net-quantity key count, so nutrition
        # rows ("PROTEIN 6.5g", "AMOUNT PER 100g") are never candidates.
        found = self._keyed(layout, _NET_KEY, rf"(?:\([^)]*\)\s*)?{_QUANTITY}", self._quantity, rf"{_QUANTITY}\s*(?:\(?\s*(?:e|℮|approx\.?)\s*\)?)?")
        return self._choose("net_quantity", found)

    # ---------------------------------------------------------------- MRP

    def _mrp(self, layout: Layout) -> dict[str, ExtractedValue]:
        def build(match: re.Match[str]) -> tuple[str, str | None] | None:
            value = f"₹{match.group(1).replace(',', '')}"
            return (value, ComplianceEngine.normalize_mrp(value)) if ComplianceEngine.normalize_mrp(value) else None

        price = rf"(?:\([^)]*\)\s*)?(?:{_CURRENCY})?\s*{_AMOUNT}\s*(?:/-|/)?"
        mrp = self._choose("mrp", self._keyed(layout, _MRP_KEY, price, build, rf"(?:{_CURRENCY})?\s*{_AMOUNT}\s*(?:/-)?(?:\s*\([^)]*\))?"))
        key_re = _key(_MRP_KEY)
        key_lines = [line for line in layout.lines if key_re.search(line.text)]
        if mrp.value:
            declaration = ExtractedValue("present_with_value", mrp.confidence, False, mrp.source_text, "mrp_declaration:key_with_value", "present_with_value", mrp.source_bboxes)
        elif key_lines:
            # The key is printed but no price is readable beside it, e.g. a price
            # stamped separately: never invent one, flag it for a person.
            declaration = ExtractedValue("present_without_value", 0.85, True, _source(key_lines[:1]), "mrp_declaration:key_without_value", "present_without_value", _boxes(key_lines[:1]))
        else:
            declaration = ExtractedValue("absent", 0.7, False, None, "mrp_declaration:no_key", "absent")
        tax_lines = [line for line in layout.lines if _TAX_INCLUSIVE.search(line.text)]
        tax = ExtractedValue("true", 0.95, False, _source(tax_lines[:1]), "tax_inclusive:statement", "true", _boxes(tax_lines[:1])) if tax_lines else ExtractedValue()
        return {"mrp": mrp, "mrp_declaration": declaration, "tax_inclusive": tax}

    def _unit_sale_price(self, layout: Layout) -> ExtractedValue:
        def build(match: re.Match[str]) -> tuple[str, str | None]:
            amount, per, unit = match.group(1).replace(",", ""), (match.group(2) or "").strip(), match.group(3).lower()
            return f"₹{amount} per {per}{' ' if per else ''}{unit}", f"₹{amount}/{per}{unit}"

        price = rf"(?:{_CURRENCY})?\s*{_AMOUNT}\s*(?:per|/)\s*(\d+\s*)?{_USP_UNIT}"
        found = self._keyed(layout, _USP_KEY, price, build)
        if not found:
            # Unlabelled "₹0.25 per g": the currency and a per-unit are mandatory.
            keyless = re.compile(rf"(?i)(?:{_CURRENCY})\s*{_AMOUNT}\s*(?:per|/)\s*(\d+\s*)?{_USP_UNIT}")
            mrp_key = _key(_MRP_KEY)
            found = [_Found(*build(match), (line,), "below_key") for line in layout.lines if not mrp_key.search(line.text) and (match := keyless.search(line.text))]
        return self._choose("unit_sale_price", found)

    # -------------------------------------------------------------- dates

    @staticmethod
    def normalize_date(value: str) -> str | None:
        """YYYY-MM-DD, YYYY-MM, or an ISO-8601 duration (P9M) for "N months from ..."."""
        text = re.sub(r"\s+", " ", value.strip().lower())
        year = lambda digits: int(digits) + (2000 if len(digits) == 2 else 0)
        try:
            if months := re.fullmatch(r"(\d{1,2}) ?months?.*", text):
                return f"P{int(months.group(1))}M"
            if full := re.fullmatch(r"(\d{1,2}) ?[/.\-] ?(\d{1,2}) ?[/.\-] ?(\d{4}|\d{2})", text):
                day, month = int(full.group(1)), int(full.group(2))
                if 1 <= month <= 12 and 1 <= day <= 31:
                    return f"{year(full.group(3)):04d}-{month:02d}-{day:02d}"
                return None
            if month_year := re.fullmatch(r"(\d{1,2}) ?[/.\-] ?(\d{4}|\d{2})", text):
                month = int(month_year.group(1))
                return f"{year(month_year.group(2)):04d}-{month:02d}" if 1 <= month <= 12 else None
            if named := re.fullmatch(r"(?:(\d{1,2}) ?[\-/ ] ?)?([a-z]{3})[a-z]*\.? ?[\-/' ]? ?(\d{4}|\d{2})", text):
                if named.group(2) not in _MONTHS:
                    return None
                prefix = f"{year(named.group(3)):04d}-{_MONTHS[named.group(2)]:02d}"
                return f"{prefix}-{int(named.group(1)):02d}" if named.group(1) else prefix
        except ValueError:
            return None
        return None

    def _dates(self, layout: Layout) -> dict[str, ExtractedValue]:
        def build(match: re.Match[str]) -> tuple[str, str | None] | None:
            raw = re.sub(r"\s+", " ", match.group(1)).strip()
            value = raw.lower() if "month" in raw.lower() else raw
            normalized = self.normalize_date(value)
            return (value, normalized) if normalized else None

        results: dict[str, ExtractedValue] = {}
        for name, key in _DATE_KEYS.items():
            # "Mfd. by" / "Pkd. for" introduce a party, never a date.
            found = self._keyed(layout, rf"(?:{key})(?:\s*date)?(?!\s*[.:]?\s*(?:by|for)(?![A-Za-z]))", rf"(?:date\s*[:\-.]?\s*)?{_DATE_VALUE}", build)
            results[name] = self._choose(name, found)
        return results

    @classmethod
    def _product_category(cls, layout: Layout) -> ExtractedValue:
        """Food when a nutrition panel or FSSAI text is printed, or an ingredients
        list on a label that does not name itself a cosmetic or household product."""
        strong = cls._first_line(layout, _STRONG_FOOD_HINT, "food", "product_category:food_keywords", 0.8)
        if strong.value or any(_NON_FOOD_HINT.search(line.text) for line in layout.lines):
            return strong
        return cls._first_line(layout, _FOOD_HINT, "food", "product_category:food_keywords", 0.8)

    # ------------------------------------------------- identifiers & origin

    def _fssai(self, layout: Layout) -> ExtractedValue:
        found = self._keyed(layout, _LICENCE_KEY, _LICENCE_NUMBER, lambda match: (re.sub(r"\s", "", match.group(1)),) * 2)
        if not found:
            return ExtractedValue()
        # Multi-unit labels print one licence per unit; the brand owner's is the
        # one printed with the fssai logo, else the first on the label.
        logos = [box for line in layout.lines for box in line.boxes if box.bbox and _FSSAI_LOGO.search(box.text)]
        first = next((item for item in found if any(self._beside_logo(item, logo) for logo in logos)), found[0])
        distinct = len(dict.fromkeys(item.value for item in found))
        pattern = f"fssai_license:{first.method}" + (f":first_of_{distinct}" if distinct > 1 else "")
        return ExtractedValue(first.value, round(CONFIDENCE[first.method] - (0.05 if distinct > 1 else 0.0), 2), False, _source(first.lines), pattern, first.value, _boxes(first.lines))

    @staticmethod
    def _beside_logo(found: _Found, logo: Any) -> bool:
        """The licence number's box is on the logo's row or just below it, overlapping it horizontally."""
        digits = found.value[:6]
        boxes = [box for line in found.lines for box in line.boxes if box.bbox and digits in re.sub(r"\s", "", box.text)]
        lx0, ly0, lx1, ly1 = logo.extent
        for box in boxes:
            x0, y0, x1, y1 = box.extent
            if box is logo or (min(x1, lx1) - max(x0, lx0) > 0 and -logo.height <= y0 - ly1 <= 3 * logo.height):
                return True
        return False

    def _batch(self, layout: Layout) -> ExtractedValue:
        def build(match: re.Match[str]) -> tuple[str, str | None] | None:
            token = match.group(1).strip("-/")
            return (token, token.upper()) if re.search(r"\d", token) else None

        return self._choose("batch_number", self._keyed(layout, _BATCH_KEY, r"[#:\-.]*\s*([A-Z0-9][A-Z0-9/\-]{1,19})(?![A-Za-z0-9])", build))

    def _country(self, layout: Layout) -> ExtractedValue:
        def build(match: re.Match[str]) -> tuple[str, str | None] | None:
            country = _clean_value(match.group(1))
            return (country, country.title()) if 0 < len(country.split()) <= 4 and not _STOP_LINE.search(country) else None

        value = r"([A-Za-zऀ-ॿ][A-Za-zऀ-ॿ .&\-]{1,40}?)\s*(?=[.,;(]|$)"
        return self._choose("country_of_origin", self._keyed(layout, _COUNTRY_KEY, value, build))

    # ------------------------------------------------------------ parties

    @staticmethod
    def _strip_unit_code(text: str) -> tuple[str | None, str]:
        match = _UNIT_CODE.match(text)
        return (match.group(1), text[match.end():]) if match else (None, text)

    @staticmethod
    def _split_company(text: str) -> tuple[str, str]:
        """Company name and the address text that follows it on the same line."""
        text = _clean_value(text)
        match = _COMPANY_NAME.match(text)
        if match:
            name, rest = match.group(1), text[match.end():]
            while extra := _TRAILING_SUFFIX.match(rest):
                # Kept as printed when a qualifier sits between ("Co. (Exports) Pvt. Ltd.").
                joined = f"{name} {rest[:extra.end()].strip()}" if extra.group(1) else f"{name}, {extra.group(2)}."
                name, rest = joined, rest[extra.end():]
        else:
            name, _, rest = text.partition(",")
            if (address := _ADDRESS_START.search(name)) and address.start() > 0:
                name, rest = name[:address.start()], f"{name[address.end():]}, {rest}" if rest else name[address.end():]
        # "PVT LTO" / "PVT LT0" are OCR misreads of the "PVT LTD" suffix.
        name = re.sub(r"(?i)(pvt\.?\s*)lt[o0]\b\.?", lambda m: m.group(1) + ("LTD" if m.group(1).isupper() else "Ltd"), _clean_value(name))
        return name, _clean_value(rest.lstrip(". "))

    def _is_company(self, text: str) -> bool:
        _, text = self._strip_unit_code(_clean_value(text))
        if not text or _ANY_ROLE.match(text) or _STOP_LINE.search(text) or _LICENCE.search(text):
            return False
        has_suffix = bool(_COMPANY_NAME.match(text))
        if not has_suffix and (address := _ADDRESS_START.search(text)) and address.start() > 0:
            text = text[:address.start()]  # "Gram Udyog 12/40 KM Stone, ..., 125001": judge the name part
        if (text[0].isdigit() or _PIN.search(text)) and not has_suffix:
            return False  # an address line, not a company
        words = [word for word in re.findall(r"[A-Za-zऀ-ॿ]{2,}", text) if word.lower() not in {"for", "by", "ltd", "limited", "pvt", "the", "and", "of"}]
        return len(words) >= 2 or (bool(words) and has_suffix)

    def _is_stop(self, text: str) -> bool:
        return bool(_ANY_ROLE.match(text) or _STOP_LINE.search(text) or _LICENCE.search(text) or _UNIT_CODE.match(text))

    def _address(self, layout: Layout, line: LayoutLine, remainder: str) -> tuple[str | None, list[LayoutLine]]:
        """Lines after the company until the next key, a licence line, or a PIN-code line (included)."""
        parts, lines = ([remainder] if remainder else []), []
        current: LayoutLine | None = line
        while not (parts and _PIN.search(parts[-1])) and len(lines) < 4:
            current = layout.next_in_column(current) if current else None
            if current is None or self._is_stop(current.text):
                break
            parts.append(current.text)
            lines.append(current)
        address = ", ".join(part for part in (_clean_value(part) for part in parts) if part)
        return (address, lines) if address and _ADDRESS_HINT.search(address) else (None, [])

    def _units(self, layout: Layout, line: LayoutLine, text: str) -> list[dict[str, Any]]:
        """Every "(CODE)-COMPANY, address…, LIC. No." unit in the block that starts at ``line``."""
        units: list[dict[str, Any]] = []
        current: LayoutLine | None = line
        while current is not None and len(units) < 20:
            code, rest = self._strip_unit_code(text)
            licence = _LICENCE.search(text)
            if code:
                name, remainder = self._split_company(rest)
                units.append({"code": code, "name": name, "address": [remainder] if remainder else [], "licence": None, "lines": [current], "address_lines": [current] if remainder else []})
            elif licence and units and not units[-1]["licence"]:
                units[-1]["licence"] = re.sub(r"\s", "", licence.group(1))
                units[-1]["lines"].append(current)
            elif not units or units[-1]["licence"] or self._is_stop(text):
                break  # after a unit's licence only another unit continues the block
            else:
                units[-1]["address"].append(text)
                units[-1]["lines"].append(current)
                units[-1]["address_lines"].append(current)
            current = layout.next_in_column(current)
            text = current.text if current else ""
        for unit in units:
            unit["address"] = ", ".join(part for part in (_clean_value(part) for part in unit["address"]) if part) or None
        return units

    def _paragraph_units(self, layout: Layout, key_line: LayoutLine, rest: str) -> _Party | None:
        """Coded units written as running text after the key ("read the code ... E) X Pvt. Ltd., address. I) Y Ltd., ...")."""
        lines, current = [key_line], key_line
        for _ in range(6):
            current = layout.next_in_column(current)
            if current is None or _ANY_ROLE.match(current.text) or _STOP_LINE.search(current.text):
                break
            lines.append(current)
        text = " ".join([rest, *(line.text for line in lines[1:])])
        markers = []
        for marker in _PARAGRAPH_UNIT.finditer(text):
            company = _COMPANY_NAME.match(text[marker.end():])
            if company and len(company.group(1).split()) <= 8:
                markers.append(marker)
        # Only when the text before the first unit is an instruction, not a company.
        if not markers or _COMPANY_NAME.match(text[:markers[0].start()]):
            return None
        units = []
        for index, marker in enumerate(markers):
            end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
            name, remainder = self._split_company(text[marker.end():end])
            units.append({"code": marker.group(1), "name": name, "address": remainder or None, "licence": None, "lines": lines, "address_lines": lines})
        first = units[0]
        return _Party(first["name"], "same_line", lines, first["address"], lines, units)

    def _party(self, layout: Layout, key_line: LayoutLine, rest: str, inline: bool = False) -> _Party | None:
        """The party named after a role key; ``inline`` keys (after other text) need a company suffix."""
        rest = _clean_value(rest)
        if rest and not inline and not _UNIT_CODE.match(rest) and (paragraph := self._paragraph_units(layout, key_line, rest)):
            return paragraph
        if rest and self._is_company(rest):
            value_line, text, method = key_line, rest, "same_line"
        else:
            for value_line, method in self._neighbours(layout, key_line):
                if self._is_company(value_line.text):
                    text = value_line.text
                    break
            else:
                return None
        evidence = [key_line] if value_line is key_line else [key_line, value_line]
        units = self._units(layout, value_line, text) if _UNIT_CODE.match(text) else []
        if len(units) > 1:
            first = units[0]
            return _Party(first["name"], method, evidence, first["address"], first["address_lines"], units)
        _, text = self._strip_unit_code(text)
        name, remainder = self._split_company(text)
        address_start, address_lines = value_line, [value_line] if remainder else []
        if not remainder and not _COMPANY_NAME.match(name) and (following := layout.next_in_column(value_line)) is not None:
            # A long name wrapped onto the next line: "... Home Products Pvt" / "Ltd, Plot No. 1, ...".
            wrapped = _COMPANY_NAME.match(_clean_value(following.text))
            if wrapped and len(wrapped.group(1).split()) <= 5 and not re.search(r"\d", wrapped.group(1)):
                name, remainder = self._split_company(f"{name} {following.text}")
                evidence, address_start, address_lines = evidence + [following], following, [following] if remainder else []
        if inline and not _COMPANY_NAME.match(name):
            return None
        address, more_lines = self._address(layout, address_start, remainder)
        return _Party(name, method, evidence, address, address_lines + more_lines)

    def _role_mentions(self, layout: Layout) -> dict[str, list[tuple[int, _Party]]]:
        """Every (line index, party) per role: single-role keys, combined keys and inline keys."""
        mentions: dict[str, list[tuple[int, _Party]]] = {role: [] for role in _ROLE_LINE}
        for line in layout.lines:
            if combined := _COMBINED_ROLE.match(line.text):
                words = re.split(r"(?i)\s*(?:,|&|/|\band\b)\s*", combined.group(1))
                roles = {"brand_owner"} if combined.group(2).lower() == "for" else {
                    role for word in words for pattern, role in _ROLE_WORDS.items() if re.fullmatch(rf"(?i){pattern}", word.strip())
                }
                if party := self._party(layout, line, line.text[combined.end():]):
                    for role in roles:
                        mentions[role].append((line.index, party))
                continue
            matched = False
            for role, key_re in _ROLE_LINE.items():
                if (match := key_re.match(line.text)) and (party := self._party(layout, line, line.text[match.end():])):
                    mentions[role].append((line.index, party))
                    matched = True
            if matched or _ANY_ROLE.match(line.text):
                continue
            inline = _INLINE_ROLE.search(line.text)
            if inline and inline.start() > 0 and (party := self._party(layout, line, line.text[inline.end():], inline=True)):
                word = inline.group(1)
                role = "brand_owner" if inline.group(2).lower() == "for" else next(role for pattern, role in _ROLE_WORDS.items() if re.fullmatch(rf"(?i){pattern}", word))
                mentions[role].append((line.index, party))
        return mentions

    def _parties(self, layout: Layout) -> dict[str, ExtractedValue]:
        values: dict[str, ExtractedValue] = {}
        for role, mentions in self._role_mentions(layout).items():
            parties = [party for _, party in sorted(mentions, key=lambda item: item[0])]
            if not parties:
                continue
            chosen = parties[0]
            ambiguous = len(dict.fromkeys(party.name.casefold() for party in parties)) > 1
            multi_unit = len(chosen.units) > 1
            confidence = CONFIDENCE[chosen.method] - (AMBIGUITY_PENALTY if ambiguous else 0.0) - (0.15 if multi_unit else 0.0)
            pattern = "multi_unit_label" if multi_unit else f"{role}:{chosen.method}"
            values[role] = ExtractedValue(chosen.name, round(confidence, 2), ambiguous or multi_unit, _source(chosen.lines + (chosen.units[0]["lines"][:1] if multi_unit else [])), pattern, chosen.name, _boxes(chosen.lines))
            if chosen.address:
                values[f"{role}_address"] = ExtractedValue(chosen.address, round(0.82 - (0.2 if ambiguous else 0.0), 2), ambiguous or multi_unit, _source(chosen.address_lines), f"{role}_address:lines_after_company", chosen.address, _boxes(chosen.address_lines))
            if multi_unit and role == "manufacturer":
                units = [{key: unit[key] for key in ("code", "name", "address", "licence")} for unit in chosen.units]
                unit_lines = [line for unit in chosen.units for line in unit["lines"]]
                serialized = json.dumps(units, ensure_ascii=False)
                values["manufacturing_units"] = ExtractedValue(serialized, 0.8, True, _source(chosen.lines[:1] + unit_lines), "multi_unit_label", serialized, _boxes(chosen.lines[:1] + unit_lines))
        return values

    # ----------------------------------------------------- consumer care

    @staticmethod
    def _website(text: str) -> str | None:
        for match in _WEB_TOKEN.finditer(text):
            token = match.group(0).rstrip(".")
            if text[match.end():match.end() + 1] == "@":
                continue
            host = re.sub(r"(?i)^https?://", "", token).split("/")[0].lower()
            labels = host.split(".")
            explicit = token.lower().startswith(("www.", "http://", "https://"))
            real_tld = any(host.endswith("." + tld) for tld in _TLDS)
            name = labels[1] if labels[0] == "www" and len(labels) > 2 else labels[0]
            if real_tld and len(labels) >= 2 and re.search(r"[a-z]{2,}", name) and (explicit or len(name) >= 3):
                return token
        return None

    def _consumer_care(self, layout: Layout) -> dict[str, ExtractedValue]:
        result = {name: ExtractedValue() for name in ("consumer_care_phone", "consumer_care_email", "consumer_care_website")}
        blocks: list[list[LayoutLine]] = []
        for line in layout.lines:
            if _CARE_KEY.search(line.text):
                block = [line]
                if (right := layout.right_of(line)) is not None:
                    block.append(right)
                current: LayoutLine | None = line
                for _ in range(4):
                    current = layout.next_in_column(current) if current else None
                    if current is None:
                        break
                    block.append(current)
                blocks.append(block)

        def in_block(name: str, find: Callable[[str], str | None], normalize: Callable[[str], str]) -> bool:
            for block in blocks:
                for line in block:
                    if value := find(line.text):
                        lines = [block[0]] if line is block[0] else [block[0], line]
                        method = "same_line" if line is block[0] else "below_key" if layout.geometric else "next_line"
                        result[name] = ExtractedValue(value, CONFIDENCE[method], False, _source(lines), f"{name}:{method}", normalize(value), _boxes(lines))
                        return True
            return False

        def phone(text: str) -> str | None:
            for pattern in _PHONE:
                if match := pattern.search(text):
                    return re.sub(r"\s+", " ", match.group(0)).strip()
            return None

        email = lambda text: match.group(0) if (match := _EMAIL.search(text)) else None
        if not in_block("consumer_care_phone", phone, lambda value: re.sub(r"[^\d+]", "", value)):
            # A toll-free 1800 / 1860 number is a consumer-care line wherever it
            # is printed, unless its line belongs to another key ("Batch 1800 ...").
            for line in layout.lines:
                if not self._is_stop(line.text) and (match := _PHONE[0].search(line.text)):
                    value = re.sub(r"\s+", " ", match.group(0)).strip()
                    result["consumer_care_phone"] = ExtractedValue(value, 0.8, False, line.text, "consumer_care_phone:toll_free_anywhere", re.sub(r"[^\d+]", "", value), _boxes([line]))
                    break
        for name, find, normalize in (("consumer_care_email", email, str.lower), ("consumer_care_website", self._website, str.lower)):
            if in_block(name, find, normalize):
                continue
            # Outside a care block only the unambiguous structure counts.
            for line in layout.lines:
                if value := find(line.text):
                    result[name] = ExtractedValue(value, 0.8, False, line.text, f"{name}:anywhere", normalize(value), _boxes([line]))
                    break
        # Like mrp_declaration: a printed heading whose details are unreadable
        # (cut off in the photo) is different from no consumer-care block at all.
        if not blocks:
            declaration = ExtractedValue("absent", 0.7, False, None, "consumer_care_declaration:no_key", "absent")
        elif any(value.value for value in result.values()):
            declaration = ExtractedValue("present_with_details", 0.9, False, blocks[0][0].text, "consumer_care_declaration:key_with_details", "present_with_details", _boxes(blocks[0][:1]))
        else:
            declaration = ExtractedValue("present_without_details", 0.85, True, blocks[0][0].text, "consumer_care_declaration:key_without_details", "present_without_details", _boxes(blocks[0][:1]))
        result["consumer_care_declaration"] = declaration
        return result

    # ------------------------------------------------------- product name

    @staticmethod
    def _name_candidates(line: LayoutLine) -> Iterator[tuple[str, str]]:
        """(text, how) a line can offer as the product name.

        Besides the whole line: the words before an inline maker key ("Tide
        Lemon & Mint Mfg by ...") and a first sentence followed by boilerplate
        ("Powder Laundry Detergent. CAUTION: ...").
        """
        inline = _INLINE_ROLE.search(line.text)
        if inline and inline.start() > 0 and not _ANY_ROLE.match(line.text):
            prefix = _clean_value(line.text[:inline.start()])
            if 1 <= len(prefix.split()) <= 6 and not re.search(r"\d", prefix):
                yield prefix, "before_maker_key"
            return
        first, separator, after = line.text.partition(". ")
        if separator and len(first.split()) >= 2 and _BOILERPLATE.search(after):
            yield _clean_value(first), "first_sentence"
            return
        yield line.text, "prominent_line"

    def _product_name(self, layout: Layout, results: dict[str, ExtractedValue]) -> ExtractedValue:
        used = {value.source_text for value in results.values() if value.value and value.source_text}
        used_lines = {text for source in used for text in source.split(" | ")}
        page_height, typical = layout.page_height, layout.median_height
        scored: list[tuple[float, LayoutLine, str, str]] = []
        for line in layout.lines:
            if line.vertical:
                continue  # rotated side text ("scan QR code") is never the product name
            for text, how in self._name_candidates(line):
                alnum = re.findall(r"[0-9A-Za-zऀ-ॿ]", text)
                if (how == "prominent_line" and text in used_lines) or not re.search(r"[A-Za-zऀ-ॿ]{2,}", text) or len(text) > 80 or len(alnum) < 3:
                    continue
                if _BOILERPLATE.search(text) or _FSSAI_LOGO.search(text) or _UNIT_CODE.match(text) or _PIN.search(text) or sum(c.isdigit() for c in alnum) / len(alnum) > 0.4:
                    continue
                if layout.geometric:
                    prominence = 2.0 * min(line.box_height / typical, 4.0)
                    position = 1.0 - min(line.center_y / page_height, 1.0)
                else:
                    prominence = 2.0
                    position = 3.0 * (1.0 - line.index / max(len(layout.lines), 1))
                words = len(text.split())
                title = (0.5 if words <= 6 else 0.0 if words <= 10 else -1.0) + (0.3 if sum(c.isalpha() for c in text) / len(text) >= 0.7 else 0.0)
                title -= (1.0 if ":" in text else 0.0) + (0.5 if "," in text else 0.0) + (0.3 if "(" in text else 0.0) + (0.3 if text.endswith(".") else 0.0)
                # The label itself says this names the product made by the maker.
                title += 1.0 if how == "before_maker_key" else 0.0
                scored.append((prominence + position + title, line, text, how))
        if not scored:
            return ExtractedValue()
        scored.sort(key=lambda item: -item[0])
        best_score, best, best_text, how = scored[0]
        rest = scored[1:]
        pattern = f"product_name:{how}"
        # A short brand mark ("Parle-G") printed large usually has a fuller
        # product line beginning with it ("Parle-G Gluco Biscuits"): prefer that.
        if len(best_text.split()) <= 3:
            brand = re.sub(r"[^0-9a-zऀ-ॿ]", "", best_text.casefold())
            extensions = [item for item in rest if (key := re.sub(r"[^0-9a-zऀ-ॿ]", "", item[2].casefold())).startswith(brand) and len(key) > len(brand)]
            if brand and extensions:
                _, best, best_text, _ = extensions[0]
                rest = [item for item in rest if item[1] is not best]
                pattern = "product_name:brand_extension"
        margin = best_score - rest[0][0] if rest else 2.0
        confidence = min(PRODUCT_NAME_MAX_CONFIDENCE, 0.5 + 0.15 * margin)
        return ExtractedValue(best_text, round(confidence, 2), margin < 1.0 or not layout.geometric, best.text, pattern, best_text, _boxes([best]))
