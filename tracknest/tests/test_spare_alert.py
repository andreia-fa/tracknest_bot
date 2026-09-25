from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot import main

NOW = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)
CHEESE = {"id": 4, "name": "Leerdammer", "category": "Dairy", "shelf_life_days": 20}


def _candidate(days_ago, quantity=1, shelf_life_days=20):
    return {
        "id": 4, "name": "Leerdammer", "category": "Dairy", "shelf_life_days": shelf_life_days,
        "last_purchase": (NOW - timedelta(days=days_ago)).isoformat(), "last_quantity": quantity,
    }


def _callback_update(data):
    update = MagicMock()
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


def _buttons(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def test_alert_waits_for_spares_already_bought():
    last = NOW - timedelta(days=30)
    assert main._spare_alert_at(last, 20, 1) == last + timedelta(days=17)
    assert main._spare_alert_at(last, 20, 2) == last + timedelta(days=17)
    assert main._spare_alert_at(last, 20, 3) == last + timedelta(days=37)


def test_alert_text_shows_its_reasoning():
    text = main._spare_alert_text(_candidate(days_ago=18), NOW)
    assert "Leerdammer (Dairy)" in text
    assert "18 days ago" in text
    assert "20 days" in text


@pytest.mark.asyncio
@patch("bot.main.datetime")
@patch("bot.main.settings")
@patch("bot.main.crud")
async def test_no_alert_right_after_buying(mock_crud, mock_settings, mock_datetime):
    mock_datetime.now.return_value = NOW
    mock_datetime.fromisoformat = datetime.fromisoformat
    mock_settings.get_chat_id.return_value = 1
    mock_crud.get_par_alert_candidates.return_value = [_candidate(days_ago=3)]
    context = MagicMock()
    context.bot.send_message = AsyncMock()

    await main.check_spare_stock_alerts(context)

    context.bot.send_message.assert_not_called()


@pytest.mark.asyncio
@patch("bot.main.datetime")
@patch("bot.main.settings")
@patch("bot.main.crud")
async def test_alert_near_run_out_comes_with_buttons(mock_crud, mock_settings, mock_datetime):
    mock_datetime.now.return_value = NOW
    mock_datetime.fromisoformat = datetime.fromisoformat
    mock_settings.get_chat_id.return_value = 1
    mock_crud.get_par_alert_candidates.return_value = [_candidate(days_ago=18)]
    context = MagicMock()
    context.bot.send_message = AsyncMock()

    await main.check_spare_stock_alerts(context)

    mock_crud.mark_spare_alert_pending.assert_called_once_with("Leerdammer")
    markup = context.bot.send_message.call_args.kwargs["reply_markup"]
    assert _buttons(markup) == ["spare_add:4", "spare_plenty:4", "shelf_edit:4", "spare_stop:4"]


@pytest.mark.asyncio
@patch("bot.main.shopping_list")
@patch("bot.main.crud")
async def test_add_to_list(mock_crud, mock_list):
    mock_crud.get_item_by_id.return_value = CHEESE
    await main.handle_spare_alert_choice(_callback_update("spare_add:4"), MagicMock())
    mock_list.add_item.assert_called_once_with("Leerdammer", 1, category="Dairy")


@pytest.mark.asyncio
@patch("bot.main.crud")
async def test_still_have_plenty_stretches_the_estimate(mock_crud):
    mock_crud.get_item_by_id.return_value = CHEESE
    await main.handle_spare_alert_choice(_callback_update("spare_plenty:4"), MagicMock())
    mock_crud.bump_shelf_life.assert_called_once_with("Leerdammer", 5)
    mock_crud.mark_spare_alert_pending.assert_called_once_with("Leerdammer", pending=False)


@pytest.mark.asyncio
@patch("bot.main.crud")
async def test_stop_switches_item_to_replace_when_low(mock_crud):
    mock_crud.get_item_by_id.return_value = CHEESE
    await main.handle_spare_alert_choice(_callback_update("spare_stop:4"), MagicMock())
    mock_crud.set_par_level.assert_called_once_with("Leerdammer", 1)
