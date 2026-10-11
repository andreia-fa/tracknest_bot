"""A week-old list entry and a receipt left unconfirmed: each asked about once — on a real SQLite file."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot import main
from db import database, receipt_queue, settings, shopping_list


@pytest.fixture
def db(tmp_path):
    with patch.object(database, "DB_PATH", str(tmp_path / "test.db")):
        database.init_db()
        settings.set_chat_id(1)
        yield


def _context():
    context = MagicMock()
    context.bot.send_message = AsyncMock()
    return context


def _tap(data):
    update = MagicMock()
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.edit_message_reply_markup = AsyncMock()
    update.callback_query.message.reply_text = AsyncMock()
    return update


def _age(table, column, row_id, modifier):
    conn = database.get_connection()
    conn.execute(f"UPDATE {table} SET {column} = datetime('now', ?) WHERE id = ?", (modifier, row_id))
    conn.commit()
    conn.close()


def _list_entry(name, days_ago):
    shopping_list.add_item(name)
    entry = next(e for e in shopping_list.get_all_items() if e["name"] == name)
    _age("shopping_list_items", "added_at", entry["id"], f"-{days_ago} days")
    return entry["id"]


def _buttons(call):
    return [b.callback_data for row in call.kwargs["reply_markup"].inline_keyboard for b in row]


@pytest.mark.asyncio
async def test_only_a_week_old_entry_is_asked_about_and_only_once_a_week(db):
    _list_entry("milk", days_ago=3)
    socks = _list_entry("Socks - decathlon", days_ago=8)
    context = _context()

    assert await main.ask_stale_list_entry(context) is True
    call = context.bot.send_message.call_args
    assert call.kwargs["text"] == "Socks - decathlon has been on your list for a week — did you buy it?"
    assert _buttons(call) == [f"stale:bought:{socks}", f"stale:keep:{socks}", f"stale:drop:{socks}"]

    assert await main.ask_stale_list_entry(context) is False  # just asked; milk is only 3 days old


@pytest.mark.asyncio
async def test_bought_takes_it_off_and_says_so_in_the_history(db):
    socks = _list_entry("Socks - decathlon", days_ago=8)
    tap = _tap(f"stale:bought:{socks}")

    await main.handle_stale_entry(tap, _context())

    assert shopping_list.get_all_items() == []
    assert [(h["name"], h["reason"]) for h in shopping_list.get_history()] == [("Socks - decathlon", "bought")]
    assert tap.callback_query.edit_message_text.call_args.args[0] == (
        "🛒 Took Socks - decathlon off your list. To count what it cost, type it like: Store, Socks - decathlon price")


@pytest.mark.asyncio
async def test_still_need_it_keeps_the_entry(db):
    berries = _list_entry("Blue berries", days_ago=8)
    await main.ask_stale_list_entry(_context())

    await main.handle_stale_entry(_tap(f"stale:keep:{berries}"), _context())

    assert [e["name"] for e in shopping_list.get_all_items()] == ["Blue berries"]
    assert shopping_list.get_stale_entry(7) is None


def _waiting_receipt(hours_ago):
    receipt_id = receipt_queue.queue_receipt(1, "file")
    receipt_queue.hold_for_review(receipt_id, {
        "items": [{"name": "HEIDELBEEREN", "quantity": 2, "unit_price": 2.49}],
        "store": "REWE", "purchase_date": "2026-10-07", "total_paid": 4.98})
    _age("pending_receipts", "review_at", receipt_id, f"-{hours_ago} hours")
    return receipt_id


@pytest.mark.asyncio
async def test_a_receipt_waiting_a_day_is_nudged_once(db):
    _waiting_receipt(hours_ago=2)
    old = _waiting_receipt(hours_ago=30)
    context = _context()

    assert await main.nudge_waiting_receipt(context) is True
    call = context.bot.send_message.call_args
    assert call.kwargs["text"] == "🧾 REWE receipt from 7 Oct is still waiting for your ✅ — nothing from it is saved yet."
    assert _buttons(call) == [f"rcpt_show:{old}"]

    assert await main.nudge_waiting_receipt(context) is False  # once only; the other is too fresh


@pytest.mark.asyncio
async def test_show_it_again_resends_the_review(db):
    receipt_id = _waiting_receipt(hours_ago=30)
    tap = _tap(f"rcpt_show:{receipt_id}")

    await main.handle_receipt_show(tap, _context())

    text = tap.callback_query.message.reply_text.call_args.args[0]
    assert text.startswith("🧾 Receipt read (REWE, 7 Oct)") and "HEIDELBEEREN" in text


@pytest.mark.asyncio
async def test_a_saved_receipt_is_never_nudged(db):
    receipt_id = _waiting_receipt(hours_ago=30)
    receipt_queue.resolve_receipt(receipt_id)

    assert await main.nudge_waiting_receipt(_context()) is False
