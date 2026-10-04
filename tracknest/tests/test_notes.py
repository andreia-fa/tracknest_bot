"""Item notes (e.g. a bra size to buy again), on a real SQLite file."""

import sqlite3
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
async def test_list_shows_the_note(db):
    crud.set_item_note("Push Up Bra", "UK/USA 34B")
    shopping_list.add_item("push up bra", 1, category="Clothing")
    update = _text("/list")

    await main.show_shopping_list(update, _context())

    assert "• push up bra (1x)\n  📝 Push Up Bra — UK/USA 34B" in update.message.reply_text.call_args.args[0]


@pytest.mark.asyncio
async def test_buying_a_noted_item_again_shows_the_note(db):
    crud.set_item_note("Push Up Bra", "Don't buy again — very gassy")
    line, _ = main._log_purchase("Push Up Bra", 1, 35.90, store="Intimissimi")
    assert "📝 Don't buy again — very gassy" in line


def _cheeses():
    for name, product, note, lasts in (
        ("Milram Käse Scheiben", "sliced cheese", "⭐ Favourite", ("days", 7)),
        ("LEERDAMMER CAR.", "cheese", "⭐ Second favourite", ("days", 7)),
        ("J.Tag Käseaufschnitt", "cheese mix", "🚫 Don't buy again — very gassy", ("one_off", None)),
        ("J.Tag Emmental", "cheese", "🚫 Don't buy again — very gassy", ("one_off", None)),
    ):
        crud.add_item(name, 0, category="Dairy", product=product)
        crud.set_treat_or_need(name, "need")
        crud.set_lasts(name, *lasts)
        crud.set_item_note(name, note)


@pytest.mark.asyncio
async def test_cheese_on_the_list_brings_up_every_cheese_note(db):
    _cheeses()
    crud.set_treat_or_need("Push Up Bra", "need")  # no open question, so "Käse" is plainly a list line
    crud.set_lasts("Push Up Bra", "days", 120)
    context = _context()
    await main.handle_text(prompt := _text("Käse"), context)
    text = prompt.message.reply_text.call_args.args[0]
    for line in ("⭐ Milram Käse Scheiben — ⭐ Favourite", "⭐ LEERDAMMER CAR. — ⭐ Second favourite",
                 "J.Tag Käseaufschnitt — 🚫 Don't buy again", "J.Tag Emmental — 🚫 Don't buy again"):
        assert line.split(" — ")[0].lstrip("⭐ ") in text and line.split(" — ")[1] in text, line
    assert "Push Up Bra" not in text  # notes of other kinds of things stay out
    order = [text.index(n) for n in ("Milram", "LEERDAMMER", "J.Tag Käseaufschnitt")]
    assert order == sorted(order)  # favourite, second favourite, then don't-buys


def test_a_dont_buy_cheese_never_lends_its_no_reminders_profile(db):
    _cheeses()
    crud.add_item("Gouda jung", 1, category="Dairy", product="cheese")
    crud.copy_product_profile("Gouda jung", "cheese")
    assert crud.get_item("Gouda jung")["lasts"] == "days"  # from Leerdammer, not one-off from J.Tag Emmental


@pytest.mark.asyncio
async def test_checkin_asks_about_the_product_with_buttons(db):
    _cheeses()
    leerdammer = {**crud.get_item("LEERDAMMER CAR."), "last_purchase": "2026-09-01T10:00:00+00:00"}
    context = _context()
    with patch("bot.main.settings.get_chat_id", return_value=1), \
            patch("bot.main.crud.get_checkin_candidates", return_value=[leerdammer]):
        await main.check_expiring_items(context)

    sent = context.bot.send_message.call_args.kwargs
    assert sent["text"] == "Do you still have cheese?"  # no brand, no notes
    buttons = [b.callback_data for row in sent["reply_markup"].inline_keyboard for b in row]
    assert buttons == [f"checkin:yes:{leerdammer['id']}", f"checkin:no:{leerdammer['id']}"]

    await main.handle_checkin_choice(tap := _tap(f"checkin:no:{leerdammer['id']}"), context)
    assert tap.callback_query.edit_message_text.call_args.args[0] == "Noted — cheese ran out."
    assert crud.get_item("LEERDAMMER CAR.")["checkin_pending"] == 0

    await main.handle_checkin_choice(again := _tap(f"checkin:yes:{leerdammer['id']}"), context)
    again.callback_query.edit_message_text.assert_called_once_with("Already answered — thanks.")
