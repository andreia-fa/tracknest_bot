from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot import main


def _text_update(text):
    update = MagicMock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    return update


def _callback_update(data):
    update = MagicMock()
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.get_bot.return_value.send_message = AsyncMock()
    return update


def _context():
    context = MagicMock()
    context.chat_data = {}
    context.bot.send_message = AsyncMock()
    return context


def _buttons(update):
    markup = update.message.reply_text.call_args.kwargs["reply_markup"]
    return [b.callback_data for row in markup.inline_keyboard for b in row]


@pytest.mark.asyncio
@patch("bot.main.shopping_list")
@patch("bot.main.crud")
async def test_list_lines_wait_for_confirmation(mock_crud, mock_list):
    mock_crud.get_pending_profile_item.return_value = None
    mock_crud.get_pending_checkin_item.return_value = None
    context = _context()
    update = _text_update("milk 2\n- bananas")

    await main.handle_text(update, context)

    mock_list.add_item.assert_not_called()
    mock_list.remove_item.assert_not_called()
    prompt = update.message.reply_text.call_args.args[0]
    assert "add 2x milk" in prompt and "remove bananas" in prompt
    assert _buttons(update) == ["typed:list:1", "typed:cancel:1"]

    await main.handle_typed_choice(_callback_update("typed:list:1"), context)

    mock_list.add_item.assert_called_once()
    mock_list.remove_item.assert_called_once_with("bananas", reason="manual")
    # The updated list follows right away, without needing /list.
    context.bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
@patch("bot.main.expenses.names_bought_at", new=lambda price: set())
@patch("bot.main._typed_purchase_store", return_value=None)
@patch("bot.main.shopping_list")
@patch("bot.main.crud")
async def test_purchase_only_does_not_resend_the_list(mock_crud, mock_list, _store):
    mock_crud.get_pending_profile_item.return_value = None
    mock_crud.get_pending_checkin_item.return_value = None
    context = _context()
    await main.handle_text(_text_update("matcha 2.50"), context)

    with patch("bot.main._log_purchase", return_value=("Logged matcha", None)), \
            patch("bot.main.send_pending_profile_question", AsyncMock()):
        await main.handle_typed_choice(_callback_update("typed:list:1"), context)

    context.bot.send_message.assert_not_called()


@pytest.mark.asyncio
@patch("bot.main.shopping_list")
@patch("bot.main.crud")
async def test_cancel_writes_nothing(mock_crud, mock_list):
    mock_crud.get_pending_profile_item.return_value = None
    mock_crud.get_pending_checkin_item.return_value = None
    context = _context()
    await main.handle_text(_text_update("milk"), context)
    cancel = _callback_update("typed:cancel:1")

    await main.handle_typed_choice(cancel, context)

    mock_list.add_item.assert_not_called()
    cancel.callback_query.edit_message_text.assert_called_once_with("Cancelled — nothing saved.")


@pytest.mark.asyncio
@patch("bot.main.shopping_list")
@patch("bot.main.crud")
async def test_open_question_offers_both_meanings(mock_crud, mock_list):
    mock_crud.get_pending_profile_item.return_value = ("HAPPY CALIF. VEG", "name")
    context = _context()
    update = _text_update("bananas")

    await main.handle_text(update, context)

    assert 'what "HAPPY CALIF. VEG" is' in update.message.reply_text.call_args.args[0]
    assert _buttons(update) == ["typed:answer:1", "typed:list:1", "typed:cancel:1"]

    await main.handle_typed_choice(_callback_update("typed:list:1"), context)

    mock_crud.set_item_product.assert_not_called()
    mock_list.add_item.assert_called_once()


@pytest.mark.asyncio
@patch("bot.main._confirm_product", new_callable=AsyncMock)
@patch("bot.main.crud")
@patch("bot.main.shopping_list.get_all_items", new=lambda: [])
async def test_answer_is_used_only_after_the_tap(mock_crud, confirm_product):
    mock_crud.get_pending_profile_item.return_value = ("HAPPY CALIF. VEG", "name")
    context = _context()
    await main.handle_text(_text_update("sushi"), context)
    confirm_product.assert_not_awaited()

    await main.handle_typed_choice(_callback_update("typed:answer:1"), context)

    assert confirm_product.call_args.args[2:] == ("HAPPY CALIF. VEG", "sushi")


