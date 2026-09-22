import pytest

from bot.parser import parse_line


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
