from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot import main


@patch("bot.main.expenses")
@patch("bot.main.crud")
@patch("bot.main.shopping_list.remove_item", return_value=True)
def test_names_the_list_entry_when_receipt_wording_differs(_remove, mock_crud, mock_expenses):
    mock_crud.get_item.return_value = None
    mock_expenses.is_duplicate_purchase.return_value = False
    mock_expenses.get_price_delta.return_value = None

    line, _ = main._log_purchase("PUSH UP", 1, 35.90, matched_list_item="Soutien")

    assert "(cleared 'Soutien' from your list)" in line


@patch("bot.main.expenses")
@patch("bot.main.crud")
@patch("bot.main.shopping_list.remove_item", return_value=True)
def test_plain_note_when_names_match(_remove, mock_crud, mock_expenses):
    mock_crud.get_item.return_value = None
    mock_expenses.is_duplicate_purchase.return_value = False
    mock_expenses.get_price_delta.return_value = None

    line, _ = main._log_purchase("Milk", 1, 1.09, matched_list_item="milk")

    assert line.endswith("(cleared from your list)")


@patch("bot.main.crud.get_alias", return_value={"canonical_name": "Frozen mixed veg", "category": "Fruits/Veg"})
def test_known_receipt_wording_maps_to_the_users_name(_alias):
    item = {"name": "BIO aln.pfanne", "category": "Snack"}
    assert main._resolve_receipt_name(item) == ("Frozen mixed veg", "Fruits/Veg", None, False)


@patch("bot.main.crud.get_alias", return_value=None)
def test_keyword_category_beats_the_models_guess(_alias):
    item = {"name": "Käsescheiben", "category": "Snack"}
    assert main._resolve_receipt_name(item) == ("Käsescheiben", "Dairy", None, True)


@patch("bot.main.crud.get_alias", return_value=None)
def test_models_category_is_the_fallback(_alias):
    item = {"name": "BIO aln.pfanne", "category": "Fruits/Veg"}
    assert main._resolve_receipt_name(item) == ("BIO aln.pfanne", "Fruits/Veg", None, True)


@patch("bot.main.crud.get_alias", return_value=None)
def test_vat_code_is_never_taken_as_a_category(_alias):
    item = {"name": "VOLVIC NATURELLE", "category": "A"}
    assert main._resolve_receipt_name(item) == ("VOLVIC NATURELLE", None, None, True)


@patch("bot.main._log_purchase", return_value=("• line", None))
@patch("bot.main.crud.get_alias", return_value=None)
@patch("bot.main.shopping_list.get_all_items", return_value=[{"name": "Salmon"}, {"name": "morangos"}])
def test_receipt_uses_the_synonym_matcher_not_the_model_alone(_list, _alias, mock_log):
    import asyncio
    parsed = {
        "items": [
            {"name": "RAEUCHERLACHS", "quantity": 1, "unit_price": 4.29,
             "category": "Other", "matched_shopping_list_item": ""},
            {"name": "dmBio schoko. Himbeeren", "quantity": 1, "unit_price": 2.23,
             "category": "Snacks", "matched_shopping_list_item": "morangos"},
        ],
        "reconciled": True, "store": "REWE",
    }
    asyncio.run(main.process_receipt_result(parsed))
    matches = [call.kwargs["matched_list_item"] for call in mock_log.call_args_list]
    assert matches == ["Salmon", None]


