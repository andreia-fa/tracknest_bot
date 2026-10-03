"""One-off (2026-10-03): products, classifications, and the pfefferbretzel duplicate.

- Jessa "SE" are Slipeinlagen (panty liners); VOLLKORNTORTILLA is a wrap;
  Pool was a swimming-pool ticket; the mango "pudding" is a yogurt.
- Purchase types the user gave for the items never classified.
- "pfefferbretzel" merges into "Pfefferbretzel" (purchases moved over,
  stock added up), so item names can become case-insensitive.

Backs the DB up first and applies everything in one transaction. Run inside the bot container:
    ssh oracle-tracknest docker exec -i tracknest-bot python - < deploy/fix_items_2026_10_03.py
"""

import sqlite3
from datetime import datetime
from pathlib import Path

DB = Path("/app/data/tracknest.db")
BACKUP_DIR = DB.parent / "backups"

# name -> (product, category); None keeps the current value
PRODUCTS = {
    "Jessa SE Cotton Normal": ("panty liners", None),
    "VOLLKORNTORTILLA": ("wrap", None),
    "Pool": ("swimming pool ticket", "Other"),
    "HP PUD.DL CHOCO V SKYR STYLE MANGO": ("yogurt", "Dairy"),
    "Kuchenbeleg": ("cake", None),
}
# name -> (purchase_type, shelf_life_days); a necessity is used up the same day
PROFILES = {
    "HP PUD.DL CHOCO V SKYR STYLE MANGO": ("essential", 14),
    "JA! MIWA STILL": ("necessity", 1),
    "Kuchenbeleg": ("necessity", 1),
    "Brotstücker": ("necessity", 1),
    "Wrap 190g (Leerdammer cheese)": ("necessity", 1),
    "Pizza Margherita": ("necessity", 1),
    "LAUGENBREZEL": ("necessity", 1),
    "TWISTER KAKAO": ("necessity", 1),
}
MERGES = {"pfefferbretzel": "Pfefferbretzel"}  # duplicate -> item it merges into

BACKUP_DIR.mkdir(exist_ok=True)
backup = BACKUP_DIR / f"tracknest-{datetime.now():%Y%m%d-%H%M%S}-before-fix-items.db"
src = sqlite3.connect(DB)
dst = sqlite3.connect(backup)
src.backup(dst)
dst.close()
print(f"Backup: {backup}")

src.row_factory = sqlite3.Row
with src:  # one transaction: commits on success, rolls back on any error
    for name, (product, category) in PRODUCTS.items():
        row = src.execute("SELECT product, category FROM inventory_items WHERE name = ?", (name,)).fetchone()
        if row is None:
            print(f"  skipped (not found): {name}")
            continue
        category = category or row["category"]
        src.execute("UPDATE inventory_items SET product = ?, category = ? WHERE name = ?", (product, category, name))
        src.execute(
            "UPDATE item_aliases SET product = ?, category = ? WHERE canonical_name = ?", (product, category, name)
        )
        print(f"  {name}: {row['product']!r}/{row['category']} -> {product!r}/{category}")
    for name, (purchase_type, days) in PROFILES.items():
        n = src.execute(
            "UPDATE inventory_items SET purchase_type = ?, shelf_life_days = ? WHERE name = ?",
            (purchase_type, days, name),
        ).rowcount
        print(f"  {name}: {purchase_type}, lasts {days} day(s)" if n else f"  skipped (not found): {name}")
    for dup, keep in MERGES.items():
        old = src.execute("SELECT id, quantity FROM inventory_items WHERE name = ?", (dup,)).fetchone()
        new = src.execute("SELECT id FROM inventory_items WHERE name = ?", (keep,)).fetchone()
        if old is None or new is None:
            print(f"  skipped merge (not found): {dup} -> {keep}")
            continue
        moved = src.execute("UPDATE item_expenses SET item_id = ? WHERE item_id = ?", (new["id"], old["id"])).rowcount
        src.execute("UPDATE inventory_items SET quantity = quantity + ? WHERE id = ?", (old["quantity"], new["id"]))
        src.execute("UPDATE item_aliases SET canonical_name = ? WHERE canonical_name = ?", (keep, dup))
        src.execute("DELETE FROM inventory_items WHERE id = ?", (old["id"],))
        print(f"  merged {dup} into {keep} ({moved} purchase(s) moved)")
src.close()
print("Done.")
