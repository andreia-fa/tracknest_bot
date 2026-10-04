"""SQLite connection factory and schema initialisation for TrackNest."""

import logging
import os
import re
import sqlite3
import time
from datetime import datetime

from config import DB_PATH

logger = logging.getLogger(__name__)

_RETRY_DELAYS = (1, 2, 4)


def get_connection():
    """Return a new SQLite connection, retrying up to 3 times on failure.

    Rows are returned as sqlite3.Row (dict-like access). Foreign keys are
    enabled per-connection, since SQLite disables them by default. Callers
    are responsible for closing the connection when done.

    Raises:
        sqlite3.Error: If all retry attempts are exhausted.
    """
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    last_exc: sqlite3.Error | None = None
    for delay in (*_RETRY_DELAYS, None):
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            return conn
        except sqlite3.Error as exc:
            last_exc = exc
            if delay is None:
                break
            logger.warning("DB connection failed (%s), retrying in %ss…", exc, delay)
            time.sleep(delay)
    raise last_exc


def backup_db(label):
    """Copy the whole DB to a timestamped file in a backups/ folder next to it.

    Uses SQLite's backup API, which is safe while the bot is running (a
    plain file copy can catch a half-written page). Anything that rewrites
    data in bulk — migrations, one-off repair scripts — calls this first.

    Returns:
        The path of the backup file.
    """
    backup_dir = os.path.join(os.path.dirname(DB_PATH) or ".", "backups")
    os.makedirs(backup_dir, exist_ok=True)
    path = os.path.join(backup_dir, f"tracknest-{datetime.now():%Y%m%d-%H%M%S}-{label}.db")
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(path)
    src.backup(dst)
    dst.close()
    src.close()
    return path


def _inventory_items_sql(table, name_collation=" COLLATE NOCASE"):
    """The inventory_items schema, with its rules enforced by the database itself.

    treat_or_need and lasts are spelled out ('unknown' until answered), never
    left blank, and shelf_life_days only holds a real number of days: it's
    required when lasts = 'days' and refused otherwise — a bug or a bad
    script can't store a value that means two things.
    """
    return f"""
        CREATE TABLE {table} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE{name_collation},
            quantity INTEGER NOT NULL,
            unit TEXT,
            category TEXT,
            alert_threshold INTEGER,
            image_path TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            treat_or_need TEXT NOT NULL DEFAULT 'unknown'
                CHECK (treat_or_need IN ('treat', 'need', 'unknown')),
            lasts TEXT NOT NULL DEFAULT 'unknown'
                CHECK (lasts IN ('same_day', 'days', 'one_off', 'unknown')),
            shelf_life_days INTEGER CHECK (shelf_life_days >= 2),
            checkin_pending INTEGER NOT NULL DEFAULT 0,
            par_level INTEGER,
            spare_alert_pending INTEGER NOT NULL DEFAULT 0,
            name_status TEXT,
            product TEXT,
            notes TEXT CHECK (notes IS NULL OR length(trim(notes)) > 0),
            CHECK ((lasts = 'days') = (shelf_life_days IS NOT NULL))
        )
    """


