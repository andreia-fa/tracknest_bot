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

    Accepts "<name>", "<name> <qty>", or "<name> <qty> <unit_price>", where qty is
    a whole number and unit_price accepts either '.' or ',' as the decimal separator
    (e.g. "Oat Milk 3 2,50"). Trailing tokens that don't match these shapes are
    treated as part of the name. Leading/trailing list-bullet punctuation (">",
    "*", "•", "--") is stripped first, so pasting a formatted list doesn't leak
    stray symbols into the stored item name.

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

    if unit_price is None and len(tokens) >= 2 and tokens[-1].isdigit():
        quantity = int(tokens[-1])
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
