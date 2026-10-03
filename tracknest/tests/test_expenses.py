from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from db import crud, database, expenses


def make_mock_conn(fetchone=None, fetchall=None):
    cursor = MagicMock()
    cursor.fetchone.return_value = fetchone
    cursor.fetchall.return_value = fetchall or []
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn, cursor


@pytest.fixture
def db(tmp_path):
    with patch.object(database, "DB_PATH", str(tmp_path / "test.db")):
        database.init_db()
        yield


def _item(name, kind="need", lasts="days", days=10, product=None, par_level=1):
    crud.add_item(name, 1, product=product)
    crud.set_treat_or_need(name, kind)
    crud.set_lasts(name, lasts, days)
    crud.set_par_level(name, par_level)


def _ago(days):
    return datetime.now(tz=timezone.utc) - timedelta(days=days)


def test_log_expense_item_exists(db):
    _item("Milk")
    assert expenses.log_expense("Milk", 2, 1.50) is True
    assert expenses.get_total_spent("Milk") == pytest.approx(3.0)


def test_log_expense_item_not_found(db):
    assert expenses.log_expense("Ghost", 1, 5.00) is False


def test_log_expense_shortens_shelf_life_on_early_repurchase(db):
    _item("Spinach", product="spinach")
    expenses.log_expense("Spinach", 1, 1.11, purchased_at=_ago(3))
    expenses.log_expense("Spinach", 1, 1.11)
    assert crud.get_item("Spinach")["shelf_life_days"] == 6  # halfway from 10 towards 3, not a jump to 3


def test_log_expense_counts_quantity_of_prior_purchase(db):
    """Two packs gone in 8 days is 4 days a pack."""
    _item("Spinach", product="spinach")
    expenses.log_expense("Spinach", 2, 1.11, purchased_at=_ago(8))
    expenses.log_expense("Spinach", 1, 1.11)
    assert crud.get_item("Spinach")["shelf_life_days"] == 7


def test_log_expense_keeps_shelf_life_for_keep_a_spare_items(db):
    """Buying the spare early is the par-2 policy working, not a sign the item runs out faster."""
    _item("Leerdammer", days=30, par_level=2)
    expenses.log_expense("Leerdammer", 1, 2.99, purchased_at=_ago(5))
    expenses.log_expense("Leerdammer", 1, 2.99)
    assert crud.get_item("Leerdammer")["shelf_life_days"] == 30


def test_log_expense_skips_adjustment_for_treats(db):
    _item("Sushi", kind="treat", days=4)
    expenses.log_expense("Sushi", 1, 10.99, purchased_at=_ago(2))
    expenses.log_expense("Sushi", 1, 10.99)
    assert crud.get_item("Sushi")["shelf_life_days"] == 4


def test_log_expense_skips_adjustment_for_same_day_items(db):
    _item("Matcha", lasts="same_day", days=None)
    expenses.log_expense("Matcha", 1, 2.50, purchased_at=_ago(1))
    expenses.log_expense("Matcha", 1, 2.50)
    assert crud.get_item("Matcha")["shelf_life_days"] is None


def test_old_receipt_counts_on_its_own_date(db):
    _item("Spinach", product="spinach")
    expenses.log_expense("Spinach", 1, 1.11, purchased_at=datetime(2026, 9, 22, 12, tzinfo=timezone.utc))
    assert expenses.get_expenses("Spinach")[0]["purchase_date"] == "2026-09-22"


def test_old_receipt_caught_up_late_doesnt_disturb_the_newer_purchase(db):
    """A September receipt sent after an October purchase: no shelf-life change, alerts left alone."""
    _item("Spinach", product="spinach")
    expenses.log_expense("Spinach", 1, 1.11)
    crud.mark_checkin_pending("Spinach")
    expenses.log_expense("Spinach", 1, 1.11, purchased_at=_ago(3))
    item = crud.get_item("Spinach")
    assert item["shelf_life_days"] == 10
    assert item["checkin_pending"] == 1


