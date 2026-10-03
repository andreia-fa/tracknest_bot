"""Expense logging and reporting for item purchases."""

from datetime import datetime, timedelta, timezone

from db.database import get_connection


def log_expense(item_name, quantity_purchased, unit_price, store=None, purchased_at=None):
    """Record a purchase for an existing inventory item.

    For a need lasting a known number of days, if this purchase
    comes sooner than the last one of the same product (any brand) should
    have lasted (per unit bought), the
    estimate is nudged halfway towards the actual gap — one early trip
    shouldn't collapse it. Items on a keep-a-spare policy (par 2) are exempt:
    buying before running out is exactly what that policy asks for, so an
    early repurchase says nothing about how fast it's used — correcting on it
    shrank the estimate every cycle until the spare alert fired right after
    each purchase. Treats are exempt (a treat bought on mood/budget doesn't
    follow a consumption schedule), and so are same-day and one-off items
    (no estimate to refine). Any pending check-in for this item is also
    cleared, since a fresh purchase starts a new shelf-life cycle — unless an
    older receipt is being caught up on and a newer purchase of the same
    product is already logged.

    Args:
        item_name: Name of the item being purchased (must already exist).
        quantity_purchased: Number of units bought.
        unit_price: Price per unit.
        store: Optional store or supplier name.
        purchased_at: When it was bought (an aware datetime), for a receipt
            sent days later; defaults to now. Both purchase_date and the
            timing below use it, so a September receipt counts in September.

    Returns:
        True if the expense was logged, False if the item does not exist.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, shelf_life_days, treat_or_need, lasts, COALESCE(product, name) AS product_key,
               COALESCE(par_level,
                        (SELECT CAST(value AS INTEGER) FROM bot_settings WHERE key = 'default_par_level'),
                        1) AS par_level
        FROM inventory_items WHERE name = ?
    """, (item_name,))
    item = cursor.fetchone()
    if not item:
        cursor.close()
        conn.close()
        return False
    now = purchased_at or datetime.now(tz=timezone.utc)
    cursor.execute("""
        SELECT 1 FROM item_expenses e JOIN inventory_items i ON i.id = e.item_id
        WHERE COALESCE(i.product, i.name) = ? AND e.logged_at > ? LIMIT 1
    """, (item["product_key"], now.isoformat()))
    is_latest = cursor.fetchone() is None

    if item["lasts"] == "days" and item["treat_or_need"] == "need" and item["par_level"] < 2:
        # Any brand of the same product counts as the previous purchase —
        # the last one before this, even when receipts arrive out of order.
        cursor.execute("""
            SELECT e.logged_at, e.quantity_purchased FROM item_expenses e
            JOIN inventory_items i ON i.id = e.item_id
            WHERE COALESCE(i.product, i.name) = ? AND e.logged_at < ?
            ORDER BY e.logged_at DESC LIMIT 1
        """, (item["product_key"], now.isoformat()))
        prior = cursor.fetchone()
        if prior and prior["logged_at"]:
            gap_days = (now - datetime.fromisoformat(prior["logged_at"])).days
            per_unit_days = gap_days / max(1, prior["quantity_purchased"] or 1)
            if 1 <= per_unit_days < item["shelf_life_days"]:
                cursor.execute(
                    "UPDATE inventory_items SET shelf_life_days = ? WHERE id = ?",
                    (max(2, round((item["shelf_life_days"] + per_unit_days) / 2)), item["id"]),
                )

    # A fresh purchase starts a new shelf-life cycle, so both the "did it
    # run out" check-in and the par=2 "buy a spare" alert reset.
    if is_latest:
        cursor.execute(
            "UPDATE inventory_items SET checkin_pending = 0, spare_alert_pending = 0 WHERE id = ?",
            (item["id"],)
        )
    cursor.execute("""
        INSERT INTO item_expenses (item_id, quantity_purchased, unit_price, store, purchase_date, logged_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (item["id"], quantity_purchased, unit_price, store, now.date().isoformat(), now.isoformat()))
    conn.commit()
    cursor.close()
    conn.close()
    return True


def is_duplicate_purchase(item_name, unit_price, window_minutes=60, purchase_date=None):
    """Check whether this exact item/price was already logged within the recent window.

    Guards against the same physical receipt getting processed twice (e.g.
    two photos of one receipt) and double-counted — nobody genuinely buys
    the identical item at the identical price again within such a short
    window, so treat a repeat within it as an accidental resend rather than
    a real second purchase.

    Args:
        item_name: Name of the item to check.
        unit_price: Price per unit to match against recent expense records.
        window_minutes: How far back counts as "too recent to be real".
        purchase_date: For a receipt with a printed date (ISO date string):
            match on that day instead of the window — old receipts sent in a
            batch would otherwise trip the window on each other's items.

    Returns:
        True if a matching expense was logged within the window (or that day).
    """
    conn = get_connection()
    cursor = conn.cursor()
    if purchase_date:
        when, value = "e.purchase_date = ?", purchase_date
    else:
        when, value = "e.logged_at >= ?", (datetime.now(tz=timezone.utc) - timedelta(minutes=window_minutes)).isoformat()
    cursor.execute(f"""
        SELECT 1 FROM item_expenses e
        JOIN inventory_items i ON i.id = e.item_id
        WHERE i.name = ? AND e.unit_price = ? AND {when}
        LIMIT 1
    """, (item_name, unit_price, value))
    found = cursor.fetchone() is not None
    cursor.close()
    conn.close()
    return found


def guess_store(item_name, product, unit_price):
    """Name the store a typed purchase came from, judging by its price.

    A price already paid for this item (or the same product under another
    name — "Pfefferbretzel" typed, "Pfefferbreze" on a Yormas receipt) at
    exactly one store points to that store. A price never seen, or seen at
    several stores, gives None: a new store gets registered by sending its
    receipt, not guessed.

    Args:
        item_name: The typed item name.
        product: What the item generically is, if known (e.g. "pretzel").
        unit_price: The typed unit price.

    Returns:
        The store name, or None if the price doesn't single one out.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT e.store
        FROM item_expenses e
        JOIN inventory_items i ON i.id = e.item_id
        WHERE e.store IS NOT NULL
          AND ROUND(e.unit_price, 2) = ROUND(?, 2)
          AND (lower(i.name) = lower(?) OR (? IS NOT NULL AND lower(i.product) = lower(?)))
    """, (unit_price, item_name, product, product))
    stores = [row["store"] for row in cursor.fetchall()]
    cursor.close()
    conn.close()
    return stores[0] if len(stores) == 1 else None


def get_price_delta(item_name, new_price, min_history=1):
    """Compare a new price against an item's purchase history.

    Must be called before log_expense records the new purchase, so the
    average is computed over prior purchases only. Unlike a spike-only
    check, this returns a comparison on every repurchase (not just unusually
    large jumps) so the caller can surface everyday price drift, not only
    dramatic outliers — useful for tracking creeping inflation on an item.

    Args:
        item_name: Name of the item being purchased.
        new_price: Unit price about to be logged.
        min_history: Minimum number of prior purchases required before a
            comparison is meaningful — too little history makes the average
            unreliable.

    Returns:
        Dict with keys avg_price (historical average), pct_change (signed,
        e.g. 8.0 for +8%), and n (number of prior purchases), or None if
        there isn't enough history yet.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT AVG(e.unit_price) AS avg_price, COUNT(*) AS n
        FROM item_expenses e
        JOIN inventory_items i ON i.id = e.item_id
        WHERE i.name = ?
    """, (item_name,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if not row or row["n"] < min_history or row["avg_price"] is None:
        return None
    avg_price = row["avg_price"]
    pct_change = (new_price - avg_price) / avg_price * 100
    return {"avg_price": avg_price, "pct_change": pct_change, "n": row["n"]}


def get_expenses(item_name=None):
    """Return expense records, optionally filtered to a single item.

    Args:
        item_name: If given, only expenses for that item are returned.

    Returns:
        List of dicts ordered by purchase_date descending. Empty list if none found.
    """
    conn = get_connection()
    cursor = conn.cursor()
    if item_name:
        cursor.execute("""
            SELECT e.*, i.name,
                   (e.quantity_purchased * e.unit_price) AS total_cost
            FROM item_expenses e
            JOIN inventory_items i ON e.item_id = i.id
            WHERE i.name = ?
            ORDER BY e.purchase_date DESC
        """, (item_name,))
    else:
        cursor.execute("""
            SELECT e.*, i.name,
                   (e.quantity_purchased * e.unit_price) AS total_cost
            FROM item_expenses e
            JOIN inventory_items i ON e.item_id = i.id
            ORDER BY e.purchase_date DESC
        """)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return [dict(row) for row in rows]


def get_total_spent(item_name=None):
    """Return the total amount spent, optionally scoped to one item.

    Args:
        item_name: If given, sum only expenses for that item.

    Returns:
        Total cost as a float. Returns 0.0 if no matching records exist.
    """
    conn = get_connection()
    cursor = conn.cursor()
    if item_name:
        cursor.execute("""
            SELECT COALESCE(SUM(e.quantity_purchased * e.unit_price), 0)
            FROM item_expenses e
            JOIN inventory_items i ON e.item_id = i.id
            WHERE i.name = ?
        """, (item_name,))
    else:
        cursor.execute("""
            SELECT COALESCE(SUM(quantity_purchased * unit_price), 0) FROM item_expenses
        """)
    total = cursor.fetchone()[0]
    cursor.close()
    conn.close()
    return float(total)
