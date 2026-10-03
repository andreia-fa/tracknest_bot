"""guess_store against a real SQLite file — the SQL is the logic here, so it isn't mocked."""

from unittest.mock import patch

import pytest

from db import crud, database, expenses


@pytest.fixture
def db(tmp_path):
    with patch.object(database, "DB_PATH", str(tmp_path / "test.db")):
        database.init_db()
        crud.add_item("Pfefferbreze", 1, product="pretzel")
        expenses.log_expense("Pfefferbreze", 1, 1.00, store="Yormas")
        crud.add_item("Brezel", 1, product="pretzel")
        expenses.log_expense("Brezel", 1, 1.20, store="REWE")
        crud.add_item("Pfefferbretzel", 1, product="pretzel")  # typed, never on a receipt
        yield


def test_known_price_names_its_store_via_the_product(db):
    assert expenses.guess_store("Pfefferbretzel", "pretzel", 1.0) == "Yormas"


def test_name_alone_is_enough_without_a_product(db):
    assert expenses.guess_store("pfefferbreze", None, 1.0) == "Yormas"


def test_new_price_means_unknown_store(db):
    assert expenses.guess_store("Pfefferbretzel", "pretzel", 1.80) is None


def test_same_price_at_two_stores_is_not_guessed(db):
    expenses.log_expense("Brezel", 1, 1.00, store="REWE")
    assert expenses.guess_store("Pfefferbretzel", "pretzel", 1.0) is None


def test_other_products_dont_count(db):
    assert expenses.guess_store("Matcha", "matcha", 1.0) is None
