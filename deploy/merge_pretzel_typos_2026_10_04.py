"""One-off (2026-10-04): fold the misspelled pretzel items into one item each.

Typos and receipt truncation had split them: "Pfefferbreze" and
"pfefferbretzel," are the user's Pfefferbretzel, and "Laugenbreze" is
LAUGENBREZEL. crud.rename_item merges each into the kept item (purchases,
stock, receipt aliases); new spellings are caught by bot/name_match.py from
now on.

Backs the DB up first. Run inside the bot container:
    ssh oracle-tracknest docker exec -i tracknest-bot python - < deploy/merge_pretzel_typos_2026_10_04.py
"""

import sys

sys.path.insert(0, "/app")

from db import crud  # noqa: E402
from db.database import backup_db  # noqa: E402

MERGES = {
    "Pfefferbreze": "Pfefferbretzel",
    "pfefferbretzel,": "Pfefferbretzel",
    "Laugenbreze": "LAUGENBREZEL",
}

for keep in set(MERGES.values()):
    if crud.get_item(keep) is None:
        raise SystemExit(f"{keep!r} not found — nothing changed")

print("Backed up to", backup_db("before-merge-pretzel-typos"))
for old, keep in MERGES.items():
    if crud.get_item(old) is None:
        print(f"{old!r}: already gone")
        continue
    final, merged = crud.rename_item(old, keep)
    if not merged:
        raise SystemExit(f"{old!r} was renamed instead of merged — check {final!r}")
    # The receipt wording keeps pointing at the kept item.
    crud.save_alias(old, keep, crud.get_item(keep).get("category"), crud.get_item(keep).get("product"))
    print(f"{old!r} → {final!r}")

for keep in sorted(set(MERGES.values())):
    print(keep, "purchases:", len(crud.get_item(keep) and __import__("db.expenses", fromlist=["x"])
                                  .get_expenses(keep) or []))
