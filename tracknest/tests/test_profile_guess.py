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
async def test_receipt_files_nothing_before_the_product_is_confirmed(db):
    crud.add_item("LEERDAMMER CAR.", 1, product="cheese", category="Dairy")
    crud.set_treat_or_need("LEERDAMMER CAR.", "need")
    crud.set_lasts("LEERDAMMER CAR.", "days", 7)

    text, keyboard = await main.process_receipt_result(_receipt(
        _line("SCHLAGCREME VEGA", "cheese"),   # misread: must NOT become a 7-day cheese
        _line("BANANE", "banana"),
    ))

    for name in ("SCHLAGCREME VEGA", "BANANE"):
        item = crud.get_item(name)
        assert (item["treat_or_need"], item["lasts"], item["name_status"]) == ("unknown", "unknown", "name")
    assert text.count("🆕 I'll ask what it is") == 2
    assert keyboard is None  # no ✏️ for answers that don't exist yet


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


def test_a_pastry_is_a_same_day_treat():
    from bot.profile_guess import guess_profile
    assert guess_profile("Pastries") == ("treat", ("same_day", None))