def _split_purchase_type(conn):
    """Replace purchase_type with two separate answers: treat_or_need and lasts.

    purchase_type ('luxury' / 'essential' / 'necessity') answered two
    questions at once — whether it's a treat, and how long it lasts
    ('necessity' meant gone the same day) — so a pretzel had to be either a
    treat or same-day, never both. Mapping: luxury -> treat, essential ->
    need, necessity -> lasts 'same_day' with treat_or_need still 'unknown'
    (it never said which). shelf_life_days 1 becomes 'same_day', 0 ("doesn't
    spoil") 'one_off', 2+ stays a number of days.

    The table is rebuilt (the new rules can't be added to existing columns)
    after a backup, in one transaction, with foreign keys off so no purchase
    can cascade away; the row count is checked before committing.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(inventory_items)")}
    if "purchase_type" not in cols:
        return
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'inventory_items'"
    ).fetchone()[0]
    collation = " COLLATE NOCASE" if "COLLATE NOCASE" in sql else ""
    conn.commit()
    logger.info("Backed up to %s before splitting purchase_type.", backup_db("before-split-purchase-type"))
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN")
        before = conn.execute("SELECT COUNT(*) FROM inventory_items").fetchone()[0]
        conn.execute(_inventory_items_sql("inventory_items_new", collation))
        conn.execute("""
            INSERT INTO inventory_items_new (
                id, name, quantity, unit, category, alert_threshold, image_path, created_at,
                treat_or_need, lasts, shelf_life_days,
                checkin_pending, par_level, spare_alert_pending, name_status, product
            )
            SELECT id, name, quantity, unit, category, alert_threshold, image_path, created_at,
                   CASE purchase_type WHEN 'luxury' THEN 'treat' WHEN 'essential' THEN 'need' ELSE 'unknown' END,
                   CASE WHEN purchase_type = 'necessity' OR shelf_life_days = 1 THEN 'same_day'
                        WHEN shelf_life_days = 0 THEN 'one_off'
                        WHEN shelf_life_days >= 2 THEN 'days'
                        ELSE 'unknown' END,
                   CASE WHEN COALESCE(purchase_type, '') != 'necessity' AND shelf_life_days >= 2
                        THEN shelf_life_days END,
                   checkin_pending, par_level, spare_alert_pending, name_status, product
            FROM inventory_items
        """)
        after = conn.execute("SELECT COUNT(*) FROM inventory_items_new").fetchone()[0]
        if after != before:
            raise sqlite3.IntegrityError(f"row count changed: {before} -> {after}")
        conn.execute("DROP TABLE inventory_items")
        conn.execute("ALTER TABLE inventory_items_new RENAME TO inventory_items")
        broken = conn.execute("PRAGMA foreign_key_check").fetchall()
        if broken:
            raise sqlite3.IntegrityError(f"foreign key check failed: {broken}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    logger.info("purchase_type split into treat_or_need and lasts.")


def _add_notes_column(conn):
    """Add inventory_items.notes: the user's own remarks on an item (a size, a brand).

    NULL means no note; a blank one is refused, so "empty" never has to be
    told apart from "nothing to say". Runs after _split_purchase_type, whose
    rebuild already includes the column for databases it migrates.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(inventory_items)")}
    if "notes" not in cols:
        conn.execute(
            "ALTER TABLE inventory_items ADD COLUMN notes TEXT CHECK (notes IS NULL OR length(trim(notes)) > 0)"
        )
        conn.commit()


