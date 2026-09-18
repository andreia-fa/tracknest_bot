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
