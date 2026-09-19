"""Expense logging and reporting for item purchases."""

from datetime import datetime, timedelta, timezone

from db.database import get_connection


def log_expense(item_name, quantity_purchased, unit_price, store=None):
    """Record a purchase for an existing inventory item.

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
    cursor.execute("SELECT id FROM inventory_items WHERE name = ?", (item_name,))
    item = cursor.fetchone()
    if not item:
        cursor.close()
        conn.close()
        return False
    now = datetime.now(tz=timezone.utc)
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
