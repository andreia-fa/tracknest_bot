"""Inventory item CRUD operations against the inventory_items table."""

from db.database import get_connection


def add_item(name, quantity, unit=None, category=None, alert_threshold=None, product=None):
    """Add a new item or restock an existing one.

    Uses an UPSERT: if an item with the same name already exists, the given
    quantity is added to its current stock rather than replacing it, and a
    product is only filled in if the item didn't have one yet.

    Args:
        name: Item name (case-sensitive, must be unique in the table).
        quantity: Units to add.
        unit: Unit of measure (e.g. "kg", "L", "pcs").
        category: Optional grouping label (e.g. "Dairy").
        alert_threshold: Optional minimum stock level for low-stock alerts.
        product: What the item generically is, brand aside (e.g. "cheese").
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO inventory_items (name, quantity, unit, category, alert_threshold, product)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            quantity = quantity + excluded.quantity,
            product = COALESCE(inventory_items.product, excluded.product)
    """, (name, quantity, unit, category, alert_threshold, product))
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


def get_item_names():
    """Return every inventory item's name."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM inventory_items")
    names = [row["name"] for row in cursor.fetchall()]
    cursor.close()
    conn.close()
    return names


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


TREAT_OR_NEED = ("treat", "need")
LASTS = ("same_day", "days", "one_off")


def set_treat_or_need(name, value):
    """Record whether an item is a treat or a need.

    Args:
        name: Item name.
        value: 'treat' or 'need'.

    Returns:
        True if the item was found and updated, False otherwise.
    """
    if value not in TREAT_OR_NEED:
        raise ValueError(f"treat_or_need must be one of {TREAT_OR_NEED}, not {value!r}")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE inventory_items SET treat_or_need = ? WHERE name = ?", (value, name))
    affected = cursor.rowcount
    conn.commit()
    cursor.close()
    conn.close()
    return affected > 0


def set_lasts(name, lasts, days=None):
    """Record how long an item lasts before it's bought again.

    Args:
        name: Item name.
        lasts: 'same_day' (gone the day it's bought — a pretzel, a pool
            ticket), 'days' (lasts `days` days, then reminders apply) or
            'one_off' (not rebought on any schedule — never reminded).
        days: Number of days (2 or more); required for 'days', ignored
            otherwise.

    Returns:
        True if the item was found and updated, False otherwise.
    """
    if lasts not in LASTS:
        raise ValueError(f"lasts must be one of {LASTS}, not {lasts!r}")
    if lasts == "days" and (days is None or days < 2):
        raise ValueError(f"lasts='days' needs 2 or more days, not {days!r}")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE inventory_items SET lasts = ?, shelf_life_days = ? WHERE name = ?",
        (lasts, days if lasts == "days" else None, name),
    )
    affected = cursor.rowcount
    conn.commit()
    cursor.close()
    conn.close()
    return affected > 0


def set_item_note(name, note):
    """Save the user's note on an item (a size, a brand, ...), or clear it.

    Args:
        name: Item name.
        note: The note; blank or None clears it.

    Returns:
        True if the item was found and updated, False otherwise.
    """
    note = (note or "").strip() or None
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE inventory_items SET notes = ? WHERE name = ?", (note, name))
    affected = cursor.rowcount
    conn.commit()
    cursor.close()
    conn.close()
    return affected > 0


def get_noted_items():
    """Return every item with a note, as dicts of name, product and notes."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name, product, notes FROM inventory_items WHERE notes IS NOT NULL ORDER BY id")
    rows = [dict(r) for r in cursor.fetchall()]
    cursor.close()
    conn.close()
    return rows


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


# The most recent purchase of anything that is the same product as item i —
# a need is covered by any brand of it, so Gouda bought yesterday means the
# household has cheese even if Leerdammer ran out. Items without a known
# product stand alone (keyed by their name).
_LATEST_PRODUCT_PURCHASE_JOIN = """
    LEFT JOIN item_expenses e ON e.id = (
        SELECT e2.id FROM item_expenses e2
        JOIN inventory_items i2 ON i2.id = e2.item_id
        WHERE COALESCE(i2.product, i2.name) = COALESCE(i.product, i.name)
        ORDER BY e2.logged_at DESC, e2.id DESC LIMIT 1
    )
"""


