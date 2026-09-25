"""Recognise receipt lines that aren't products, so they never become inventory items.

Pure string matching, no model involved — the vision model reliably reads
these lines but can't be relied on to know they aren't purchases.
"""

import re

# Money that really changed hands but isn't a product: counts toward the
# receipt total, never logged as an item.
_ADJUSTMENT = re.compile(
    r"\b(pfand|leergut|rabatt|preisvorteil|coupon|gutschein)", re.IGNORECASE
)
# Informational lines — no money of their own (or money already counted
# elsewhere): ignored entirely.
_INFO = re.compile(
    r"\b(normalpreis|zwischensumme|summe|mwst|ust|netto|brutto|steuer|"
    r"sie sparen|ec[- ]?karte|kartenzahlung|r(ü|ue)ckgeld|gegeben)\b",
    re.IGNORECASE,
)

# A bare count in front of the name ("1 Weleda...", "2x Milch", "3 Stk
# Eier") — the model copies the receipt's quantity/tax-code column into the
# name. Leading only: a trailing digit or letter is too often real ("Pampers
# 4", "Vitamin C", "150g").
_LEADING_COUNT = re.compile(r"^\s*\d+\s*(x|stk\.?)?\s+(?=\S)", re.IGNORECASE)


def clean_name(name: str) -> str:
    """Strip a stray leading quantity from an item name the model read off a receipt."""
    return _LEADING_COUNT.sub("", name).strip()


def classify_line(name: str) -> str:
    """Classify a receipt line by its name.

    Returns:
        'adjustment' (deposit/discount: counts toward the total, not an
        item), 'info' (ignore entirely), or 'item'.
    """
    if _ADJUSTMENT.search(name):
        return "adjustment"
    if _INFO.search(name):
        return "info"
    return "item"
