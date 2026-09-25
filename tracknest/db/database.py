"""SQLite connection factory and schema initialisation for TrackNest."""

import logging
import os
import sqlite3
import time

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


def init_db():
    """Create the required tables if they do not already exist (idempotent)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inventory_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            quantity INTEGER NOT NULL,
            unit TEXT,
            category TEXT,
            alert_threshold INTEGER,
            image_path TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # shelf_life_days / is_luxury added after the initial schema — ALTER
    # instead of a fresh CREATE so existing databases keep their data.
    # shelf_life_days: NULL = not yet asked, 0 = doesn't spoil / n/a.
    existing_inv_cols = {row[1] for row in cursor.execute("PRAGMA table_info(inventory_items)")}
    if "shelf_life_days" not in existing_inv_cols:
        cursor.execute("ALTER TABLE inventory_items ADD COLUMN shelf_life_days INTEGER")
    # is_luxury (0/1) replaced 2026-09-22 by purchase_type ('luxury' /
    # 'essential' / 'necessity') — a same-day-consumed item (coffee, a
    # pretzel) isn't a "luxury" or a stocked "essential", and conflating
    # its shelf life with "doesn't spoil" under shelf_life_days=0 was
    # wrong in the other direction. Migrate existing values once, then
    # drop the old column.
    if "purchase_type" not in existing_inv_cols:
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
    conn.close()
