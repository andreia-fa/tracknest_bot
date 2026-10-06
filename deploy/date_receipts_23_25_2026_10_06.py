"""Repair (2026-10-06): date receipts #23 (Yormas) and #25 (REWE) 5 Oct, add #25's missed line.

Both were read before the date fix, which dropped "05. 10. 2026" and fell
back to today; #25's reading also skipped "WRAP THUNFISCH 1,99" (it's on the
photo; every re-read found it). The user confirmed all three receipts are
from 5 Oct and asked for the line to be added. Both stay under review: the
fresh review is sent to the chat and nothing is saved until the user taps ✅.

Backs up first, writes both in one transaction, and stops if either reading
isn't what was inspected. Run inside the bot container:
    ssh oracle-tracknest docker exec -i tracknest-bot python - < deploy/date_receipts_23_25_2026_10_06.py
"""

import asyncio
import json
import sys

sys.path.insert(0, "/app")

from telegram import Bot  # noqa: E402

from bot.main import receipt_review  # noqa: E402
from config import BOT_TOKEN  # noqa: E402
from db import database, receipt_queue  # noqa: E402

DATE = "2026-10-05"
WRAP = {"name": "WRAP THUNFISCH", "quantity": 1, "unit_price": 1.99, "category": "Other",
        "product": "", "unsure": False, "alternatives": []}

conn = database.get_connection()
rows = {r["id"]: r for r in conn.execute("SELECT id, chat_id, status, parsed FROM pending_receipts WHERE id IN (23, 25)")}
readings = {rid: json.loads(rows[rid]["parsed"]) for rid in rows if rows[rid]["status"] == "review"}
names_25 = [i["name"] for i in readings.get(25, {}).get("items", [])]
if (set(readings) != {23, 25} or any(r.get("purchase_date") for r in readings.values())
        or names_25 != ["EMMENTALER SCHB.", "RAEUCHERTOFU", "EIER MARM."]):
    sys.exit(f"Not what was inspected — nothing changed. {readings}")

print("Backup:", database.backup_db("date-receipts-23-25"))
readings[23]["purchase_date"] = DATE
readings[25]["purchase_date"] = DATE
readings[25]["items"].insert(2, WRAP)  # printed between the tofu and the eggs
with conn:  # one transaction
    for rid, parsed in readings.items():
        parsed["items_total"] = round(sum(i["quantity"] * i["unit_price"] for i in parsed["items"]), 2)
        parsed["reconciled"] = abs(parsed["items_total"] - parsed["total_paid"]) <= 0.02
        conn.execute("UPDATE pending_receipts SET parsed = ? WHERE id = ? AND status = 'review'",
                     (json.dumps(parsed), rid))
conn.close()


async def resend():
    bot = Bot(token=BOT_TOKEN)
    for rid in (23, 25):
        parsed = receipt_queue.get_review(rid)
        text, markup = receipt_review(rid, parsed)
        print(f"\n#{rid}:\n{text}")
        await bot.send_message(rows[rid]["chat_id"], text, reply_markup=markup)

asyncio.run(resend())
