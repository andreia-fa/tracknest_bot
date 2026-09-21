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

from bot.receipt import parse_receipt
from config import BOT_TOKEN
from telegram import Bot

logger = logging.getLogger(__name__)

_SSH_HOST = "oracle-tracknest"
_CONTAINER = "tracknest-bot"
_POLL_INTERVAL_SECONDS = 60
_SSH_TIMEOUT_SECONDS = 30


def _remote_call(op: str, args: dict | None = None):
    """Run a db.remote_cli operation inside the cloud container over SSH.

    Raises:
        subprocess.CalledProcessError: If the SSH/docker exec call fails.
    """
    result = subprocess.run(
        ["ssh", _SSH_HOST, "docker", "exec", _CONTAINER, "python", "-m", "db.remote_cli",
         op, json.dumps(args or {})],
        capture_output=True, text=True, check=True, timeout=_SSH_TIMEOUT_SECONDS,
    )
    return json.loads(result.stdout)


async def _process_one(bot: Bot, receipt: dict):
    """Download, parse, and hand off a single queued receipt."""
    receipt_id, chat_id, file_id = receipt["id"], receipt["chat_id"], receipt["telegram_file_id"]
    try:
        telegram_file = await bot.get_file(file_id)
        image_bytes = bytes(await telegram_file.download_as_bytearray())
        shopping_list_names = _remote_call("get_shopping_list_names")
        parsed = await asyncio.to_thread(parse_receipt, image_bytes, shopping_list_names)
    except Exception:
        logger.exception("Failed to process queued receipt %s", receipt_id)
        _remote_call("fail_receipt", {"receipt_id": receipt_id, "chat_id": chat_id})
        return
    _remote_call("finish_receipt", {"receipt_id": receipt_id, "chat_id": chat_id, "parsed": parsed})
    logger.info("Receipt %s processed and logged.", receipt_id)


async def run_once(bot: Bot):
    """Process every receipt currently queued, oldest first."""
    for receipt in _remote_call("get_pending_receipts"):
        await _process_one(bot, receipt)


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
    asyncio.run(main())
