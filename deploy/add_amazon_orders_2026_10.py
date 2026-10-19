"""One-off (2026-10-19): log the user's October Amazon orders, given in chat.

Amazon sends no receipt to photograph, so the user listed them. Each line
goes through the bot's own functions (crud + expenses.log_expense), filed as
the user described: how long things last where they said, one-off for the
moving supplies, a note where they mentioned stock on hand. Anything they
didn't say is left 'unknown' for the bot's usual profiling question.

Backs the DB up first and refuses to run if an Amazon purchase is already
logged, so a second run can't double anything. Run inside the bot container:
    ssh oracle-tracknest docker exec -i tracknest-bot python - < deploy/add_amazon_orders_2026_10.py
"""

import sys
from datetime import datetime, timezone

sys.path.insert(0, "/app/tracknest")

from db import crud, expenses  # noqa: E402
from db.database import backup_db, get_connection  # noqa: E402

STORE = "Amazon"

# (name, product, category, treat_or_need, lasts, days, date, qty, line total, note)
ORDERS = [
    ("Redken Conditioner", "conditioner", "Hygiene/Personal Care", "need", "unknown", None,
     "2026-10-07", 1, 21.47, None),
    ("CeraVe Augencreme", "eye cream", "Hygiene/Personal Care", "need", "unknown", None,
     "2026-10-03", 1, 13.20, None),
    ("Pura öko Feuchtpapier (40 Tücher)", "wet toilet paper", "Hygiene/Personal Care", "need", "days", 10,
     "2026-10-16", 3, 7.19, "Ordered monthly, 3 packs. Backup at home: not needed in November."),
    ("Umzugkartons (10 Stück)", "moving boxes", "Household", "need", "one_off", None,
     "2026-10-16", 1, 21.49, None),
    ("Motorola Sichtschutz", "screen protector", "Other", "need", "days", 90,
     "2026-10-15", 2, 6.88, "Bought 2 every 6 months; one lasts about 3 months."),
    ("Etiketten", "labels", "Household", "need", "one_off", None,
     "2026-10-15", 1, 6.70, None),
    ("Dunkle Stifte für Etiketten", "pens", "Household", "need", "one_off", None,
     "2026-10-15", 1, 14.57, None),
    ("Haarklammern", "hair clips", "Hygiene/Personal Care", "need", "unknown", None,
     "2026-10-16", 1, 6.49, None),
    ("Bulk Vanille 1l", "vanilla", "Pantry", "need", "days", 135,
     "2026-10-15", 1, 7.49, "Bought every 4-5 months."),
    ("Kabelbinder", "cable ties", "Household", "need", "one_off", None,
     "2026-10-15", 1, 3.99, None),
    ("Amazon Essentials Kokosmilch", "coconut milk", "Pantry", "unknown", "unknown", None,
     "2026-10-16", 1, 1.22, None),
    ("Redken Intensive Treatment", "hair treatment", "Hygiene/Personal Care", "need", "days", 38,
     "2026-10-16", 1, 20.17, "Lasts about 30-45 days."),
    ("Alpro Haferdrink 1l", "oat drink", "Beverages", "need", "days", 7,
     "2026-10-16", 8, 12.74, "Comes as 8×1l about every 2 months. Plenty at home: not needed for ~2 months."),
    ("Alnatura Bio Datteln 200g", "dates", "Pantry", "unknown", "unknown", None,
     "2026-10-16", 1, 1.89, None),
]

conn = get_connection()
already = conn.execute("SELECT COUNT(*) FROM item_expenses WHERE store = ?", (STORE,)).fetchone()[0]
conn.close()
if already:
    sys.exit(f"Stopped: {already} Amazon purchase(s) already logged — nothing written.")

print("Backed up to", backup_db("before-amazon-orders-2026-10"))
for name, product, category, kind, lasts, days, day, qty, total, note in ORDERS:
    crud.add_item(name, qty, category=category, product=product)
    if kind != "unknown":
        crud.set_treat_or_need(name, kind)
    if lasts != "unknown":
        crud.set_lasts(name, lasts, days)
    if note:
        crud.set_item_note(name, note)
    when = datetime.fromisoformat(day).replace(hour=12, tzinfo=timezone.utc)
    expenses.log_expense(name, qty, total / qty, store=STORE, purchased_at=when, trip_key=f"amazon:{day}")
    print(f"{day}  {name}: {qty} × €{total / qty:.2f} = €{total:.2f}")
print(f"\n{len(ORDERS)} purchases, €{sum(o[8] for o in ORDERS):.2f} in total.")
