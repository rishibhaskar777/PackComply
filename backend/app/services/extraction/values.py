"""Value-first linking of label declarations.

Key-first extraction looks for a value beside each key.  On phone photos the
value is often elsewhere: on the next row of a curved bottle, in a table
column, in a stamped "coding area" the key points to ("MRP, Mfd., Use before:
see below"), or behind a letter code ("M 12/24").  This pass works the other
way round:

1. find every candidate VALUE on the label with its position (prices, unit
   sale prices, net quantities, dates);
2. find every KEY, fuzzily ("VIRP", "Net Contemt"), with any code it defines
   ("Mfg. Date ($)", "MFD (M)") and any redirect that applies to it ("see
   below", "see coding area");
3. score every compatible key/value pair by position and assign greedily:
   each value goes to at most one field and each field gets one value.

Every assignment records how it was made, e.g. ``mrp:redirect_coding_area``
or ``manufacturing_date:letter_code_M``.  Nothing is inferred: a key whose
value cannot be found stays unassigned, and the caller reports it as a
declaration whose value could not be read.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import math
import re
from typing import Iterable

from backend.app.services.extraction import fuzzy
from backend.app.services.extraction.fuzzy import Phrase, phrases
from backend.app.services.extraction.layout import Layout, LayoutLine, is_substantial

Extent = tuple[float, float, float, float]

# ---------------------------------------------------------------- vocabulary
# Digits are [0-9] throughout: Python's \d also matches Devanagari digits, and
# "२" is a common misreading of "₹".

_MONTHS = {name: index for index, name in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_STRONG_CURRENCY = r"₹|rs\.?|r≤\.?|inr|रु\.?|रू\.?"
# Shapes OCR gives "₹": accepted only with a price-shaped amount (two decimals or "/-").
_WEAK_CURRENCY = r"र|२|\?|T|z|Z"
_AMOUNT = r"([0-9]{1,3}(?:,[0-9]{2,3})+(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)"
_STRONG_PRICE = re.compile(rf"(?i)(?<![A-Za-z])(?:{_STRONG_CURRENCY})\s*[:.]?\s*(?:([0-9]+) ([0-9]{{2}})(?![0-9.])|{_AMOUNT}(?![0-9%]))(\s*/-)?")
_WEAK_PRICE = re.compile(rf"(?<![A-Za-z0-9])(?:{_WEAK_CURRENCY})\s?(?:([0-9]+\.[0-9]{{2}})(?![0-9])|([0-9]+)(?=\s*/-)|([0-9]+) ([0-9]{{2}})(?![0-9.]))")
# A price in a sentence ("also available with bottle @ ₹40/-", "vouchers worth ₹2000") is an offer, not the MRP.
_OFFER = re.compile(r"(?i)(?:@|\bat\b|\bonly\b|\bworth\b|\bsave\b|\boffers?\b|\bfree\b|\bavailable\b|\bup\s*to\b|\bupto\b)\W*$")
_LEADING_AMOUNT = re.compile(r"^\W{0,2}([0-9]{1,5}(?:\.[0-9]{2})?)(?![0-9%/A-Za-z]|\.[0-9])")
_AFTER_KEY_AMOUNT = re.compile(rf"^[\s:.\-=(\[]*(?:{_WEAK_CURRENCY})?\s?([0-9]{{1,5}}(?:\.[0-9]{{1,2}})?)(?![0-9%/])")
_USP_UNIT = r"kg|gms?|grams?|g|ml|millilit(?:re|er)s?|ltr|lit(?:re|er)s?|l|units?|pcs?|pieces?|nos?|n|m[nk1i]|9"
# A unit price is recognised by its "/unit" shape, so OCR's wilder readings of "₹" ("R0.48/ml", "%0.48/ml") are safe here.
_USP_CURRENCY = rf"{_STRONG_CURRENCY}|{_WEAK_CURRENCY}|R|%"
_USP = re.compile(rf"(?i)(?:(?:{_USP_CURRENCY})\s*)?(?<![0-9])(?<![0-9]\.)([0-9]+(?:[.,][0-9]{{1,2}})?)\s*(?:per|/)\s*([0-9]+\s*)?({_USP_UNIT})(?![A-Za-z0-9])")
_QUANTITY_UNIT = r"kgs?|g|gms?|grams?|ml|m\s?l|l|ltrs?|litres?|liters?|cl|cc|n|nos?|pcs?|pieces?|units?"
_HINDI_UNITS = {r"किलोग्राम|किग्रा": "kg", r"ग्राम|ग्रा\.?": "g", r"मिलीलीटर|मि\.\s*ली\.?|मिली": "ml", r"लीटर": "l"}
_QUANTITY = re.compile(rf"(?i)(?<![0-9A-Za-z.,])([0-9]+(?:[.,][0-9]+)?)\s*({_QUANTITY_UNIT}|{'|'.join(_HINDI_UNITS)})(?:(?![A-Za-z0-9])|(?=nett?(?![A-Za-z])))")
# Lines whose quantities describe nutrition, dosage, dilution or composition, never the net quantity.
_NOT_NET = re.compile(
    r"(?i)(?<![a-z])(?:energy|protein|carbohydrates?|sugars?|fats?|fibre|fiber|sodium|cholesterol|kcal|serving|serve|rda|nutrition(?:al)?|"
    r"w/v|w/w|v/v|tablespoons?|teaspoons?|dilute|equivalent|each|squeeze|composition|ingredients?|contains)(?![a-z])"
)
_DATE_NUMERIC = re.compile(r"([0-9]{1,2})\s*([/.\-])\s*([0-9]{1,2})\s*\2\s*([0-9]{4}|[0-9]{2})(?![0-9])|([0-9]{1,2})\s*[/.\-]\s*([0-9]{4}|[0-9]{2})(?![0-9])")
_DATE_NAMED = re.compile(r"(?i)(?<![A-Za-z])(?:([0-9]{1,2})\s*[\-/ ]\s*)?(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s*[\-/'. ]?\s*([0-9]{4}|[0-9]{2})(?![0-9])")
_SHELF_LIFE = re.compile(r"(?i)(?<![0-9])([0-9]{1,2})\s*months?")
_CODE_SYMBOLS = "#$¥@*^&%~+§"
_KEY_CODE = re.compile(rf"^\s*[:.]?\s*[(\[]\s*([A-Z]{{1,2}}|[{re.escape(_CODE_SYMBOLS)}]{{1,2}})\s*[)\]]|^([{re.escape(_CODE_SYMBOLS)}]{{1,2}})")
_PREFIX_CODE = re.compile(rf"([{re.escape(_CODE_SYMBOLS)}]{{1,2}})\s*$")
_VALUE_CODE = re.compile(rf"(?:(?<=\s)|^)([A-Z]|[{re.escape(_CODE_SYMBOLS)}]{{1,2}})\s*[:\-]?\s*$")
# Conventional coding-area letters, used when the label does not define its own.
CONVENTIONAL_CODES = {"M": "manufacturing_date", "U": "best_before", "E": "expiry_date", "P": "packing_date"}
# "Mfd. by", "Mfd. & Mktd. by", "Mfg. Lic. No.", "For mfg. unit address" introduce something other than a date.
_NOT_A_DATE_KEY = re.compile(r"(?i)^\W*(?:by|for|lic|licen[cs]e|unit|address|details)\b|^\W*(?:&|and|/|,)\s*[a-z]{2,12}\.?\s*(?:by|for)\b")
# Code letters a coding stamp may glue to its date ("M12/24"); any other letter before digits is a name ("C12-15").
_GLUED_CODE_LETTERS = "MUEPB"

KINDS = {
    "batch_number": (),  # never assigned here; its key belongs to declaration blocks and tables
    "mrp": ("price", "amount"),
    "unit_sale_price": ("usp",),
    "net_quantity": ("quantity",),
    "manufacturing_date": ("date",),
    "packing_date": ("date",),
    "expiry_date": ("date",),
    "best_before": ("date", "shelf_life"),
    "use_by": ("date",),
}
KEY_PHRASES: tuple[Phrase, ...] = (
    *phrases("mrp", "mrp", "m r p", "maximum retail price", "max retail price", "अधिकतम खुदरा मूल्य", "एमआरपी"),
    *phrases("unit_sale_price", "unit sale price", "unit sale", "usp", "u s p"),
    *phrases("net_quantity", "net weight", "net wt", "net quantity", "net qty", "net content", "net contents", "net volume", "net vol",
             "net mass", "contents", "n w", "शुद्ध मात्रा", "शुद्ध वजन", "कुल मात्रा"),
    *phrases("manufacturing_date", "mfg date", "mfd date", "mfg dt", "mfg", "mfd", "date of manufacture", "date of mfg", "manufacturing date",
             "manufacture date", "manufactured on", "mfd on", "month and year of mfg", "month and year of manufacture", "निर्माण तिथि"),
    *phrases("packing_date", "packed on", "pkd", "pkd on", "pkd date", "date of packing", "packing date", "month and year of packing", "पैकिंग तिथि"),
    *phrases("expiry_date", "expiry date", "expiry", "exp", "exp date", "expires on"),
    *phrases("best_before", "best before", "use before", "bb"),
    *phrases("use_by", "use by", "use by date"),
    *phrases("batch_number", "batch no", "batch number", "batch", "b no", "lot no", "lot number"),
)
REDIRECT_PHRASES: tuple[Phrase, ...] = (
    *phrases("below", "see below", "refer below", "given below", "printed below", "mentioned below"),
    *phrases("bottom", "see bottom", "see at bottom", "refer bottom", "see base", "see bottom of pack", "see bottom of the pack", "see bottom of package"),
    *phrases("coding_area", "see coding area", "refer coding area", "see coding", "in coding area"),
    *phrases("back", "see back", "see back of pack", "see overleaf", "refer overleaf"),
    *phrases("side", "see side", "see side panel", "see top", "see lid", "see cap", "see crimp", "see seal"),
    *phrases("pack", "see pack", "see package", "see on pack", "printed on pack", "printed on the pack", "refer band", "see band", "see pouch"),
)
# "For MRP and other details, see bottom of the pack": every other declaration is on another panel.
OTHER_DETAILS = phrases("other_details", "other details", "all other details", "other declarations")


def _number(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


def normalize_date(value: str) -> str | None:
    """YYYY-MM-DD, YYYY-MM, or an ISO-8601 duration (P9M) for "N months from ..."."""
    text = re.sub(r"\s+", " ", value.strip().lower())
    year = lambda digits: int(digits) + (2000 if len(digits) == 2 else 0)
    if months := re.fullmatch(r"([0-9]{1,2}) ?months?.*", text):
        return f"P{int(months.group(1))}M"
    if full := re.fullmatch(r"([0-9]{1,2}) ?[/.\-] ?([0-9]{1,2}) ?[/.\-] ?([0-9]{4}|[0-9]{2})", text):
        day, month = int(full.group(1)), int(full.group(2))
        return f"{year(full.group(3)):04d}-{month:02d}-{day:02d}" if 1 <= month <= 12 and 1 <= day <= 31 else None
    if month_year := re.fullmatch(r"([0-9]{1,2}) ?[/.\-] ?([0-9]{4}|[0-9]{2})", text):
        month = int(month_year.group(1))
        return f"{year(month_year.group(2)):04d}-{month:02d}" if 1 <= month <= 12 else None
    if named := re.fullmatch(r"(?:([0-9]{1,2}) ?[\-/ ] ?)?([a-z]{3})[a-z]*\.? ?[\-/'. ]? ?([0-9]{4}|[0-9]{2})", text):
        if named.group(2) not in _MONTHS:
            return None
        prefix = f"{year(named.group(3)):04d}-{_MONTHS[named.group(2)]:02d}"
        return f"{prefix}-{int(named.group(1)):02d}" if named.group(1) else prefix
    return None


# ------------------------------------------------------------------- model

@dataclass(frozen=True)
class Span:
    line: LayoutLine
    start: int
    end: int

    @property
    def text(self) -> str:
        return self.line.text[self.start:self.end]


@dataclass
class Value:
    kind: str
    span: Span
    box: Extent | None
    value: str
    normalized: str
    code: str | None = None
    weak: bool = False      # a bare amount: only taken right beside a key
    lead: bool = False      # first token of its line
    offer: bool = False
    glued_key: str | None = None  # "550mlnet": the key is glued to the value


@dataclass
class Key:
    field: str
    span: Span
    box: Extent | None
    edits: int
    phrase: str
    code: str | None = None
    redirect: str | None = None
    redirect_span: Span | None = None
    block: int = -1
    coded_block: bool = False


@dataclass(frozen=True)
class Assignment:
    field: str
    value: Value
    key: Key | None
    score: float
    how: str

    @property
    def pattern(self) -> str:
        return f"{self.field}:{self.how}"


@dataclass
class Matching:
    assignments: dict[str, Assignment] = field(default_factory=dict)
    keys: dict[str, list[Key]] = field(default_factory=dict)
    values: list[Value] = field(default_factory=list)
    other_details: Span | None = None


# ------------------------------------------------------------- geometry

def _height(box: Extent) -> float:
    return max(box[3] - box[1], 1.0)


def _center_y(box: Extent) -> float:
    return (box[1] + box[3]) / 2


def _x_overlap(a: Extent, b: Extent) -> float:
    return max(min(a[2], b[2]) - max(a[0], b[0]), 0.0)


def _gap(a: Extent, b: Extent) -> float:
    """Shortest distance between two boxes (0 when they touch or overlap)."""
    dx = max(b[0] - a[2], a[0] - b[2], 0.0)
    dy = max(b[1] - a[3], a[1] - b[3], 0.0)
    return math.hypot(dx, dy)


# ------------------------------------------------------------- candidates

def _code_before(text: str) -> str | None:
    match = _VALUE_CODE.search(text)
    return match.group(1) if match else None


def _usp_value(match: re.Match[str]) -> tuple[str, str] | None:
    amount, per, unit = match.group(1).replace(",", "."), (match.group(2) or "").strip(), match.group(3).lower()
    if unit == "9" and "." not in amount:
        return None  # "/9" is read as "/g" only for a paise-shaped amount ("0.36/9")
    unit = "ml" if re.fullmatch(r"m[nk1i]|millilit(?:re|er)s?", unit) else "g" if unit == "9" or re.fullmatch(r"grams?", unit) else "l" if re.fullmatch(r"lit(?:re|er)s?", unit) else unit
    return f"₹{amount} per {per}{' ' if per else ''}{unit}", f"₹{amount}/{per}{unit}"


def _quantity_value(amount: str, unit: str) -> tuple[str, str] | None:
    from backend.app.services.compliance.rule_engine import ComplianceEngine

    unit = re.sub(r"\s+", "", unit)
    for pattern, latin in _HINDI_UNITS.items():
        if re.fullmatch(pattern, unit):
            unit = latin
    declared = f"{amount} {unit}"
    normalized = ComplianceEngine.normalize_quantity(declared)
    return (declared, normalized) if normalized else None


def candidates(layout: Layout) -> list[Value]:
    """Every value-shaped token on the label, overlapping readings resolved (prices > quantities > dates)."""
    found: list[Value] = []
    for line in layout.lines:
        text = line.text
        taken: list[tuple[int, int]] = []

        def add(kind: str, start: int, end: int, value: str, normalized: str, **extra) -> None:
            if any(start < other_end and end > other_start for other_start, other_end in taken):
                return
            taken.append((start, end))
            span = Span(line, start, end)
            found.append(Value(kind, span, line.span_extent(start, end) if layout.geometric else None, value, normalized,
                               code=extra.pop("code", _code_before(text[:start])), lead=not re.search(r"[0-9A-Za-z]", text[:start]), **extra))

        for match in _USP.finditer(text):
            if built := _usp_value(match):
                add("usp", match.start(), match.end(), *built)
        for match in _STRONG_PRICE.finditer(text):
            amount = match.group(3) or f"{match.group(1)}.{match.group(2)}"
            if (number := _number(amount)) and number > 0:
                add("price", match.start(), match.end(), f"₹{amount.replace(',', '')}", f"₹{number.normalize():f}", offer=bool(_OFFER.search(text[:match.start()])))
        for match in _WEAK_PRICE.finditer(text):
            amount = match.group(1) or match.group(2) or f"{match.group(3)}.{match.group(4)}"
            if (number := _number(amount)) and number > 0:
                add("price", match.start(), match.end(), f"₹{amount}", f"₹{number.normalize():f}", offer=bool(_OFFER.search(text[:match.start()])))
        if not _NOT_NET.search(text):
            for match in _QUANTITY.finditer(text):
                before, after = text[:match.start()], text[match.end():]
                # "10 mg/ml", "5 g per sachet", "125g x 8": a strength or a part; "100 ml / 3.38 fl. oz." is a dual declaration.
                if re.search(r"(?i)(?:\bper|\bin|\bof|[x×(]|@)\s*$", before) or re.match(r"(?i)\s*(?:[x×%]|/\s*[a-z]|per\b|each\b)", after) or match.group(2).lower() == "mg":
                    continue
                if built := _quantity_value(match.group(1), match.group(2)):
                    glued = "net" if re.match(r"(?i)nett?", after) else None
                    add("quantity", match.start(), match.end(), *built, glued_key=glued)
        for match in _DATE_NUMERIC.finditer(text):
            # Part of a longer number ("1-800-42..", "100.54") is not a date; a range "01/2026-12/2028" is two.
            # A slash code ("HIM/COS/06/94") is not a date either, nor is a decimal ("6.90": m.yy needs a 4-digit year).
            before, after = text[max(match.start() - 2, 0):match.start()], text[match.end():]
            if re.search(r"[0-9]$|[0-9][/.]$|[A-Za-z0-9]/$", before) or re.match(r"(?:[0-9]|[/.][0-9]|-[0-9]+(?![0-9]*[/.][0-9]))", after) or re.match(r"\s*(?:%|[a-z])", after):
                continue
            if match.group(5) and "." in match.group(0) and len(match.group(6)) == 2:
                continue
            if before[-1:].isalpha() and before[-1:] not in _GLUED_CODE_LETTERS:
                continue
            raw = re.sub(r"\s+", "", match.group(0))
            if normalized := normalize_date(raw):
                add("date", match.start(), match.end(), raw, normalized)
        for match in _DATE_NAMED.finditer(text):
            raw = re.sub(r"\s+", " ", match.group(0)).strip()
            if normalized := normalize_date(raw):
                add("date", match.start(), match.end(), raw, normalized)
        for match in _SHELF_LIFE.finditer(text):
            add("shelf_life", match.start(), match.end(), f"{match.group(1)} months", f"P{int(match.group(1))}M")
        # Bare amounts: the first token of a line, only taken right beside an MRP key.
        # Rotated side text ("3323" printed up a panel edge) never qualifies.
        if not line.vertical and (lead := _LEADING_AMOUNT.match(text)) and (number := _number(lead.group(1))) and number > 0:
            add("amount", lead.start(1), lead.end(1), f"₹{lead.group(1)}", f"₹{number.normalize():f}", weak=True)
    return found


# ------------------------------------------------------------------ keys

def _short_key_confirmed(text: str, hit: fuzzy.Hit) -> bool:
    """A short key read with one OCR error ("MAP", "MFO") counts only when the line confirms it."""
    after = text[hit.end:hit.end + 30]
    if re.match(rf"(?i)\s*[:.\-]?\s*(?:\(\s*(?:₹|r|rs)\s*\)|{_STRONG_CURRENCY}|[0-9])", after):
        return True
    others = [other for other in fuzzy.find(text, KEY_PHRASES) if other.edits == 0 and other.start != hit.start]
    return bool(others or fuzzy.find(text, REDIRECT_PHRASES) or re.search(r"(?i)incl|tax", after))


def find_keys(layout: Layout) -> list[Key]:
    keys: list[Key] = []
    for line in layout.lines:
        text = line.text
        for hit in fuzzy.find(text, KEY_PHRASES, short_edits=1, prefix=True):
            short = len(hit.phrase.folded) < 4
            if short and hit.edits and not _short_key_confirmed(text, hit):
                continue
            if hit.phrase.text in ("bb", "n w") and not re.match(r"\s*[:.\-]?\s*[0-9]", text[hit.end:]):
                continue
            if hit.phrase.text == "contents" and not re.match(r"\s*(?:[:\-]|[0-9])", text[hit.end:]):
                continue  # "transfer contents to a clean container" is an instruction, not a key
            if hit.name in ("manufacturing_date", "packing_date") and _NOT_A_DATE_KEY.match(text[hit.end:]) and "date" not in hit.phrase.text:
                continue  # "Mfd. by", "Mfg. Lic. No.", "For mfg. unit address" introduce something else
            code_match = _KEY_CODE.match(text[hit.end:])
            code = (code_match.group(1) or code_match.group(2)) if code_match else None
            if code == "R" and hit.name in ("mrp", "unit_sale_price"):
                code = None  # "MRP (R)" is "MRP (₹)", not a code
            if code is None and (prefix := _PREFIX_CODE.search(text[max(hit.start - 3, 0):hit.start])):
                code = prefix.group(1)
            span = Span(line, hit.start, hit.end)
            keys.append(Key(hit.name, span, line.span_extent(hit.start, hit.end) if layout.geometric else None, hit.edits, hit.phrase.text, code))
    return keys


def _blocks(layout: Layout, keys: list[Key]) -> dict[int, list[LayoutLine]]:
    """Runs of key lines read as one declaration block ("MRP / Unit Sale Price / Batch No / Mfg Date: see below").

    A run continues to the next line below (or the next line, without
    geometry) that holds a key or a redirect; one short line between them
    (an unreadable fragment) does not break it.  Redirect lines on the same row
    as a block line belong to it.
    """
    key_lines = {key.span.line.index for key in keys}
    redirect_lines = {line.index for line in layout.lines if fuzzy.find(line.text, REDIRECT_PHRASES)}
    marked = key_lines | redirect_lines
    blocks: dict[int, list[LayoutLine]] = {}
    block_of: dict[int, int] = {}
    for line in layout.lines:
        if line.index not in marked or line.index in block_of:
            continue
        block = len(blocks)
        members, frontier = [line], [line]
        block_of[line.index] = block
        bridged: set[int] = set()
        while frontier:
            current = frontier.pop()
            neighbours = [right] if (right := layout.right_of(current)) is not None else []
            neighbours += layout.below(current, gap_factor=1.6)[:2]
            if not layout.geometric and current.index + 1 < len(layout.lines):
                neighbours.append(layout.lines[current.index + 1])
            for other in neighbours:
                if other.index in block_of or other.index in bridged:
                    continue
                if other.index in marked:
                    block_of[other.index] = block
                    members.append(other)
                    frontier.append(other)
                elif current.index not in bridged and len(other.text.split()) <= 3 and not re.search(r"[0-9]", other.text):
                    bridged.add(other.index)  # a short unreadable fragment between two key lines
                    frontier.append(other)
        blocks[block] = members
    for key in keys:
        key.block = block_of.get(key.span.line.index, -1)
    return blocks


def _apply_redirects(layout: Layout, keys: list[Key], blocks: dict[int, list[LayoutLine]]) -> None:
    for block, members in blocks.items():
        redirect: tuple[str, Span] | None = None
        for line in sorted(members, key=lambda item: item.index):
            if hits := fuzzy.find(line.text, REDIRECT_PHRASES):
                redirect = (hits[0].name, Span(line, hits[0].start, hits[0].end))
                break
        block_keys = [key for key in keys if key.block == block]
        coded = sum(key.code is not None for key in block_keys) >= 2
        for key in block_keys:
            key.coded_block = coded
            if redirect:
                key.redirect, key.redirect_span = redirect


# -------------------------------------------------------------- matching

def _table_pairs(layout: Layout, keys: list[Key], values: list[Value]) -> set[tuple[int, int]]:
    """Stacked key boxes facing a stacked column of value boxes: pair them row by row.

    Curved packs tilt rows so that nearest-row matching pairs a key with the
    neighbouring row's value; when the key column and the value column hold
    the same number of entries, their order decides.
    """
    if not layout.geometric:
        return set()
    boxes = [(line, start, end, box) for line in layout.lines for start, end, box in line.box_spans() if box.bbox]
    key_boxes = []
    for key in keys:
        for line, start, end, box in boxes:
            letters = len(re.sub(r"[^0-9A-Za-z]", "", line.text[start:end]))
            if line is key.span.line and start <= key.span.start < end and len(re.sub(r"[^0-9A-Za-z]", "", key.span.text)) >= 0.6 * letters:
                key_boxes.append((key, box))
    pairs: set[tuple[int, int]] = set()
    if len(key_boxes) < 2:
        return pairs
    key_boxes.sort(key=lambda item: _center_y(item[1].extent))
    columns: list[list[tuple[Key, object]]] = [[key_boxes[0]]]
    for item in key_boxes[1:]:
        last = columns[-1][-1][1].extent
        extent = item[1].extent
        if abs(extent[0] - last[0]) <= _height(last) and extent[1] - last[3] <= 1.2 * _height(last):
            columns[-1].append(item)
        else:
            columns.append([item])
    key_box_ids = {id(box) for _, box in key_boxes}
    for column in columns:
        if len(column) < 2:
            continue
        top = min(box.extent[1] for _, box in column)
        bottom = max(box.extent[3] for _, box in column)
        right = max(box.extent[2] for _, box in column)
        height = max(_height(box.extent) for _, box in column)
        facing = [
            (line, start, end, box) for line, start, end, box in boxes
            if id(box) not in key_box_ids and is_substantial(box.text) and right - 0.5 * height <= box.extent[0] <= right + 15 * height
            and top - 0.5 * height <= _center_y(box.extent) <= bottom + 2.5 * height
        ]
        # Boxes side by side ("372 028") are one row of the value column.
        rows: list[list[tuple]] = []
        for item in sorted(facing, key=lambda item: _center_y(item[3].extent)):
            if rows and abs(_center_y(item[3].extent) - _center_y(rows[-1][0][3].extent)) <= 0.5 * height:
                rows[-1].append(item)
            else:
                rows.append([item])
        # Pair the first rows when every key sits the same distance above its row (one consistent tilt).
        if len(rows) < len(column):
            continue
        rows = rows[:len(column)]
        offsets = [_center_y(row[0][3].extent) - _center_y(box.extent) for (_, box), row in zip(column, rows)]
        if max(offsets) - min(offsets) > 0.6 * height:
            continue
        for (key, _), row in zip(column, rows):
            for line, start, end, _ in row:
                for index, value in enumerate(values):
                    if value.span.line is line and start <= value.span.start < end:
                        pairs.add((id(key), index))
    return pairs


def _closer_key_between(key: Key, value: Value, keys: list[Key]) -> bool:
    return any(
        other is not key and other.span.line is key.span.line and key.span.end <= other.span.start < value.span.start
        and value.kind in KINDS[other.field]
        for other in keys
    )


def _score(layout: Layout, key: Key, value: Value, keys: list[Key], defined: dict[str, str], coding_lines: set[int], table: set[tuple[int, int]], index: int) -> tuple[float, str] | None:
    if value.kind not in KINDS[key.field] or value.offer:
        return None
    if value.code:
        if key.code == value.code:
            return 0.97, f"code_{value.code}"
        owner = defined.get(value.code) or (CONVENTIONAL_CODES.get(value.code) if value.code.isalpha() else None)
        if owner and owner != key.field:
            return None
        if owner == key.field and value.code not in defined:
            return 0.8, f"letter_code_{value.code}"
    line, value_line = key.span.line, value.span.line
    if value_line is line and value.span.start >= key.span.end:
        if _closer_key_between(key, value, keys):
            return None
        if value.weak and re.search(r"[0-9A-Za-z]", line.text[key.span.end:value.span.start].replace("(", "")):
            return None
        return 1.0 - 0.002 * (value.span.start - key.span.end), "same_line"
    # "Batch No./Mfg. Date: See package." says the value is not beside the key.
    redirected_here = key.redirect_span is not None and key.redirect_span.line is line
    if (id(key), index) in table and not redirected_here:
        return 0.9, "table_row"
    # A key or value placed in a key/value table pairs only along its row.
    in_table = any(pair[0] == id(key) or pair[1] == index for pair in table)
    if redirected_here or in_table:
        pass
    elif layout.geometric and key.box and value.box:
        kb, vb = key.box, value.box
        height = max(_height(kb), 1.0)
        reach = max(height, _height(vb))
        if abs(_center_y(vb) - _center_y(kb)) <= 0.8 * reach and kb[2] - 0.5 * height <= vb[0] <= kb[2] + 10 * height:
            if value.weak and not value.lead:
                return None
            return 0.92 - 0.004 * (vb[0] - kb[2]) / height, "right_of_key"
        # A value opening the next line counts as below the key even when the line starts further left,
        # and values under a line of several keys ("Mfg. Date - Use before Date") align with the line.
        column = value_line.extent if value.lead and value_line.extent else vb
        combined = sum(other.span.line is line and bool(KINDS[other.field]) for other in keys) > 1
        key_column = line.extent if combined and line.extent else kb
        if vb[1] >= kb[3] - 0.3 * height and _x_overlap(key_column, column) > 0 and vb[1] - kb[3] <= 3.5 * height:
            if value.weak and not value.lead:
                return None
            return 0.88 - 0.04 * (vb[1] - kb[3]) / height, "below_key"
        if not value.weak and _gap(kb, vb) <= 6 * height and vb[1] >= kb[1] - 3 * height:
            return 0.6 - 0.01 * _gap(kb, vb) / height, "nearest_in_region"
    elif not layout.geometric and 0 < value_line.index - line.index <= 2:
        if value.weak and not value.lead:
            return None
        return 0.82 - 0.04 * (value_line.index - line.index - 1), "next_line"
    # Far from its key, a value must stand alone like a stamp: a date inside an ingredients sentence is not one.
    if len(re.findall(r"[A-Za-z]{3,}", value_line.text)) > 3:
        return None
    if key.coded_block and value_line.index in coding_lines:
        return (0.42 if value.weak else 0.5), "coding_area"
    if key.redirect and not value.weak:
        if key.redirect == "below" and not _is_below(layout, key, value):
            return None
        return 0.45, f"redirect_{key.redirect}"
    return None


def _coding_area(layout: Layout, coded: set[int]) -> set[int]:
    """Lines holding a coded value ("¥12/28") and the stamped lines right beside or above/below them."""
    area = set(coded)
    for line in layout.lines:
        if line.index in coded or not line.extent or line.vertical:
            continue
        for other in layout.lines:
            if other.index in coded and other.extent and _gap(line.extent, other.extent) <= 3 * min(line.box_height, other.box_height):
                area.add(line.index)
    return area


def _is_below(layout: Layout, key: Key, value: Value) -> bool:
    anchor = key.redirect_span.line if key.redirect_span else key.span.line
    if layout.geometric and anchor.extent and value.span.line.extent:
        return _center_y(value.span.line.extent) > _center_y(anchor.extent)
    return value.span.line.index > anchor.index


def match(layout: Layout, claimed: Iterable[tuple[int, str]] = ()) -> Matching:
    """Assign values to keys; ``claimed`` are (line index, normalized value) pairs another pass already used."""
    result = Matching()
    if not layout.lines:
        return result
    values = candidates(layout)
    keys = find_keys(layout)
    blocks = _blocks(layout, keys)
    _apply_redirects(layout, keys, blocks)
    for line in layout.lines:
        if fuzzy.find(line.text, OTHER_DETAILS) and fuzzy.find(line.text, REDIRECT_PHRASES):
            result.other_details = Span(line, 0, len(line.text))
            break
    # Bare amounts right after an MRP key on its own line ("VIRP:100.54", "MRP २2 incl.").
    for key in keys:
        if key.field == "mrp" and (after := _AFTER_KEY_AMOUNT.match(key.span.line.text[key.span.end:])) and (number := _number(after.group(1))) and number > 0:
            start = key.span.end + after.start(1)
            if not any(value.span.line is key.span.line and value.span.start <= start < value.span.end for value in values):
                span = Span(key.span.line, start, key.span.end + after.end(1))
                values.append(Value("amount", span, key.span.line.span_extent(span.start, span.end) if layout.geometric else None, f"₹{after.group(1)}", f"₹{number.normalize():f}", weak=True))
    defined = {key.code: key.field for key in keys if key.code}
    coding_lines = _coding_area(layout, {value.span.line.index for value in values if value.code and value.code in defined})
    table = _table_pairs(layout, keys, values)
    claimed_set = set(claimed)
    options: list[tuple[float, int, Key | None, Value, str]] = []
    for index, value in enumerate(values):
        if (value.span.line.index, value.normalized) in claimed_set:
            continue
        if value.glued_key == "net":
            options.append((1.0, index, None, value, "glued_key"))
        # A coded date ("M 12/24", "¥12/28") belongs to its coded field even when the key line is not in the photo.
        if value.kind == "date" and value.code and (owner := defined.get(value.code) or CONVENTIONAL_CODES.get(value.code)):
            how = f"code_{value.code}" if value.code in defined else f"letter_code_{value.code}"
            options.append((0.7, index, Key(owner, value.span, value.box, 0, "code"), value, how))
        for key in keys:
            if scored := _score(layout, key, value, keys, defined, coding_lines, table, index):
                options.append((scored[0], index, key, value, scored[1]))
    options.sort(key=lambda item: (-item[0], item[1]))
    used: set[int] = set()
    for score, index, key, value, how in options:
        name = key.field if key else "net_quantity"
        if index in used or name in result.assignments:
            continue
        used.add(index)
        result.assignments[name] = Assignment(name, value, key if key and key.phrase != "code" else None, round(score, 3), how)
    _order_dates(result, keys, values)
    result.keys = {}
    for key in keys:
        result.keys.setdefault(key.field, []).append(key)
    result.values = values
    return result


def _order_dates(result: Matching, keys: list[Key], values: list[Value]) -> None:
    """Two date keys and two dates on one line each ("Mfg. Date - Use before Date" / "01/2026-12/2028"): pair in order."""
    dated = [assignment for assignment in result.assignments.values() if assignment.value.kind == "date" and assignment.key]
    by_line: dict[tuple[int, int], list[Assignment]] = {}
    for assignment in dated:
        by_line.setdefault((assignment.key.span.line.index, assignment.value.span.line.index), []).append(assignment)
    for group in by_line.values():
        if len(group) < 2:
            continue
        ordered_keys = sorted(group, key=lambda item: item.key.span.start)
        ordered_values = sorted((item.value for item in group), key=lambda value: value.span.start)
        for assignment, value in zip(ordered_keys, ordered_values):
            if assignment.value is not value:
                result.assignments[assignment.field] = Assignment(assignment.field, value, assignment.key, assignment.score, assignment.how)


def consistent_price(values: list[Value], usp: str | None, quantity: str | None) -> Value | None:
    """An unkeyed price that the unit sale price and net quantity confirm as the MRP.

    "₹355.00" printed without its key beside "USP ₹0.89/ml" on a 400 ml pack:
    355 / 400 = 0.8875, so it is the MRP.  Offers and bare amounts never qualify.
    """
    if not usp or not quantity:
        return None
    usp_match = re.fullmatch(r"₹([0-9.]+)/([0-9.]*)(g|kg|ml|l|n|nos?|units?|pcs?|pieces?)", usp)
    quantity_match = re.fullmatch(r"([0-9.]+) (g|ml|count)", quantity)
    if not usp_match or not quantity_match:
        return None
    per = Decimal(usp_match.group(2) or "1") * {"kg": 1000, "l": 1000}.get(usp_match.group(3), 1)
    price_per = Decimal(usp_match.group(1))
    units = Decimal(quantity_match.group(1)) / per
    for value in values:
        if value.kind != "price" or value.offer or units <= 0:
            continue
        expected = Decimal(value.normalized[1:]) / units
        if abs(expected - price_per) <= max(Decimal("0.02") * expected, Decimal("0.006")):
            return value
    return None
