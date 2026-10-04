"""Recognises a typed or receipt name as one the household already uses, despite typos and stray spacing."""

import re
import unicodedata
from difflib import SequenceMatcher

# How alike two names' letters must be to count as the same thing:
# "pfefer bretzl" vs "Pfefferbretzel" scores 0.92, "Laugenbrezel" vs
# "Pfefferbretzel" 0.62. Kept high on purpose: a wrong merge mixes two items'
# prices, while a miss only costs one extra question.
_MIN_RATIO = 0.85
# A known name bought before at exactly this price is more likely the same
# thing, so it needs less: "Laugenbrot"/"Laugenbrezel" (0.73) still stays two
# items, and so do same-brand neighbours at one price ("Milram Gouda" /
# "Milram Edamer", 0.70).
_MIN_RATIO_SAME_PRICE = 0.75
# Below this many letters a single typo is a different word ("milk"/"silk").
_MIN_KEY_LEN = 5
# Two candidates this close in score are a coin toss — better to ask.
_TIE_MARGIN = 0.03

_EDGE_PUNCT = " \t,.;:!?\"'()[]{}"
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def clean_name(name: str) -> str:
    """Collapse runs of spaces and drop stray punctuation at the ends ("pfefferbretzel," → "pfefferbretzel")."""
    return " ".join(name.split()).strip(_EDGE_PUNCT)


def name_key(name: str) -> str:
    """Letters and digits only, lower-case, umlauts and accents spelled out — what a name sounds like."""
    folded = name.casefold().translate(_UMLAUTS)
    folded = unicodedata.normalize("NFKD", folded)
    return "".join(ch for ch in folded if ch.isalnum() and not unicodedata.combining(ch))


def _keys_match(a: str, b: str) -> float:
    """Similarity of two keys (0-1); 0 when their numbers differ — "Milch 1,5%" is not "Milch 3,5%"."""
    if re.findall(r"\d+", a) != re.findall(r"\d+", b):
        return 0.0
    if len(a) < _MIN_KEY_LEN or len(b) < _MIN_KEY_LEN:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def match_known(name: str, known: list[str], same_price: set[str] = frozenset()) -> str | None:
    """Return the known name this one most likely means, or None if none is close enough or it's a toss-up.

    Spacing, case, punctuation and umlaut spellings never matter
    ("Pfeffer  Bretzel" is "Pfefferbretzel"); beyond that, a few typos are
    forgiven on longer names as long as the numbers in them agree — more
    of them when that name was bought before at the same price.

    Args:
        name: What was typed or read off a receipt.
        known: Names already in use (items, or shopping-list entries).
        same_price: The known names already bought at this exact price.
    """
    key = name_key(name)
    if not key:
        return None
    for candidate in known:
        if name_key(candidate) == key:
            return candidate
    scored = sorted(
        ((_keys_match(key, name_key(candidate)), candidate) for candidate in known),
        reverse=True,
    )
    passing = [(score, candidate) for score, candidate in scored
               if score >= (_MIN_RATIO_SAME_PRICE if candidate in same_price else _MIN_RATIO)]
    if not passing:
        return None
    best_score, best = passing[0]
    if len(passing) > 1 and best_score - passing[1][0] < _TIE_MARGIN and name_key(passing[1][1]) != name_key(best):
        return None
    return best