@pytest.mark.asyncio
@patch("bot.main._confirm_product", new_callable=AsyncMock)
@patch("bot.main.crud")
@patch("bot.main.shopping_list.get_all_items", new=lambda: [])
async def test_answer_refused_if_the_question_changed(mock_crud, confirm_product):
    mock_crud.get_pending_profile_item.return_value = ("HAPPY CALIF. VEG", "name")
    context = _context()
    await main.handle_text(_text_update("sushi"), context)
    mock_crud.get_pending_profile_item.return_value = ("BANANE", "name")
    tap = _callback_update("typed:answer:1")

    await main.handle_typed_choice(tap, context)

    confirm_product.assert_not_awaited()
    assert "changed" in tap.callback_query.edit_message_text.call_args.args[0]


@pytest.mark.asyncio
@patch("bot.main.shopping_list")
@patch("bot.main.crud")
async def test_only_the_latest_message_can_be_confirmed(mock_crud, mock_list):
    mock_crud.get_pending_profile_item.return_value = None
    mock_crud.get_pending_checkin_item.return_value = None
    context = _context()
    await main.handle_text(_text_update("milk"), context)
    await main.handle_text(_text_update("eggs"), context)
    stale = _callback_update("typed:list:1")

    await main.handle_typed_choice(stale, context)

    mock_list.add_item.assert_not_called()
    assert "expired" in stale.callback_query.edit_message_text.call_args.args[0]


@pytest.mark.asyncio
@patch("bot.main.crud")
@patch("bot.main.shopping_list.get_all_items", new=lambda: [])
async def test_category_question_never_takes_text(mock_crud):
    mock_crud.get_pending_profile_item.return_value = ("BANANE", "category")
    mock_crud.get_pending_checkin_item.return_value = None
    update = _text_update("milk")

    await main.handle_text(update, _context())

    assert _buttons(update) == ["typed:list:1", "typed:cancel:1"]


@pytest.mark.asyncio
@patch("bot.main.shopping_list")
@patch("bot.main.crud")
@patch("bot.main.expenses")
async def test_a_priced_line_is_never_taken_as_an_answer(mock_expenses, mock_crud, mock_list):
    # "sesame ring 1,49" while "what is SCHLAGCREME VEGA?" was open was offered as its answer.
    mock_crud.get_pending_profile_item.return_value = ("SCHLAGCREME VEGA", "name")
    mock_expenses.guess_store.return_value = None
    for text in ("sesame ring 1,49", "milk\nbread"):
        update = _text_update(text)
        await main.handle_text(update, _context())
        prompt = update.message.reply_text.call_args.args[0]
        assert "SCHLAGCREME" not in prompt and prompt.startswith("Just to be sure, I'll:")
        assert _buttons(update)[0] == "typed:list:1"


@pytest.mark.asyncio
async def test_typed_purchase_can_be_logged_for_yesterday(tmp_path):
    from datetime import datetime, timedelta
    from db import crud, database, expenses
    with patch.object(database, "DB_PATH", str(tmp_path / "test.db")):
        database.init_db()
        context = _context()
        update = _text_update("sesame ring 1,49")
        await main.handle_text(update, context)
        assert _buttons(update) == ["typed:list:1", "typed:yesterday:1", "typed:cancel:1"]

        tap = _callback_update("typed:yesterday:1")
        with patch("bot.main.send_pending_profile_question", AsyncMock()):
            await main.handle_typed_choice(tap, context)

        yesterday = (datetime.now(tz=main._LOCAL_TZ).date() - timedelta(days=1)).isoformat()
        assert expenses.get_expenses("sesame ring")[0]["purchase_date"] == yesterday
        assert crud.get_item("sesame ring") is not None
        assert tap.callback_query.edit_message_text.call_args.args[0].startswith("📅 Logged for yesterday")
