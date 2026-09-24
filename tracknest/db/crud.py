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


def get_item_by_id(item_id):
    """Return a single inventory item by id, or None if not found."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM inventory_items WHERE id = ?", (item_id,))
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


def set_profile(name, shelf_life_days=None, purchase_type=None):
    """Set shelf-life and/or purchase-type metadata on an existing item.

    Each field is only overwritten when explicitly passed (via SQL COALESCE),
    so the two can be set independently across separate calls — e.g. asking
    the user two follow-up questions in sequence.

    Args:
        name: Exact item name to update.
        shelf_life_days: Typical days until it spoils; 0 means "doesn't
            apply / non-perishable" (distinct from NULL, meaning "not yet
            asked"). Leave unset to not touch this field.
        purchase_type: One of 'luxury', 'essential', 'necessity' (a
            same-day-consumed item like a coffee or a pretzel — not stocked,
            not tracked on a schedule). Leave unset to not touch this field.

    Returns:
        True if the item was found and updated, False otherwise.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE inventory_items
        SET shelf_life_days = COALESCE(?, shelf_life_days),
            purchase_type = COALESCE(?, purchase_type)
        WHERE name = ?
    """, (shelf_life_days, purchase_type, name))
    affected = cursor.rowcount
    conn.commit()
    cursor.close()
    conn.close()
    return affected > 0


def set_par_level(name, level):
    """Set a per-item par-level override (1 or 2), superseding the household default.

    Args:
        name: Exact item name to update.
        level: 1 (replace when low) or 2 (always keep a spare in stock).

    Returns:
        True if the item was found and updated, False otherwise.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE inventory_items SET par_level = ? WHERE name = ?", (level, name))
    affected = cursor.rowcount
    conn.commit()
    cursor.close()
    conn.close()
    return affected > 0


