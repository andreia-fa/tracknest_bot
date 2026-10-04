"""One-off (2026-10-05): add Knusperbrot to the shopping list, asked for in chat.

Uses the household's own spelling if the item is already known (typos
forgiven), skips it if already listed. Backs the DB up first.
Run inside the bot container:
    ssh oracle-tracknest docker exec -i tracknest-bot python - < deploy/add_knusperbrot_2026_10_05.py
"""

import sys

sys.path.insert(0, "/app")

from bot.categorize import infer_category  # noqa: E402
from bot.name_match import match_known  # noqa: E402
from db import crud, shopping_list  # noqa: E402
from db.database import backup_db  # noqa: E402

name = match_known("Knusperbrot", crud.get_item_names()) or "Knusperbrot"
listed = [entry["name"] for entry in shopping_list.get_all_items()]
already = match_known(name, listed) or next((e for e in listed if e.casefold() == name.casefold()), None)
if already:
    print(f"Already on the list as {already!r}")
else:
    print("Backed up to", backup_db("before-add-knusperbrot"))
    shopping_list.add_item(name, 1, category=infer_category(name))
    print(f"Added {name!r}")
print("List now:", ", ".join(entry["name"] for entry in shopping_list.get_all_items()))
