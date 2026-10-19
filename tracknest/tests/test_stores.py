"""One name per shop, however a receipt prints it."""

from unittest.mock import patch

import pytest

from bot.stores import canonical_store
from db import crud, database, expenses


@pytest.mark.parametrize("raw, name", [
    ("Yormas im Bahnhof Klettppassage", "Yormas"),
    ("Yormas im Bahnhof Klett Passage", "Yormas"),
    ("YORMAS'S", "Yormas"),
    ("REWE Sezgin Kitis oHG", "REWE"),
    ("PENNY - MARKT GmbH", "PENNY"),
    ("PENNY-MARKT GmbH", "PENNY"),
    ("dm-drogerie markt", "dm"),
    ("BURGER KING®", "Burger King"),
    ("dean and david 3 Koenigsbau GmbH", "dean&david"),
    ("tegut... gute Lebensmittel GmbH & Co. KG", "tegut"),
    ("ALDI SÜD", "Aldi Süd"),
    ("Intimisimi", "Intimissimi"),  # a misread chain still lands
])
def test_a_chain_has_one_name(raw, name):
    assert canonical_store(raw) == name


def test_an_unknown_shop_matches_a_known_one_despite_a_misread():
    assert canonical_store("Giulia Piza & Airomore", ["Giulia Pizza & Airomore"]) == "Giulia Pizza & Airomore"


def test_unrelated_shops_are_not_merged():
    assert canonical_store("Admiral Bar", ["avec"]) == "Admiral Bar"
    assert canonical_store("dmBio") == "dmBio"  # "dm" only as a word of its own
    assert canonical_store("Bäckerei Müller") == "Bäckerei Müller"  # a bakery, not the drugstore
    assert canonical_store("  ") is None and canonical_store(None) is None


def test_a_purchase_is_saved_under_the_shops_one_name(tmp_path):
    with patch.object(database, "DB_PATH", str(tmp_path / "test.db")):
        database.init_db()
        crud.add_item("Matcha", 1)
        expenses.log_expense("Matcha", 1, 4.20, store="Yormas im Bahnhof Klettppassage")
        assert expenses.get_known_stores() == ["Yormas"]
