"""One-off (2026-10-05): put the user's items for the 2026-10-06 shop on the shopping list.

Asked for in chat by the user. Skips anything already listed (by name or as a typo
of a listed name), so nothing is doubled. Backs the DB up first.
Run inside the bot container:
    ssh oracle-tracknest docker exec -i tracknest-bot python - < deploy/add_shop_2026_10_06.py
"""

import sys

sys.path.insert(0, "/app")

from bot.categorize import infer_category  # noqa: E402
from bot.list_match import same_kind  # noqa: E402
from bot.name_match import match_known  # noqa: E402
from db import shopping_list  # noqa: E402
from db.database import backup_db  # noqa: E402

WANTED = ["coloured eggs", "broccoli", "frozen strawberries", "salmon", "panty liners"]

print("Backed up to", backup_db("before-add-shop-2026-10-06"))
listed = [entry["name"] for entry in shopping_list.get_all_items()]
for name in WANTED:
    already = (next((e for e in listed if e.casefold() == name.casefold()), None) or match_known(name, listed)
               or next((e for e in listed if same_kind(e, [name])), None))
    if already:
        print(f"{name}: already on the list as {already!r}")
        continue
    shopping_list.add_item(name, 1, category=infer_category(name))
    print(f"{name}: added")
print("\nList now:", ", ".join(entry["name"] for entry in shopping_list.get_all_items()))
