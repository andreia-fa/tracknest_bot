"""One-off (2026-10-03): corrections from the first batch of old receipts, checked against the photos.

- BANANE was weighed both times and logged at the per-kilo price €1.99:
  22 Sep 1,256 kg = €2.50; 29 Sep 1,064 kg = €2.12.
- 30 Sep REWE (€12.28): the model joined two lines into "HAPPY CALIF. VEG
  THUNFISCHFI. WAS" (€10.99) and read the till code "X01" as a product
  (€1.29). Really: HAPPY CALIF. VEG (the usual sushi) €10.99 + THUNFISCHFI.
  WAS (tuna fillets in water, a can) €1.29.
- TS CR. ROSM. (rosemary crackers, per the user) was filed under Dairy.

Backs the DB up first and applies everything in one transaction. Run inside the bot container:
    ssh oracle-tracknest docker exec -i tracknest-bot python - < deploy/fix_september_batch.py
"""

from db.database import backup_db, get_connection

print(f"Backup: {backup_db('before-september-batch-fix')}")
conn = get_connection()
with conn:  # one transaction: commits on success, rolls back on any error
    for date, price in (("2026-09-22", 2.50), ("2026-09-29", 2.12)):
        n = conn.execute("""
            UPDATE item_expenses SET unit_price = ?
            WHERE purchase_date = ? AND unit_price BETWEEN 1.98 AND 2.00
              AND item_id = (SELECT id FROM inventory_items WHERE name = 'BANANE')
        """, (price, date)).rowcount
        print(f"  BANANE {date}: €1.99 -> €{price:.2f}" if n == 1 else f"  BANANE {date}: {n} rows matched, skipped")
        if n != 1:
            raise SystemExit("unexpected match count — nothing changed")

    for bad in ("HAPPY CALIF. VEG THUNFISCHFI. WAS", "X01"):
        item = conn.execute("SELECT id FROM inventory_items WHERE name = ?", (bad,)).fetchone()
        if item:
            conn.execute("DELETE FROM item_expenses WHERE item_id = ?", (item["id"],))
            conn.execute("DELETE FROM inventory_items WHERE id = ?", (item["id"],))
            print(f"  removed misread '{bad}'")
    sushi = conn.execute("SELECT id FROM inventory_items WHERE name = 'HAPPY CALIF. VEG'").fetchone()
    conn.execute("UPDATE inventory_items SET quantity = quantity + 1 WHERE id = ?", (sushi["id"],))
    conn.execute("""
        INSERT INTO inventory_items (name, quantity, category, product, treat_or_need, lasts, shelf_life_days)
        VALUES ('THUNFISCHFI. WAS', 1, 'Pantry', 'canned tuna', 'need', 'days', 60)
    """)
    tuna = conn.execute("SELECT id FROM inventory_items WHERE name = 'THUNFISCHFI. WAS'").fetchone()
    for item_id, price in ((sushi["id"], 10.99), (tuna["id"], 1.29)):
        conn.execute("""
            INSERT INTO item_expenses (item_id, quantity_purchased, unit_price, store, purchase_date, logged_at)
            VALUES (?, 1, ?, 'REWE', '2026-09-30', '2026-09-30T12:00:00+00:00')
        """, (item_id, price))
    conn.execute("""INSERT OR REPLACE INTO item_aliases (receipt_name, canonical_name, category, product)
                    VALUES ('THUNFISCHFI. WAS', 'THUNFISCHFI. WAS', 'Pantry', 'canned tuna')""")
    print("  30 Sep: HAPPY CALIF. VEG €10.99 + THUNFISCHFI. WAS (canned tuna, need) €1.29")

    conn.execute("UPDATE inventory_items SET category = 'Snacks' WHERE name = 'TS CR. ROSM.'")
    conn.execute("UPDATE item_aliases SET category = 'Snacks' WHERE canonical_name = 'TS CR. ROSM.'")
    print("  TS CR. ROSM.: Dairy -> Snacks")

    total = conn.execute(
        "SELECT ROUND(SUM(quantity_purchased * unit_price), 2) FROM item_expenses WHERE purchase_date = '2026-09-30' AND store = 'REWE'"
    ).fetchone()[0]
    print(f"  REWE 30 Sep now totals €{total:.2f} (receipts: €12.28 + €6.28 = €18.56)")
conn.close()
print("Done.")
