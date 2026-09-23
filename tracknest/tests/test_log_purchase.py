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


@patch("bot.main.crud.get_alias", return_value={"canonical_name": "Frozen mixed veg", "category": "Fruits/Veg"})
def test_known_receipt_wording_maps_to_the_users_name(_alias):
    item = {"name": "BIO aln.pfanne", "category": "Snack"}
    assert main._resolve_receipt_name(item) == ("Frozen mixed veg", "Fruits/Veg", False)


@patch("bot.main.crud.get_alias", return_value=None)
def test_keyword_category_beats_the_models_guess(_alias):
    item = {"name": "Käsescheiben", "category": "Snack"}
    assert main._resolve_receipt_name(item) == ("Käsescheiben", "Dairy", True)


@patch("bot.main.crud.get_alias", return_value=None)
def test_models_category_is_the_fallback(_alias):
    item = {"name": "BIO aln.pfanne", "category": "Frozen"}
    assert main._resolve_receipt_name(item) == ("BIO aln.pfanne", "Frozen", True)
