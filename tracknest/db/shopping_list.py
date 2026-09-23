"""Shopping list operations against the shopping_list_items table."""

from db.database import get_connection


def add_item(name, quantity=1, category=None):
    """Add an item to the shopping list, or bump its quantity if already listed.

    Uses an UPSERT: if an item with the same name is already on the list,
    the given quantity is added to it rather than replacing it. The
    category isn't touched on that conflict path — the first guess stands.

    Args:
        name: Item name.
        quantity: How many units are needed.
        category: Optional category label (e.g. from bot.categorize).
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO shopping_list_items (name, quantity, category)
        VALUES (?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET quantity = quantity + excluded.quantity
    """, (name, quantity, category))
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


def remove_item(name, reason="manual", source=None):
    """Remove an item from the shopping list by name, case-insensitively, keeping a record.

    The removed row is copied into shopping_list_history first, so it can be
    reviewed and put back (restore_item) if the removal was a mistake.

    Args:
        name: Item name to remove. Matched regardless of case, since a user
            typing "- bananas" should remove an item stored as "Bananas".
        reason: Why it came off: 'manual' (the user removed it), 'receipt'
            or 'purchase' (a logged purchase cleared it).
        source: What cleared it — e.g. the receipt line's wording.

    Returns:
        The history entry's id if the item was found and removed, None otherwise.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name, quantity, category, added_at FROM shopping_list_items WHERE name = ? COLLATE NOCASE",
        (name,),
    )
    row = cursor.fetchone()
    history_id = None
    if row:
        cursor.execute("""
            INSERT INTO shopping_list_history (name, quantity, category, added_at, reason, source)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (row["name"], row["quantity"], row["category"], row["added_at"], reason, source))
        history_id = cursor.lastrowid
        cursor.execute("DELETE FROM shopping_list_items WHERE name = ? COLLATE NOCASE", (name,))
    conn.commit()
    cursor.close()
    conn.close()
    return history_id


def restore_item(history_id):
    """Put a removed item back on the shopping list.

    Returns:
        The restored item's name, or None if that entry doesn't exist or was
        already put back.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name, quantity, category FROM shopping_list_history WHERE id = ? AND restored = 0",
        (history_id,),
    )
    row = cursor.fetchone()
    if row:
        cursor.execute("UPDATE shopping_list_history SET restored = 1 WHERE id = ?", (history_id,))
        conn.commit()
    cursor.close()
    conn.close()
    if not row:
        return None
    add_item(row["name"], row["quantity"], category=row["category"])
    return row["name"]


def get_history(limit=10):
    """Return the most recent shopping-list removals, newest first.

    Returns:
        List of dicts: id, name, quantity, category, removed_at, reason,
        source, restored.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, name, quantity, category, removed_at, reason, source, restored
        FROM shopping_list_history ORDER BY id DESC LIMIT ?
    """, (limit,))
    rows = [dict(r) for r in cursor.fetchall()]
    cursor.close()
    conn.close()
    return rows
