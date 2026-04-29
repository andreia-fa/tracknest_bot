from unittest.mock import patch, MagicMock
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
    conn, cursor = make_mock_conn(fetchone={"id": 1})
    mock_conn.return_value = conn
    assert expenses.log_expense("Milk", 2, 1.50) is True
    conn.commit.assert_called_once()


@patch("db.expenses.get_connection")
def test_log_expense_calculates_total(mock_conn):
    conn, cursor = make_mock_conn(fetchone={"id": 1})
    mock_conn.return_value = conn
    expenses.log_expense("Milk", 3, 2.00)
    insert_args = cursor.execute.call_args_list[1][0][1]
    assert insert_args[3] == 6.00  # total_cost = 3 * 2.00


@patch("db.expenses.get_connection")
def test_log_expense_item_not_found(mock_conn):
    conn, cursor = make_mock_conn(fetchone=None)
    mock_conn.return_value = conn
    assert expenses.log_expense("Ghost", 1, 5.00) is False
    conn.commit.assert_not_called()


@patch("db.expenses.get_connection")
def test_get_expenses_all(mock_conn):
    rows = [{"name": "Milk", "quantity_purchased": 2, "unit_price": 1.5, "total_cost": 3.0, "purchase_date": "2026-04-01"}]
    conn, cursor = make_mock_conn(fetchall=rows)
    mock_conn.return_value = conn
    result = expenses.get_expenses()
    assert len(result) == 1
    assert result[0]["name"] == "Milk"


@patch("db.expenses.get_connection")
def test_get_expenses_by_item(mock_conn):
    rows = [{"name": "Milk", "quantity_purchased": 1, "unit_price": 1.5, "total_cost": 1.5, "purchase_date": "2026-04-01"}]
    conn, cursor = make_mock_conn(fetchall=rows)
    mock_conn.return_value = conn
    result = expenses.get_expenses("Milk")
    assert result[0]["name"] == "Milk"


@patch("db.expenses.get_connection")
def test_get_expenses_empty(mock_conn):
    conn, cursor = make_mock_conn(fetchall=[])
    mock_conn.return_value = conn
    assert expenses.get_expenses() == []


@patch("db.expenses.get_connection")
def test_get_total_spent_overall(mock_conn):
    conn, cursor = make_mock_conn(fetchone=(12.50,))
    mock_conn.return_value = conn
    assert expenses.get_total_spent() == 12.50


@patch("db.expenses.get_connection")
def test_get_total_spent_by_item(mock_conn):
    conn, cursor = make_mock_conn(fetchone=(3.00,))
    mock_conn.return_value = conn
    assert expenses.get_total_spent("Milk") == 3.00


@patch("db.expenses.get_connection")
def test_get_total_spent_zero(mock_conn):
    conn, cursor = make_mock_conn(fetchone=(0,))
    mock_conn.return_value = conn
    assert expenses.get_total_spent() == 0.0
