"""Parsing for plain-text inventory entries (no slash commands)."""

import re

# bot/main.py's handle_text checks the ORIGINAL, unmodified line for a
# leading "-" (its remove-command trigger) before parse_line ever runs, so
# stripping a leading "-" in here too is safe — it only affects what's left
# over after a different bullet char (e.g. ">") has already been peeled off
# (e.g. "> -- Soutien branco --" -> "-- Soutien branco --" -> "Soutien branco").
_LEADING_BULLET_RE = re.compile(r"^[\s\->*•]+")
_TRAILING_BULLET_RE = re.compile(r"[\s\-*•]+$")


def parse_line(line: str) -> tuple[str, int, float | None]:
    """Parse one line of free text into an item name, quantity, and optional unit price.

    Accepts "<name>", "<name> <qty>", "<name> <price>", or "<name> <qty>
    <price>", where qty is a whole number and price accepts either '.' or
    ',' as the decimal separator (e.g. "Oat Milk 3 2,50" or "Matcha 2.50").
    A price is only recognized when its token contains a decimal separator
    — a bare integer is always read as quantity, never price, so "Bananas
    2" still means two bananas, not €2. Trailing tokens that don't match
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
