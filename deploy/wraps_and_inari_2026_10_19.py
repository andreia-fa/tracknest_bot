"""One-off (2026-10-19): tuna wraps to Ready Meals; the Inari sushi's product tidied.

User agreed: a filled wrap is eaten as it is, so it's a ready meal, not
fish to cook. And the Inari's product had been saved as a whole sentence
("it's actually sushi. salmon sushi"). Uses crud's own helpers, which update
the receipt-wording aliases too, so the next receipt lands right. Backs the
DB up first. Run inside the bot container:
    ssh oracle-tracknest docker exec -i tracknest-bot python - < deploy/wraps_and_inari_2026_10_19.py
"""

import sys

sys.path.insert(0, "/app/tracknest")

from db import crud  # noqa: E402
from db.database import backup_db  # noqa: E402

print("Backed up to", backup_db("before-wraps-and-inari"))
for name in ("WRAP THUNFISCH", "Wrap 190g"):
    print(name, "-> Ready Meals:", crud.change_item_category(name, "Ready Meals"))
crud.set_item_product("INARI TOFU-LACHS A", "sushi")
item = crud.get_item("INARI TOFU-LACHS A")
print("INARI TOFU-LACHS A product:", item["product"], "·", item["category"])
