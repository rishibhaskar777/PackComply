"""Display normalisation for OCR-read product and brand names.

OCR returns whatever casing the pack printed, often with damaged mixed case
("Parle-G Gluco BiscuITs") or run-together words ("BABYSHAMPOO"). The display
form fixes those; the raw OCR value is always kept beside it. Pure and
deterministic, with no network or model: a small built-in vocabulary is used
only to split a long unspaced word.
"""
from __future__ import annotations

import re
from functools import lru_cache

# Printed in capitals on purpose; kept as they are.
ACRONYMS = frozenset({
    "PVT", "LTD", "LLP", "USA", "UK", "UAE", "FSSAI", "MRP", "ISI", "SPF", "UV", "TV", "LED", "PET", "RO", "XL", "XXL", "XXXL",
    "DNA", "PH", "ID", "HD", "UHT", "GMO", "BPA", "AA", "AAA", "ABC", "MCT", "OTC", "MSG", "DHA", "EPA", "ORS",
})
# Lower-case inside a name unless first.
SMALL_WORDS = frozenset({"a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to", "with", "de", "la", "&"})
_VOCABULARY = frozenset("""
baby shampoo soap biscuit biscuits cream lotion oil hair face wash body tooth paste brush milk tea coffee rice wheat flour atta salt
sugar noodles instant powder juice water chips namkeen butter cheese ghee honey jam sauce ketchup masala spice spices dal gold classic
original natural herbal fresh pure premium active care gel mask sunscreen moisturiser moisturizer cleanser conditioner detergent
liquid bar tablet tablets extract vitamin protein energy drink cola soda glucose gluco cookies cookie cake rusk toast snack snacks
fruit fruits mango orange apple lemon almond almonds cashew peanut chocolate candy toffee mint strong extra plus max super ultra mild
soft smooth shine silk repair anti dandruff nourish tulsi neem aloe vera rose sandal coconut olive mustard sunflower refined
whitening fairness handwash dishwash floor cleaner toilet freshener perfume deodorant talc talcum cotton wipes diaper pants
cereal oats corn flakes muesli wafer wafers cracker crackers dairy yogurt curd paneer pickle vinegar tomato potato onion garlic
ginger turmeric chilli pepper cumin coriander mixed fruit jelly pudding custard ice cream milkshake health mix malt barley
""".split())
_WORD = re.compile(r"[A-Za-z]+")


@lru_cache(maxsize=2048)
def _split_run_together(word: str) -> tuple[str, ...] | None:
    """Segment an unspaced word into vocabulary words, using as few as possible; None if it does not split cleanly."""
    lowered = word.lower()
    if len(lowered) < 7 or lowered in _VOCABULARY:
        return None
    best: dict[int, list[str]] = {0: []}
    for end in range(1, len(lowered) + 1):
        for start in range(end):
            piece = lowered[start:end]
            if start in best and len(piece) >= 3 and piece in _VOCABULARY:
                candidate = best[start] + [piece]
                if end not in best or len(candidate) < len(best[end]):
                    best[end] = candidate
    parts = best.get(len(lowered))
    return tuple(parts) if parts and len(parts) >= 2 else None


def _damaged_mixed_case(word: str) -> bool:
    """Capitals inside a word that no brand casing explains: a run of 2+ capitals after a lowercase letter ("BiscuITs")."""
    return bool(re.search(r"[a-z][A-Z]{2,}", word)) or bool(re.search(r"[a-z][A-Z][a-z]*[A-Z]", word) and re.search(r"[A-Z]{2,}", word))


def _title(word: str) -> str:
    return word[:1].upper() + word[1:].lower()


def _fix_word(word: str, first: bool) -> str:
    """One alphabetic run, or a run-together word that expands to several."""
    if word.upper() in ACRONYMS and word.isupper():
        return word
    if len(word) == 1:
        return word.upper()
    split = _split_run_together(word) if (word.isupper() or word.islower()) else None
    if split:
        return " ".join(_title(part) for part in split)
    if word.isupper() or _damaged_mixed_case(word):
        return _title(word)
    if word.islower():
        return word if (word in SMALL_WORDS and not first) else _title(word)
    return word  # a deliberate CamelCase or Capitalised word


def _fix_token(token: str, first: bool) -> str:
    if any(character.isdigit() for character in token):
        return token  # quantities and codes ("70g", "B12", "A2") keep their printed form
    if not _WORD.search(token):
        return token
    pieces = re.split(r"(-)", token)
    out: list[str] = []
    for index, piece in enumerate(pieces):
        if piece == "-":
            out.append(piece)
            continue
        out.append(_WORD.sub(lambda m: _fix_word(m.group(0), first and index == 0), piece))
    return "".join(out)


def normalise_display_name(raw: str | None) -> str | None:
    """Tidy the casing of an OCR-read name for display; None / blank stay as they are."""
    if raw is None:
        return None
    text = re.sub(r"\s+", " ", raw).strip()
    if not text:
        return raw
    letters = [c for c in text if c.isalpha()]
    # Names in other scripts, or mostly non-Latin, are left exactly as read.
    if not letters or sum(c.isascii() for c in letters) / len(letters) < 0.8:
        return text
    tokens = text.split(" ")
    result: list[str] = []
    seen_word = False
    for token in tokens:
        fixed = _fix_token(token, first=not seen_word)
        if _WORD.search(token):
            seen_word = True
        # Small words stay lower-case except at the start, and the apostrophe tail ("Nature's") stays lower.
        fixed = re.sub(r"(?<=[A-Za-z])'([A-Z])\b", lambda m: "'" + m.group(1).lower(), fixed)
        result.append(fixed)
    return " ".join(result)
