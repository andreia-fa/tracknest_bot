"""treat_or_need / lasts on a real SQLite file: the database's own rules are what's under test."""

import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from db import crud, database, expenses


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "test.db"
    with patch.object(database, "DB_PATH", str(path)):
        database.init_db()
        yield path


def test_new_items_start_unknown_not_blank(db):
    crud.add_item("Brezel", 1)
    item = crud.get_item("Brezel")
    assert (item["treat_or_need"], item["lasts"], item["shelf_life_days"]) == ("unknown", "unknown", None)


@pytest.mark.parametrize("sql", [
    "UPDATE inventory_items SET treat_or_need = 'luxuryy'",
    "UPDATE inventory_items SET lasts = 'forever'",
    "UPDATE inventory_items SET lasts = 'days', shelf_life_days = NULL",      # days without a number
    "UPDATE inventory_items SET lasts = 'same_day', shelf_life_days = 7",     # a number that means nothing
    "UPDATE inventory_items SET lasts = 'days', shelf_life_days = 1",         # 1 day is 'same_day'
])
def test_database_refuses_values_that_mean_two_things(db, sql):
    crud.add_item("Brezel", 1)
    conn = sqlite3.connect(db)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql)


def test_set_lasts_keeps_days_only_for_days(db):
    crud.add_item("Brezel", 1)
    crud.set_lasts("Brezel", "days", 4)
    crud.set_lasts("Brezel", "same_day")
    assert crud.get_item("Brezel")["shelf_life_days"] is None
    with pytest.raises(ValueError):
        crud.set_lasts("Brezel", "days", 1)
    with pytest.raises(ValueError):
        crud.set_treat_or_need("Brezel", "luxury")


def test_questions_come_in_order(db):
    crud.add_item("Brezel", 1)
    assert crud.get_pending_profile_item() == ("Brezel", "treat_or_need")
    crud.set_treat_or_need("Brezel", "treat")
    assert crud.get_pending_profile_item() == ("Brezel", "lasts")
    crud.set_lasts("Brezel", "same_day")
    assert crud.get_pending_profile_item() is None


def test_another_brand_lends_both_answers(db):
    crud.add_item("Laugenbrezel", 1, product="pretzel")
    crud.set_treat_or_need("Laugenbrezel", "treat")
    crud.set_lasts("Laugenbrezel", "same_day")
    crud.add_item("Pfefferbreze", 1, product="pretzel")

    assert crud.copy_product_profile("Pfefferbreze", "pretzel") is True
    item = crud.get_item("Pfefferbreze")
    assert (item["treat_or_need"], item["lasts"]) == ("treat", "same_day")


def test_only_needs_lasting_days_get_reminders(db):
    long_ago = (datetime.now(tz=timezone.utc) - timedelta(days=30)).isoformat()
    for name, kind, lasts, days in [("Spinat", "need", "days", 7), ("Sushi", "treat", "days", 2),
                                    ("Brezel", "treat", "same_day", None), ("Pool", "treat", "one_off", None)]:
        crud.add_item(name, 1)
        crud.set_treat_or_need(name, kind)
        crud.set_lasts(name, lasts, days)
        expenses.log_expense(name, 1, 1.0)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE item_expenses SET logged_at = ?", (long_ago,))
    conn.commit()

    assert [c["name"] for c in crud.get_checkin_candidates()] == ["Spinat"]
    assert [c["name"] for c in crud.get_par_alert_candidates(default_par_level=2)] == ["Spinat"]


def test_bump_never_goes_below_two_days(db):
    crud.add_item("Spinat", 1)
    crud.set_lasts("Spinat", "days", 3)
    crud.bump_shelf_life("Spinat", -10)
    assert crud.get_item("Spinat")["shelf_life_days"] == 2


_OLD_SCHEMA = """
    CREATE TABLE inventory_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        quantity INTEGER NOT NULL,
        unit TEXT, category TEXT, alert_threshold INTEGER, image_path TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        shelf_life_days INTEGER, checkin_pending INTEGER NOT NULL DEFAULT 0, par_level INTEGER,
        spare_alert_pending INTEGER NOT NULL DEFAULT 0, purchase_type TEXT, name_status TEXT, product TEXT
    )
"""


def test_split_migration_maps_every_old_answer(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(_OLD_SCHEMA)
    conn.executemany(
        "INSERT INTO inventory_items (name, quantity, purchase_type, shelf_life_days) VALUES (?, 1, ?, ?)",
        [("Sushi", "luxury", 2), ("Spinat", "essential", 7), ("Brezel", "necessity", 1),
         ("Pfefferbreze", "luxury", 1), ("Salz", "essential", 0), ("Neu", None, None)],
    )
    conn.commit()
    conn.close()

    with patch.object(database, "DB_PATH", str(path)):
        database.init_db()
        got = {n: (crud.get_item(n)["treat_or_need"], crud.get_item(n)["lasts"], crud.get_item(n)["shelf_life_days"])
               for n in ("Sushi", "Spinat", "Brezel", "Pfefferbreze", "Salz", "Neu")}

    assert got == {
        "Sushi": ("treat", "days", 2),
        "Spinat": ("need", "days", 7),
        "Brezel": ("unknown", "same_day", None),
        "Pfefferbreze": ("treat", "same_day", None),
        "Salz": ("need", "one_off", None),
        "Neu": ("unknown", "unknown", None),
    }
    assert list((tmp_path / "backups").glob("*-before-split-purchase-type.db"))
