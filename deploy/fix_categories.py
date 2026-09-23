"""Repair inventory categories the receipt model filled with junk (VAT codes "A"/"B").

Dry run by default; pass --apply to write. Only clearly broken categories
are touched (empty, one or two characters, or just the item's own name):
those the keyword list recognises get its category, the rest are flagged so
the bot asks "what is it?" — same question a brand-new receipt item gets.
"""
import sys

from bot.categorize import infer_category
from db import crud
from db.database import get_connection

apply = "--apply" in sys.argv
broken = [
    item for item in crud.get_all_items()
    if not item["category"] or len(item["category"].strip()) <= 2 or item["category"] == item["name"]
]
if not broken:
    print("Nothing to fix.")
for item in broken:
    guess = infer_category(item["name"])
    if guess != "Other":
        action = f"-> {guess}"
        if apply:
            conn = get_connection()
            conn.execute("UPDATE inventory_items SET category = ? WHERE name = ?", (guess, item["name"]))
            conn.commit()
            conn.close()
    else:
        action = "-> will ask you what it is"
        if apply:
            crud.mark_name_pending(item["name"])
    print(f"{'Fixed' if apply else 'Would fix'}: {item['name']} ({item['category']!r}) {action}")
if broken and not apply:
    print("\nRe-run with --apply to write these.")
