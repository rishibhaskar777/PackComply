"""Deterministic, evidence-preserving package-label extraction heuristics.

Values are only taken from text attached to an explicit label key ("Net Wt.",
"M.R.P.", "Marketed by", "LIC. No."), found on the key's own line or — with
OCR boxes — in the box to its right or the block directly below it.  A second,
value-first pass (``values.py``) then links values the key-first pass could not:
values on a tilted next row, in a key/value table, behind a letter code, or in
a coding area a key points to.  Keys are matched fuzzily (``fuzzy.py``).

Nothing is inferred: a key without a readable value yields no value, and
``declaration_status`` records per mandatory declaration whether it was read
with its value, only its key was found, or nothing was detected, so compliance
never treats an unread value as a missing declaration.  Confidence is a
heuristic for *how* the value was found, never a probability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import re
from statistics import mean, median
from typing import Any, Callable, Iterator

from backend.app.services.compliance.rule_engine import ComplianceEngine
from backend.app.services.extraction import fuzzy, values as value_first
from backend.app.services.extraction.fuzzy import phrases
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
    # Evidence for compliance, not label text: JSON per mandatory declaration and for the photo.
    "declaration_status", "ocr_coverage",
)
FIELD_NAMES = CORE_FIELD_NAMES + SUPPLEMENTARY_FIELD_NAMES

# Confidence by how a value was located: the key's own line, the box to its
# right, the block below it, or (text-only OCR) simply the next line.
CONFIDENCE = {"same_line": 0.95, "right_of_key": 0.9, "below_key": 0.88, "next_line": 0.82}
AMBIGUITY_PENALTY = 0.25
PRODUCT_NAME_MAX_CONFIDENCE = 0.8
# Value-first matches scoring below this are flagged for review (nearest-in-region, redirects, coding areas).
VALUE_FIRST_REVIEW_BELOW = 0.8
# Declaration states recorded in declaration_status (see rule_engine).
DECLARED_WITH_VALUE, KEY_FOUND_VALUE_UNREAD, NOT_DETECTED = "DECLARED_WITH_VALUE", "KEY_FOUND_VALUE_UNREAD", "NOT_DETECTED"
# A detection below this confidence counts as poorly read for ocr_coverage.
LOW_CONFIDENCE = 0.7


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
# Role words joined by ",", "&", "/", "and" or just a space ("Imported Marketed by").
_COMBINED_ROLE = re.compile(rf"(?i)^[\s*•\-–'\"]*((?:{_ROLE_WORD})(?:(?:\s*(?:,|&|/|and)\s*|\s+)(?:{_ROLE_WORD}))+)\s*(by|for)(?![A-Za-z])\s*[:\-.]?\s*")
# "(1) Imported by", "2. Marketed by": numbering before a role key.
_ROLE_NUMBERING = re.compile(r"^\s*(?:\(\s*[0-9]{1,2}\s*\)|[0-9]{1,2}[.)])\s*")
# Role keys as OCR misreads them ("Manutactured by:", "Markelad by"), matched fuzzily at a line start.
# Single-word role nouns ("Manufacturer") are left to the exact patterns: one edit
# turns "MANUFACTURED IN A FACILITY ..." into "Manufacturer".  A licensed user of
# a trademark is the party that makes the goods under it.
_ROLE_PHRASES = (
    *phrases("manufacturer", "manufactured by", "mfd by", "mfg by", "licensed user"),
    *phrases("brand_owner", "marketed by", "mktd by", "distributed by", "marketed and distributed by", "brand owner", "manufactured for"),
    *phrases("packer", "packed by", "pkd by"),
    *phrases("importer", "imported by", "imported in india by"),
)
# "For mfg. unit address read the code ...", "For manufacturer details ...": the manufacturer is declared through codes.
_MAKER_REFERENCE = re.compile(r"(?i)(?<![a-z])for\s+(?:mfg|manufacturing|manufacturer'?s?)\.?\s*(?:unit\s*)?(?:address|details)")
# Tax-inclusive statements, matched fuzzily ("(loclusive of all laxes)", "incl. of al taxes)").
_TAX_PHRASES = phrases("tax", "inclusive of all taxes", "incl of all taxes", "including all taxes", "inclusive of taxes", "of all taxes", "सभी करों सहित")
# The opening of the statement, cut off by the photo ("(Inct. of af"): present but unread.
_TAX_OPENING = re.compile(r"(?i)\(\s*in[a-z]{1,3}\.?\s*of\b")
# "02165/ml": a per-unit price that could not be parsed still shows the unit sale price is printed.
_PER_UNIT_SHAPE = re.compile(r"(?i)[0-9]\s*/\s*(?:ml|g|kg|l)(?![a-z])")
_CARE_PHRASES = phrases("care", "consumer care", "customer care", "consumer complaints", "consumer feedback", "consumer relations", "contact", "toll free", "helpline")
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
# Product category, strongest evidence first (see _product_category).
# Food for certain: a nutrition panel or FSSAI text (a 14-digit licence is checked separately).
_STRONG_FOOD_HINT = re.compile(r"(?i)(?<![A-Za-z])(?:nutrition(?:al)?|[fj]ssa[ilíì]?)(?![A-Za-z])|पोषण")
_DRUG_HINT = re.compile(
    r"(?i)(?<![A-Za-z])(?:tablets?|capsules?|syrups?|schedule\s*h1?|rx|antiseptic|ointment|for\s*dental\s*use|drug|medicine|m\.?\s*l\.?\s*no)(?![A-Za-z])"
)
# A manufacturing licence for cosmetics ("M.L. No. HIM/COS/06/94") is not a drug licence.
_COSMETIC_LICENCE = re.compile(r"(?i)/\s*cos\s*/")
_PERSONAL_CARE_HINT = re.compile(
    r"(?i)(?<![A-Za-z])(?:lotion|serum|sunscreen|shampoos?|moisturi[sz]er|soaps?|face\s*wash|hand\s*wash|body\s*wash|conditioner|toothpaste|"
    r"deodorants?|cosmetics?|tfm|for\s*external\s*use(?:\s*only)?|dermatologically)(?![A-Za-z])"
)
_HOUSEHOLD_HINT = re.compile(
    r"(?i)(?<![A-Za-z])(?:insecticides?|vapori[sz]er|mosquito(?:es)?|dish\s*wash|detergents?|cleaners?|disinfectants?|surfactants?|laundry)(?![A-Za-z])"
)
# Weak food evidence: a best-before date with an ingredients list of food words.
_INGREDIENTS = re.compile(r"(?i)(?<![A-Za-z])ingredients?(?![A-Za-z])|सामग्री")
_FOOD_WORDS = re.compile(r"(?i)(?<![A-Za-z])(?:wheat|flour|sugar|salt|milk|edible|spices?|rice|cocoa|maida|atta|starch|vegetable\s*oil|palm\s*oil)(?![A-Za-z])")
_ELECTRONICS_HINT = re.compile(r"(?i)(?<![A-Za-z])(?:phone|charger|cable|usb|earphones?|headphones?|adapter|tpu|device)(?![A-Za-z])")
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
# Headings and instructions that are never a product name, matched fuzzily ("Ingredlents").
_HEADING_PHRASES = phrases(
    "heading", "ingredients", "directions", "direction", "caution", "warning", "how to use", "promise", "made in", "storage",
    "nutrition", "contains", "price", "address", "composition", "information", "benefits", "dosage", "instructions", "usage",
    "recommendations", "precautions", "for sale", "refer", "registered", "registration", "trademark", "licence", "license", "copyright",
    "customer", "consumer", "feedback", "complaints", "manufactured", "marketed", "imported", "packed", "keep out",
)
# Generic commodity names: a line naming one is likely the product name ("Baby Shampoo", "Dishwash Liquid").
_GENERIC_NOUN = re.compile(
    r"(?i)(?:shampoo|soap|biscuits?|cookies?|lotion|serum|liquid|powder|noodles|oil|cream|cand(?:y|ies)|case|cover|refill|detergent|gel|"
    r"hand\s*wash|face\s*wash|body\s*wash|moisturi[sz]er|sunscreen|toothpaste|deodorant|vapori[sz]er|insecticide|mouthwash|cleaner|spray|"
    r"conditioner|balm|ointment|tablets?|capsules?|syrup|juice|tea|coffee|salt|sugar|atta|flour|rice|masala|sauce|ketchup|jam|chips|"
    r"namkeen|chocolates?|wafers?|bars?|drink|milk|curd|paneer|butter|cheese|bread|ghee|honey|pickle|paste|charger|cable)(?![a-z])|"
    r"शैम्पू|साबुन|बिस्कुट|कैंडी|तेल|नूडल्स|दूध"
)
# Marketing copy in another language (multilingual export packs): diacritics or function words.
_FOREIGN_TEXT = re.compile(r"[ßäöüñçøåışğ]|(?<![A-Za-z])(?:EN|FR|DE|ES|IT|JP|TR|PT|NL)(?![A-Za-z])")
_FOREIGN_WORDS = re.compile(r"(?i)(?<![a-z])(?:und|für|der|die|das|le|la|les|pour|avec|para|con|el|del|una|un|et|ou|y)(?![a-z])")
_NAME_MIN_CONFIDENCE = 0.7
_NAME_PROMINENT = 1.5
# Below this score margin over the runner-up the product name is left empty (and flagged) rather than guessed.
_NAME_MIN_MARGIN = 1.0
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
        matching = value_first.match(layout, self._claimed(layout, results))
        self._fill_from_values(results, matching)
        results["tax_inclusive"], tax_state = self._tax_inclusive(layout, results, matching)
        results["mrp_declaration"] = self._mrp_declaration(results, matching)
        results["product_category"] = self._product_category(layout, results["fssai_license"])
        results["product_name"] = self._product_name(layout, results)
        results["declaration_status"] = self._declaration_status(layout, results, matching, tax_state)
        results["ocr_coverage"] = self._ocr_coverage(layout, detections)
        logger.debug("Extraction layout geometric=%s lines=%d fields=%s", layout.geometric, len(layout.lines), {name: value.value for name, value in results.items() if value.value})
        return results

    # -------------------------------------------------------- value-first

    @staticmethod
    def _claimed(layout: Layout, results: dict[str, ExtractedValue]) -> set[tuple[int, str]]:
        """(line, normalized value) pairs the key-first pass already used, so no second field takes them."""
        by_text: dict[str, list[int]] = {}
        for line in layout.lines:
            by_text.setdefault(line.text, []).append(line.index)
        claimed = set()
        for name in value_first.KINDS:
            item = results.get(name)
            if item and item.value and item.source_text and item.normalized_value:
                claimed |= {(index, item.normalized_value) for text in item.source_text.split(" | ") for index in by_text.get(text, [])}
        return claimed

    @staticmethod
    def _fill_from_values(results: dict[str, ExtractedValue], matching: value_first.Matching) -> None:
        """Fields the key-first pass left empty take their value-first assignment."""
        for name, assignment in matching.assignments.items():
            if name not in results or results[name].value:
                continue
            lines = [assignment.key.span.line, assignment.value.span.line] if assignment.key else [assignment.value.span.line]
            confidence = round(min(0.95, 0.5 + 0.45 * assignment.score), 2)
            results[name] = ExtractedValue(assignment.value.value, confidence, assignment.score < VALUE_FIRST_REVIEW_BELOW, _source(lines),
                                           assignment.pattern, assignment.value.normalized, _boxes(lines))
        if not results["mrp"].value:
            price = value_first.consistent_price(matching.values, results["unit_sale_price"].normalized_value, results["net_quantity"].normalized_value)
            if price is not None:
                lines = [price.span.line]
                results["mrp"] = ExtractedValue(price.value, 0.7, True, _source(lines), "mrp:usp_consistency", price.normalized, _boxes(lines))

    @staticmethod
    def _tax_inclusive(layout: Layout, results: dict[str, ExtractedValue], matching: value_first.Matching) -> tuple[ExtractedValue, tuple[str, str | None]]:
        """The "inclusive of all taxes" statement and its declaration state.

        Partly read counts as present but unread: the statement's opening cut
        off by the photo ("(Incl. of al"), or a low-confidence fragment right
        beside the MRP key where the statement is printed.
        """
        if results["tax_inclusive"].value:
            return results["tax_inclusive"], (DECLARED_WITH_VALUE, results["tax_inclusive"].source_text)
        for line in layout.lines:
            if hit := fuzzy.contains(line.text, _TAX_PHRASES):
                return ExtractedValue("true", 0.85 if hit.edits == 0 else 0.75, hit.edits > 0, line.text, "tax_inclusive:fuzzy_statement", "true", _boxes([line])), (DECLARED_WITH_VALUE, line.text)
        for line in layout.lines:
            if _TAX_OPENING.search(line.text):
                return ExtractedValue(), (KEY_FOUND_VALUE_UNREAD, line.text)
        for key in matching.keys.get("mrp", []):
            beside = [layout.right_of(key.span.line), *layout.below(key.span.line, gap_factor=1.6)[:1]]
            for line in filter(None, beside):
                lowest = min((box.confidence for box in line.boxes if box.confidence is not None), default=1.0)
                if lowest < 0.8 and len(line.text.split()) <= 3 and not re.search(r"[0-9]", line.text):
                    return ExtractedValue(), (KEY_FOUND_VALUE_UNREAD, f"{key.span.line.text} | {line.text}")
        if results["mrp"].matched_pattern == "mrp:usp_consistency":
            # The MRP was read without its key: the statement beside the key is in an unread area.
            return ExtractedValue(), (KEY_FOUND_VALUE_UNREAD, results["mrp"].source_text)
        return ExtractedValue(), (NOT_DETECTED, None)

    @staticmethod
    def _mrp_declaration(results: dict[str, ExtractedValue], matching: value_first.Matching) -> ExtractedValue:
        mrp, current = results["mrp"], results["mrp_declaration"]
        if mrp.value:
            return ExtractedValue("present_with_value", mrp.confidence, False, mrp.source_text, "mrp_declaration:key_with_value", "present_with_value", mrp.source_bboxes)
        if current.value != "present_without_value" and (keys := matching.keys.get("mrp")):
            line = keys[0].span.line
            return ExtractedValue("present_without_value", 0.8, True, line.text, "mrp_declaration:fuzzy_key_without_value", "present_without_value", _boxes([line]))
        return current

    def _declaration_status(self, layout: Layout, results: dict[str, ExtractedValue], matching: value_first.Matching, tax: tuple[str, str | None]) -> ExtractedValue:
        """Per mandatory declaration: DECLARED_WITH_VALUE, KEY_FOUND_VALUE_UNREAD (a key or a redirect
        such as "see below" is printed but no value was linked) or NOT_DETECTED (no key at all)."""
        other = matching.other_details.line.text if matching.other_details else None

        def entry(declared: ExtractedValue | None, keys: list[str | None], redirectable: bool = True) -> dict[str, str | None]:
            if declared is not None and declared.value:
                return {"state": DECLARED_WITH_VALUE, "evidence": declared.source_text}
            evidence = next((key for key in keys if key), None) or (other if redirectable else None)
            return {"state": KEY_FOUND_VALUE_UNREAD, "evidence": evidence} if evidence else {"state": NOT_DETECTED, "evidence": None}

        key_text = lambda field: [key.span.line.text for key in matching.keys.get(field, [])]
        party = next((results[role] for role in ("manufacturer", "brand_owner", "packer", "importer") if results[role].value), None)
        role_keys = [line.text for line in layout.lines if _ANY_ROLE.search(line.text) or _COMBINED_ROLE.match(line.text) or _MAKER_REFERENCE.search(line.text)
                     or any(not re.search(r"[A-Za-z0-9]", line.text[:hit.start]) for hit in fuzzy.find(line.text, _ROLE_PHRASES))]
        dates = results["manufacturing_date"] if results["manufacturing_date"].value else results["packing_date"]
        # A printed price without its MRP key, or a per-unit price that could not be parsed, shows the declaration is there.
        prices = [value.span.line.text for value in matching.values if value.kind == "price" and not value.offer]
        usp_shaped = [line.text for line in layout.lines if _PER_UNIT_SHAPE.search(line.text)]
        care = next((results[name] for name in ("consumer_care_phone", "consumer_care_email", "consumer_care_website") if results[name].value), None)
        care_key = results["consumer_care_declaration"].source_text if results["consumer_care_declaration"].value == "present_without_details" else None
        status = {
            "manufacturer": entry(party, role_keys, redirectable=False),
            "net_quantity": entry(results["net_quantity"], key_text("net_quantity")),
            "mrp": entry(results["mrp"], key_text("mrp") + prices),
            "manufacturing_date": entry(dates, key_text("manufacturing_date") + key_text("packing_date")),
            "unit_sale_price": entry(results["unit_sale_price"], key_text("unit_sale_price") + usp_shaped),
            "tax_inclusive": {"state": tax[0], "evidence": tax[1]},
            "consumer_care": entry(care, [care_key], redirectable=False),
        }
        serialized = json.dumps(status, ensure_ascii=False, sort_keys=True)
        return ExtractedValue(serialized, 0.9, False, None, "declaration_status:computed", serialized)

    @staticmethod
    def _ocr_coverage(layout: Layout, detections: list[Any] | None) -> ExtractedValue:
        """How much of the photo OCR read, so compliance can tell "not printed" from "not in view"."""
        confidences = [
            float(confidence) for item in detections or []
            if (confidence := (item.get("confidence") if isinstance(item, dict) else getattr(item, "confidence", None))) is not None
        ]
        lines = [line for line in layout.lines if is_substantial(line.text)]
        low = [line for line in lines if min((box.confidence for box in line.boxes if box.confidence is not None), default=1.0) < LOW_CONFIDENCE]
        coverage = {
            "text_lines": len(lines),
            "mean_confidence": round(mean(confidences), 3) if confidences else None,
            "low_confidence_line_share": round(len(low) / len(lines), 3) if lines and layout.geometric else None,
        }
        serialized = json.dumps(coverage, sort_keys=True)
        return ExtractedValue(serialized, 0.9, False, None, "ocr_coverage:computed", serialized)

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
        return value_first.normalize_date(value)

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
    def _product_category(cls, layout: Layout, licence: ExtractedValue) -> ExtractedValue:
        """food / drug / personal_care / household / other, strongest evidence first.

        Food is certain with a nutrition panel, FSSAI text or a 14-digit licence;
        then medicine words (tablet, syrup, Schedule H, a drug manufacturing
        licence); then personal-care and household words, which override the weak
        food guess of an ingredients list with food words beside a best-before
        date; electronics accessories are "other".  No evidence: no category.
        """
        if licence.value and len(licence.value) == 14:
            return ExtractedValue("food", 0.85, False, licence.source_text, "product_category:fssai_licence", "food", licence.source_bboxes)
        checks = (
            (_STRONG_FOOD_HINT, "food", "food_keywords"),
            (_DRUG_HINT, "drug", "drug_keywords"),
            (_PERSONAL_CARE_HINT, "personal_care", "personal_care_keywords"),
            (_HOUSEHOLD_HINT, "household", "household_keywords"),
        )
        for pattern, category, how in checks:
            line = next((line for line in layout.lines if pattern.search(line.text)), None)
            if line is None or (category == "drug" and _COSMETIC_LICENCE.search(line.text)):
                continue
            return ExtractedValue(category, 0.8, False, line.text, f"product_category:{how}", category, _boxes([line]))
        text = " ".join(line.text for line in layout.lines)
        ingredients = next((line for line in layout.lines if _INGREDIENTS.search(line.text)), None)
        if ingredients and _FOOD_WORDS.search(text) and re.search(r"(?i)best\s*before|use\s*by|expiry", text):
            return ExtractedValue("food", 0.7, True, ingredients.text, "product_category:ingredients_with_food_words", "food", _boxes([ingredients]))
        if line := next((line for line in layout.lines if _ELECTRONICS_HINT.search(line.text)), None):
            return ExtractedValue("other", 0.7, False, line.text, "product_category:electronics_keywords", "other", _boxes([line]))
        return ExtractedValue()

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
    def _company_text(text: str) -> str:
        """A company line as printed, minus what OCR and footnotes add.

        Drops footnote marks and an "M/s" prefix ("#Godrej ...", "M/S. Acme
        Ltd") and splits a legal suffix glued to the name ("UNILEVERLTD").
        """
        text = re.sub(r"^[\s#*^@$¥§]+", "", _clean_value(text))
        text = re.sub(r"(?i)^m\s*/\s*s\.?\s*", "", text)
        return re.sub(r"(?<=[A-Z]{3})(LTD|LIMITED)(?=[\s.,(]|$)", r" \1", text)

    @staticmethod
    def _split_company(text: str) -> tuple[str, str]:
        """Company name and the address text that follows it on the same line."""
        text = ExtractionService._company_text(text)
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
        _, text = self._strip_unit_code(self._company_text(text))
        if not text or _ANY_ROLE.match(text) or _COMBINED_ROLE.match(text) or _STOP_LINE.search(text) or _LICENCE.search(text) or self._key_continuation(text):
            return False
        if re.search(r"[A-Za-z]{16,}", text) and not _COMPANY_NAME.match(text):
            return False  # words glued together by OCR
        has_suffix = bool(_COMPANY_NAME.match(text))
        if not has_suffix and (address := _ADDRESS_START.search(text)) and address.start() > 0:
            text = text[:address.start()]  # "Gram Udyog 12/40 KM Stone, ..., 125001": judge the name part
        if (text[0].isdigit() or _PIN.search(text)) and not has_suffix:
            return False  # an address line, not a company
        words = [word for word in re.findall(r"[A-Za-zऀ-ॿ]{2,}", text) if word.lower() not in {"for", "by", "ltd", "limited", "pvt", "the", "and", "of"}]
        return len(words) >= 2 or (bool(words) and has_suffix)

    @staticmethod
    def _key_continuation(text: str) -> bool:
        """The rest of a multi-line key ("Registered Address / Consumer Feedback:"), not a company."""
        if _COMPANY_NAME.match(text):
            return False
        # Headings and contact-person descriptors ("Manager (Consumer Relations)") continue a key.
        return text.rstrip().endswith(":") or bool(fuzzy.find(text, _CARE_PHRASES)) or bool(re.search(
            r"(?i)(?<![a-z])(?:registered|address|feedback|manager|officer|executive|department|desk|relations)(?![a-z])", text))

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

    def _party(self, layout: Layout, key_line: LayoutLine, rest: str, inline: bool = False, require_suffix: bool = False) -> _Party | None:
        """The party named after a role key; ``inline`` keys (after other text) and ``require_suffix`` need a company suffix."""
        rest = _clean_value(rest)
        if rest and not inline and not _UNIT_CODE.match(rest) and (paragraph := self._paragraph_units(layout, key_line, rest)):
            return paragraph
        following = layout.next_in_column(key_line)
        wrapped_start = self._wrapped_name(rest, following, max_words=5) if rest and not self._has_suffix(rest) else None
        if wrapped_start:
            # "MKTD. BY: HINDUSTAN" / "UNILEVER LTD., MUMBAI": the name starts on the key line.
            value_line, text, method = following, wrapped_start, "same_line"
        elif rest and self._is_company(rest):
            value_line, text, method = key_line, rest, "same_line"
        else:
            for value_line, method in self._neighbours(layout, key_line):
                if self._is_company(value_line.text):
                    text = value_line.text
                    break
            else:
                if (found := self._company_below_key_lines(layout, key_line)) is None:
                    return None
                value_line, text, method = *found, "below_key"
        evidence = [key_line] if value_line is key_line else [key_line, value_line]
        units = self._units(layout, value_line, text) if _UNIT_CODE.match(text) else []
        if len(units) > 1:
            first = units[0]
            return _Party(first["name"], method, evidence, first["address"], first["address_lines"], units)
        _, text = self._strip_unit_code(text)
        name, remainder = self._split_company(text)
        address_start, address_lines = value_line, [value_line] if remainder else []
        if not remainder and not _COMPANY_NAME.match(name) and (following := layout.next_in_column(value_line)) is not None:
            # A long name wrapped onto the next line: "... Home Products Pvt" / "Ltd, Plot No. 1, ..."
            # or "Acme Consumer Products" / "Limited, Plot 4, ...".
            if joined := self._wrapped_name(name, following):
                name, remainder = self._split_company(joined)
                evidence, address_start, address_lines = evidence + [following], following, [following] if remainder else []
        if (inline or require_suffix) and not _COMPANY_NAME.match(name):
            return None
        # An unreadable line ("aksarRoadHnrar-4dnatarakhand") is not a name unless it has a legal suffix.
        if not _COMPANY_NAME.match(name) and min((box.confidence for box in value_line.boxes if box.confidence is not None), default=1.0) < LOW_CONFIDENCE:
            return None
        address, more_lines = self._address(layout, address_start, remainder)
        return _Party(name, method, evidence, address, address_lines + more_lines)

    def _has_suffix(self, text: str) -> bool:
        return bool(_COMPANY_NAME.match(self._company_text(text)))

    def _wrapped_name(self, start: str, following: LayoutLine | None, max_words: int = 8) -> str | None:
        """``start`` plus the next line when that line completes a company name ("... Ltd., address")."""
        start = self._company_text(start)
        if following is None or not start or len(start.split()) > max_words or re.search(r"\d", start) or self._key_continuation(start) or self._is_stop(start):
            return None
        text = self._company_text(following.text)
        if suffix := re.match(r"(?i)^((?:pvt\.?\s*|private\s+)?(?:ltd|limited)\.?)(?![A-Za-z])", text):
            return f"{start} {suffix.group(1)}, {text[suffix.end():].lstrip(' ,')}".rstrip(", ")
        wrapped = _COMPANY_NAME.match(text)
        if wrapped and len(wrapped.group(1).split()) <= 5 and not re.search(r"\d", wrapped.group(1)):
            return f"{start} {text}"
        return None

    def _company_below_key_lines(self, layout: Layout, key_line: LayoutLine) -> tuple[LayoutLine, str] | None:
        """A company under a multi-line key ("Marketed by / Registered Address / Consumer Feedback:" / "Acme Ltd").

        Only lines that continue the key are skipped, and the company must carry a
        legal suffix (on its line or wrapped onto the next one).
        """
        current: LayoutLine | None = key_line
        for _ in range(4):
            current = layout.next_in_column(current) if current else None
            if current is None or self._is_stop(current.text):
                return None
            if self._key_continuation(current.text):
                continue
            if self._has_suffix(current.text) and self._is_company(current.text):
                return current, current.text
            following = layout.next_in_column(current)
            if joined := self._wrapped_name(current.text, following):
                return following, joined
            return None
        return None

    def _role_mentions(self, layout: Layout) -> dict[str, list[tuple[int, _Party]]]:
        """Every (line index, party) per role: single-role keys, combined keys and inline keys."""
        mentions: dict[str, list[tuple[int, _Party]]] = {role: [] for role in _ROLE_LINE}
        for line in layout.lines:
            body = line.text
            if (numbering := _ROLE_NUMBERING.match(body)) and (_COMBINED_ROLE.match(body[numbering.end():]) or _ANY_ROLE.match(body[numbering.end():])
                                                             or fuzzy.find(body[numbering.end():numbering.end() + 40], _ROLE_PHRASES)):
                body = body[numbering.end():]
            if combined := _COMBINED_ROLE.match(body):
                words = re.split(r"(?i)\s*(?:,|&|/|\band\b)\s*|\s+", combined.group(1))
                roles = {"brand_owner"} if combined.group(2).lower() == "for" else {
                    role for word in words for pattern, role in _ROLE_WORDS.items() if re.fullmatch(rf"(?i){pattern}", word.strip())
                }
                if party := self._party(layout, line, body[combined.end():]):
                    for role in roles:
                        mentions[role].append((line.index, party))
                continue
            matched = False
            for role, key_re in _ROLE_LINE.items():
                if (match := key_re.match(body)) and (party := self._party(layout, line, body[match.end():])):
                    mentions[role].append((line.index, party))
                    matched = True
            if matched or _ANY_ROLE.match(body):
                continue
            # A role key misread by OCR ("Manutactured by:", "Markelad by") at the start of the line.
            # Its value must carry a legal suffix: a misread key sits in a poorly read area.
            hit = next((hit for hit in fuzzy.find(body, _ROLE_PHRASES) if not re.search(r"[A-Za-z0-9]", body[:hit.start])), None)
            if hit is not None:
                if party := self._party(layout, line, body[hit.end:], require_suffix=hit.edits > 0):
                    mentions[hit.name].append((line.index, party))
                continue
            # "For mfg. unit address read the code ...": coded units, or a named company with a legal suffix.
            if reference := _MAKER_REFERENCE.search(body):
                if party := self._party(layout, line, body[reference.end():], require_suffix=True):
                    mentions["manufacturer"].append((line.index, party))
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
    def _repaired_email(token: str) -> str | None:
        """An e-mail whose "@" OCR read as "ld", "O", "0" or "Q" ("care0example.com"), when exactly one reading is plausible.

        The result needs a local part of 2+ characters and a domain whose first
        label has 3+ letters and that ends in a known top-level domain.
        """
        token = token.strip(".,;:()[]")
        if "@" in token or token.lower().startswith(("www.", "http")) or not re.fullmatch(r"[\w.+-]+\.[A-Za-z]{2,}", token):
            return None
        readings = set()
        for misread in ("ld", "O", "0", "Q"):
            for match in re.finditer(re.escape(misread), token):
                local, domain = token[:match.start()], token[match.end():]
                labels = domain.lower().split(".")
                if len(local) >= 2 and len(labels) >= 2 and re.fullmatch(r"[a-z][a-z0-9-]{2,}", labels[0]) and any(domain.lower().endswith("." + tld) for tld in _TLDS):
                    readings.add(f"{local}@{domain}")
        return readings.pop() if len(readings) == 1 else None

    @classmethod
    def _email(cls, text: str, context: bool) -> str | None:
        """An e-mail on the line; a misread "@" is restored only in a contact context (care block or e-mail key)."""
        if match := _EMAIL.search(text):
            return match.group(0)
        if context or re.search(r"(?i)e-?\s*mail|(?<![A-Za-z])e\s*:", text):
            for token in text.split():
                if repaired := cls._repaired_email(token):
                    return repaired
        return None

    @staticmethod
    def _phone_text(text: str) -> str:
        """Phone-shaped OCR repairs: "(022)62487999" -> "022 62487999"; "C800"/"I800" -> "1800"."""
        text = re.sub(r"\((0[0-9]{2,4})\)\s*", r"\1 ", text)
        return re.sub(r"(?<![0-9A-Za-z])[CIl|](?=8[0O]{2}[\s\-]?[0-9])", "1", text)

    @classmethod
    def _website(cls, text: str) -> str | None:
        for match in _WEB_TOKEN.finditer(text):
            token = match.group(0).rstrip(".")
            if text[match.end():match.end() + 1] == "@" or cls._repaired_email(token):
                continue  # never report a misread e-mail as a website
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
            if _CARE_KEY.search(line.text) or fuzzy.find(line.text, _CARE_PHRASES):
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
            text = self._phone_text(text)
            for pattern in _PHONE:
                if match := pattern.search(text):
                    return re.sub(r"\s+", " ", match.group(0)).strip()
            return None

        if not in_block("consumer_care_phone", phone, lambda value: re.sub(r"[^\d+]", "", value)):
            # A toll-free 1800 / 1860 number is a consumer-care line wherever it
            # is printed, unless its line belongs to another key ("Batch 1800 ...").
            for line in layout.lines:
                if not self._is_stop(line.text) and (match := _PHONE[0].search(self._phone_text(line.text))):
                    value = re.sub(r"\s+", " ", match.group(0)).strip()
                    result["consumer_care_phone"] = ExtractedValue(value, 0.8, False, line.text, "consumer_care_phone:toll_free_anywhere", re.sub(r"[^\d+]", "", value), _boxes([line]))
                    break
        for name, find, normalize in (("consumer_care_email", lambda text: self._email(text, True), str.lower), ("consumer_care_website", self._website, str.lower)):
            if in_block(name, find, normalize):
                continue
            # Outside a care block only the unambiguous structure counts.
            for line in layout.lines:
                if value := (self._email(line.text, False) if name == "consumer_care_email" else find(line.text)):
                    result[name] = ExtractedValue(value, 0.8, False, line.text, f"{name}:anywhere", normalize(value), _boxes([line]))
                    break
        if not result["consumer_care_phone"].value:
            # A contact cluster without a heading: a phone number printed with (or next to) the e-mail or website.
            contact = {line.index for line in layout.lines if self._email(line.text, False) or self._website(line.text)}
            for line in layout.lines:
                near = line.index in contact or (line.index - 1) in contact or (line.index + 1) in contact
                if near and not self._is_stop(line.text) and not _LICENCE.search(line.text) and (value := phone(line.text)):
                    result["consumer_care_phone"] = ExtractedValue(value, 0.75, True, line.text, "consumer_care_phone:contact_cluster", re.sub(r"[^\d+]", "", value), _boxes([line]))
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
        Lemon & Mint Mfg by ..."), a first sentence followed by boilerplate
        ("Powder Laundry Detergent. CAUTION: ...") and the first segment of a
        catalogue title ("Brand Name | Model | Colour").
        """
        inline = _INLINE_ROLE.search(line.text)
        if inline and inline.start() > 0 and not _ANY_ROLE.match(line.text):
            prefix = _clean_value(line.text[:inline.start()])
            if 1 <= len(prefix.split()) <= 6 and not re.search(r"\d", prefix):
                yield prefix, "before_maker_key"
            return
        first, separator, after = line.text.partition(". ")
        if separator and len(first.split()) >= 2 and (_BOILERPLATE.search(after) or fuzzy.find(after, _HEADING_PHRASES) or _GENERIC_NOUN.search(first)):
            yield _clean_value(first), "first_sentence"
            return
        segment, bar, _ = line.text.partition(" | ")
        if bar and len(segment.split()) >= 2:
            yield _clean_value(segment), "title_segment"
            return
        yield line.text, "prominent_line"

    @staticmethod
    def _title_stacks(layout: Layout) -> Iterator[tuple[list[LayoutLine], str]]:
        """A title printed large over two or three lines ("HAND" / "WASH").

        Lines of similar large type stacked directly under each other join,
        unless an upper line already names a commodity on its own ("VIM GEL" /
        "VIM DISHWASH LIQUID" are two names, not one).
        """
        if not layout.geometric:
            return
        large = 1.15 * layout.median_height
        for line in layout.lines:
            stack = [line]
            while len(stack) < 3:
                current = stack[-1]
                if current.box_height < large or _GENERIC_NOUN.search(current.text):
                    break
                below = next((other for other in layout.below(current, gap_factor=0.6) if not other.vertical), None)
                if below is None or not 0.75 <= below.box_height / current.box_height <= 1.33:
                    break
                narrow = min(current.extent[2] - current.extent[0], below.extent[2] - below.extent[0])
                if min(current.extent[2], below.extent[2]) - max(current.extent[0], below.extent[0]) < 0.5 * narrow:
                    break
                stack.append(below)
            text = _clean_value(" ".join(item.text for item in stack))
            if len(stack) > 1 and len(text.split()) <= 8:
                yield stack, text

    @staticmethod
    def _not_a_name(text: str, line: LayoutLine) -> bool:
        """Headings, instructions, codes, addresses, company names, foreign-language copy, unreadable text."""
        alnum = re.findall(r"[0-9A-Za-zऀ-ॿ]", text)
        letters = re.findall(r"[A-Za-zऀ-ॿ]+", text)
        if len(alnum) < 3 or len(text) > 80 or not any(len(word) >= 3 for word in letters) or (len(text.split()) == 1 and len(letters[0]) < 4):
            return True
        if sum(char.isdigit() for char in alnum) / len(alnum) > 0.4 or any(len(word) >= 16 for word in letters):
            return True  # numbers, or words glued together by OCR
        if min((box.confidence for box in line.boxes if box.confidence is not None), default=1.0) < _NAME_MIN_CONFIDENCE:
            return True
        if _BOILERPLATE.search(text) or fuzzy.find(text, _HEADING_PHRASES) or fuzzy.find(text, value_first.REDIRECT_PHRASES):
            return True
        if _FSSAI_LOGO.search(text) or _UNIT_CODE.match(text) or _PIN.search(text) or _COMPANY_NAME.match(text):
            return True
        if re.search(r"(?i)(?<![a-z])[0-9]*\s*packs?(?![a-z])|(?<![a-z])(?:free|offer|combo)(?![a-z])", text) and not _GENERIC_NOUN.search(text):
            return True  # "5PACKS", "BUY 2 GET 1 FREE": a pack badge, not a name
        if re.match(r"(?i)\W*(?:vs|and|or|of|with|for|to|in|on|the|a|an|is|are)\b|\W*[0-9]+\s*(?:g|ml|kg|l)(?:\b|nett?)", text) or re.match(r"\W*[a-z]", text):
            return True  # the middle of a sentence ("vs. soap bar ...", "ne Smooth ..." cut off) or a quantity ("550mlnet")
        if text.count(",") >= 2 or fuzzy.find(text, value_first.KEY_PHRASES):
            return True  # a list (ingredients, an address) or a declaration key ("Exp. Date ...")
        if text.count("(") != text.count(")") or text.count("[") != text.count("]"):
            return True  # a fragment of longer text ("BUTTER) FLAVOURING SUBSTANCES, ...")
        return bool(_FOREIGN_TEXT.search(text) or len(_FOREIGN_WORDS.findall(text)) >= 2)

    def _product_name(self, layout: Layout, results: dict[str, ExtractedValue]) -> ExtractedValue:
        """The most prominent name-like text; empty (flagged for review) when no candidate clearly stands out.

        Precision matters more than recall here: a wrong name is reported as a
        fact, an empty one only asks the inspector to look.
        """
        # Lines holding other fields' values are not the name; the category's keyword line may well be.
        used = {value.source_text for name, value in results.items() if value.value and value.source_text and name != "product_category"}
        used_lines = {text for source in used for text in source.split(" | ")}
        horizontal = [line.box_height for line in layout.lines if not line.vertical and line.extent]
        page_height = layout.page_height
        typical = median(horizontal) if horizontal else layout.median_height
        tallest = max(horizontal, default=0.0)
        candidates: list[tuple[list[LayoutLine], str, str]] = []
        for line in layout.lines:
            if line.vertical:
                continue  # rotated side text ("scan QR code") is never the product name
            for text, how in self._name_candidates(line):
                if not (how == "prominent_line" and text in used_lines) and not self._not_a_name(text, line):
                    candidates.append(([line], text, how))
        for stack, text in self._title_stacks(layout):
            if not any(item.text in used_lines for item in stack) and not any(self._not_a_name(item.text, item) for item in stack):
                candidates.append((stack, text, "stacked_title"))
        scored: list[tuple[float, list[LayoutLine], str, str]] = []
        smaller: list[tuple[float, list[LayoutLine], str, str]] = []  # still a brand mark's fuller name
        for lines, text, how in candidates:
            height = max(line.box_height for line in lines)
            # Without a commodity word only large type ("BRAND", the label's tallest text) or the maker's own phrasing counts as a name.
            if layout.geometric and not _GENERIC_NOUN.search(text) and how != "before_maker_key" and height < min(_NAME_PROMINENT * typical, tallest):
                smaller.append((0.0, lines, text, how))
                continue
            if layout.geometric:
                prominence = 2.0 * min(height / typical, 12.0)
                position = 1.0 - min(lines[0].center_y / page_height, 1.0)
            else:
                prominence = 2.0
                position = 3.0 * (1.0 - lines[0].index / max(len(layout.lines), 1))
            words = len(text.split())
            title = (0.5 if words <= 6 else 0.0 if words <= 10 else -1.0) + (0.3 if sum(c.isalpha() for c in text) / len(text) >= 0.7 else 0.0)
            title -= (1.0 if ":" in text else 0.0) + (0.5 if "," in text else 0.0) + (0.3 if "(" in text else 0.0) + (0.3 if text.endswith(".") else 0.0)
            # The label itself says this names the product made by the maker.
            title += 1.0 if how == "before_maker_key" else 0.0
            title += 1.5 if _GENERIC_NOUN.search(text) else 0.0
            scored.append((prominence + position + title, lines, text, how))
        if not scored:
            return ExtractedValue()
        scored.sort(key=lambda item: -item[0])
        best_score, best, best_text, how = scored[0]
        plain = lambda text: re.sub(r"[^0-9a-zऀ-ॿ]", "", text.casefold())
        pattern = f"product_name:{how}"
        # A short brand mark ("Parle-G") printed large usually has a fuller
        # product line beginning with it ("Parle-G Gluco Biscuits"): prefer that.
        if len(best_text.split()) <= 3:
            brand = plain(best_text)
            extensions = [item for item in scored[1:] + smaller if (key := plain(item[2])).startswith(brand) and len(key) > len(brand)]
            if brand and extensions:
                _, best, best_text, _ = extensions[0]
                pattern = "product_name:brand_extension"
        # Competitors are other text: not a part of the winner (a stack and its own lines), nor the same name again.
        rest = [item for item in scored if item[1] is not best and not set(map(id, item[1])) & set(map(id, best))
                and plain(item[2]) not in plain(best_text) and plain(best_text) not in plain(item[2])]
        margin = best_score - rest[0][0] if rest else 2.0
        # Text-only OCR has no type sizes, so its margin reflects line order alone: keep the guess, flagged.
        if margin < _NAME_MIN_MARGIN and pattern != "product_name:brand_extension" and layout.geometric:
            return ExtractedValue(None, 0.0, True, _source(best), "product_name:ambiguous", None, None)
        confidence = min(PRODUCT_NAME_MAX_CONFIDENCE, 0.5 + 0.15 * margin)
        return ExtractedValue(best_text, round(confidence, 2), margin < _NAME_MIN_MARGIN or not layout.geometric, _source(best), pattern, best_text, _boxes(best))
