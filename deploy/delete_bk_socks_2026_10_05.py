"""One-off (2026-10-05): delete the Burger King line the receipt model named "Socks - decathlon".

The model copied the shopping-list entry as the name of an unreadable €3.99
Burger King line. The user doesn't know what it really was and chose to
delete it. This removes the item and that one purchase (cascade), which also
closes its open "what is it?" question. The shopping-list entry stays —
socks are still to buy. Backs the DB up first, and refuses unless the item
holds exactly that one Burger King purchase.
Run inside the bot container:
    ssh oracle-tracknest docker exec -i tracknest-bot python - < deploy/delete_bk_socks_2026_10_05.py
"""

import sys

sys.path.insert(0, "/app")

from db import crud, shopping_list  # noqa: E402
from db.database import backup_db, get_connection  # noqa: E402

NAME = "Socks - decathlon"

if crud.get_item(NAME) is None:
    raise SystemExit("Item not found — nothing changed")
conn = get_connection()
rows = conn.execute("""
    SELECT e.store, e.unit_price, e.quantity_purchased, e.purchase_date FROM item_expenses e
    JOIN inventory_items i ON i.id = e.item_id WHERE i.name = ?
""", (NAME,)).fetchall()
conn.close()
print("Purchases on this item:", [dict(r) for r in rows])
if len(rows) != 1 or "burger" not in (rows[0]["store"] or "").lower():
    raise SystemExit("Not exactly the one Burger King purchase — nothing changed, check by hand")

print("Backed up to", backup_db("before-delete-bk-socks"))
crud.delete_item(NAME)
print("Deleted. Item still exists:", crud.get_item(NAME) is not None)
print("Still on the list:", [e["name"] for e in shopping_list.get_all_items() if "sock" in e["name"].lower()])
