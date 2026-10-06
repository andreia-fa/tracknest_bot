"""Read-only (2026-10-06): receipts whose date the model couldn't read, logged as "today".

Opens the DB read-only. Run inside the bot container:
    ssh oracle-tracknest docker exec -i tracknest-bot python - < deploy/inspect_receipt_dates_2026_10_06.py
"""

import json
import sqlite3

conn = sqlite3.connect("file:/app/data/tracknest.db?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
print("Receipts queued since 2026-10-04 (id, status, queued, resolved, store, date read, lines):")
for r in conn.execute("SELECT id, status, queued_at, resolved_at, parsed FROM pending_receipts "
                      "WHERE queued_at >= '2026-10-04' ORDER BY id"):
    parsed = json.loads(r["parsed"]) if r["parsed"] else {}
    print(r["id"], r["status"], r["queued_at"], r["resolved_at"], parsed.get("store"),
          parsed.get("purchase_date"), len(parsed.get("items", [])))
print("\nPurchases dated 2026-10-05/06 (date, store, trip, item, qty, price, logged_at):")
for r in conn.execute("""
    SELECT e.purchase_date, e.store, e.trip_key, i.name, e.quantity_purchased, e.unit_price, e.logged_at
    FROM item_expenses e JOIN inventory_items i ON i.id = e.item_id
    WHERE e.purchase_date IN ('2026-10-05', '2026-10-06') ORDER BY e.trip_key, e.id
"""):
    print(tuple(r))
conn.close()
