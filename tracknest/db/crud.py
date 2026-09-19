"""Inventory item CRUD operations against the inventory_items table."""

from db.database import get_connection


def add_item(name, quantity, unit=None, category=None, alert_threshold=None):
    """Add a new item or restock an existing one.

    Uses an UPSERT: if an item with the same name already exists, the given
    quantity is added to its current stock rather than replacing it.

    Args:
        name: Item name (case-sensitive, must be unique in the table).
        quantity: Units to add.
        unit: Unit of measure (e.g. "kg", "L", "pcs").
        category: Optional grouping label (e.g. "Dairy").
        alert_threshold: Optional minimum stock level for low-stock alerts.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO inventory_items (name, quantity, unit, category, alert_threshold)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET quantity = quantity + excluded.quantity
    """, (name, quantity, unit, category, alert_threshold))
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
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM inventory_items WHERE name = ?", (name,))
    item = cursor.fetchone()
    cursor.close()
    conn.close()
    return dict(item) if item else None


def get_all_items():
    """Return all inventory items ordered alphabetically by name.

    Returns:
        List of dicts, one per row. Empty list if the table has no rows.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM inventory_items ORDER BY name")
    items = cursor.fetchall()
    cursor.close()
    conn.close()
    return [dict(item) for item in items]


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
        "UPDATE inventory_items SET quantity = ? WHERE name = ?",
        (quantity, name)
    )
    affected = cursor.rowcount
    conn.commit()
    cursor.close()
    conn.close()
    return affected > 0


def set_profile(name, shelf_life_days=None, is_luxury=None):
    """Set shelf-life and/or luxury-tier metadata on an existing item.

    Each field is only overwritten when explicitly passed (via SQL COALESCE),
    so the two can be set independently across separate calls — e.g. asking
    the user two follow-up questions in sequence.

    Args:
        name: Exact item name to update.
        shelf_life_days: Typical days until it spoils; 0 means "doesn't
            apply / non-perishable" (distinct from NULL, meaning "not yet
            asked"). Leave unset to not touch this field.
        is_luxury: 1 for a luxury/treat purchase, 0 for a regular essential.
            Leave unset to not touch this field.

    Returns:
        True if the item was found and updated, False otherwise.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE inventory_items
        SET shelf_life_days = COALESCE(?, shelf_life_days),
            is_luxury = COALESCE(?, is_luxury)
        WHERE name = ?
    """, (shelf_life_days, is_luxury, name))
    affected = cursor.rowcount
    conn.commit()
    cursor.close()
    conn.close()
    return affected > 0


def get_checkin_candidates():
    """Return non-luxury, profiled items eligible for a shelf-life check-in.

    Excludes luxury items (tracing an expiry date isn't meaningful for a
    treat bought on mood/budget rather than a consumption schedule), items
    with no shelf-life estimate yet, and items with one already pending.

    Returns:
        List of dicts: name, shelf_life_days, last_purchase (ISO datetime of
        the most recent expense's logged_at, or None if never purchased).
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT i.name, i.shelf_life_days,
               (SELECT MAX(e.logged_at) FROM item_expenses e WHERE e.item_id = i.id) AS last_purchase
        FROM inventory_items i
        WHERE i.is_luxury = 0
          AND i.shelf_life_days IS NOT NULL
          AND i.shelf_life_days > 0
          AND i.checkin_pending = 0
    """)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return [dict(r) for r in rows]


def get_pending_checkin_item():
    """Return the name of the oldest item awaiting a check-in reply, or None."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM inventory_items WHERE checkin_pending = 1 ORDER BY id LIMIT 1")
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row["name"] if row else None


def mark_checkin_pending(name, pending=True):
    """Set or clear the checkin_pending flag for an item.

    Args:
        name: Exact item name to update.
        pending: True to flag it as awaiting a reply, False to clear it.

    Returns:
        True if the item was found and updated, False otherwise.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE inventory_items SET checkin_pending = ? WHERE name = ?",
        (1 if pending else 0, name)
    )
    affected = cursor.rowcount
    conn.commit()
    cursor.close()
    conn.close()
    return affected > 0


def bump_shelf_life(name, days_delta):
    """Adjust an item's shelf_life_days estimate by a relative amount, floored at 1 day.

    Used when a check-in reply says an item still lasts, pushing the
    estimate (and so the next check-in) further out rather than re-asking
    on every subsequent day.

    Args:
        name: Exact item name to update.
        days_delta: Amount to add to the current estimate (can be negative).

    Returns:
        True if the item was found and updated, False otherwise.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE inventory_items SET shelf_life_days = MAX(1, shelf_life_days + ?) WHERE name = ?",
        (days_delta, name)
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
    cursor.execute("DELETE FROM inventory_items WHERE name = ?", (name,))
    affected = cursor.rowcount
    conn.commit()
    cursor.close()
    conn.close()
    return affected > 0
