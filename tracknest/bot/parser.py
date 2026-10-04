"""Parsing for plain-text inventory entries (no slash commands)."""

import re
from dataclasses import dataclass
from datetime import date, timedelta

# bot/main.py's handle_text checks the ORIGINAL, unmodified line for a
# leading "-" (its remove-command trigger) before parse_line ever runs, so
# stripping a leading "-" in here too is safe — it only affects what's left
# over after a different bullet char (e.g. ">") has already been peeled off
# (e.g. "> -- Soutien branco --" -> "-- Soutien branco --" -> "Soutien branco").
_LEADING_BULLET_RE = re.compile(r"^[\s\->*•]+")
_TRAILING_BULLET_RE = re.compile(r"[\s\-*•]+$")
# A trailing price marked with a euro sign, either side: "1€", "3,50 €", "€6.90".
# The sign makes it a price even without decimals.
_EURO_PRICE_RE = re.compile(r"\s*(?:€\s*(\d+(?:[.,]\d+)?)|(\d+(?:[.,]\d+)?)\s*€)$")


def parse_line(line: str) -> tuple[str, int, float | None]:
    """Parse one line of free text into an item name, quantity, and optional unit price.

    Accepts "<name>", "<name> <qty>", "<name> <price>", or "<name> <qty>
    <price>", where qty is a whole number and price accepts either '.' or
    ',' as the decimal separator (e.g. "Oat Milk 3 2,50" or "Matcha 2.50").
    A price is only recognized when its token contains a decimal separator
    — a bare integer is always read as quantity, never price, so "Bananas
    2" still means two bananas, not €2 — unless it carries a euro sign
    ("Pfefferbretzel 1€", "Matcha €6.90"), which always marks a price. Trailing tokens that don't match
    these shapes are treated as part of the name. Leading/trailing
    list-bullet punctuation (">", "*", "•", "--") is stripped first, so
    pasting a formatted list doesn't leak stray symbols into the stored
    item name.

    Callers use whether unit_price came back non-None to decide what a
    line means: a price present means "I just bought this, log it now"
    (handle_text logs an expense directly); no price means "put this on
    the shopping list" (the previous, still-default behaviour).

    Args:
        line: One line of user-typed text.

    Returns:
        A (name, quantity, unit_price) tuple. quantity defaults to 1 and
        unit_price to None when not given.

    Raises:
        ValueError: If the line is empty or has no name left after parsing.
    """
    line = _TRAILING_BULLET_RE.sub("", _LEADING_BULLET_RE.sub("", line))
    tokens = line.strip().split()
    if not tokens:
        raise ValueError("empty line")

    quantity = 1
    unit_price = None

    euro = _EURO_PRICE_RE.search(line)
    if euro:
        unit_price = _parse_price(euro.group(1) or euro.group(2))
        tokens = line[:euro.start()].split()
        if len(tokens) >= 2 and tokens[-1].isdigit():
            quantity = int(tokens[-1])
            tokens = tokens[:-1]

    if len(tokens) >= 3 and tokens[-2].isdigit():
        price = _parse_price(tokens[-1])
        if price is not None:
            quantity = int(tokens[-2])
            unit_price = price
            tokens = tokens[:-2]

    if unit_price is None and len(tokens) >= 2:
        last = tokens[-1]
        if last.isdigit():
            quantity = int(last)
            tokens = tokens[:-1]
        elif "." in last or "," in last:
            price = _parse_price(last)
            if price is not None:
                unit_price = price
                tokens = tokens[:-1]

    name = " ".join(tokens)
    if not name:
        raise ValueError("missing item name")
    return name, quantity, unit_price


def _parse_price(token: str) -> float | None:
    """Parse a price token, accepting '.' or ',' as the decimal separator."""
    try:
        return float(token.replace(",", "."))
    except ValueError:
        return None


# "3/10" or "3/10/2026" — a slash, because "3.10" reads as a price.
_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?[,\s]+")
_YESTERDAY_RE = re.compile(r"^(yesterday|gestern|ontem)[,\s]+", re.IGNORECASE)


@dataclass(frozen=True)
class TypedPurchase:
    """A typed purchase: "[3/10] [Store,] product [+ product ...] price"."""

    items: list[tuple[str, int]]  # (name, quantity)
    price: float                  # per unit for one product; the total when several share it
    store: str | None = None
    purchase_date: str | None = None  # ISO date when one was typed

    @property
    def shared(self) -> bool:
        """Whether the price is one total for several products."""
        return len(self.items) > 1


def _leading_date(line: str, today: date) -> tuple[str | None, str]:
    """Peel a leading purchase date off a line: (ISO date or None, the rest)."""
    if match := _YESTERDAY_RE.match(line):
        return (today - timedelta(days=1)).isoformat(), line[match.end():]
    match = _DATE_RE.match(line)
    if not match:
        return None, line
    day, month, year = int(match.group(1)), int(match.group(2)), match.group(3)
    try:
        if year:
            when = date(int(year) + (2000 if len(year) == 2 else 0), month, day)
        else:
            when = date(today.year, month, day)
            if when > today:  # "28/12" typed in January means last December
                when = date(today.year - 1, month, day)
    except ValueError:
        return None, line
    return (when.isoformat(), line[match.end():]) if when <= today else (None, line)


def parse_purchase(line: str, today: date) -> TypedPurchase | None:
    """Parse a typed purchase line, or return None if it has no price (a shopping-list line).

    Shapes, all optional but the product and price: a date first ("3/10",
    "yesterday"), then "Store," then one product — the price is per unit —
    or several joined by "+", where the price at the end is what they cost
    together ("Lidl, milk + bread + eggs 5,40").
    """
    purchase_date, rest = _leading_date(line.strip(), today)
    try:
        name, quantity, price = parse_line(rest)
    except ValueError:
        return None
    if price is None:
        return None
    store = None
    if "," in name:
        store, name = (part.strip() for part in name.split(",", 1))
    parts = [part.strip() for part in name.split("+")]
    items = []
    for i, part in enumerate(parts):
        try:
            part_name, part_qty, _ = parse_line(part)
        except ValueError:
            return None
        items.append((part_name, quantity if i == len(parts) - 1 and quantity != 1 else part_qty))
    return TypedPurchase(items=items, price=price, store=store or None, purchase_date=purchase_date)
