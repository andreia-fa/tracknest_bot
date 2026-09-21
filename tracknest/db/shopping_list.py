"""Shopping list operations against the shopping_list_items table."""

from db.database import get_connection


def add_item(name, quantity=1):
    """Add an item to the shopping list, or bump its quantity if already listed.

    Uses an UPSERT: if an item with the same name is already on the list,
    the given quantity is added to it rather than replacing it.

    Args:
        name: Item name.
        quantity: How many units are needed.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO shopping_list_items (name, quantity)
        VALUES (?, ?)
        ON CONFLICT(name) DO UPDATE SET quantity = quantity + excluded.quantity
    """, (name, quantity))
    conn.commit()
    cursor.close()
    conn.close()


def get_all_items():
    """Return all shopping list items ordered by when they were added.

    Returns:
        List of dicts, one per row. Empty list if the list is empty.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM shopping_list_items ORDER BY added_at")
    items = cursor.fetchall()
    cursor.close()
    conn.close()
    return [dict(item) for item in items]


def remove_item(name):
    """Remove an item from the shopping list by name, case-insensitively.

    Args:
        name: Item name to remove. Matched regardless of case, since a user
            typing "- bananas" should remove an item stored as "Bananas".

    Returns:
        True if the item was found and removed, False if no matching row exists.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM shopping_list_items WHERE name = ? COLLATE NOCASE", (name,))
    affected = cursor.rowcount
    conn.commit()
    cursor.close()
    conn.close()
    return affected > 0