@pytest.mark.asyncio
@patch("bot.main.send_pending_profile_question", new_callable=AsyncMock)
@patch("bot.main.shopping_list")
@patch("bot.main.crud")
async def test_naming_a_receipt_item_clears_its_list_entry(mock_crud, mock_list, _next_question):
    # "PUSH UP" can't be matched to "Soutien branco" until the user says it's a bra.
    mock_crud.get_item.return_value = {"category": None}
    mock_list.get_all_items.return_value = [{"name": "Soutien branco"}, {"name": "Salmon"}]
    mock_list.remove_item.return_value = 42
    bot = MagicMock()
    bot.send_message = AsyncMock()

    await main._confirm_product(bot, 1, "PUSH UP", "bra")

    mock_crud.set_item_product.assert_called_once_with("PUSH UP", "bra")
    mock_crud.set_item_category.assert_called_once_with("PUSH UP", "Clothing")
    mock_crud.save_alias.assert_called_once_with("PUSH UP", "PUSH UP", "Clothing", "bra")
    mock_list.remove_item.assert_called_once_with("Soutien branco", reason="receipt", source="PUSH UP")
    texts = [c.args[1] for c in bot.send_message.call_args_list]
    assert "Cleared 'Soutien branco' from your shopping list." in texts


@pytest.mark.asyncio
@patch("bot.main.send_pending_profile_question", new_callable=AsyncMock)
@patch("bot.main.shopping_list")
@patch("bot.main.crud")
async def test_naming_leaves_unrelated_list_entries_alone(mock_crud, mock_list, _next_question):
    mock_crud.get_item.return_value = {"category": "Fruits/Veg"}
    mock_list.get_all_items.return_value = [{"name": "Salmon"}]
    bot = MagicMock()
    bot.send_message = AsyncMock()

    await main._confirm_product(bot, 1, "BIO aln.pfanne", "frozen veg")

    mock_list.remove_item.assert_not_called()


@pytest.mark.asyncio
@patch("bot.main.crud")
async def test_new_receipt_item_question_offers_the_models_guess(mock_crud):
    mock_crud.get_pending_profile_item.return_value = ("LEERDAMMER CAR", "name")
    mock_crud.get_item.return_value = {"product": "cheese"}
    bot = MagicMock()
    bot.send_message = AsyncMock()

    await main.send_pending_profile_question(bot, 1)

    text = bot.send_message.call_args.args[1]
    markup = bot.send_message.call_args.kwargs["reply_markup"]
    assert "I think it's cheese" in text
    assert markup.inline_keyboard[0][0].text == "✓ Yes, cheese"


@pytest.mark.asyncio
@patch("bot.main._confirm_product", new_callable=AsyncMock)
@patch("bot.main.crud")
async def test_confirming_the_guess_saves_it(mock_crud, mock_confirm):
    mock_crud.get_pending_profile_item.return_value = ("LEERDAMMER CAR", "name")
    mock_crud.get_item.return_value = {"product": "cheese"}
    update = MagicMock()
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()

    await main.handle_product_ok(update, MagicMock())

    assert mock_confirm.call_args.args[2:] == ("LEERDAMMER CAR", "cheese")


@patch("bot.main.crud")
def test_models_product_is_kept_for_a_brand_only_line(mock_crud):
    mock_crud.get_alias.return_value = None
    item = {"name": "LEERDAMMER CAR", "category": "Dairy", "product": "cheese"}
    assert main._resolve_receipt_name(item)[2] == "cheese"


@patch("bot.main.crud")
def test_alias_product_beats_the_models_guess(mock_crud):
    mock_crud.get_alias.return_value = {"canonical_name": "Leerdammer", "category": "Dairy", "product": "cheese"}
    item = {"name": "LEERDAMMER CAR", "category": "Dairy", "product": "ham"}
    assert main._resolve_receipt_name(item) == ("Leerdammer", "Dairy", "cheese", False)


@patch("bot.main.expenses")
@patch("bot.main.crud")
@patch("bot.main.shopping_list.remove_item", return_value=None)
def test_purchase_line_shows_what_the_item_is(_remove, mock_crud, mock_expenses):
    mock_expenses.is_duplicate_purchase.return_value = False
    mock_expenses.get_price_delta.return_value = None
    mock_crud.get_item.return_value = {"product": "cheese"}

    line, _ = main._log_purchase("LEERDAMMER CAR", 1, 2.99, product="cheese")

    assert line.startswith("• 1x LEERDAMMER CAR (cheese) at")
    assert mock_crud.add_item.call_args.kwargs["product"] == "cheese"
