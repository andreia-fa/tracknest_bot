"""One-off (2026-10-04): send SCHLAGCREME VEGA back through the new-item questions.

The receipt model read it as "cheese", and before d218802 that guess was
used straight away: it copied the cheese's "need, lasts 7 days" (and got
filed under Snacks) before anyone confirmed what it is. The new flow only
fills blanks, so this blanks those answers, offers the readings it really
could be as buttons, and re-sends the "what is it?" question.

Backs the DB up first and applies the change in one transaction. Run inside
the bot container:
    ssh oracle-tracknest docker exec -i tracknest-bot python - < deploy/repair_schlagcreme_2026_10_04.py
"""

import asyncio
import sys

sys.path.insert(0, "/app")

from db import crud, settings  # noqa: E402
from db.database import backup_db, get_connection  # noqa: E402

NAME = "SCHLAGCREME VEGA"
OPTIONS = ["vegan whipping cream", "whipping cream", "plant-based cream"]

print("Backed up to", backup_db("before-repair-schlagcreme"))
conn = get_connection()
try:
    conn.execute("BEGIN")
    updated = conn.execute("""
        UPDATE inventory_items
        SET treat_or_need = 'unknown', lasts = 'unknown', shelf_life_days = NULL, category = NULL,
            checkin_pending = 0, spare_alert_pending = 0, name_status = 'name'
        WHERE name = ?
    """, (NAME,)).rowcount
    if updated != 1:
        raise RuntimeError(f"expected 1 item named {NAME!r}, found {updated}")
    # An alias would teach the next receipt the wrong answers.
    conn.execute("DELETE FROM item_aliases WHERE receipt_name = ? AND product = 'cheese'", (NAME,))
    conn.commit()
except Exception:
    conn.rollback()
    raise
finally:
    conn.close()
crud.set_product_options(NAME, OPTIONS)
print("Repaired:", {k: crud.get_item(NAME)[k] for k in ("product", "category", "treat_or_need", "lasts", "name_status")})
print("Next question is about:", crud.get_pending_profile_item())


async def ask():
    from telegram import Bot

    from bot.main import send_pending_profile_question
    from config import BOT_TOKEN

    async with Bot(BOT_TOKEN) as bot:
        await send_pending_profile_question(bot, settings.get_chat_id(), force=True)


asyncio.run(ask())
print("Question sent.")
