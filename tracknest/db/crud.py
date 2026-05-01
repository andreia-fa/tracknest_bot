"""Inventory item CRUD operations against the inventory_items table."""

from db.database import get_connection


def add_item(name, quantity, category=None, alert_threshold=None):
    """Add a new item or restock an existing one.

    Uses an UPSERT: if an item with the same name already exists, the given
    quantity is added to its current stock rather than replacing it.

    Args:
        name: Item name (case-sensitive, must be unique in the table).
        quantity: Units to add.
        category: Optional grouping label (e.g. "Dairy").
        alert_threshold: Optional minimum stock level for low-stock alerts.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO inventory_items (name, quantity, category, alert_threshold)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE quantity = quantity + VALUES(quantity)
    """, (name, quantity, category, alert_threshold))
    conn.commit()
    cursor.close()
    conn.close()


def get_item(name):
    """Return a single inventory item by name, or None if not found.

    Args:
        name: Exact item name to look up.

    Returns:
        A dict of column values, or None if no matching row exists.
    """
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM inventory_items WHERE name = %s", (name,))
    item = cursor.fetchone()
    cursor.close()
    conn.close()
    return item


def get_all_items():
    """Return all inventory items ordered alphabetically by name.

    Returns:
        List of dicts, one per row. Empty list if the table has no rows.
    """
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM inventory_items ORDER BY name")
    items = cursor.fetchall()
    cursor.close()
    conn.close()
    return items


def update_item_quantity(name, quantity):
    """Set the quantity of an existing item to an absolute value.

    Unlike add_item, this replaces the current quantity rather than adding to it.

    Args:
        name: Exact item name to update.
        quantity: New quantity to set.

    Returns:
        True if the item was found and updated, False if no matching row exists.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE inventory_items SET quantity = %s WHERE name = %s",
        (quantity, name)
    )
    affected = cursor.rowcount
    conn.commit()
    cursor.close()
    conn.close()
    return affected > 0


def delete_item(name):
    """Remove an item from inventory by name.

    Deleting an item also cascades to its associated expense records.

    Args:
        name: Exact item name to delete.

    Returns:
        True if the item was found and removed, False if no matching row exists.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM inventory_items WHERE name = %s", (name,))
    affected = cursor.rowcount
    conn.commit()
    cursor.close()
    conn.close()
    return affected > 0
