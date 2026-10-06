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


@pytest.mark.asyncio
async def test_a_receipt_without_a_date_asks_for_it_before_saving(db):
    receipt_id = _held_receipt()
    parsed = receipt_queue.get_review(receipt_id)
    parsed["purchase_date"] = None
    receipt_queue.update_review(receipt_id, parsed)
    text, markup = main.receipt_review(receipt_id, parsed)
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "couldn't read the date" in text
    assert f"rcpt:ok:{receipt_id}" not in callbacks
    assert len([c for c in callbacks if c.startswith(f"rcpt:day:{receipt_id}:")]) == 3

    context = _context()
    await _tap(f"rcpt:ok:{receipt_id}", context)  # an old ✅ can't sneak it through
    assert crud.get_item_names() == []

    await _tap(f"rcpt:day:{receipt_id}:20261005", context)
    assert receipt_queue.get_review(receipt_id)["purchase_date"] == "2026-10-05"
    await _tap(f"rcpt:ok:{receipt_id}", context)
    assert expenses.get_expenses("Whopper")[0]["purchase_date"] == "2026-10-05"


@pytest.mark.asyncio
async def test_a_read_date_can_be_changed(db):
    receipt_id = _held_receipt()
    _text, markup = main.receipt_review(receipt_id, receipt_queue.get_review(receipt_id))
    assert f"rcpt:date:{receipt_id}" in [b.callback_data for row in markup.inline_keyboard for b in row]
    context = _context()
    picker = await _tap(f"rcpt:date:{receipt_id}", context)
    assert "When did you buy this?" in picker.args[0]
    await _tap(f"rcpt:day:{receipt_id}:20261002", context)
    assert receipt_queue.get_review(receipt_id)["purchase_date"] == "2026-10-02"


@pytest.mark.asyncio
async def test_a_missed_line_blocks_saving_until_added(db):
    receipt_id = _held_receipt()
    context = _context()
    review = await _tap(f"rcpt:drop:{receipt_id}:1", context)  # as if the model had missed the socks line
    callbacks = [b.callback_data for row in review.kwargs["reply_markup"].inline_keyboard for b in row]
    assert "€3.99 is missing" in review.args[0]
    assert f"rcpt:ok:{receipt_id}" not in callbacks and f"rcpt:add:{receipt_id}" in callbacks
    await _tap(f"rcpt:ok:{receipt_id}", context)  # an old ✅ can't sneak it through
    assert crud.get_item_names() == []

    await _tap(f"rcpt:add:{receipt_id}", context)
    assert "name and the price" in await _type("just words", context)
    reply = await _type("Wrap Thunfisch 3,99", context)
    assert "2. 1x Wrap Thunfisch — €3.99" in reply and "✓ matches the receipt" in reply
    await _tap(f"rcpt:ok:{receipt_id}", context)
    assert sorted(crud.get_item_names()) == ["Whopper", "Wrap Thunfisch"]


@pytest.mark.asyncio
async def test_a_misread_total_can_be_fixed(db):
    receipt_id = _held_receipt()
    context = _context()
    await _tap(f"rcpt:drop:{receipt_id}:1", context)
    await _tap(f"rcpt:total:{receipt_id}", context)
    reply = await _type("6,99", context)
    assert "Total €6.99 ✓ matches the receipt" in reply
