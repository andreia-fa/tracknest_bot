from unittest.mock import patch

from bot import main


@patch("bot.main.expenses")
@patch("bot.main.crud")
@patch("bot.main.shopping_list.remove_item", return_value=True)
def test_names_the_list_entry_when_receipt_wording_differs(_remove, _crud, mock_expenses):
    mock_expenses.is_duplicate_purchase.return_value = False
    mock_expenses.get_price_delta.return_value = None

    line = main._log_purchase("PUSH UP", 1, 35.90, matched_list_item="Soutien")

    assert "(cleared 'Soutien' from your list)" in line


@patch("bot.main.expenses")
@patch("bot.main.crud")
@patch("bot.main.shopping_list.remove_item", return_value=True)
def test_plain_note_when_names_match(_remove, _crud, mock_expenses):
    mock_expenses.is_duplicate_purchase.return_value = False
    mock_expenses.get_price_delta.return_value = None

    line = main._log_purchase("Milk", 1, 1.09, matched_list_item="milk")

    assert line.endswith("(cleared from your list)")
