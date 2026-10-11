"""/shop: a week's shop from what runs out, by store, asking instead of assuming."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot import main
from db import crud, database, expenses, metrics, shopping_list


@pytest.fixture
def db(tmp_path):
    with patch.object(database, "DB_PATH", str(tmp_path / "test.db")):
        database.init_db()
        yield


def _need(name, product, lasts_days, bought_days_ago, store, category="Dairy", packs=1):
    if crud.get_item(name) is None:
        crud.add_item(name, 1, category=category, product=product)
        crud.set_treat_or_need(name, "need")
        crud.set_lasts(name, "days", lasts_days)
    when = datetime.now(tz=timezone.utc) - timedelta(days=bought_days_ago, hours=1)
    expenses.log_expense(name, packs, 2.49, store=store, purchased_at=when)


def test_the_plan_covers_a_week_and_counts_what_it_cant_predict(db):
    _need("Eier bunt 10er", "eggs", 5, 3, "REWE")           # runs out in 1 day
    _need("Reis Basmati", "rice", 60, 2, "Lidl", "Pantry")  # fine for weeks
    _need("Milram Gouda", "cheese", 10, 14, "REWE")        # should be gone
    crud.add_item("Tiefkühl Erdbeeren", 1, category="Fruits/Veg")
    expenses.log_expense("Tiefkühl Erdbeeren", 1, 2.99, store="Lidl")  # lifespan unknown
    plan = metrics.get_shop_plan(7)
    assert [(i["name"], i["store"]) for i in plan["items"]] == [("Milram Gouda", "REWE"), ("Eier bunt 10er", "REWE")]
    assert plan["items"][0]["days_left"] < 0 and plan["items"][1]["days_left"] == 1
    assert plan["unknown"] == 1


@pytest.mark.asyncio
async def test_shop_groups_by_store_asks_about_overdue_and_unknown(db):
    _need("Eier bunt 10er", "eggs", 5, 3, "REWE")
    _need("Milram Gouda", "cheese", 10, 14, "REWE")
    crud.add_item("Tiefkühl Erdbeeren", 1, category="Fruits/Veg")
    expenses.log_expense("Tiefkühl Erdbeeren", 1, 2.99, store="Lidl")
    shopping_list.add_item("broccoli", 1)

    text, markup = main._shop_text_and_buttons()

    assert text.startswith("📅 Suggested: shop tomorrow — eggs runs out then.")
    assert "REWE:\n• eggs — runs out in 1 day ⏳ one lasts 5 days — 2 would last until the next shop" in text
    assert "Store not known yet:\n• broccoli — on your list" in text
    assert "do you still have them?" in text and "cheese" not in text.split("❓")[0]
    assert "I can't predict 1 thing yet" in text
    data = [b.callback_data for row in markup.inline_keyboard for b in row]
    gouda = crud.get_item("Milram Gouda")
    assert f"checkin:yes:{gouda['id']}" in data and gouda["checkin_pending"]
    assert "shop_add" in data and "shop_ask" in data


@pytest.mark.asyncio
async def test_add_puts_only_the_missing_products_on_the_list(db):
    _need("Eier bunt 10er", "eggs", 5, 3, "REWE")
    _need("Butter", "butter", 14, 10, "REWE")
    shopping_list.add_item("eggs", 1)
    query = MagicMock(data="shop_add", answer=AsyncMock(), edit_message_text=AsyncMock())
    context = MagicMock()
    context.bot.send_message = AsyncMock()
    await main.handle_shop_choice(MagicMock(callback_query=query), context)
    assert sorted(i["name"] for i in shopping_list.get_all_items()) == ["butter", "eggs"]


def test_two_packs_last_twice_as_long(db):
    _need("Eier bunt 10er", "eggs", 5, 3, "REWE", packs=2)
    [eggs] = metrics.get_shop_plan(14)["items"]
    assert eggs["days_left"] == 6 and eggs["last_quantity"] == 2


def test_a_new_brand_replaces_the_old_one(db):
    _need("Milram Gouda", "cheese", 10, 30, "REWE")   # long gone, but...
    _need("Leerdammer", "cheese", 10, 2, "Lidl")      # ...cheese was bought again
    [cheese] = metrics.get_shop_plan(14)["items"]
    assert cheese["name"] == "Leerdammer" and cheese["days_left"] == 7
    assert metrics.get_running_low(7) == [{
        "name": "Leerdammer", "product": "cheese", "category": "Dairy", "days_left": 7,
        "run_out_date": cheese["run_out_date"], "shelf_life_days": 10}]


def test_the_week_counts_from_the_suggested_day(db):
    _need("Brot", "bread", 6, 2, "Bäcker", "Bread/Bakery")   # runs out in 3 days -> shop then
    _need("Reis", "rice", 12, 2, "Lidl", "Pantry")          # runs out in 9 days: before the shop after
    _need("Öl", "oil", 30, 2, "Lidl", "Pantry")             # weeks away
    plan = main._shop_plan()
    assert plan["day_offset"] == 3 and plan["first"] == "bread"
    assert [i["need"] for i in plan["to_buy"]] == ["bread", "rice"]


def test_nothing_predicted_means_no_rush(db):
    shopping_list.add_item("frozen strawberries", 1)
    text, _markup = main._shop_text_and_buttons()
    assert text.startswith("📅 Nothing I can predict runs out in the next two weeks — shop whenever suits you.")
    assert "• frozen strawberries — on your list" in text


@pytest.mark.asyncio
async def test_the_check_in_waits_for_every_pack(db):
    _need("Eier bunt 10er", "eggs", 5, 7, "REWE", packs=2)   # 2 packs = 10 days, 7 gone
    context = MagicMock()
    context.bot.send_message = AsyncMock()
    with patch("bot.main.settings.get_chat_id", return_value=1):
        assert await main.check_expiring_items(context) is False
