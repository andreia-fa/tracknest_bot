from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot import main

NOW = datetime(2026, 10, 3, 16, tzinfo=timezone.utc)


def _due_checkin(name):
    return {
        "id": 1, "name": name, "product": None, "shelf_life_days": 3,
        "last_purchase": (NOW - timedelta(days=10)).isoformat(),
    }


def _context():
    context = MagicMock()
    context.bot.send_message = AsyncMock()
    return context


@pytest.mark.asyncio
@patch("bot.main.datetime")
@patch("bot.main.settings")
@patch("bot.main.crud")
async def test_checkins_go_out_one_per_call(mock_crud, mock_settings, mock_datetime):
    mock_datetime.now.return_value = NOW
    mock_datetime.fromisoformat = datetime.fromisoformat
    mock_settings.get_chat_id.return_value = 1
    mock_crud.get_checkin_candidates.return_value = [_due_checkin("BANANE"), _due_checkin("EIER")]
    context = _context()

    assert await main.check_expiring_items(context) is True

    context.bot.send_message.assert_awaited_once()
    mock_crud.mark_checkin_pending.assert_called_once_with("BANANE")


@pytest.mark.asyncio
@patch("bot.main.check_spare_stock_alerts", new_callable=AsyncMock)
@patch("bot.main.check_expiring_items", new_callable=AsyncMock)
@patch("bot.main.check_budget_alert", new_callable=AsyncMock)
@patch("bot.main.remind_pending_profile", new_callable=AsyncMock)
async def test_round_stops_after_first_message(remind, budget, checkin, spare):
    budget.return_value = False
    checkin.return_value = True

    await main.proactive_round(_context())

    budget.assert_awaited_once()
    checkin.assert_awaited_once()
    spare.assert_not_awaited()
    remind.assert_not_awaited()


@pytest.mark.asyncio
@patch("bot.main.send_pending_profile_question", new_callable=AsyncMock)
@patch("bot.main.datetime")
@patch("bot.main.settings")
@patch("bot.main.crud")
async def test_profile_reminder_once_a_day(mock_crud, mock_settings, mock_datetime, send_question):
    mock_datetime.now.return_value = NOW
    mock_settings.get_chat_id.return_value = 1
    mock_crud.get_pending_profile_item.return_value = ("BANANE", "purchase_type")
    mock_settings.get_profile_reminded_on.return_value = NOW.date().isoformat()

    assert await main.remind_pending_profile(_context()) is False
    send_question.assert_not_awaited()

    mock_settings.get_profile_reminded_on.return_value = "2026-10-02"
    assert await main.remind_pending_profile(_context()) is True
    send_question.assert_awaited_once()
    mock_settings.set_profile_reminded_on.assert_called_once_with(NOW.date().isoformat())


@pytest.mark.asyncio
@patch("bot.main.settings")
@patch("bot.main.crud")
async def test_open_question_is_not_sent_again(mock_crud, mock_settings):
    """Four receipts in a row used to send the same unanswered question four times."""
    mock_crud.get_pending_profile_item.return_value = ("X01", "treat_or_need")
    mock_settings.get_open_question.return_value = None
    bot = MagicMock()
    bot.send_message = AsyncMock()

    await main.send_pending_profile_question(bot, 1)
    mock_settings.get_open_question.return_value = "X01|treat_or_need"
    await main.send_pending_profile_question(bot, 1)
    await main.send_pending_profile_question(bot, 1)
    assert bot.send_message.await_count == 1

    await main.send_pending_profile_question(bot, 1, force=True)  # the daily reminder
    assert bot.send_message.await_count == 2
    mock_crud.get_pending_profile_item.return_value = ("BANANE", "lasts")  # answered -> next one goes out
    await main.send_pending_profile_question(bot, 1)
    assert bot.send_message.await_count == 3