def _make_item_names_case_insensitive(conn):
    """Rebuild inventory_items so item names ignore case ("pfefferbretzel" is "Pfefferbretzel").

    SQLite can't change a column's collation in place, so the table is
    rebuilt (same columns and rows) inside one transaction, after a backup.
    With the collation on the column itself, every lookup, update and the
    UNIQUE/upsert check ignore case without touching any query. Skipped —
    loudly — while two names still differ only by case: merging them is a
    data decision for a person to confirm, not something to do at startup.
    """
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'inventory_items'"
    ).fetchone()[0]
    if "COLLATE NOCASE" in sql:
        return
    clashes = [row[0] for row in conn.execute(
        "SELECT lower(name) FROM inventory_items GROUP BY lower(name) HAVING COUNT(*) > 1"
    )]
    if clashes:
        logger.error("Item names differing only by case (%s) — merge them first; names stay case-sensitive.",
                     ", ".join(clashes))
        return
    # A table that was itself renamed into place is stored as
    # CREATE TABLE "inventory_items", quoted.
    new_sql = re.sub(r'CREATE TABLE "?inventory_items"?', "CREATE TABLE inventory_items_new", sql, count=1).replace(
        "name TEXT NOT NULL UNIQUE", "name TEXT NOT NULL UNIQUE COLLATE NOCASE", 1)
    if "inventory_items_new" not in new_sql or "COLLATE NOCASE" not in new_sql:
        logger.error("Unexpected inventory_items schema — case-insensitive names not applied.")
        return
    conn.commit()
    logger.info("Backed up to %s before making item names case-insensitive.", backup_db("before-nocase-names"))
    # Off so dropping the old table can't cascade-delete purchases; it can
    # only change outside a transaction.
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN")
        conn.execute(new_sql)
        conn.execute("INSERT INTO inventory_items_new SELECT * FROM inventory_items")
        conn.execute("DROP TABLE inventory_items")
        conn.execute("ALTER TABLE inventory_items_new RENAME TO inventory_items")
        broken = conn.execute("PRAGMA foreign_key_check").fetchall()
        if broken:
            raise sqlite3.IntegrityError(f"foreign key check failed: {broken}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    logger.info("Item names are now case-insensitive.")


def init_db():
    """Create the required tables if they do not already exist (idempotent)."""
    conn = get_connection()
    cursor = conn.cursor()
    has_inventory = cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'inventory_items'"
    ).fetchone()
    if not has_inventory:
        cursor.execute(_inventory_items_sql("inventory_items"))
    # shelf_life_days / is_luxury added after the initial schema — ALTER
    # instead of a fresh CREATE so existing databases keep their data.
    # (Legacy meaning, before _split_purchase_type: NULL = not yet asked,
    # 0 = doesn't spoil / n/a.)
    existing_inv_cols = {row[1] for row in cursor.execute("PRAGMA table_info(inventory_items)")}
    if "shelf_life_days" not in existing_inv_cols:
        cursor.execute("ALTER TABLE inventory_items ADD COLUMN shelf_life_days INTEGER")
    # is_luxury (0/1) replaced 2026-09-22 by purchase_type ('luxury' /
    # 'essential' / 'necessity') — a same-day-consumed item (coffee, a
    # pretzel) isn't a "luxury" or a stocked "essential", and conflating
    # its shelf life with "doesn't spoil" under shelf_life_days=0 was
    # wrong in the other direction. Migrate existing values once, then
    # drop the old column.
    # (Only on databases from before treat_or_need/lasts replaced it — see
    # _split_purchase_type.)
    if "purchase_type" not in existing_inv_cols and "lasts" not in existing_inv_cols:
        cursor.execute("ALTER TABLE inventory_items ADD COLUMN purchase_type TEXT")
        if "is_luxury" in existing_inv_cols:
            cursor.execute("""
                UPDATE inventory_items SET purchase_type = CASE is_luxury
                    WHEN 1 THEN 'luxury' WHEN 0 THEN 'essential' ELSE NULL END
            """)
    if "is_luxury" in existing_inv_cols:
        cursor.execute("ALTER TABLE inventory_items DROP COLUMN is_luxury")
    if "checkin_pending" not in existing_inv_cols:
        cursor.execute(
            "ALTER TABLE inventory_items ADD COLUMN checkin_pending INTEGER NOT NULL DEFAULT 0"
        )
    # par_level: NULL = use the household default (bot_settings), 1 = replace
    # right when it runs low, 2 = always keep one spare in stock.
    if "par_level" not in existing_inv_cols:
        cursor.execute("ALTER TABLE inventory_items ADD COLUMN par_level INTEGER")
    if "spare_alert_pending" not in existing_inv_cols:
        cursor.execute(
            "ALTER TABLE inventory_items ADD COLUMN spare_alert_pending INTEGER NOT NULL DEFAULT 0"
        )
    # name_status: NULL = name settled; 'name' / 'category' = an item first
    # seen on a receipt, still waiting for the user to say what it really is
    # (receipt names are abbreviations like "BIO aln.pfanne"). Asked before
    # purchase type, so the profiling questions use the real name.
    if "name_status" not in existing_inv_cols:
        cursor.execute("ALTER TABLE inventory_items ADD COLUMN name_status TEXT")
    # product: what the item generically is ("cheese" for "LEERDAMMER CAR",
    # "milk" for any brand of milk) — needs are reasoned about per product,
    # not per brand. NULL = not known yet.
    if "product" not in existing_inv_cols:
        cursor.execute("ALTER TABLE inventory_items ADD COLUMN product TEXT")
    # shelf_life_corrected tracked whether log_expense had auto-corrected an
    # estimate, but nothing ever read it — dropped 2026-09-20.
    if "shelf_life_corrected" in existing_inv_cols:
        cursor.execute("ALTER TABLE inventory_items DROP COLUMN shelf_life_corrected")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS shopping_list_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            quantity INTEGER NOT NULL,
            added_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # category added after the initial schema — ALTER instead of a fresh
    # CREATE so existing databases keep their data.
    existing_list_cols = {row[1] for row in cursor.execute("PRAGMA table_info(shopping_list_items)")}
    if "category" not in existing_list_cols:
        cursor.execute("ALTER TABLE shopping_list_items ADD COLUMN category TEXT")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS item_expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER,
            quantity_purchased INTEGER,
            unit_price REAL,
            store TEXT,
            purchase_date TEXT,
            FOREIGN KEY (item_id) REFERENCES inventory_items(id) ON DELETE CASCADE
        )
    """)
    # logged_at added after the initial schema — ALTER instead of a fresh
    # CREATE so existing databases pick it up without losing data.
    existing_cols = {row[1] for row in cursor.execute("PRAGMA table_info(item_expenses)")}
    if "logged_at" not in existing_cols:
        cursor.execute("ALTER TABLE item_expenses ADD COLUMN logged_at TEXT")
    # trip_key: which visit a purchase belongs to — "receipt:<id>" or
    # "typed:<when>" (one per typed message). NULL on purchases from before
    # it existed; those fall back to one trip per store per day.
    if "trip_key" not in existing_cols:
        cursor.execute("ALTER TABLE item_expenses ADD COLUMN trip_key TEXT")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bot_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    # Every shopping-list removal, so a wrong receipt match (the model once
    # cleared "Tuna" for a smoked-salmon purchase) can be seen and undone
    # instead of the entry being lost for good.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS shopping_list_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            category TEXT,
            added_at TEXT,
            removed_at TEXT DEFAULT CURRENT_TIMESTAMP,
            reason TEXT NOT NULL,
            source TEXT,
            restored INTEGER NOT NULL DEFAULT 0
        )
    """)
    # What the user said a receipt's wording really is — consulted on every
    # later receipt, so each abbreviation is only ever asked about once.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS item_aliases (
            receipt_name TEXT PRIMARY KEY COLLATE NOCASE,
            canonical_name TEXT NOT NULL,
            category TEXT
        )
    """)
    existing_alias_cols = {row[1] for row in cursor.execute("PRAGMA table_info(item_aliases)")}
    if "product" not in existing_alias_cols:
        cursor.execute("ALTER TABLE item_aliases ADD COLUMN product TEXT")
    # The model's other readings of what a new receipt item is ("cream",
    # "cream cheese"), offered as buttons when the user says its first guess
    # is wrong — so correcting it is a tap, not typing. Dropped once answered.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS product_options (
            item_id INTEGER NOT NULL REFERENCES inventory_items(id) ON DELETE CASCADE,
            rank INTEGER NOT NULL,
            product TEXT NOT NULL CHECK (length(trim(product)) > 0),
            PRIMARY KEY (item_id, rank)
        )
    """)
    # Receipt photos are queued here by the (cloud) bot and processed later by
    # the local worker, which is the only thing with access to Ollama.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pending_receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            telegram_file_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            queued_at TEXT DEFAULT CURRENT_TIMESTAMP,
            resolved_at TEXT
        )
    """)
    conn.commit()
    cursor.close()
    _make_item_names_case_insensitive(conn)
    _split_purchase_type(conn)
    _add_notes_column(conn)
    conn.close()
