"""/item and the 🏷 Category button, on a real SQLite file."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot import main
from bot.categorize import CATEGORY_NAMES, infer_category
from db import crud, database, expenses, metrics


@pytest.fixture
def db(tmp_path):
    with patch.object(database, "DB_PATH", str(tmp_path / "test.db")):
        database.init_db()
        crud.add_item("Matcha", 1, category="Beverages")
        expenses.log_expense("Matcha", 1, 6.90, purchased_at=datetime(2026, 9, 25, 12, tzinfo=timezone.utc),
                             trip_key="typed:a")
        yield


def _tap(data):
    update = MagicMock()
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


@pytest.mark.asyncio
async def test_item_command_then_category_makes_a_cafe_drink_leisure(db):
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.args = ["matcha"]
    await main.item_cmd(update, context)
    buttons = [b.callback_data for row in update.message.reply_text.call_args.kwargs["reply_markup"].inline_keyboard
               for b in row]
    item_id = crud.get_item("Matcha")["id"]
    assert f"cat_edit:{item_id}" in buttons

    await main.handle_category_set(_tap(f"cat_set:{item_id}:{CATEGORY_NAMES.index('Leisure')}"), MagicMock())

    assert crud.get_item("Matcha")["category"] == "Leisure"
    trips = metrics.get_shopping_trips(2026, 9)
    assert trips["count"] == 0 and trips["not_shopping"] == pytest.approx(6.90)


def test_cafe_drinks_are_leisure_but_supermarket_coffee_is_not():
    assert infer_category("Matcha Latte") == "Leisure"
    assert infer_category("Cappuccino") == "Leisure"
    assert infer_category("Lavazza Espresso") != "Leisure"


def test_changing_category_leaves_an_open_question_open(db):
    crud.mark_name_pending("Matcha")
    crud.change_item_category("Matcha", "Leisure")
    assert crud.get_item("Matcha")["name_status"] == "name"
