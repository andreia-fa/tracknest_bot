"""One name per shop, however a receipt prints or misreads it ("YORMAS'S", "Yormas im Bahnhof Klettppassage")."""

import re
from difflib import SequenceMatcher

# Chains, recognised by their name anywhere in the receipt header, mapped to
# the one name the dashboard shows. Branch and company suffixes ("im Bahnhof
# Klettpassage", "Sezgin Kitis oHG", "-MARKT GmbH") are dropped on purpose:
# the user counts a chain as one shop (2026-10-19). Checked in order, so the
# more specific "aldi sued" comes before "aldi". Not "Müller": too common a
# name ("Bäckerei Müller" is a bakery, not the drugstore).
_CHAINS = [
    ("yormas", "Yormas"), ("rewe", "REWE"), ("penny", "PENNY"), ("pennymarkt", "PENNY"), ("dm", "dm"),
    ("aldi sued", "Aldi Süd"), ("aldi nord", "Aldi Nord"), ("aldi", "Aldi"),
    ("lidl", "Lidl"), ("edeka", "EDEKA"), ("netto", "Netto"), ("kaufland", "Kaufland"),
    ("rossmann", "Rossmann"), ("tegut", "tegut"), ("alnatura", "Alnatura"),
    ("denns", "denn's"), ("go asia", "go asia"), ("burger king", "Burger King"),
    ("mcdonald", "McDonald's"), ("dean and david", "dean&david"), ("dean david", "dean&david"),
    ("primark", "Primark"), ("decathlon", "Decathlon"), ("intimissimi", "Intimissimi"),
    ("amazon", "Amazon"), ("starbucks", "Starbucks"), ("subway", "Subway"), ("kfc", "KFC"),
]
# A shop that isn't a known chain still matches one already used despite a
# misread letter or two — kept high, since merging two real shops is worse
# than a second row.
_MIN_RATIO = 0.85
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def _key(store: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", store.lower().translate(_UMLAUTS)).split())


def canonical_store(store: str | None, known: list[str] = ()) -> str | None:
    """Return the one name used for this shop.

    Args:
        store: The store as read from a receipt or typed; None stays None.
        known: Store names already in use, matched despite small misreads.

    Returns:
        The chain's name for a known chain, else the closest known store
        name, else the store as given with its spacing tidied.
    """
    if not store or not store.strip():
        return None
    key = _key(store)
    for pattern, name in _CHAINS:
        if re.search(rf"\b{pattern}\b", key):
            return name
    # Chain names count as known too, so a misread chain ("Intimisimi") still lands.
    candidates = [*known, *(name for _, name in _CHAINS)]
    best = max(candidates, key=lambda k: SequenceMatcher(None, key, _key(k)).ratio(), default=None)
    if best and SequenceMatcher(None, key, _key(best)).ratio() >= _MIN_RATIO:
        return best
    return " ".join(store.split())
