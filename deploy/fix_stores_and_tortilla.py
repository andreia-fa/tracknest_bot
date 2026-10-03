"""One-off (2026-10-03): stores for past typed purchases; VOLLKORNTORTILLA is an essential.

Same rule as expenses.guess_store (used for new typed purchases): a price
paid for the same item/product at exactly one store names that store.
Prices never seen elsewhere stay without a store. The tortilla wraps are
bought regularly (user, 2026-10-03): essential, a pack lasting ~14 days.

Backs the DB up first and applies everything in one transaction. Run inside the bot container:
    ssh oracle-tracknest docker exec -i tracknest-bot python - < deploy/fix_stores_and_tortilla.py
"""

from db.database import backup_db, get_connection

print(f"Backup: {backup_db('before-stores-and-tortilla')}")
conn = get_connection()
with conn:  # one transaction: commits on success, rolls back on any error
    typed = conn.execute("""
        SELECT e.id, e.unit_price, i.name, i.product FROM item_expenses e
        JOIN inventory_items i ON i.id = e.item_id WHERE e.store IS NULL
    """).fetchall()
    for row in typed:
        stores = [r[0] for r in conn.execute("""
            SELECT DISTINCT e.store FROM item_expenses e JOIN inventory_items i ON i.id = e.item_id
            WHERE e.store IS NOT NULL AND ROUND(e.unit_price, 2) = ROUND(?, 2)
              AND (lower(i.name) = lower(?) OR (? IS NOT NULL AND lower(i.product) = lower(?)))
        """, (row["unit_price"], row["name"], row["product"], row["product"]))]
        if len(stores) == 1:
            conn.execute("UPDATE item_expenses SET store = ? WHERE id = ?", (stores[0], row["id"]))
            print(f"  {row['name']} at €{row['unit_price']:.2f}: store -> {stores[0]}")
        else:
            print(f"  {row['name']} at €{row['unit_price']:.2f}: no single store at that price, left empty")
    n = conn.execute(
        "UPDATE inventory_items SET purchase_type = 'essential', shelf_life_days = 14 WHERE name = 'VOLLKORNTORTILLA'"
    ).rowcount
    print("  VOLLKORNTORTILLA: essential, lasts 14 day(s)" if n else "  skipped (not found): VOLLKORNTORTILLA")
conn.close()
print("Done.")
