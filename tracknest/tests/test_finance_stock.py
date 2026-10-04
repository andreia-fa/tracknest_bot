from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot import main
from db import crud, database, expenses, shopping_list

SPENDING = {
    "total": 100.0, "treat": 40.0, "need": 50.0, "unknown": 10.0,
    "top_items": [], "top_categories": [{"category": "Fruits/Veg", "total": 33.30}],
}


def _message_update():
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    return update


def _callback_update():
    update = MagicMock()
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.message.chat_id = 1
    return update


def _finance(spending=SPENDING, projected=None, budget=None, goal=None):
    with patch("bot.main.metrics") as m:
        m.MIN_DAYS_FOR_PROJECTION = 5
        m.get_spending_summary.return_value = spending
        m.get_month_pace.return_value = {"spent": spending["total"], "days_elapsed": 4,
                                         "days_in_month": 31, "projected": projected}
        m.get_budget_status.return_value = budget
        m.get_goal_status.return_value = goal
        return main._finance_text()


def test_finance_is_money_only_and_short():
    text = _finance(
        budget={"budget": 377.0, "spent": 100.0, "pct": 26.5},
        goal={"name": "Trips!", "pace_per_month": 195.0, "days_left": 78, "target_date": "2026-12-21"},
    )
    lines = text.splitlines()
    assert lines[1] == "€100.00 spent — too early to project (from day 5)"
    assert "Budget: 26% of €377 used" in lines
    assert "Most on: Fruits/Veg €33.30" in lines
    assert "Treats 40% · Needs 50% · Not sorted 10%" in lines
    assert lines[-1] == "🎯 Trips!: put aside €195/month (78 days left)"
    assert len(lines) <= 6
    for gone in ("€/day", "Running low", "Rising", "Needs you", "check-in"):
        assert gone not in text


def test_finance_projects_and_flags_an_over_budget_pace():
    text = _finance(projected=420.0, budget={"budget": 377.0, "spent": 100.0, "pct": 26.5})
    assert "€100.00 spent — on pace for ~€420" in text
    assert "Budget: 26% of €377 used — over pace, treats are the easiest cut" in text


def test_finance_with_nothing_set_or_spent():
    empty = {"total": 0.0, "treat": 0.0, "need": 0.0, "unknown": 0.0, "top_items": [], "top_categories": []}
    assert len(_finance(spending=empty).splitlines()) == 2


@pytest.mark.asyncio
async def test_report_still_answers_with_finance():
    update = _message_update()
    with patch("bot.main._finance_text", return_value="money"):
        await main.finance_cmd(update, MagicMock())
    update.message.reply_text.assert_awaited_once_with("money")


def _low(name, product, days_left, category=None):
    return {"name": name, "product": product, "category": category, "days_left": days_left}


@pytest.mark.asyncio
@patch("bot.main.metrics")
async def test_stock_names_products_once_soonest_first(mock_metrics):
    mock_metrics.get_running_low.return_value = [
        _low("Greenl. Erdbeere", "strawberries", 1),
        _low("BANANE", "bananas", 2),
        _low("Chiquita Banane", "Bananas", 4),
        _low("PROTEINBROETCHEN", None, 0),
    ]
    update = _message_update()
    await main.stock_cmd(update, MagicMock())
    text = update.message.reply_text.await_args.args[0]
    assert text == ("Running out soon:\n• PROTEINBROETCHEN — today\n"
                    "• strawberries — 1 day\n• bananas — 2 days")
    keyboard = update.message.reply_text.await_args.kwargs["reply_markup"]
    assert keyboard.inline_keyboard[0][0].callback_data == "stock_add_all"


@pytest.mark.asyncio
@patch("bot.main.metrics")
async def test_stock_when_nothing_runs_out(mock_metrics):
    mock_metrics.get_running_low.return_value = []
    update = _message_update()
    await main.stock_cmd(update, MagicMock())
    update.message.reply_text.assert_awaited_once_with("Nothing is running out in the next 7 days. 🟢")


@pytest.fixture
def db(tmp_path):
    with patch.object(database, "DB_PATH", str(tmp_path / "test.db")):
        database.init_db()
        yield


def _need(name, product, shelf_life_days, bought_days_ago, category="Fruits/Veg"):
    crud.add_item(name, 1, category=category, product=product)
    crud.set_treat_or_need(name, "need")
    crud.set_lasts(name, "days", shelf_life_days)
    when = datetime.now(tz=timezone.utc) - timedelta(days=bought_days_ago)
    expenses.log_expense(name, 1, 1.0, store="REWE", purchased_at=when)


@pytest.mark.asyncio
async def test_add_all_lists_each_product_once_and_skips_listed(db):
    _need("BANANE", "bananas", 7, bought_days_ago=5)
    _need("Greenl. Erdbeere", "strawberries", 4, bought_days_ago=2)
    _need("PEANUT BUTTER", "peanut butter", 60, bought_days_ago=1)
    shopping_list.add_item("Strawberries", 1)
    update = _callback_update()
    context = MagicMock()
    context.bot.send_message = AsyncMock()

    await main.handle_stock_add_all(update, context)
    await main.handle_stock_add_all(update, context)

    listed = {i["name"]: (i["quantity"], i["category"]) for i in shopping_list.get_all_items()}
    assert listed == {"Strawberries": (1, None), "bananas": (1, "Fruits/Veg")}
    assert update.callback_query.edit_message_text.await_args_list[0].args[0] == \
        "Added to your shopping list: bananas."
    assert update.callback_query.edit_message_text.await_args_list[1].args[0] == \
        "Everything running out is already on your shopping list."