def get_par_alert_candidates(default_par_level):
    """Return needs lasting a number of days, on a par=2 policy, eligible for a spare-stock alert.

    Reasoned per product: only the most recently bought item of each product
    is a candidate, timed from that purchase, so one product never alerts
    twice and a fresh purchase of another brand quiets the alert.

    Mirrors get_checkin_candidates, but only for items whose effective par
    level (per-item override, or the household default) is 2 — a par=1 item
    just waits for the regular shelf-life check-in instead. Excludes treats
    (no consumption schedule), anything same-day or one-off — a same-day
    item's "run out soon" point is the moment it's bought, so the alert
    would only be noise — and anything already on the shopping list (by
    name or product), since that's what the alert would ask for.

    Args:
        default_par_level: The household's default par level, used for any
            item without a per-item override.

    Returns:
        List of dicts: id, name, product, category, shelf_life_days, notes,
        last_purchase (ISO datetime of the product's most recent expense, or
        None if never purchased), last_quantity (units bought that time) and
        last_store (where, if known).
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"""
        SELECT i.id, i.name, i.product, i.category, i.shelf_life_days, i.notes,
               e.logged_at AS last_purchase, e.quantity_purchased AS last_quantity, e.store AS last_store
        FROM inventory_items i
        {_LATEST_PRODUCT_PURCHASE_JOIN}
        WHERE i.treat_or_need = 'need'
          AND i.lasts = 'days'
          AND i.spare_alert_pending = 0
          AND COALESCE(i.par_level, ?) >= 2
          AND (e.id IS NULL OR e.item_id = i.id)
          AND NOT EXISTS (
              -- Not "s.name = ... COLLATE NOCASE OR ...": SQLite 3.45 answers that
              -- wrongly against the unique index on s.name.
              SELECT 1 FROM shopping_list_items s
              WHERE lower(s.name) IN (lower(i.name), lower(COALESCE(i.product, i.name)))
          )
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
    """Return needs lasting a number of days that are eligible for a shelf-life check-in.

    Excludes treats (tracing an expiry date isn't meaningful for a treat
    bought on mood/budget rather than a consumption schedule), same-day and
    one-off items (never stocked, so there's nothing to check in on), items
    whose shelf life isn't known yet, and items with one already pending. Like the spare alert, it's reasoned per
    product: only the most recently bought item of each product is asked
    about, timed from that purchase.

    Returns:
        List of dicts: id, name, product, shelf_life_days, notes, last_purchase
        (ISO datetime of the product's most recent expense, or None if never
        purchased) and last_store (where, if known).
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"""
        SELECT i.id, i.name, i.product, i.shelf_life_days, i.notes, e.logged_at AS last_purchase, e.store AS last_store
        FROM inventory_items i
        {_LATEST_PRODUCT_PURCHASE_JOIN}
        WHERE i.treat_or_need = 'need'
          AND i.lasts = 'days'
          AND i.checkin_pending = 0
          AND (e.id IS NULL OR e.item_id = i.id)
    """)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return [dict(r) for r in rows]


def get_pending_profile_item():
    """Return (name, stage) for the oldest item still mid item-profiling, or None.

    Derived from the item's own columns rather than a separate queue: a
    name still to settle comes first (name_status), then treat_or_need, then
    lasts, while either is still 'unknown'. Works the same regardless of
    which process (the live bot or the offline receipt worker) logged the
    item.

    Returns:
        (name, stage) tuple, stage being 'name' or 'category' (a receipt item
        not yet named by the user — see name_status), 'treat_or_need' or
        'lasts', or None if no item needs profiling right now.
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
    for stage in ("treat_or_need", "lasts"):
        cursor.execute(f"SELECT name FROM inventory_items WHERE {stage} = 'unknown' ORDER BY id LIMIT 1")
        row = cursor.fetchone()
        if row:
            cursor.close()
            conn.close()
            return row["name"], stage
    cursor.close()
    conn.close()
    return None


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
    """Adjust an item's shelf-life estimate by a relative amount, floored at 2 days.

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
        "UPDATE inventory_items SET shelf_life_days = MAX(2, shelf_life_days + ?) WHERE name = ? AND lasts = 'days'",
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
        "SELECT canonical_name, category, product FROM item_aliases WHERE receipt_name = ?",
        (receipt_name,),
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return dict(row) if row else None


def save_alias(receipt_name, canonical_name, category=None, product=None):
    """Remember (or update) the real name, category and product behind a receipt's wording."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO item_aliases (receipt_name, canonical_name, category, product) VALUES (?, ?, ?, ?)
        ON CONFLICT(receipt_name) DO UPDATE SET
            canonical_name = excluded.canonical_name, category = excluded.category,
            product = COALESCE(excluded.product, item_aliases.product)
    """, (receipt_name, canonical_name, category, product))
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
    # Receipt wordings already mapped to the old name follow it, so the next
    # receipt doesn't recreate the item under its old name.
    cursor.execute("UPDATE item_aliases SET canonical_name = ? WHERE canonical_name = ?", (final_name, old_name))
    conn.commit()
    cursor.close()
    conn.close()
    return final_name, merged


def change_item_category(name, category):
    """Move an already-settled item to another category (e.g. a café drink to Leisure).

    Unlike set_item_category, leaves any open naming question alone. Aliases
    follow, so the next receipt with the same wording lands there too.

    Returns:
        True if the item was found and updated, False otherwise.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE inventory_items SET category = ? WHERE name = ?", (category, name))
    affected = cursor.rowcount
    cursor.execute("UPDATE item_aliases SET category = ? WHERE canonical_name = ?", (category, name))
    conn.commit()
    cursor.close()
    conn.close()
    return affected > 0


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


