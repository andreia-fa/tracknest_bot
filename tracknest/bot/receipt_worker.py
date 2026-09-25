"""Local worker: processes receipts queued by the cloud bot using this machine's Ollama.

Runs continuously. Talks to the cloud VM's DB only via SSH + `docker exec`
against the running container (db/remote_cli.py) — the SQLite file itself
is never touched over the network, only small JSON payloads are. Downloads
each receipt photo directly from Telegram (works from any machine with the
bot token, independent of which process is long-polling for updates).
"""

import asyncio
import json
import logging
import subprocess

from bot.receipt import guess_products, parse_receipt
from config import BOT_TOKEN
from telegram import Bot

logger = logging.getLogger(__name__)

_SSH_HOST = "oracle-tracknest"
_CONTAINER = "tracknest-bot"
_POLL_INTERVAL_SECONDS = 60
_SSH_TIMEOUT_SECONDS = 30
_PRODUCT_GUESS_BATCH = 15


def _remote_call(op: str, args: dict | None = None):
    """Run a db.remote_cli operation inside the cloud container over SSH.

    Args are sent on stdin, not as a trailing argv string — ssh joins
    trailing arguments into one string for the remote shell to re-parse,
    which would word-split a JSON payload on its spaces and strip its
    quotes before db.remote_cli ever saw it (`docker exec -i` is required
    for the container to receive that stdin at all).

    Raises:
        subprocess.CalledProcessError: If the SSH/docker exec call fails.
    """
    try:
        result = subprocess.run(
            ["ssh", _SSH_HOST, "docker", "exec", "-i", _CONTAINER, "python", "-m", "db.remote_cli", op],
            input=json.dumps(args or {}),
            capture_output=True, text=True, check=True, timeout=_SSH_TIMEOUT_SECONDS,
        )
    except subprocess.CalledProcessError as e:
        logger.error("Remote call %r failed (exit %s): %s", op, e.returncode, e.stderr.strip())
        raise
    return json.loads(result.stdout)


async def _process_one(bot: Bot, receipt: dict):
    """Download, parse, and hand off a single queued receipt.

    Any failure here — parsing or the remote write — is contained to this
    one receipt (marked failed via fail_receipt) so a single bad receipt
    can't block every receipt queued behind it.
    """
    receipt_id, chat_id, file_id = receipt["id"], receipt["chat_id"], receipt["telegram_file_id"]
    try:
        telegram_file = await bot.get_file(file_id)
        image_bytes = bytes(await telegram_file.download_as_bytearray())
        shopping_list_names = _remote_call("get_shopping_list_names")
        parsed = await asyncio.to_thread(parse_receipt, image_bytes, shopping_list_names)
        _remote_call("finish_receipt", {"receipt_id": receipt_id, "chat_id": chat_id, "parsed": parsed})
    except Exception:
        logger.exception("Failed to process queued receipt %s", receipt_id)
        _remote_call("fail_receipt", {"receipt_id": receipt_id, "chat_id": chat_id})
        return
    logger.info("Receipt %s processed and logged.", receipt_id)


async def _backfill_products():
    """Guess products for items logged before products existed, one batch per idle cycle.

    The guesses only queue questions — each is confirmed by the user — and
    an item leaves the backlog as soon as it's queued, so this stops by
    itself once everything has been through it.
    """
    names = _remote_call("get_items_without_product")[:_PRODUCT_GUESS_BATCH]
    if not names:
        return
    guesses = await asyncio.to_thread(guess_products, names)
    # Anything the model skipped still gets asked, just without a guess.
    guesses = {name: guesses.get(name, "") for name in names}
    result = _remote_call("suggest_products", {"guesses": guesses})
    logger.info("Queued product questions for %d existing item(s).", result["updated"])


async def run_once(bot: Bot):
    """Process every receipt currently queued, oldest first; when idle, backfill products."""
    receipts = _remote_call("get_pending_receipts")
    for receipt in receipts:
        await _process_one(bot, receipt)
    if not receipts:
        await _backfill_products()


async def main():
    bot = Bot(token=BOT_TOKEN)
    logger.info("Receipt worker started, polling every %ss.", _POLL_INTERVAL_SECONDS)
    while True:
        try:
            await run_once(bot)
        except Exception:
            logger.exception("Poll cycle failed — will retry next interval.")
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        level=logging.INFO,
    )
    # httpx logs the full request URL at INFO, which for Telegram's API
    # embeds the bot token — keep it out of the journal.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(main())
