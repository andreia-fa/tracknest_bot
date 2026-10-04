"""One-off (2026-10-05): add the €1.80 extra patty the Burger King receipt (receipt:20) lost.

The receipt's total was €7.98 (user, from the photo): burger 2.19 + extra
plant-based patty 1.80 + iced matcha 3.99. The model folded the patty into
the burger's name and dropped its price, so only €6.18 was logged. Adds the
patty as its own line on the same receipt/trip, with the burger's category
and profile, so it opens no new question. Refuses unless the trip still sums
to €6.18. Backs the DB up first.
Run inside the bot container:
    ssh oracle-tracknest docker exec -i tracknest-bot python - < deploy/add_bk_patty_2026_10_05.py
"""

import sys
from datetime import datetime

sys.path.insert(0, "/app")

from db import crud, expenses  # noqa: E402
from db.database import backup_db, get_connection  # noqa: E402

TRIP = "receipt:20"
PATTY = "Extra Plant-Based Patty"


def trip_rows():
    conn = get_connection()
    rows = conn.execute("""
        SELECT i.name, i.category, i.treat_or_need, i.lasts, e.store, e.logged_at,
               e.quantity_purchased * e.unit_price AS total
        FROM item_expenses e JOIN inventory_items i ON i.id = e.item_id WHERE e.trip_key = ?
    """, (TRIP,)).fetchall()
    conn.close()
    return rows


rows = trip_rows()
for r in rows:
    print(dict(r))
total = round(sum(r["total"] for r in rows), 2)
print("Trip total now:", total)
if total != 6.18:
    raise SystemExit("Trip doesn't sum to €6.18 any more — nothing changed, check by hand")
burger = next((r for r in rows if "burger" in r["name"].lower()), None)
if burger is None or crud.get_item(PATTY) is not None:
    raise SystemExit("No burger line, or the patty item already exists — nothing changed")

print("Backed up to", backup_db("before-add-bk-patty"))
crud.add_item(PATTY, 1, category=burger["category"], product="burger")
crud.set_treat_or_need(PATTY, burger["treat_or_need"] if burger["treat_or_need"] != "unknown" else "treat")
if burger["lasts"] == "same_day" or burger["lasts"] == "unknown":
    crud.set_lasts(PATTY, "same_day")
expenses.log_expense(PATTY, 1, 1.80, store=burger["store"], trip_key=TRIP,
                     purchased_at=datetime.fromisoformat(burger["logged_at"]))
print("Trip total now:", round(sum(r["total"] for r in trip_rows()), 2))
print("Open profile question:", crud.get_pending_profile_item())
