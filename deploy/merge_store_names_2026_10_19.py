"""One-off (2026-10-19): give every shop one name in the purchases already logged.

The user saw Yormas six times on the dashboard ("Klettppassage", "Klett
Passage", "YORMAS'S"...) — receipt misreads of one shop — and asked to fix
"all similar cases". New purchases are named by bot.stores.canonical_store
in expenses.log_expense; this applies the same mapping to the old rows.

Prints the mapping and changes nothing unless run with "apply"; then backs
the DB up and renames everything in one transaction. Run inside the bot
container:
    ssh oracle-tracknest docker exec -i tracknest-bot python - < deploy/merge_store_names_2026_10_19.py
    ssh oracle-tracknest docker exec -i tracknest-bot python - apply < deploy/merge_store_names_2026_10_19.py
"""

import sys

sys.path.insert(0, "/app/tracknest")

from bot.stores import canonical_store  # noqa: E402
from db.database import backup_db, get_connection  # noqa: E402

conn = get_connection()
stores = [row["store"] for row in conn.execute(
    "SELECT DISTINCT store FROM item_expenses WHERE store IS NOT NULL ORDER BY lower(store)")]
renames = {}
for store in stores:
    others = [s for s in stores if s != store]
    new = canonical_store(store, others)
    if new != store:
        renames[store] = new
for old, new in renames.items():
    print(f"{old!r} -> {new!r}")
if not renames:
    sys.exit("Nothing to rename.")

if sys.argv[1:] != ["apply"]:
    sys.exit(f"\nDry run: {len(renames)} names would change. Run with 'apply' to write.")

print("\nBacked up to", backup_db("before-merge-store-names"))
with conn:
    for old, new in renames.items():
        conn.execute("UPDATE item_expenses SET store = ? WHERE store = ?", (new, old))
conn.close()
print(f"Renamed {len(renames)} store names.")
