"""One-off (2026-10-03): receipts 9-16 checked against their photos; trip keys for every receipt.

- Receipt 9 (REWE 25.09. 19:28) really had HP PUDD.CHOCO V €0.99 + SKYR
  STYLE MANGO €2.19 = €3.18. The bot logged: the two joined as one item
  x2 at €0.99, a ghost "Kuchenbeleg" €3.18 (the "Kundenbeleg" footer with
  the total as its price) and a ghost EIER BH M-L €2.19 (the mango's
  price on the eggs from the shopping list).
- Receipts 9-12 were processed on 29.09, before printed dates were read,
  so they sat on 29.09: really 25.09, 26.09, 26.09 and 28.09.
- Receipts 13-16 get their printed times (they were logged at noon).
- Every purchase from a receipt gets trip_key "receipt:<id>" (1-8 matched
  by the second they were logged at, which is when their receipt was
  processed), so trips count per receipt.
- Pool moves to the new Leisure category: spending, not a shopping trip.

Needs the deploy that adds item_expenses.trip_key. Checks every row it
touches first; anything unexpected aborts with nothing changed. Backs up
first, one transaction. Run inside the bot container:
    ssh oracle-tracknest docker exec -i tracknest-bot python - < deploy/fix_receipts_9_to_16.py
"""

from db.database import backup_db, get_connection

# expense id -> (item name, unit price) it must currently be
EXPECTED = {
    39: ("HP PUD.DL CHOCO V SKYR STYLE MANGO", 0.99), 40: ("Kuchenbeleg", 3.18), 42: ("EIER BH M-L", 2.19),
    43: ("VOLLKORNTORTILLA", 1.59), 44: ("Wrap 190g (Leerdammer cheese)", 1.99), 45: ("Pizza Margherita", 0.99),
    46: ("LAUGENBREZEL", 0.49), 47: ("TWISTER KAKAO", 0.99), 48: ("JA! MIWA STILL", 0.15),
    49: ("BANANE", 2.12), 50: ("TWISTER KAKAO", 0.99), 51: ("PROTEINBROETCHEN", 1.89),
    52: ("EIER MARM.", 2.99), 53: ("TS CR. ROSM.", 3.29), 58: ("HAPPY CALIF. VEG", 10.99), 59: ("THUNFISCHFI. WAS", 1.29),
}
# receipt id -> (expense ids, printed date/time in UTC)
RECEIPTS = {
    9: ([39], "2026-09-25T17:28:47+00:00"),
    10: ([43], "2026-09-26T12:00:00+00:00"),  # time not on the photo
    11: ([44, 45], "2026-09-26T12:58:00+00:00"),
    12: ([46, 47, 48], "2026-09-28T12:00:00+00:00"),  # time not on the photo
    13: ([49, 50, 51], "2026-09-29T15:53:43+00:00"),
    14: ([52, 53], "2026-09-30T18:51:57+00:00"),
    15: (None, "2026-09-30T19:01:23+00:00"),  # go asia: found by name below
    16: ([58, 59], "2026-09-30T10:45:17+00:00"),
}

print(f"Backup: {backup_db('before-receipts-9-to-16')}")
conn = get_connection()
if "trip_key" not in {r[1] for r in conn.execute("PRAGMA table_info(item_expenses)")}:
    raise SystemExit("item_expenses.trip_key missing — deploy first. Nothing changed.")
with conn:  # one transaction: commits on success, rolls back on any error
    for eid, (name, price) in EXPECTED.items():
        row = conn.execute("""SELECT i.name, e.unit_price FROM item_expenses e
                              JOIN inventory_items i ON i.id = e.item_id WHERE e.id = ?""", (eid,)).fetchone()
        if row is None or row["name"] != name or abs(row["unit_price"] - price) > 0.005:
            raise SystemExit(f"expense {eid} isn't {name} €{price} ({row and dict(row)}) — nothing changed")

    # Receipt 9: drop the two ghosts, split the joined line.
    kuchen = conn.execute("SELECT item_id FROM item_expenses WHERE id = 40").fetchone()["item_id"]
    conn.execute("DELETE FROM item_expenses WHERE id IN (40, 42)")
    conn.execute("DELETE FROM inventory_items WHERE id = ?", (kuchen,))
    conn.execute("UPDATE inventory_items SET quantity = MAX(0, quantity - 1) WHERE name = 'EIER BH M-L'")
    conn.execute("""UPDATE inventory_items SET name = 'SKYR STYLE MANGO', quantity = 1
                    WHERE name = 'HP PUD.DL CHOCO V SKYR STYLE MANGO'""")
    conn.execute("UPDATE item_expenses SET quantity_purchased = 1, unit_price = 2.19 WHERE id = 39")
    pudding = conn.execute("SELECT id FROM inventory_items WHERE name = 'HP PUDD. CHOCO V'").fetchone()["id"]
    conn.execute("UPDATE inventory_items SET quantity = quantity + 1 WHERE id = ?", (pudding,))
    conn.execute("""INSERT INTO item_expenses (item_id, quantity_purchased, unit_price, store, purchase_date, logged_at, trip_key)
                    VALUES (?, 1, 0.99, 'REWE', '2026-09-25', '2026-09-25T17:28:47+00:00', 'receipt:9')""", (pudding,))
    print("  receipt 9: ghosts 'Kuchenbeleg' €3.18 and EIER BH M-L €2.19 removed; "
          "HP PUDD. CHOCO V €0.99 + SKYR STYLE MANGO €2.19")

    go_asia = [r["id"] for r in conn.execute(
        "SELECT id FROM item_expenses WHERE store = 'go asia Supermarkt' AND purchase_date = '2026-09-30'")]
    for receipt_id, (ids, when) in RECEIPTS.items():
        ids = go_asia if ids is None else ids
        for eid in ids:
            conn.execute("UPDATE item_expenses SET purchase_date = ?, logged_at = ?, trip_key = ? WHERE id = ?",
                         (when[:10], when, f"receipt:{receipt_id}", eid))
        print(f"  receipt {receipt_id}: {len(ids)} purchase(s) -> {when[:16].replace('T', ' ')} UTC")

    matched = conn.execute("""
        UPDATE item_expenses SET trip_key = 'receipt:' || (
            SELECT r.id FROM pending_receipts r
            WHERE r.resolved_at IS NOT NULL
              AND ABS(strftime('%s', r.resolved_at) - strftime('%s', item_expenses.logged_at)) <= 5)
        WHERE trip_key IS NULL AND logged_at IS NOT NULL AND EXISTS (
            SELECT 1 FROM pending_receipts r WHERE r.resolved_at IS NOT NULL
              AND ABS(strftime('%s', r.resolved_at) - strftime('%s', item_expenses.logged_at)) <= 5)
    """).rowcount
    print(f"  receipts 1-8: {matched} purchase(s) linked to their receipt")

    conn.execute("UPDATE inventory_items SET category = 'Leisure' WHERE name = 'Pool'")
    print("  Pool -> Leisure")
    total = conn.execute(
        "SELECT ROUND(SUM(quantity_purchased * unit_price), 2) FROM item_expenses WHERE purchase_date LIKE '2026-09%'"
    ).fetchone()[0]
    print(f"  September now totals €{total:.2f} (was €172.49)")
conn.close()
print("Done.")
