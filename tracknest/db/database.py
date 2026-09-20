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
    # is_luxury: NULL = not yet asked, 0 = essential, 1 = luxury/treat.
    existing_inv_cols = {row[1] for row in cursor.execute("PRAGMA table_info(inventory_items)")}
    if "shelf_life_days" not in existing_inv_cols:
        cursor.execute("ALTER TABLE inventory_items ADD COLUMN shelf_life_days INTEGER")
    if "is_luxury" not in existing_inv_cols:
        cursor.execute("ALTER TABLE inventory_items ADD COLUMN is_luxury INTEGER")
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
    conn.commit()
    cursor.close()
    conn.close()
