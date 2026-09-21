"""CLI bridge so the local receipt worker can finish a queued receipt.

Invoked over SSH + `docker exec` against the running container (see
`bot/receipt_worker.py`), so the DB is only ever touched on the machine
where the SQLite file actually lives — no network filesystem, no ad-hoc SQL
built on the calling side. Reuses the exact same functions the live bot
uses (bot.main.process_receipt_result / send_pending_profile_question),
so the item-logging and follow-up-question logic only exists in one place.
"""

import asyncio
import json
import sys

from config import BOT_TOKEN
from db import receipt_queue, shopping_list
from telegram import Bot


async def _finish_receipt(args: dict) -> dict:
    """Log a parsed receipt's items, resolve the queue entry, and reply to the chat."""
    from bot.main import process_receipt_result, send_pending_profile_question

    reply = await process_receipt_result(args["parsed"])
    receipt_queue.resolve_receipt(args["receipt_id"], status="done")
    bot = Bot(token=BOT_TOKEN)
    await bot.send_message(args["chat_id"], reply)
    await send_pending_profile_question(bot, args["chat_id"])
    return {"ok": True}


async def _fail_receipt(args: dict) -> dict:
    """Resolve a queue entry as failed and let the chat know."""
    receipt_queue.resolve_receipt(args["receipt_id"], status="failed")
    bot = Bot(token=BOT_TOKEN)
    await bot.send_message(
        args["chat_id"], "Sorry, I couldn't process that receipt (parsing error). Please try again."
    )
    return {"ok": True}


_SYNC_OPS = {
    "get_pending_receipts": lambda args: receipt_queue.get_pending_receipts(),
    "get_shopping_list_names": lambda args: [i["name"] for i in shopping_list.get_all_items()],
}
_ASYNC_OPS = {
    "finish_receipt": _finish_receipt,
    "fail_receipt": _fail_receipt,
}


def main():
    """Dispatch: `python -m db.remote_cli <op> [json-args]`, prints JSON to stdout."""
    op = sys.argv[1]
    args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    if op in _SYNC_OPS:
        result = _SYNC_OPS[op](args)
    elif op in _ASYNC_OPS:
        result = asyncio.run(_ASYNC_OPS[op](args))
    else:
        raise SystemExit(f"Unknown op: {op}")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
