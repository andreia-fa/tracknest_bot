"""Item names ignore case — migrated on a real SQLite file, since the schema is the point."""

import sqlite3
from unittest.mock import patch

import pytest

from db import crud, database, expenses

_OLD_SCHEMA = """
    CREATE TABLE inventory_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        quantity INTEGER NOT NULL,
        unit TEXT,
        category TEXT,
        alert_threshold INTEGER,
        image_path TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
"""


@pytest.fixture
def old_db(tmp_path):
    path = tmp_path / "test.db"
    conn = sqlite3.connect(path)
    conn.execute(_OLD_SCHEMA)
    conn.commit()
    conn.close()
    with patch.object(database, "DB_PATH", str(path)):
        yield path


def test_migration_keeps_rows_and_purchases(old_db):
    database.init_db()  # adds the later columns to the old table, then migrates
    crud.add_item("Pfefferbretzel", 1, product="pretzel")
    expenses.log_expense("Pfefferbretzel", 1, 1.00, store="Yormas")

    item = crud.get_item("pfefferbretzel")
    assert item["name"] == "Pfefferbretzel"
    crud.add_item("PFEFFERBRETZEL", 2)
    assert crud.get_item("Pfefferbretzel")["quantity"] == 3
    assert len(expenses.get_expenses("pfefferbretzel")) == 1
    assert list((old_db.parent / "backups").glob("*-before-nocase-names.db"))


def test_existing_rows_survive_the_rebuild(old_db):
    conn = sqlite3.connect(old_db)
    conn.execute("INSERT INTO inventory_items (name, quantity) VALUES ('Matcha', 1)")
    conn.commit()
    conn.close()
    with patch.object(database, "_make_item_names_case_insensitive"):
        database.init_db()  # old schema + new columns, not migrated yet
    expenses.log_expense("Matcha", 1, 6.90)

    database.init_db()

    assert crud.get_item("MATCHA")["name"] == "Matcha"
    assert expenses.get_total_spent("matcha") == pytest.approx(6.90)


def test_clashing_names_block_the_migration(old_db):
    conn = sqlite3.connect(old_db)
    conn.executemany("INSERT INTO inventory_items (name, quantity) VALUES (?, 1)", [("Brezel",), ("brezel",)])
    conn.commit()
    conn.close()

    database.init_db()

    schema = sqlite3.connect(old_db).execute(
        "SELECT sql FROM sqlite_master WHERE name = 'inventory_items'").fetchone()[0]
    assert "COLLATE NOCASE" not in schema
    assert not (old_db.parent / "backups").exists()