def set_item_product(name, product):
    """Set what an item generically is, and update any alias pointing at it to match."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE inventory_items SET product = ? WHERE name = ?", (product, name))
    cursor.execute("UPDATE item_aliases SET product = ? WHERE canonical_name = ?", (product, name))
    conn.commit()
    cursor.close()
    conn.close()


def copy_product_profile(name, product):
    """Give an item the profile its product already has from another brand.

    A new cheese needs no questions if another cheese was already profiled:
    treat-or-need, how long it lasts and keep-a-spare policy are properties
    of the need, not the brand. Only answers the item doesn't have yet
    (still 'unknown', or no par level) are filled. A one-off item never
    lends its profile: a cheese marked "don't buy again" (one-off, no
    reminders) says nothing about how cheese is used.

    Returns:
        True if another item of that product had a profile to copy.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT treat_or_need, lasts, shelf_life_days, par_level FROM inventory_items
        WHERE product = ? AND name != ? AND (treat_or_need != 'unknown' OR lasts != 'unknown')
          AND lasts != 'one_off'
        ORDER BY (treat_or_need = 'unknown') + (lasts = 'unknown'), id DESC LIMIT 1
    """, (product, name))
    source = cursor.fetchone()
    if source:
        # Every SET expression sees the row's old values, so lasts and
        # shelf_life_days move together (the table refuses one without the other).
        cursor.execute("""
            UPDATE inventory_items
            SET treat_or_need = CASE WHEN treat_or_need = 'unknown' THEN ? ELSE treat_or_need END,
                lasts = CASE WHEN lasts = 'unknown' THEN ? ELSE lasts END,
                shelf_life_days = CASE WHEN lasts = 'unknown' THEN ? ELSE shelf_life_days END,
                par_level = COALESCE(par_level, ?)
            WHERE name = ?
        """, (source["treat_or_need"], source["lasts"], source["shelf_life_days"], source["par_level"], name))
        conn.commit()
    cursor.close()
    conn.close()
    return source is not None


def get_items_without_product():
    """Return names of settled items whose product was never worked out (logged before products existed)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM inventory_items WHERE product IS NULL AND name_status IS NULL ORDER BY id")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return [r["name"] for r in rows]


def suggest_product(name, product):
    """Store a model's product guess for an existing item and queue the what-is-it question.

    The guess goes into product (shown as a one-tap "✓ Yes, cheese") and the
    item re-enters the naming stage, so it gets asked like a new receipt
    item. An empty guess queues the question without one. Only touches items
    still without a product and not mid-question, so a repeated run is a no-op.

    Returns:
        True if the item was updated.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE inventory_items SET product = ?, name_status = 'name'
        WHERE name = ? AND product IS NULL AND name_status IS NULL
    """, (product or None, name))
    affected = cursor.rowcount
    conn.commit()
    cursor.close()
    conn.close()
    return affected > 0


def set_product_options(name, options):
    """Store the model's other readings of what an item is, in order, replacing earlier ones."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        DELETE FROM product_options WHERE item_id = (SELECT id FROM inventory_items WHERE name = ?)
    """, (name,))
    cursor.executemany("""
        INSERT INTO product_options (item_id, rank, product)
        SELECT id, ?, ? FROM inventory_items WHERE name = ?
    """, [(rank, product, name) for rank, product in enumerate(options)])
    conn.commit()
    cursor.close()
    conn.close()


def get_product_options(name):
    """The stored other readings of what an item is, best first."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT o.product FROM product_options o JOIN inventory_items i ON i.id = o.item_id
        WHERE i.name = ? ORDER BY o.rank
    """, (name,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return [r["product"] for r in rows]


def get_known_products(exclude_name=None):
    """Every product already in the household, most-bought items' products first."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT i.product FROM inventory_items i LEFT JOIN item_expenses e ON e.item_id = i.id
        WHERE i.product IS NOT NULL AND i.product != '' AND i.name_status IS NULL AND i.name IS NOT ?
        GROUP BY i.product ORDER BY COUNT(e.id) DESC, i.product
    """, (exclude_name,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return [r["product"] for r in rows]


def set_name_status(name, status):
    """Set where an item is in its first-purchase questions: 'name', 'card', or None when done."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE inventory_items SET name_status = ? WHERE name = ?", (status, name))
    conn.commit()
    cursor.close()
    conn.close()
