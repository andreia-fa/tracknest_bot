from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot import main

PRAWNS = {"id": 7, "name": "Berida Garnele"}


def _callback_update(data):
    update = MagicMock()
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


def _text_update(text):
    update = MagicMock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    return update


def _buttons(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def test_shelf_keyboard_offers_long_lasting_choices():
    assert {"profile_shelf:30", "profile_shelf:90"} <= set(_buttons(main._PROFILE_SHELF_KEYBOARD))


@pytest.mark.asyncio
@patch("bot.main.crud")
async def test_answer_comes_back_with_a_change_button(mock_crud):
    mock_crud.get_pending_profile_item.side_effect = [("Berida Garnele", "shelf_life"), None]
    mock_crud.get_item.return_value = PRAWNS
    update = _callback_update("profile_shelf:1")

    await main.handle_profile_shelf_choice(update, MagicMock())

    mock_crud.set_profile.assert_called_once_with("Berida Garnele", shelf_life_days=1)
    kwargs = update.callback_query.edit_message_text.call_args.kwargs
    assert _buttons(kwargs["reply_markup"]) == ["shelf_edit:7"]


@pytest.mark.asyncio
@patch("bot.main.crud")
async def test_change_offers_the_choices_for_that_item(mock_crud):
    mock_crud.get_item_by_id.return_value = PRAWNS
    update = _callback_update("shelf_edit:7")

    await main.handle_shelf_edit(update, MagicMock())

    kwargs = update.callback_query.edit_message_text.call_args.kwargs
    assert "shelf_set:7:90" in _buttons(kwargs["reply_markup"])


@pytest.mark.asyncio
@patch("bot.main.crud")
async def test_changed_answer_is_saved_and_alerts_restart(mock_crud):
    mock_crud.get_item_by_id.return_value = PRAWNS
    update = _callback_update("shelf_set:7:90")

    await main.handle_shelf_set(update, MagicMock())

    mock_crud.set_profile.assert_called_once_with("Berida Garnele", shelf_life_days=90)
    mock_crud.mark_spare_alert_pending.assert_called_once_with("Berida Garnele", pending=False)
    mock_crud.mark_checkin_pending.assert_called_once_with("Berida Garnele", pending=False)
    assert "90 day(s)" in update.callback_query.edit_message_text.call_args.args[0]


@pytest.mark.asyncio
@patch("bot.main.crud")
async def test_changed_answer_can_be_typed(mock_crud):
    mock_crud.get_item_by_id.return_value = PRAWNS
    context = MagicMock()
    context.chat_data = {}

    await main.handle_shelf_set(_callback_update("shelf_set:7:custom"), context)
    update = _text_update("45")
    await main.handle_text(update, context)

    mock_crud.set_profile.assert_called_once_with("Berida Garnele", shelf_life_days=45)
    assert "shelf_edit_item" not in context.chat_data
    assert "45 day(s)" in update.message.reply_text.call_args.args[0]


@pytest.mark.asyncio
@patch("bot.main.crud")
async def test_change_on_a_deleted_item(mock_crud):
    mock_crud.get_item_by_id.return_value = None
    update = _callback_update("shelf_set:7:90")

    await main.handle_shelf_set(update, MagicMock())

    mock_crud.set_profile.assert_not_called()
    update.callback_query.edit_message_text.assert_called_once_with("That item no longer exists.")
