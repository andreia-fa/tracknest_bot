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
    conn.commit()
    cursor.close()
    conn.close()
