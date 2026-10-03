"""Fewer questions: a new item's answers come from its product, then its category — on a real DB."""

from unittest.mock import patch

import pytest

from bot import main
from bot.profile_guess import guess_profile
from db import crud, database


@pytest.fixture
def db(tmp_path):
    with patch.object(database, "DB_PATH", str(tmp_path / "test.db")):
        database.init_db()
        yield


def _line(name, product, price=1.0, category=None):
    return {"name": name, "quantity": 1, "unit_price": price, "matched_shopping_list_item": "",
            "product": product, "category": category}


def _receipt(*items):
    return {"items": list(items), "store": "REWE", "reconciled": True,
            "items_total": sum(i["unit_price"] for i in items), "total_paid": sum(i["unit_price"] for i in items)}


def test_mixed_categories_have_no_default():
    assert guess_profile("Bread/Bakery") == (None, None)
    assert guess_profile(None) == (None, None)


@pytest.mark.asyncio
async def test_receipt_files_new_items_from_product_then_category(db):
    crud.add_item("Pfefferbretzel", 1, product="pretzel", category="Bread/Bakery")
    crud.set_treat_or_need("Pfefferbretzel", "treat")
    crud.set_lasts("Pfefferbretzel", "same_day")

    text, keyboard = await main.process_receipt_result(_receipt(
        _line("LAUGENBREZEL", "pretzel"),        # known product -> its answers
        _line("BANANE", "banana"),                # Fruits/Veg -> need, 7 days
        _line("Kuchenbeleg", "cake"),             # nothing to go on -> asked
    ))

    brezel, banane, kuchen = (crud.get_item(n) for n in ("LAUGENBREZEL", "BANANE", "Kuchenbeleg"))
    assert (brezel["treat_or_need"], brezel["lasts"]) == ("treat", "same_day")
    assert (banane["treat_or_need"], banane["lasts"], banane["shelf_life_days"]) == ("need", "days", 7)
    assert kuchen["treat_or_need"] == "unknown"
    assert "🍫 treat · used up the same day" in text
    assert "🧺 need · lasts 7 day(s)" in text
    assert "❓ treat or need" in text
    buttons = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert buttons == [f"fix:{brezel['id']}", f"fix:{banane['id']}", f"fix:{kuchen['id']}", "fixok"]


@pytest.mark.asyncio
async def test_known_items_are_not_refiled(db):
    crud.add_item("BANANE", 1, category="Fruits/Veg")
    crud.set_treat_or_need("BANANE", "treat")  # the user's own answer beats any default
    crud.set_lasts("BANANE", "days", 5)

    text, keyboard = await main.process_receipt_result(_receipt(_line("BANANE", "banana")))

    item = crud.get_item("BANANE")
    assert (item["treat_or_need"], item["shelf_life_days"]) == ("treat", 5)
    assert "need" not in text and keyboard is None


@pytest.mark.asyncio
async def test_old_receipt_is_logged_on_its_printed_date(db):
    from db import expenses
    parsed = _receipt(_line("BANANE", "banana", price=1.99))
    parsed["purchase_date"] = "2026-09-22"

    text, _keyboard = await main.process_receipt_result(parsed)

    assert text.startswith("Receipt processed (REWE, 22 Sep):")
    assert expenses.get_expenses("BANANE")[0]["purchase_date"] == "2026-09-22"
    # The same receipt sent again is caught, however long after.
    again, _ = await main.process_receipt_result(parsed)
    assert "skipped" in again


@pytest.mark.asyncio
async def test_unreadable_date_is_flagged(db):
    text, _keyboard = await main.process_receipt_result(_receipt(_line("BANANE", "banana")))
    assert "couldn't read the date" in text
