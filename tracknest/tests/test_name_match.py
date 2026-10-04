"""Typos and spacing still land on the item the household already has."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot import main
from bot.name_match import clean_name, match_known
from db import crud, database, expenses, shopping_list

KNOWN = ["Pfefferbretzel", "LAUGENBREZEL", "Bio Milch 1,5%", "Bio Milch 3,5%", "milk", "Erdbeeren"]


@pytest.mark.parametrize("typed", [
    "pfefer bretzl", "Pfeffer  Bretzel", "pfefferbrtzel,", "Pfefferbrezel", "peffer bretzel", "PFEFFERBRETZEL",
])
def test_typos_of_a_known_name_are_recognised(typed):
    assert match_known(typed, KNOWN) == "Pfefferbretzel"


def test_spacing_and_umlaut_spellings_never_matter():
    assert match_known("laugen brezel", KNOWN) == "LAUGENBREZEL"
    assert match_known("Kaese", ["Käse"]) == "Käse"
    assert match_known("Strasse", ["Straße"]) == "Straße"


@pytest.mark.parametrize("typed", ["Heidelbeeren", "silk", "Bio Milch 0,3%", "Laugenstange", "Pfefferbretzel mit Käse"])
def test_different_things_stay_different(typed):
    assert match_known(typed, KNOWN) is None


def test_numbers_must_agree():
    assert match_known("Bio Milch 1,5", KNOWN) == "Bio Milch 1,5%"
    assert match_known("Bio Mlich 3,5%", KNOWN) == "Bio Milch 3,5%"


def test_a_toss_up_is_not_guessed():
    assert match_known("Joghurt Kirsch", ["Joghurt Kirsche", "Joghurt Kirsch."]) == "Joghurt Kirsch."
    assert match_known("Bananen", ["Banane", "Bananas"]) == "Banane"  # clearly the closer one
    assert match_known("Kirschjoghurt", ["Kirschjoghurd", "Kirschjoghurx"]) is None


def test_clean_name_drops_stray_punctuation_and_spaces():
    assert clean_name("  pfefferbretzel,  ") == "pfefferbretzel"
    assert clean_name("Oat   Milk.") == "Oat Milk"


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


async def _send_and_confirm(text, context):
    update = MagicMock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    await main.handle_text(update, context)
    tap = MagicMock()
    tap.callback_query.data = f"typed:list:{context.chat_data['typed']['token']}"
    tap.callback_query.answer = AsyncMock()
    tap.callback_query.edit_message_text = AsyncMock()
    with patch("bot.main.send_pending_profile_question", AsyncMock()):
        await main.handle_typed_choice(tap, context)
    return update.message.reply_text.call_args.args[0], tap.callback_query.edit_message_text.call_args.args[0]


@pytest.mark.asyncio
async def test_a_misspelled_purchase_is_logged_to_the_known_item(db):
    crud.add_item("Pfefferbretzel", 1, product="pretzel")
    expenses.log_expense("Pfefferbretzel", 1, 1.80, store="Yormas",
                         purchased_at=datetime.now(tz=timezone.utc) - timedelta(days=1))
    shopping_list.add_item("Pfefferbretzel", 1)

    preview, reply = await _send_and_confirm("yormas, pfefer  bretzl, 1,80", _context())

    assert "1x Pfefferbretzel at €1.80 each at Yormas (you typed 'pfefer bretzl')" in preview
    assert crud.get_item_names() == ["Pfefferbretzel"]
    assert "(cleared from your list)" in reply and "🆕" not in reply
    assert shopping_list.get_all_items() == []


@pytest.mark.asyncio
async def test_list_lines_reuse_the_spelling_already_used(db):
    crud.add_item("Pfefferbretzel", 0)

    await _send_and_confirm("pfeffer bretzl", _context())
    await _send_and_confirm("Pfefferbretzel", _context())
    assert [(i["name"], i["quantity"]) for i in shopping_list.get_all_items()] == [("Pfefferbretzel", 2)]

    await _send_and_confirm("- pfefferbrezel", _context())
    assert shopping_list.get_all_items() == []


def test_a_receipt_spelling_of_a_known_item_is_not_new(db):
    crud.add_item("LAUGENBREZEL", 0, category="Bread/Bakery", product="pretzel")
    assert main._resolve_receipt_name({"name": "Laugenbreze"}) == ("LAUGENBREZEL", "Bread/Bakery", "pretzel", False)


def test_the_same_price_forgives_a_bit_more():
    # 0.79 alike: too far on spelling alone, the same thing at the same price.
    assert match_known("Kaesestange", ["Käse-Laugenstange"]) is None
    assert match_known("Kaesestange", ["Käse-Laugenstange"], {"Käse-Laugenstange"}) == "Käse-Laugenstange"
    # Same-brand neighbours often share a price and must stay apart.
    assert match_known("Milram Edamer", ["Milram Gouda"], {"Milram Gouda"}) is None


def test_a_receipt_line_at_a_known_price_lands_on_that_item(db):
    crud.add_item("Käse-Laugenstange", 0, category="Bread/Bakery", product="cheese pretzel stick")
    expenses.log_expense("Käse-Laugenstange", 1, 1.49, store="Yormas")
    assert main._resolve_receipt_name({"name": "Kaesestange", "unit_price": 1.49})[0] == "Käse-Laugenstange"
    assert main._resolve_receipt_name({"name": "Kaesestange", "unit_price": 1.99})[0] == "Kaesestange"


@pytest.mark.asyncio
async def test_an_old_question_never_hijacks_a_list_line(db):
    """Real, 2026-10-05: "tempeh" got "is this your answer to what 'Socks - decathlon' is?" over and over."""
    crud.add_item("Iced Matcha Mango", 1)
    crud.mark_name_pending("Iced Matcha Mango")
    context = _context()

    preview, _reply = await _send_and_confirm("tempeh", context)
    assert "your answer" not in preview and "add 1x tempeh" in preview  # nothing was asked lately

    await main.send_pending_profile_question(MagicMock(send_message=AsyncMock()), 1)
    update = MagicMock()
    update.message.text = "matcha drink"
    update.message.reply_text = AsyncMock()
    await main.handle_text(update, _context())
    assert "your answer" in update.message.reply_text.call_args.args[0]  # just asked: both meanings
