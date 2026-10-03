"""One-off (2026-10-03): treat-or-need for the items that used to be "necessity"; drop Brotstücker.

Run AFTER the bot is deployed with the treat_or_need/lasts split (the old
"necessity" items come out of that migration as treat_or_need 'unknown').
The user's rule: a need is a raw staple you can't live without
(vegetables, salmon, tofu, rice, water, bread); pretzels, pastries, cake,
ready meals and outings are treats.

Brotstücker and Kuchenbeleg were both €3.18 on the same REWE receipt,
logged in the same second — most likely one line read twice. The user
wasn't sure, so Brotstücker (and its one purchase) goes.

Backs the DB up first and applies everything in one transaction. Run inside the bot container:
    ssh oracle-tracknest docker exec -i tracknest-bot python - < deploy/treat_or_need_2026_10_03.py
"""

from db.database import backup_db, get_connection

TREATS = [
    "Pfefferbretzel", "Pfefferbreze", "LAUGENBREZEL", "Zimtschenecke", "TWISTER KAKAO",
    "Kuchenbeleg", "Pizza Margherita", "Wrap 190g (Leerdammer cheese)", "Matcha", "Pool",
]
NEEDS = ["Water Bottle", "JA! MIWA STILL"]
DELETE = "Brotstücker"

print(f"Backup: {backup_db('before-treat-or-need')}")
conn = get_connection()
with conn:  # one transaction: commits on success, rolls back on any error
    for kind, names in (("treat", TREATS), ("need", NEEDS)):
        for name in names:
            n = conn.execute("UPDATE inventory_items SET treat_or_need = ? WHERE name = ?", (kind, name)).rowcount
            print(f"  {name}: {kind}" if n else f"  skipped (not found): {name}")
    item = conn.execute("SELECT id FROM inventory_items WHERE name = ?", (DELETE,)).fetchone()
    if item:
        gone = conn.execute("DELETE FROM item_expenses WHERE item_id = ?", (item["id"],)).rowcount
        conn.execute("DELETE FROM inventory_items WHERE id = ?", (item["id"],))
        print(f"  deleted {DELETE} and its {gone} purchase(s)")
    else:
        print(f"  skipped (not found): {DELETE}")
    left = [r["name"] for r in conn.execute(
        "SELECT name FROM inventory_items WHERE treat_or_need = 'unknown' OR lasts = 'unknown'")]
    print(f"  still unsorted: {', '.join(left) if left else 'nothing'}")
conn.close()
print("Done.")
