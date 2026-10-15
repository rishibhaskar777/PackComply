"""Deterministic fuzzy matching of label keys in OCR text (standard library only).

OCR misreads keys in small, predictable ways: a letter dropped or swapped
("Manufactued by", "Net Contemt"), and a few shape confusions ("VIRP" for
"MRP", "Ingredlents" for "Ingredients", "0" for "O").  Both the key and the text
are *folded* (lower case, confusable shapes mapped to one letter, punctuation
dropped) and then compared with a bounded edit distance:

- keys of fewer than 4 folded characters must match exactly ("mrp", "exp");
- keys of 4-7 characters allow one edit, keys of 8 or more allow two.

Matching is over whole OCR tokens, so a key never matches inside a longer word.
A short key may be allowed one edit by the caller when the surrounding text
confirms it (``Hit.edits`` tells the caller how far the match was).
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re
from typing import Iterable

# Applied in order after lower-casing.  "ivi" before "i" so "IVIRP" folds to "mrp".
_FOLDS = (("ivi", "m"), ("vi", "m"), ("rn", "m"), ("1", "l"), ("i", "l"), ("0", "o"), ("5", "s"))
_TOKEN = re.compile(r"[0-9A-Za-zÀ-ɏऀ-ॿ]+")
_DROP = re.compile(r"[^0-9a-zÀ-ɏऀ-ॿ]")


@lru_cache(maxsize=4096)
def fold(text: str) -> str:
    """Comparable form: lower case, OCR-confusable shapes merged, punctuation and spaces dropped."""
    value = text.lower()
    for source, target in _FOLDS:
        value = value.replace(source, target)
    return _DROP.sub("", value)


def max_edits(length: int) -> int:
    return 2 if length >= 8 else 1 if length >= 4 else 0


def distance(a: str, b: str, limit: int) -> int:
    """Levenshtein distance, or ``limit + 1`` as soon as it must exceed ``limit``."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    if a == b:
        return 0
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, 1):
        current = [i] + [0] * len(b)
        for j, char_b in enumerate(b, 1):
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char_a != char_b))
        if min(current) > limit:
            return limit + 1
        previous = current
    return previous[-1]


@dataclass(frozen=True)
class Phrase:
    """A key as printed on labels; ``name`` is what it declares (a field, or a role)."""
    name: str
    text: str

    @property
    def folded(self) -> str:
        return fold(self.text)

    @property
    def words(self) -> int:
        return len(_TOKEN.findall(self.text))


@dataclass(frozen=True)
class Hit:
    phrase: Phrase
    start: int
    end: int
    edits: int

    @property
    def name(self) -> str:
        return self.phrase.name


def tokens(text: str) -> list[tuple[int, int, str]]:
    return [(match.start(), match.end(), fold(match.group(0))) for match in _TOKEN.finditer(text)]


def _small_words_present(phrase: Phrase, window: list[str]) -> bool:
    """In a multi-token window every 1-2 letter word of the key must be read exactly.

    The edit budget belongs to the long words: "Manufactured" alone must not
    pass for "manufactured on", nor "Mfd. by" for "mfd on".
    """
    small = [fold(word) for word in _TOKEN.findall(phrase.text) if len(fold(word)) <= 2]
    if len(window) < 2:
        # One token for a key with small words is a glued key ("Manufacturdby"): it must be nearly complete.
        return not small or len(window[0]) >= len(phrase.folded) - 1
    return all(word in window for word in small)


def find(text: str, phrases: Iterable[Phrase], short_edits: int = 0, prefix: bool = False) -> list[Hit]:
    """Non-overlapping key hits in ``text``, best first where they compete.

    ``short_edits`` allows that many edits for 3-character keys (the caller
    must confirm such hits from context); 1-2 character keys always match
    exactly.  ``prefix`` also accepts a key cut off at the end of the line
    ("Manufacture D" for "manufacture date"): the window must end the line and
    cover at least 8 characters of the key.
    """
    words = tokens(text)
    line_end = len(text.rstrip(" :.-;,"))
    found: list[Hit] = []
    for phrase in phrases:
        target = phrase.folded
        if not target:
            continue
        short = len(target) < 4
        limit = max_edits(len(target)) or (short_edits if len(target) >= 3 else 0)
        for first in range(len(words)):
            joined = ""
            for last in range(first, min(first + phrase.words + 1, len(words))):
                joined += words[last][2]
                if len(joined) > len(target) + limit:
                    break
                window = [word[2] for word in words[first:last + 1]]
                if joined[:1] != target[:1] and limit < 2:
                    # A first-character error needs two allowed edits, except one stray
                    # leading character before a short key ("GMRP" for "*MRP").
                    if short and limit and joined[1:] == target:
                        found.append(Hit(phrase, words[first][0], words[last][1], 1))
                    continue
                if short and len(joined) < len(target):
                    continue  # never shorten a short key: "mg" is not "mfg"
                if not _small_words_present(phrase, window):
                    continue
                edits = distance(joined, target, limit)
                if edits > limit and prefix and 8 <= len(joined) < len(target) and words[last][1] >= line_end:
                    edits = distance(joined, target[:len(joined)], 1)
                    edits = edits if edits <= 1 else limit + 1
                if edits <= limit:
                    found.append(Hit(phrase, words[first][0], words[last][1], edits))
    # Keep the best of overlapping hits: fewest edits, then the longest key.
    found.sort(key=lambda hit: (hit.edits, -(len(hit.phrase.folded)), hit.start))
    kept: list[Hit] = []
    for hit in found:
        if all(hit.end <= other.start or hit.start >= other.end for other in kept):
            kept.append(hit)
    return sorted(kept, key=lambda hit: hit.start)


def contains(text: str, phrases: Iterable[Phrase], prefix: bool = False) -> Hit | None:
    hits = find(text, phrases, prefix=prefix)
    return hits[0] if hits else None


def phrases(name: str, *texts: str) -> tuple[Phrase, ...]:
    return tuple(Phrase(name, text) for text in texts)
