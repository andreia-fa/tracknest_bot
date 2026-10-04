"""A new receipt item: confirm what it is (buttons), then confirm how it's filed — on a real DB."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot import main
from db import crud, database


@pytest.fixture
def db(tmp_path):
    with patch.object(database, "DB_PATH", str(tmp_path / "test.db")):
        database.init_db()
        yield


def _receipt(*lines):
    items = [{"name": name, "quantity": 1, "unit_price": 1.0, "matched_shopping_list_item": "",
              "product": product, "category": None, "alternatives": alternatives}
             for name, product, alternatives in lines]
    return {"items": items, "store": "REWE", "reconciled": True, "items_total": len(items), "total_paid": len(items)}


def _bot():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


def _tap(data):
    update = MagicMock()
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.message.chat_id = 1
    update.effective_chat.id = 1
    return update


def _context(bot):
    context = MagicMock()
    context.bot = bot
    context.chat_data = {}
    return context


def _buttons(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


@pytest.fixture
def household(db):
    for name, product, category, kind, lasts in (
        ("Pfefferbretzel", "pretzel", "Bread/Bakery", "treat", ("same_day",)),
        ("LEERDAMMER CAR.", "cheese", "Dairy", "need", ("days", 7)),
    ):
        crud.add_item(name, 1, product=product, category=category)
        crud.set_treat_or_need(name, kind)
        crud.set_lasts(name, *lasts)


@pytest.mark.asyncio
async def test_same_product_in_another_language_is_filed_like_the_others(household):
    await main.process_receipt_result(_receipt(("Laugenbreze", "brezel", [])))
    bot = _bot()
    with patch("bot.main.settings.get_open_question", return_value=None):
        await main.send_pending_profile_question(bot, 1)
    assert bot.send_message.call_args.args[1] == "🧾 New: Laugenbreze\nI think it's: pretzel"  # the household's word

    with patch("bot.main.settings.get_open_question", return_value=None):
        await main.handle_product_ok(_tap("product_ok"), _context(bot))

    item = crud.get_item("Laugenbreze")
    assert (item["product"], item["treat_or_need"], item["lasts"], item["name_status"]) == (
        "pretzel", "treat", "same_day", None)
    assert bot.send_message.call_args.args[1] == (
        "Pretzel (Laugenbreze): 🍫 treat · used up the same day · 🏷 Bread/Bakery")


@pytest.mark.asyncio
async def test_wrong_guess_is_fixed_with_taps_and_confirmed_on_one_card(household):
    await main.process_receipt_result(_receipt(("SCHLAGCREME VEGA", "cheese", ["vegan whipping cream", "cream"])))
    bot = _bot()
    context = _context(bot)

    no = _tap("product_no")
    await main.handle_product_no(no, context)
    text, = no.callback_query.edit_message_text.call_args.args
    assert text == "🧾 SCHLAGCREME VEGA\nNot cheese — what is it then?"
    assert _buttons(no.callback_query.edit_message_text.call_args.kwargs["reply_markup"]) == [
        "product_pick:vegan whipping cream", "product_pick:cream", "product_type"]

    with patch("bot.main.settings.get_open_question", return_value=None):
        await main.handle_product_pick(_tap("product_pick:vegan whipping cream"), context)
    item = crud.get_item("SCHLAGCREME VEGA")
    assert item["product"] == "vegan whipping cream" and item["name_status"] == "card"
    assert crud.get_product_options("SCHLAGCREME VEGA") == []
    card = bot.send_message.call_args
    assert card.args[1].startswith("Vegan whipping cream (SCHLAGCREME VEGA) — I'd file it as:\n")
    item_id = item["id"]

    # It's a treat for a cake, bought once — two taps, then ✅.
    await main.handle_card(_tap(f"card_kind:{item_id}:treat"), context)
    edit = _tap(f"card_setl:{item_id}:oneoff")
    await main.handle_card(edit, context)
    text = edit.callback_query.edit_message_text.call_args.args[0]
    assert "• 🍫 Treat\n• 🎂 Bought once — no reminders" in text
    assert _buttons(edit.callback_query.edit_message_text.call_args.kwargs["reply_markup"])[0] == f"card_ok:{item_id}"

    with patch("bot.main.settings.get_open_question", return_value=None):
        await main.handle_card(_tap(f"card_ok:{item_id}"), context)
    item = crud.get_item("SCHLAGCREME VEGA")
    assert (item["treat_or_need"], item["lasts"], item["name_status"]) == ("treat", "one_off", None)


@pytest.mark.asyncio
async def test_card_hides_confirm_until_every_line_is_answered(household):
    await main.process_receipt_result(_receipt(("Kuchenbeleg", "", [])))
    with patch("bot.main.settings.get_open_question", return_value=None):
        await main._confirm_product(_bot(), 1, "Kuchenbeleg", "birthday cake")
    item = crud.get_item("Kuchenbeleg")
    # Bread/Bakery is too mixed to guess (a loaf is a need, a cake a treat).
    assert main._card_text(item) == (
        "Birthday cake (Kuchenbeleg) — I'd file it as:\n"
        "• ❓ Treat or need?\n• ⏳ ❓ How long does it last?\n• 🏷 Bread/Bakery")
    assert _buttons(main._card_keyboard(item)) == [
        f"card_kind:{item['id']}:treat", f"card_kind:{item['id']}:need",
        f"card_lasts:{item['id']}", f"card_cat:{item['id']}"]
