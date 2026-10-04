"""One-off (2026-10-05): re-read the Giulia Pizza receipt (queue id 21), which logged only "Nettoumsatz".

The model logged the tax box's net figure as the only product. The paper
receipt is gone, but the photo's Telegram file_id still works, so: delete the
wrong line (only if it's exactly that one purchase from receipt 21) and queue
the same photo again. The worker re-reads it and the user reviews the result
before anything is saved. Backs the DB up first.
Run inside the bot container:
    ssh oracle-tracknest docker exec -i tracknest-bot python - < deploy/reread_pizza_receipt_2026_10_05.py
"""

import sys

sys.path.insert(0, "/app")

from db import crud, receipt_queue  # noqa: E402
from db.database import backup_db, get_connection  # noqa: E402

WRONG = "Nettoumsatz"
RECEIPT_ID = 21

conn = get_connection()
receipt = conn.execute("SELECT chat_id, telegram_file_id, status FROM pending_receipts WHERE id = ?",
                       (RECEIPT_ID,)).fetchone()
rows = conn.execute("""
    SELECT e.trip_key, e.store, e.unit_price FROM item_expenses e
    JOIN inventory_items i ON i.id = e.item_id WHERE i.name = ?
""", (WRONG,)).fetchall()
conn.close()
print("Receipt:", dict(receipt) if receipt else None)
print("Purchases on", repr(WRONG), [dict(r) for r in rows])
if not receipt:
    raise SystemExit("Receipt not found — nothing changed")
if len(rows) != 1 or rows[0]["trip_key"] != f"receipt:{RECEIPT_ID}":
    raise SystemExit("Not exactly the one line from that receipt — nothing changed, check by hand")

print("Backed up to", backup_db("before-reread-pizza-receipt"))
crud.delete_item(WRONG)
new_id = receipt_queue.queue_receipt(receipt["chat_id"], receipt["telegram_file_id"])
print(f"Deleted {WRONG!r}; the photo is queued again as receipt {new_id} — a preview follows once it's read.")
