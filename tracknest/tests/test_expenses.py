from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from db import expenses


def make_mock_conn(fetchone=None, fetchall=None):
    cursor = MagicMock()
    cursor.fetchone.return_value = fetchone
    cursor.fetchall.return_value = fetchall or []
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn, cursor


@patch("db.expenses.get_connection")
def test_log_expense_item_exists(mock_conn):
    conn, _cursor = make_mock_conn(fetchone={"id": 1, "shelf_life_days": None, "is_luxury": 0})
    mock_conn.return_value = conn
    assert expenses.log_expense("Milk", 2, 1.50) is True
    conn.commit.assert_called_once()



@patch("db.expenses.get_connection")
def test_log_expense_item_not_found(mock_conn):
    conn, _cursor = make_mock_conn(fetchone=None)
    mock_conn.return_value = conn
    assert expenses.log_expense("Ghost", 1, 5.00) is False
    conn.commit.assert_not_called()


@patch("db.expenses.get_connection")
def test_log_expense_shortens_shelf_life_on_early_repurchase(mock_conn):
    conn, cursor = make_mock_conn()
    mock_conn.return_value = conn
    three_days_ago = (datetime.now(tz=timezone.utc) - timedelta(days=3)).isoformat()
    cursor.fetchone.side_effect = [
        {"id": 1, "shelf_life_days": 10, "is_luxury": 0},
        {"logged_at": three_days_ago},
    ]
    expenses.log_expense("Spinach", 1, 1.11)
    update_calls = [c for c in cursor.execute.call_args_list if "SET shelf_life_days = ?" in c[0][0]]
    assert len(update_calls) == 1
    assert update_calls[0][0][1][0] == 3


@patch("db.expenses.get_connection")
def test_log_expense_skips_adjustment_for_luxury(mock_conn):
    conn, cursor = make_mock_conn()
    mock_conn.return_value = conn
    cursor.fetchone.side_effect = [{"id": 1, "shelf_life_days": 2, "is_luxury": 1}]
    expenses.log_expense("Sushi", 1, 10.99)
    update_calls = [c for c in cursor.execute.call_args_list if "SET shelf_life_days = ?" in c[0][0]]
    assert len(update_calls) == 0


@patch("db.expenses.get_connection")
def test_log_expense_marks_correction(mock_conn):
    conn, cursor = make_mock_conn()
    mock_conn.return_value = conn
    three_days_ago = (datetime.now(tz=timezone.utc) - timedelta(days=3)).isoformat()
    cursor.fetchone.side_effect = [
        {"id": 1, "shelf_life_days": 10, "is_luxury": 0},
        {"logged_at": three_days_ago},
    ]
    expenses.log_expense("Spinach", 1, 1.11)
    update_calls = [c for c in cursor.execute.call_args_list if "shelf_life_corrected = 1" in c[0][0]]
    assert len(update_calls) == 1


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
