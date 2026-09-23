"""Print the live products, shopping list and receipt aliases as tables. Read-only.

Run inside the bot container, e.g.:
    ssh oracle-tracknest docker exec -i tracknest-bot python - < deploy/show_products.py
"""

from db.database import get_connection


def table(title, headers, rows):
    print(f"\n{title} ({len(rows)})")
    if not rows:
        print("  (empty)")
        return
    cells = [[("" if v is None else str(v)) for v in row] for row in rows]
    widths = [min(32, max(len(h), *(len(r[i]) for r in cells))) for i, h in enumerate(headers)]
    line = "  " + "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    print(line)
    print("  " + "  ".join("-" * w for w in widths))
    for r in cells:
        print("  " + "  ".join(v[:w].ljust(w) for v, w in zip(r, widths)))


conn = get_connection()
products = conn.execute("""
    SELECT i.name, i.category, i.purchase_type, i.shelf_life_days,
           COUNT(e.id) AS buys,
           printf('%.2f', (SELECT unit_price FROM item_expenses x
                           WHERE x.item_id = i.id ORDER BY x.id DESC LIMIT 1)) AS last_price,
           printf('%.2f', COALESCE(SUM(e.quantity_purchased * e.unit_price), 0)) AS total,
           MAX(e.purchase_date) AS last_bought,
           i.name_status
    FROM inventory_items i LEFT JOIN item_expenses e ON e.item_id = i.id
    GROUP BY i.id ORDER BY i.category, i.name
""").fetchall()
table("PRODUCTS",
      ["name", "category", "type", "shelf days", "buys", "last €", "total €", "last bought", "naming"],
      [tuple(r) for r in products])

shopping = conn.execute(
    "SELECT name, quantity, category, added_at FROM shopping_list_items ORDER BY category, name"
).fetchall()
table("SHOPPING LIST", ["name", "qty", "category", "added"], [tuple(r) for r in shopping])

aliases = conn.execute(
    "SELECT receipt_name, canonical_name, category FROM item_aliases ORDER BY receipt_name"
).fetchall()
table("RECEIPT NAMES YOU TAUGHT ME", ["on receipt", "is really", "category"], [tuple(r) for r in aliases])
conn.close()
