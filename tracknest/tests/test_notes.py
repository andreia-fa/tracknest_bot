"""Item notes (e.g. a bra size to buy again), on a real SQLite file."""

import sqlite3
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot import main
from db import crud, database, shopping_list


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "test.db"
    with patch.object(database, "DB_PATH", str(path)):
        database.init_db()
        crud.add_item("Push Up Bra", 1, category="Clothing", product="bra")
        yield path


def _context():
    context = MagicMock()
    context.chat_data = {}
    context.args = []
    context.bot.send_message = AsyncMock()
    return context


def _text(text):
    update = MagicMock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    return update


def _tap(data):
    update = MagicMock()
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.get_bot.return_value.send_message = AsyncMock()
    return update


def test_blank_notes_are_refused_and_cleared_to_none(db):
    conn = sqlite3.connect(db, isolation_level=None)  # autocommit: a refused write leaves no lock
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE inventory_items SET notes = '   '")
    conn.close()
    crud.set_item_note("Push Up Bra", "UK/USA 34B")
    crud.set_item_note("push up bra", "  ")
    assert crud.get_item("Push Up Bra")["notes"] is None


@pytest.mark.asyncio
async def test_note_command_saves_only_after_confirmation(db):
    context = _context()
    context.args = ["push", "up", "bra"]
    await main.note_cmd(_text("/note push up bra"), context)

    await main.handle_text(prompt := _text("UK/USA 34B"), context)
    assert "note for Push Up Bra" in prompt.message.reply_text.call_args.args[0]
    assert crud.get_item("Push Up Bra")["notes"] is None  # not before the tap

    await main.handle_typed_choice(_tap("typed:answer:1"), context)
    assert crud.get_item("Push Up Bra")["notes"] == "UK/USA 34B"


@pytest.mark.asyncio
async def test_note_typed_with_a_purchase(db):
    context = _context()
    await main.handle_text(prompt := _text("Push Up Bra 35.90 // UK/USA 34B"), context)
    assert "📝 new note: UK/USA 34B" in prompt.message.reply_text.call_args.args[0]

    await main.handle_typed_choice(_tap("typed:list:1"), context)
    assert crud.get_item("Push Up Bra")["notes"] == "UK/USA 34B"


@pytest.mark.asyncio
async def test_list_and_reminders_show_the_note(db):
    crud.set_item_note("Push Up Bra", "UK/USA 34B")
    shopping_list.add_item("push up bra", 1, category="Clothing")
    update = _text("/list")

    await main.show_shopping_list(update, _context())

    assert "• push up bra (1x)\n📝 UK/USA 34B" in update.message.reply_text.call_args.args[0]
    item = {"name": "Push Up Bra", "product": "bra", "shelf_life_days": 120, "last_quantity": 1,
            "last_purchase": "2026-09-23T17:32:26+00:00", "last_store": "Intimissimi", "notes": "UK/USA 34B"}
    text = main._spare_alert_text(item, datetime(2026, 10, 3, tzinfo=timezone.utc))
    assert "at Intimissimi" in text and text.endswith("📝 UK/USA 34B")