def get_par_alert_candidates(default_par_level):
    """Return essential, profiled items on a par=2 policy eligible for a spare-stock alert.

    Mirrors get_checkin_candidates, but only for items whose effective par
    level (per-item override, or the household default) is 2 — a par=1 item
    just waits for the regular shelf-life check-in instead. Excludes both
    luxury (no consumption schedule) and necessity (same-day, never stocked)
    purchase types, and any item that lasts a day or less — its "run out
    soon" point is the moment it's bought, so the alert would only be noise.

    Args:
        default_par_level: The household's default par level, used for any
            item without a per-item override.

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
        WHERE i.purchase_type = 'essential'
          AND i.shelf_life_days IS NOT NULL
          AND i.shelf_life_days > 1
          AND i.spare_alert_pending = 0
          AND COALESCE(i.par_level, ?) >= 2
    """, (default_par_level,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return [dict(r) for r in rows]


def mark_spare_alert_pending(name, pending=True):
    """Set or clear the spare-stock alert flag for a par=2 item.

    Args:
        name: Exact item name to update.
        pending: True once the "buy a spare" alert has been sent, False to
            clear it (e.g. on a fresh purchase, starting a new cycle).

    Returns:
        True if the item was found and updated, False otherwise.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE inventory_items SET spare_alert_pending = ? WHERE name = ?",
        (1 if pending else 0, name)
    )
    affected = cursor.rowcount
    conn.commit()
    cursor.close()
    conn.close()
    return affected > 0


def get_checkin_candidates():
    """Return essential, profiled items eligible for a shelf-life check-in.

    Excludes luxury items (tracing an expiry date isn't meaningful for a
    treat bought on mood/budget rather than a consumption schedule) and
    necessity items (same-day-consumed, never actually stocked, so there's
    nothing to check in on), items with no shelf-life estimate yet, and
    items with one already pending.

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
        WHERE i.purchase_type = 'essential'
          AND i.shelf_life_days IS NOT NULL
          AND i.shelf_life_days > 0
          AND i.checkin_pending = 0
    """)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return [dict(r) for r in rows]


def get_pending_profile_item():
    """Return (name, stage) for the oldest item still mid item-profiling, or None.

    Derived entirely from purchase_type/shelf_life_days being NULL ("not yet
    asked") rather than a separate flag — an item only ever gets these
    columns via the profiling flow, so this is a faithful, persisted
    replacement for an in-memory queue. Stage 'purchase_type' comes first
    for any item never asked at all. Stage 'shelf_life' follows only for a
    'luxury' or 'essential' item with no shelf-life estimate yet — a
    'necessity' item (same-day-consumed: a coffee, a pretzel) has its
    shelf_life_days set to 1 automatically the moment purchase_type is
    answered, so it's never asked this question at all. Works the same
    regardless of which process (the live bot or the offline receipt worker)
    logged the item, since both just leave these columns NULL.

    Returns:
        (name, stage) tuple, stage being 'name' or 'category' (a receipt item
        not yet named by the user — see name_status), 'purchase_type' or
        'shelf_life', or None if no item needs profiling right now.
    """
    conn = get_connection()
    cursor = conn.cursor()
    # Naming comes first: an item fresh off a receipt is asked what it really
    # is before any profiling question, so those use the real name.
    cursor.execute(
        "SELECT name, name_status FROM inventory_items WHERE name_status IS NOT NULL ORDER BY id LIMIT 1"
    )
    row = cursor.fetchone()
    if row:
        cursor.close()
        conn.close()
        return row["name"], row["name_status"]
    cursor.execute("SELECT name FROM inventory_items WHERE purchase_type IS NULL ORDER BY id LIMIT 1")
    row = cursor.fetchone()
    if row:
        cursor.close()
        conn.close()
        return row["name"], "purchase_type"
    cursor.execute("""
        SELECT name FROM inventory_items
        WHERE purchase_type IN ('luxury', 'essential') AND shelf_life_days IS NULL
        ORDER BY id LIMIT 1
    """)
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return (row["name"], "shelf_life") if row else None


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


def get_alias(receipt_name):
    """Look up what the user said a receipt's wording really is.

    Returns:
        Dict with canonical_name and category, or None if never asked.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT canonical_name, category FROM item_aliases WHERE receipt_name = ?",
        (receipt_name,),
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return dict(row) if row else None


def save_alias(receipt_name, canonical_name, category=None):
    """Remember (or update) the real name and category behind a receipt's wording."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO item_aliases (receipt_name, canonical_name, category) VALUES (?, ?, ?)
        ON CONFLICT(receipt_name) DO UPDATE SET
            canonical_name = excluded.canonical_name, category = excluded.category
    """, (receipt_name, canonical_name, category))
    conn.commit()
    cursor.close()
    conn.close()


def mark_name_pending(name):
    """Flag an item first seen on a receipt, so the user gets asked what it really is."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE inventory_items SET name_status = 'name' WHERE name = ?", (name,))
    conn.commit()
    cursor.close()
    conn.close()


def rename_item(old_name, new_name):
    """Give an item its real name, merging into an existing item of that name.

    Merging (moving the purchases over, adding up stock) keeps one item's
    price history whole instead of splitting it across a receipt spelling
    and the name the user types.

    Returns:
        (final_name, merged) — merged is True when new_name already existed,
        in which case that item's category and profile carry over and there
        is nothing left to ask.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, quantity FROM inventory_items WHERE name = ?", (old_name,))
    old = cursor.fetchone()
    cursor.execute(
        "SELECT id, name FROM inventory_items WHERE name = ? COLLATE NOCASE AND id != ?",
        (new_name, old["id"]),
    )
    existing = cursor.fetchone()
    if existing:
        cursor.execute("UPDATE item_expenses SET item_id = ? WHERE item_id = ?", (existing["id"], old["id"]))
        cursor.execute(
            "UPDATE inventory_items SET quantity = quantity + ? WHERE id = ?",
            (old["quantity"], existing["id"]),
        )
        cursor.execute("DELETE FROM inventory_items WHERE id = ?", (old["id"],))
        final_name, merged = existing["name"], True
    else:
        cursor.execute(
            "UPDATE inventory_items SET name = ?, name_status = 'category' WHERE id = ?",
            (new_name, old["id"]),
        )
        final_name, merged = new_name, False
    conn.commit()
    cursor.close()
    conn.close()
    return final_name, merged


def keep_item_name(name):
    """Accept the receipt's wording as the item's name; the category is asked next."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE inventory_items SET name_status = 'category' WHERE name = ?", (name,))
    conn.commit()
    cursor.close()
    conn.close()


def set_item_category(name, category):
    """Set an item's category and finish its naming step.

    Also updates any alias pointing at this item, so the next receipt with
    the same wording lands in the same category.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE inventory_items SET category = ?, name_status = NULL WHERE name = ?",
        (category, name),
    )
    cursor.execute("UPDATE item_aliases SET category = ? WHERE canonical_name = ?", (category, name))
    conn.commit()
    cursor.close()
    conn.close()
