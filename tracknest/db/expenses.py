"""Expense logging and reporting for item purchases."""

from datetime import date
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
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id FROM inventory_items WHERE name = %s", (item_name,))
    item = cursor.fetchone()
    if not item:
        cursor.close()
        conn.close()
        return False
    cursor.execute("""
        INSERT INTO item_expenses (item_id, quantity_purchased, unit_price, store, purchase_date)
        VALUES (%s, %s, %s, %s, %s)
    """, (item["id"], quantity_purchased, unit_price, store, date.today()))
    conn.commit()
    cursor.close()
    conn.close()
    return True


def get_expenses(item_name=None):
    """Return expense records, optionally filtered to a single item.

    Args:
        item_name: If given, only expenses for that item are returned.

    Returns:
        List of dicts ordered by purchase_date descending. Empty list if none found.
    """
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    if item_name:
        cursor.execute("""
            SELECT e.*, i.name,
                   (e.quantity_purchased * e.unit_price) AS total_cost
            FROM item_expenses e
            JOIN inventory_items i ON e.item_id = i.id
            WHERE i.name = %s
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
    return rows


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
            WHERE i.name = %s
        """, (item_name,))
    else:
        cursor.execute("""
            SELECT COALESCE(SUM(quantity_purchased * unit_price), 0) FROM item_expenses
        """)
    total = cursor.fetchone()[0]
    cursor.close()
    conn.close()
    return float(total)
