"""A parsed receipt waits for the user's ✅ before anything is written."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot import main
from db import crud, database, expenses, receipt_queue, shopping_list


@pytest.fixture
def db(tmp_path):
    with patch.object(database, "DB_PATH", str(tmp_path / "test.db")):
        database.init_db()
        yield


def _held_receipt() -> int:
    shopping_list.add_item("Socks - decathlon", 1)
    receipt_id = receipt_queue.queue_receipt(1, "file")
    receipt_queue.hold_for_review(receipt_id, {
        "store": "Burger King", "purchase_date": "2026-10-03", "total_paid": 10.98, "items_total": 10.98,
        "reconciled": True, "items": [
            {"name": "Whopper", "quantity": 1, "unit_price": 6.99, "category": "Other", "product": "burger"},
            {"name": "Socks - decathlon", "quantity": 1, "unit_price": 3.99, "category": "Clothing",
             "product": "socks"},
        ]})
    return receipt_id


async def _tap(data, context):
    update = MagicMock()
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    with patch("bot.main.send_pending_profile_question", AsyncMock()):
        await main.handle_receipt_review(update, context)
    return update.callback_query.edit_message_text.call_args


def _context():
    context = MagicMock()
    context.chat_data = {}
    return context


def test_the_review_lists_every_line_before_saving(db):
    receipt_id = _held_receipt()
    text, _markup = main.receipt_review(receipt_id, receipt_queue.get_review(receipt_id))
    assert "1. 1x Whopper — €6.99" in text and "2. 1x Socks - decathlon — €3.99" in text
    assert "Total €10.98 ✓ matches the receipt" in text
    assert crud.get_item_names() == []  # nothing written yet


@pytest.mark.asyncio
async def test_rename_a_line_then_save(db):
    receipt_id = _held_receipt()
    context = _context()
    await _tap(f"rcpt:rename:{receipt_id}:1", context)
    message = MagicMock()
    message.text = "Pommes"
    message.reply_text = AsyncMock()
    update = MagicMock(message=message)
    await main.handle_text(update, context)
    assert "2. 1x Pommes — €3.99" in message.reply_text.call_args.args[0]
    assert crud.get_item_names() == []

    await _tap(f"rcpt:ok:{receipt_id}", context)
    assert sorted(crud.get_item_names()) == ["Pommes", "Whopper"]
    assert [i["name"] for i in shopping_list.get_all_items()] == ["Socks - decathlon"]  # still to buy
    assert receipt_queue.get_review(receipt_id) is None


@pytest.mark.asyncio
async def test_remove_a_line_or_discard_everything(db):
    receipt_id = _held_receipt()
    context = _context()
    args = await _tap(f"rcpt:drop:{receipt_id}:1", context)
    assert "Socks" not in args.args[0] and "€6.99, the receipt says €10.98" in args.args[0]
    await _tap(f"rcpt:cancel:{receipt_id}", context)
    assert crud.get_item_names() == [] and expenses.names_bought_at(6.99) == set()
    assert "already saved or discarded" in (await _tap(f"rcpt:ok:{receipt_id}", context)).args[0]


@pytest.mark.asyncio
async def test_a_stale_rename_never_swallows_a_list_line(db):
    receipt_id = _held_receipt()
    context = _context()
    await _tap(f"rcpt:rename:{receipt_id}:1", context)
    context.chat_data["receipt_edit"]["at"] = "2026-01-01T00:00:00+00:00"
    assert not main._awaiting_receipt_edit(context)
    assert "receipt_edit" not in context.chat_data


async def _type(text, context):
    message = MagicMock()
    message.text = text
    message.reply_text = AsyncMock()
    await main.handle_text(MagicMock(message=message), context)
    return message.reply_text.call_args.args[0]


@pytest.mark.asyncio
async def test_change_a_price(db):
    receipt_id = _held_receipt()
    context = _context()
    await _tap(f"rcpt:price:{receipt_id}:0", context)
    assert "doesn't look like a price" in await _type("seven", context)
    reply = await _type("€5,49", context)
    assert "1. 1x Whopper — €5.49" in reply and "Lines add up to €9.48, the receipt says €10.98" in reply
    assert "receipt_edit" not in context.chat_data


@pytest.mark.asyncio
async def test_an_unreadable_line_must_be_named_before_saving(db):
    receipt_id = receipt_queue.queue_receipt(1, "file")
    receipt_queue.hold_for_review(receipt_id, {
        "store": "Burger King", "purchase_date": "2026-10-03", "total_paid": 10.98, "items_total": 10.98,
        "reconciled": True, "items": [
            {"name": "Whopper", "unsure": True, "quantity": 1, "unit_price": 6.99, "category": "Other", "product": "burger"},
            {"name": "", "unsure": True, "quantity": 1, "unit_price": 3.99, "category": "Other", "product": ""},
        ]})
    text, markup = main.receipt_review(receipt_id, receipt_queue.get_review(receipt_id))
    assert "1. 1x Whopper — €6.99 ⚠️ hard to read" in text and "❓ couldn't read the name" in text
    assert "before I can save" in text
    assert all(b.callback_data != f"rcpt:ok:{receipt_id}" for row in markup.inline_keyboard for b in row)

    context = _context()
    await _tap(f"rcpt:ok:{receipt_id}", context)  # an old ✅ can't sneak it through
    assert crud.get_item_names() == []

    await _tap(f"rcpt:rename:{receipt_id}:1", context)
    reply = await _type("Pommes", context)
    assert "❓" not in reply and "2. 1x Pommes — €3.99" in reply
    await _tap(f"rcpt:ok:{receipt_id}", context)
    assert sorted(crud.get_item_names()) == ["Pommes", "Whopper"]
