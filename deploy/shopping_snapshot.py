"""Read-only snapshot of purchases, stock estimates and the shopping list, for planning a shop.

Opens the DB read-only (SQLite mode=ro), so it can never change anything.
Run inside the bot container:
    ssh oracle-tracknest docker exec -i tracknest-bot python - < deploy/shopping_snapshot.py
"""

import sqlite3
from datetime import date

conn = sqlite3.connect("file:/app/data/tracknest.db?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
today = date.today()

print(f"== Items (today {today}) — last bought, times, lasts, est. days left ==")
rows = conn.execute("""
    SELECT i.name, COALESCE(i.product, '') AS product, COALESCE(i.category, '') AS category,
           i.treat_or_need, i.lasts, i.shelf_life_days, i.checkin_pending,
           COUNT(e.id) AS times, MAX(e.purchase_date) AS last_day,
           GROUP_CONCAT(DISTINCT e.store) AS stores,
           ROUND(AVG(e.unit_price), 2) AS avg_price, SUM(e.quantity_purchased) AS units
    FROM inventory_items i LEFT JOIN item_expenses e ON e.item_id = i.id
    GROUP BY i.id ORDER BY i.category, i.name
""").fetchall()
for r in rows:
    left = ""
    if r["last_day"] and r["shelf_life_days"]:
        left = (r["shelf_life_days"] - (today - date.fromisoformat(r["last_day"])).days)
        left = f"{left}d left"
    print(f"{r['category'][:12]:12} | {r['name'][:28]:28} | {r['product'][:16]:16} | {r['treat_or_need']:7} | "
          f"{r['lasts']:8} {r['shelf_life_days'] or '':>3} | x{r['times']} ({r['units'] or 0}u) last {r['last_day'] or '-'} "
          f"| {left:9} | {r['stores'] or ''} | avg €{r['avg_price'] or 0}"
          + (" | check-in pending" if r["checkin_pending"] else ""))

print("\n== Trips (date, store, items, total) ==")
for r in conn.execute("""
    SELECT MIN(purchase_date) AS day, store, COUNT(*) AS n, ROUND(SUM(quantity_purchased * unit_price), 2) AS total
    FROM item_expenses GROUP BY COALESCE(trip_key, purchase_date || store) ORDER BY day
"""):
    print(f"{r['day']} | {r['store'] or 'typed'} | {r['n']} items | €{r['total']}")

print("\n== Shopping list now ==")
for r in conn.execute("SELECT name, quantity, category FROM shopping_list_items ORDER BY category, name"):
    print(f"- {r['name']} ({r['quantity']}x) [{r['category'] or ''}]")
conn.close()
