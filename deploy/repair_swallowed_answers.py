"""One-off (2026-10-03): undo the damage from the product backfill flood.

The backfill (8417887) queued 32 "what is this?" questions at once, and
handle_text read every shopping-list line typed meanwhile as the answer to
the next one — so items got products like "bananas"/"salmon" (and their
categories re-guessed from those), the backfill's own guesses were mostly
garbage, and the questions kept firing. This sets each item's real product
and category, fixes the aliases that learned the wrong answers, and clears
the naming flags and stale check-ins.

Backs the DB up first and applies everything in one transaction — either
all of it lands or none of it does. Run inside the bot container:
    ssh oracle-tracknest docker exec -i tracknest-bot python - < deploy/repair_swallowed_answers.py
"""

import sqlite3
from datetime import datetime
from pathlib import Path

DB = Path("/app/data/tracknest.db")
BACKUP_DIR = DB.parent / "backups"

# name -> (product, category or None to keep the current one)
FIXES = {
    "PROTEINBROETCHEN": ("bread roll", "Bread/Bakery"),
    "HAPPY CALIF. VEG": ("sushi", "Ready Meals"),
    "BABYSPINAT BIO": ("spinach", "Fruits/Veg"),
    "ERDNUSSMUS": ("peanut butter", "Pantry"),
    "dmBio schoko. Himbeeren 150g*": ("chocolate raspberries", "Snacks"),
    "BANANE": ("banana", None),
    "LEERDAMMER CAR.": ("cheese", None),
    "EIER MARM.": ("eggs", None),
    "HP PUDD. CHOCO V": ("pudding", None),
    "HP PUDDING": ("pudding", None),
    "Pfefferbreze": ("pretzel", None),
    "Push Up Bra": ("bra", None),
    "Haarprodukt": ("hair product", None),
    "BIO Ain.Pfanne": ("vegetable stir-fry mix", None),
    "Milram Käse Scheiben": ("sliced cheese", None),
    "J.Tag Käseaufschnitt": ("sliced cheese", None),
    "RAEUCHERLACHS": ("smoked salmon", None),
    "Berida Garnele": ("shrimp", None),
    "Greenl. Gemüse": ("vegetables", None),
    "Naturgut Broccol": ("broccoli", None),
    "Greenl. Erdbeere": ("strawberries", None),
    "Water Bottle": ("water", None),
    "Matcha": ("matcha", "Beverages"),
    "WELEDA HANDCREME": ("hand cream", None),
    "Pfefferbretzel": ("pretzel", "Bread/Bakery"),
    "pfefferbretzel": ("pretzel", "Bread/Bakery"),
    "Zimtschenecke": ("cinnamon roll", "Bread/Bakery"),
    "Pool": ("pool", None),
    "HP PUD.DL CHOCO V SKYR STYLE MANGO": ("pudding", "Snacks"),
    "Kuchenbeleg": ("cake topping", None),
    "Brotstücker": ("bread", None),
    "VOLLKORNTORTILLA": ("tortilla", "Bread/Bakery"),
    "Wrap 190g (Leerdammer cheese)": ("wrap", "Ready Meals"),
    "Pizza Margherita": ("pizza", None),
    "LAUGENBREZEL": ("pretzel", None),
    "TWISTER KAKAO": ("cinnamon roll", "Bread/Bakery"),  # a Zimtschnecke rung up wrong at the till
    "JA! MIWA STILL": ("still water", None),
}

BACKUP_DIR.mkdir(exist_ok=True)
backup = BACKUP_DIR / f"tracknest-{datetime.now():%Y%m%d-%H%M%S}-before-repair.db"
src = sqlite3.connect(DB)
dst = sqlite3.connect(backup)
src.backup(dst)
dst.close()
print(f"Backup: {backup}")

src.row_factory = sqlite3.Row
with src:  # one transaction: commits on success, rolls back on any error
    for name, (product, category) in FIXES.items():
        row = src.execute("SELECT product, category FROM inventory_items WHERE name = ?", (name,)).fetchone()
        if row is None:
            print(f"  skipped (not found): {name}")
            continue
        category = category or row["category"]
        src.execute(
            "UPDATE inventory_items SET product = ?, category = ?, name_status = NULL WHERE name = ?",
            (product, category, name),
        )
        src.execute(
            "UPDATE item_aliases SET product = ?, category = ? WHERE canonical_name = ? AND product IS NOT NULL",
            (product, category, name),
        )
        print(f"  {name}: {row['product']!r}/{row['category']} -> {product!r}/{category}")
    left = src.execute("UPDATE inventory_items SET name_status = NULL WHERE name_status IS NOT NULL").rowcount
    checkins = src.execute("UPDATE inventory_items SET checkin_pending = 0 WHERE checkin_pending = 1").rowcount
    print(f"  cleared {left} other naming flags, {checkins} stale check-ins")
src.close()
print("Done.")