def test_duplicate_check_by_receipt_date(db):
    _item("Brezel", kind="treat", lasts="same_day", days=None)
    expenses.log_expense("Brezel", 1, 1.0, purchased_at=datetime(2026, 9, 22, 12, tzinfo=timezone.utc))
    assert expenses.is_duplicate_purchase("Brezel", 1.0, purchase_date="2026-09-22") is True
    assert expenses.is_duplicate_purchase("Brezel", 1.0, purchase_date="2026-09-23") is False


@patch("db.expenses.get_connection")
def test_get_price_delta_positive(mock_conn):
    conn, _cursor = make_mock_conn(fetchone={"avg_price": 2.00, "n": 3})
    mock_conn.return_value = conn
    result = expenses.get_price_delta("Milk", 3.00)
    assert result == {"avg_price": 2.00, "pct_change": 50.0, "n": 3}


@patch("db.expenses.get_connection")
def test_get_price_delta_negative(mock_conn):
    conn, _cursor = make_mock_conn(fetchone={"avg_price": 2.00, "n": 3})
    mock_conn.return_value = conn
    result = expenses.get_price_delta("Milk", 1.80)
    assert result == pytest.approx({"avg_price": 2.00, "pct_change": -10.0, "n": 3})


@patch("db.expenses.get_connection")
def test_get_price_delta_insufficient_history(mock_conn):
    conn, _cursor = make_mock_conn(fetchone={"avg_price": 2.00, "n": 0})
    mock_conn.return_value = conn
    assert expenses.get_price_delta("Milk", 10.00, min_history=1) is None


@patch("db.expenses.get_connection")
def test_get_price_delta_no_history(mock_conn):
    conn, _cursor = make_mock_conn(fetchone={"avg_price": None, "n": 0})
    mock_conn.return_value = conn
    assert expenses.get_price_delta("Milk", 10.00) is None


@patch("db.expenses.get_connection")
def test_get_expenses_all(mock_conn):
    rows = [{"name": "Milk", "quantity_purchased": 2, "unit_price": 1.5, "total_cost": 3.0, "purchase_date": "2026-04-01"}]
    conn, _cursor = make_mock_conn(fetchall=rows)
    mock_conn.return_value = conn
    result = expenses.get_expenses()
    assert len(result) == 1
    assert result[0]["name"] == "Milk"


@patch("db.expenses.get_connection")
def test_get_expenses_by_item(mock_conn):
    rows = [{"name": "Milk", "quantity_purchased": 1, "unit_price": 1.5, "total_cost": 1.5, "purchase_date": "2026-04-01"}]
    conn, _cursor = make_mock_conn(fetchall=rows)
    mock_conn.return_value = conn
    result = expenses.get_expenses("Milk")
    assert result[0]["name"] == "Milk"


@patch("db.expenses.get_connection")
def test_get_expenses_empty(mock_conn):
    conn, _cursor = make_mock_conn(fetchall=[])
    mock_conn.return_value = conn
    assert expenses.get_expenses() == []


@patch("db.expenses.get_connection")
def test_get_total_spent_overall(mock_conn):
    conn, _cursor = make_mock_conn(fetchone=(12.50,))
    mock_conn.return_value = conn
    assert expenses.get_total_spent() == 12.50


@patch("db.expenses.get_connection")
def test_get_total_spent_by_item(mock_conn):
    conn, _cursor = make_mock_conn(fetchone=(3.00,))
    mock_conn.return_value = conn
    assert expenses.get_total_spent("Milk") == 3.00


@patch("db.expenses.get_connection")
def test_get_total_spent_zero(mock_conn):
    conn, _cursor = make_mock_conn(fetchone=(0,))
    mock_conn.return_value = conn
    assert expenses.get_total_spent() == 0.0


@patch("db.expenses.get_connection")
def test_is_duplicate_purchase_found(mock_conn):
    conn, _cursor = make_mock_conn(fetchone=(1,))
    mock_conn.return_value = conn
    assert expenses.is_duplicate_purchase("Milk", 1.50) is True


@patch("db.expenses.get_connection")
def test_is_duplicate_purchase_not_found(mock_conn):
    conn, _cursor = make_mock_conn(fetchone=None)
    mock_conn.return_value = conn
    assert expenses.is_duplicate_purchase("Milk", 1.50) is False
