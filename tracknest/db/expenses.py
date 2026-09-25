"""Expense logging and reporting for item purchases."""

from datetime import datetime, timedelta, timezone

from db.database import get_connection


def log_expense(item_name, quantity_purchased, unit_price, store=None):
    """Record a purchase for an existing inventory item.

    For an essential item with a known shelf-life estimate, if this purchase
    comes sooner than the last one of the same product (any brand) should
    have lasted (per unit bought), the
    estimate is nudged halfway towards the actual gap — one early trip
    shouldn't collapse it. Items on a keep-a-spare policy (par 2) are exempt:
    buying before running out is exactly what that policy asks for, so an
    early repurchase says nothing about how fast it's used — correcting on it
    shrank the estimate every cycle until the spare alert fired right after
    each purchase. Luxury items are exempt (a treat bought on mood/budget
    doesn't follow a consumption schedule); necessity items are exempt too
    (their shelf_life_days is a fixed same-day marker, not an estimate to
    refine). Any pending check-in for this item is also cleared, since a
    fresh purchase starts a new shelf-life cycle.

    Args:
        item_name: Name of the item being purchased (must already exist).
        quantity_purchased: Number of units bought.
        unit_price: Price per unit.
        store: Optional store or supplier name.

    Returns:
        True if the expense was logged, False if the item does not exist.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, shelf_life_days, purchase_type, COALESCE(product, name) AS product_key,
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
    now = datetime.now(tz=timezone.utc)

    if item["shelf_life_days"] and item["purchase_type"] == "essential" and item["par_level"] < 2:
        # Any brand of the same product counts as the previous purchase.
        cursor.execute("""
            SELECT e.logged_at, e.quantity_purchased FROM item_expenses e
            JOIN inventory_items i ON i.id = e.item_id
            WHERE COALESCE(i.product, i.name) = ?
            ORDER BY e.logged_at DESC LIMIT 1
        """, (item["product_key"],))
        prior = cursor.fetchone()
        if prior and prior["logged_at"]:
            gap_days = (now - datetime.fromisoformat(prior["logged_at"])).days
            per_unit_days = gap_days / max(1, prior["quantity_purchased"] or 1)
            if 1 <= per_unit_days < item["shelf_life_days"]:
                cursor.execute(
                    "UPDATE inventory_items SET shelf_life_days = ? WHERE id = ?",
                    (round((item["shelf_life_days"] + per_unit_days) / 2), item["id"]),
                )

    # A fresh purchase starts a new shelf-life cycle, so both the "did it
    # run out" check-in and the par=2 "buy a spare" alert reset.
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


def is_duplicate_purchase(item_name, unit_price, window_minutes=60):
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

    Returns:
        True if a matching expense was logged within the window.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(minutes=window_minutes)).isoformat()
    cursor.execute("""
        SELECT 1 FROM item_expenses e
        JOIN inventory_items i ON i.id = e.item_id
        WHERE i.name = ? AND e.unit_price = ? AND e.logged_at >= ?
        LIMIT 1
    """, (item_name, unit_price, cutoff))
    found = cursor.fetchone() is not None
    cursor.close()
    conn.close()
    return found


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
