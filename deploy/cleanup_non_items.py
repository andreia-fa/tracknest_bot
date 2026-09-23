"""Delete inventory items that are really receipt non-items (Pfand, Normalpreis...).

Dry run by default; pass --apply to delete. Deleting cascades to the
items' expense rows, so they also drop out of spend totals.
"""
import sys

from bot.receipt_lines import classify_line
from db import crud

apply = "--apply" in sys.argv
bogus = [i["name"] for i in crud.get_all_items() if classify_line(i["name"]) != "item"]
if not bogus:
    print("Nothing to clean up.")
for name in bogus:
    if apply:
        crud.delete_item(name)
    print(("Deleted: " if apply else "Would delete: ") + name)
if bogus and not apply:
    print("\nRe-run with --apply to delete these.")
