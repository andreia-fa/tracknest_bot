"""One-off (2026-10-05): file the Burger King receipt's lines correctly.

The keyword guess put the iced matcha in Fruits/Veg ("mango") and the
burger in Meat/Fish as a need. By the user's rules: café drinks are Leisure,
ready meals and eating out are treats. Backs the DB up first.
Run inside the bot container:
    ssh oracle-tracknest docker exec -i tracknest-bot python - < deploy/fix_bk_categories_2026_10_05.py
"""

import sys

sys.path.insert(0, "/app")

from db import crud  # noqa: E402
from db.database import backup_db  # noqa: E402

FIXES = {
    "Iced Matcha Mango Oatly medium Disposable Cup": "Leisure",
    "Plant-Based Hamburger Remove Mustard Remove Pickles Extra Plant-Based Patty": "Ready Meals",
    "Extra Plant-Based Patty": "Ready Meals",
}
missing = [name for name in FIXES if crud.get_item(name) is None]
if missing:
    raise SystemExit(f"Not found: {missing} — nothing changed")
print("Backed up to", backup_db("before-fix-bk-categories"))
for name, category in FIXES.items():
    crud.set_item_category(name, category)
    crud.set_treat_or_need(name, "treat")
    crud.set_lasts(name, "same_day")
    item = crud.get_item(name)
    print(name, "→", item["category"], item["treat_or_need"], item["lasts"])
print("Open profile question:", crud.get_pending_profile_item())
