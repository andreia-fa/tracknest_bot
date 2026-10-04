from datetime import date

import pytest

from bot.parser import TypedPurchase, parse_line, parse_purchase


def test_name_only():
    assert parse_line("Oat Milk") == ("Oat Milk", 1, None)


def test_name_and_quantity():
    assert parse_line("Oat Milk 3") == ("Oat Milk", 3, None)


def test_name_quantity_and_price():
    assert parse_line("Oat Milk 3 2.50") == ("Oat Milk", 3, 2.50)


def test_price_accepts_comma_decimal():
    assert parse_line("Oat Milk 3 2,50") == ("Oat Milk", 3, 2.50)


def test_name_with_trailing_non_numeric_word():
    assert parse_line("Alpro Oat Plus") == ("Alpro Oat Plus", 1, None)


def test_name_ending_in_digit_without_price():
    assert parse_line("7 Up") == ("7 Up", 1, None)


def test_name_ending_in_digit_with_quantity():
    assert parse_line("7 Up 5") == ("7 Up", 5, None)


def test_empty_line_raises():
    with pytest.raises(ValueError):
        parse_line("   ")


def test_qty_without_valid_price_keeps_tokens_as_name():
    assert parse_line("Milk 3 abc") == ("Milk 3 abc", 1, None)


def test_strips_leading_and_trailing_bullet_punctuation():
    assert parse_line("> -- Soutien branco --") == ("Soutien branco", 1, None)


def test_strips_leading_dash_bullet():
    # Only reachable here if handle_text didn't already treat the raw line
    # as a remove command (see the comment on _LEADING_BULLET_RE).
    assert parse_line("- bananas") == ("bananas", 1, None)


def test_strips_asterisk_and_bullet_point():
    assert parse_line("* Milk") == ("Milk", 1, None)
    assert parse_line("• Milk") == ("Milk", 1, None)


def test_name_and_price_with_implicit_quantity_one():
    assert parse_line("Matcha 2.50") == ("Matcha", 1, 2.50)


def test_name_and_price_accepts_comma_decimal():
    assert parse_line("Matcha 2,50") == ("Matcha", 1, 2.50)


def test_bare_integer_is_still_quantity_not_price():
    # No decimal separator -> always quantity, never a price, even though
    # "2" alone would also parse fine as a price.
    assert parse_line("Bananas 2") == ("Bananas", 2, None)


@pytest.mark.parametrize("line, expected", [
    ("Pfefferbretzel 1€", ("Pfefferbretzel", 1, 1.0)),
    ("Zimtschenecke 3.50€", ("Zimtschenecke", 1, 3.5)),
    ("Zimtschenecke 3,50 €", ("Zimtschenecke", 1, 3.5)),
    ("Matcha €6.90", ("Matcha", 1, 6.9)),
    ("Oat Milk 3 2,50€", ("Oat Milk", 3, 2.5)),
])
def test_euro_sign_marks_a_price(line, expected):
    assert parse_line(line) == expected


_TODAY = date(2026, 10, 4)


@pytest.mark.parametrize("line, expected", [
    ("sesame ring 1,49", TypedPurchase([("sesame ring", 1)], 1.49)),
    ("avec, sesame ring 1,49", TypedPurchase([("sesame ring", 1)], 1.49, store="avec")),
    ("Lidl, milk + bread + eggs 5,40", TypedPurchase([("milk", 1), ("bread", 1), ("eggs", 1)], 5.4, store="Lidl")),
    ("3/10 avec, sesame ring 1,49", TypedPurchase([("sesame ring", 1)], 1.49, "avec", "2026-10-03")),
    ("yesterday REWE, oat milk 2 1,99", TypedPurchase([("oat milk", 2)], 1.99, "REWE", "2026-10-03")),
    ("28/12 dm, soap 2,00", TypedPurchase([("soap", 1)], 2.0, "dm", "2025-12-28")),  # last December
    ("1/2/2026 dm, soap 2,00", TypedPurchase([("soap", 1)], 2.0, "dm", "2026-02-01")),
])
def test_typed_purchase_shapes(line, expected):
    assert parse_purchase(line, _TODAY) == expected


@pytest.mark.parametrize("line", ["milk", "milk + bread", "milk, bread", "Bananas 2", "31/02 x"])
def test_lines_without_a_price_are_not_purchases(line):
    assert parse_purchase(line, _TODAY) is None


def test_a_date_that_does_not_exist_stays_part_of_the_line():
    assert parse_purchase("31/02 soap 2,00", _TODAY).purchase_date is None
