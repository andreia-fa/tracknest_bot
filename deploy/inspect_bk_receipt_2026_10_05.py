"""Read-only (2026-10-05): what the Burger King receipt logged, and which question is open.

Opens the DB read-only. Run inside the bot container:
    ssh oracle-tracknest docker exec -i tracknest-bot python - < deploy/inspect_bk_receipt_2026_10_05.py
"""

import sys

sys.path.insert(0, "/app")

import sqlite3  # noqa: E402

from db import crud  # noqa: E402

print("Open profile question:", crud.get_pending_profile_item())
conn = sqlite3.connect("file:/app/data/tracknest.db?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
print("\nItems with 'sock' in the name:",
      [dict(r) for r in conn.execute("SELECT id, name, name_status FROM inventory_items WHERE lower(name) LIKE '%sock%'")])
print("\nEvery purchase on 2026-10-03/04 (date, store, trip, item, qty, price):")
for r in conn.execute("""
    SELECT e.purchase_date, e.store, e.trip_key, i.name, e.quantity_purchased, e.unit_price, e.logged_at
    FROM item_expenses e JOIN inventory_items i ON i.id = e.item_id
    WHERE e.purchase_date IN ('2026-10-03', '2026-10-04') ORDER BY e.trip_key, e.id
"""):
    print(dict(r))
print("\nReceipts queued since 2026-10-03:")
for r in conn.execute("SELECT id, status, queued_at, resolved_at FROM pending_receipts WHERE queued_at >= '2026-10-03'"):
    print(dict(r))
conn.close()
