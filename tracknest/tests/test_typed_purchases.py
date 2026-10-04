"""Typed purchases with a store, a date and a shared total — on a real SQLite file."""

import sqlite3
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot import main
from db import crud, database, expenses


@pytest.fixture
def db(tmp_path):
    with patch.object(database, "DB_PATH", str(tmp_path / "test.db")):
        database.init_db()
        yield


def _context():
    context = MagicMock()
    context.chat_data = {}
    context.bot.send_message = AsyncMock()
    return context


def _text(text, edited=False):
    update = MagicMock()
    message = MagicMock()
    message.text = text
    message.reply_text = AsyncMock()
    update.message = None if edited else message
    update.edited_message = message
    return update, message


def _tap(data):
    update = MagicMock()
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


async def _send_and_confirm(text, context, edited=False):
    update, message = _text(text, edited)
    await main.handle_text(update, context)
    tap = _tap(f"typed:list:{context.chat_data['typed']['token']}")
    with patch("bot.main.send_pending_profile_question", AsyncMock()):
        await main.handle_typed_choice(tap, context)
    return message.reply_text.call_args.args[0], tap.callback_query.edit_message_text.call_args.args[0]


def _rows():
    conn = database.get_connection()
    rows = [dict(r) for r in conn.execute("""
        SELECT i.name, e.quantity_purchased, e.unit_price, e.store, e.purchase_date, e.trip_key, e.price_kind
        FROM item_expenses e JOIN inventory_items i ON i.id = e.item_id ORDER BY e.id""")]
    conn.close()
    return rows


@pytest.mark.asyncio
async def test_shared_total_is_split_and_kept_out_of_price_comparisons(db):
    crud.add_item("milk", 0)
    expenses.log_expense("milk", 1, 1.09, store="Lidl")  # a real price, from a receipt

    preview, reply = await _send_and_confirm("lidl, milk + bread + eggs 5,40", _context())

    assert preview.startswith("Just to be sure, I'll:\n• log a purchase at Lidl: milk, bread, eggs — €5.40 together")
    shared = _rows()[1:]
    assert [(r["name"], r["unit_price"], r["store"], r["price_kind"]) for r in shared] == [
        ("milk", 1.8, "Lidl", "share"), ("bread", 1.8, "Lidl", "share"), ("eggs", 1.8, "Lidl", "share")]
    assert len({r["trip_key"] for r in shared}) == 1  # one trip
    assert "• 1x milk — share of €5.40 at Lidl" in reply
    # The 1.80 share is no price: milk's price history is still just 1.09.
    assert expenses.get_price_delta("milk", 1.09) == {"avg_price": 1.09, "pct_change": 0.0, "n": 1}
    assert expenses.guess_store("bread", None, 1.8) is None


@pytest.mark.asyncio
async def test_store_and_date_typed_first_and_an_edited_message_works(db):
    day = datetime.now(tz=main._LOCAL_TZ).date() - timedelta(days=1)
    preview, _reply = await _send_and_confirm(f"{day.day}/{day.month} avec, sesame ring 1,49", _context(), edited=True)

    assert f"on {day.strftime('%-d %b')}: 1x sesame ring at €1.49 each at avec" in preview
    row, = _rows()
    assert (row["store"], row["purchase_date"], row["price_kind"]) == ("avec", day.isoformat(), "unit")


def test_shares_add_up_to_the_total():
    assert main._shares(5.40, 3) == [1.8, 1.8, 1.8]
    assert main._shares(1.00, 3) == [0.33, 0.33, 0.34]


def test_price_kind_only_takes_its_two_values(db):
    crud.add_item("milk", 0)
    conn = database.get_connection()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO item_expenses (item_id, unit_price, price_kind) VALUES (1, 1.0, 'guess')")
    conn.close()
