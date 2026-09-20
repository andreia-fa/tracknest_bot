from unittest.mock import MagicMock, patch

import pytest

from db import metrics


def make_mock_conn(fetchone_side_effect=None, fetchall_side_effect=None):
    cursor = MagicMock()
    if fetchone_side_effect is not None:
        cursor.fetchone.side_effect = fetchone_side_effect
    if fetchall_side_effect is not None:
        cursor.fetchall.side_effect = fetchall_side_effect
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn, cursor


@patch("db.metrics.get_connection")
def test_get_spending_summary(mock_conn):
    conn, _cursor = make_mock_conn(
        fetchone_side_effect=[(45.5,)],
        fetchall_side_effect=[
            [{"name": "Milk", "total": 20.0}],
            [{"category": "Dairy", "total": 20.0}],
        ],
    )
    mock_conn.return_value = conn
    result = metrics.get_spending_summary(year=2026, month=4)
    assert result["total"] == 45.5
    assert result["top_items"] == [{"name": "Milk", "total": 20.0}]
    assert result["top_categories"] == [{"category": "Dairy", "total": 20.0}]


@patch("db.metrics.get_spending_summary")
@patch("db.settings.get_monthly_budget")
def test_get_budget_status_no_budget(mock_budget, mock_summary):
    mock_budget.return_value = None
    assert metrics.get_budget_status() is None
    mock_summary.assert_not_called()


@patch("db.metrics.get_spending_summary")
@patch("db.settings.get_monthly_budget")
def test_get_budget_status_with_budget(mock_budget, mock_summary):
    mock_budget.return_value = 200.0
    mock_summary.return_value = {"total": 150.0, "top_items": [], "top_categories": []}
    result = metrics.get_budget_status()
    assert result == {"budget": 200.0, "spent": 150.0, "pct": 75.0}


@patch("db.metrics.get_connection")
def test_get_consumption_accuracy(mock_conn):
    conn, _cursor = make_mock_conn(fetchone_side_effect=[{"tracked": 5, "corrected": 2}])
    mock_conn.return_value = conn
    assert metrics.get_consumption_accuracy() == {"tracked": 5, "corrected": 2}


@patch("db.metrics.get_connection")
def test_get_consumption_accuracy_empty(mock_conn):
    conn, _cursor = make_mock_conn(fetchone_side_effect=[{"tracked": 0, "corrected": None}])
    mock_conn.return_value = conn
    assert metrics.get_consumption_accuracy() == {"tracked": 0, "corrected": 0}


@patch("db.metrics.get_connection")
def test_get_price_trends(mock_conn):
    rows = [
        {"name": "Milk", "unit_price": 2.00},
        {"name": "Milk", "unit_price": 2.20},
        {"name": "Milk", "unit_price": 3.00},
        {"name": "Rice", "unit_price": 1.00},
        {"name": "Rice", "unit_price": 0.90},
    ]
    conn, _cursor = make_mock_conn(fetchall_side_effect=[rows])
    mock_conn.return_value = conn
    result = metrics.get_price_trends()
    assert len(result) == 1
    assert result[0]["name"] == "Milk"
    assert result[0]["pct_change"] == pytest.approx(42.857142857142854)
    assert result[0]["latest_price"] == 3.00
    assert result[0]["avg_price"] == pytest.approx(2.10)


@patch("db.metrics.get_connection")
def test_get_price_trends_insufficient_history(mock_conn):
    rows = [{"name": "Milk", "unit_price": 2.00}]
    conn, _cursor = make_mock_conn(fetchall_side_effect=[rows])
    mock_conn.return_value = conn
    assert metrics.get_price_trends() == []


@patch("db.metrics.get_connection")
def test_get_inventory_health(mock_conn):
    conn, _cursor = make_mock_conn(fetchone_side_effect=[(1,), (2,), (3,)])
    mock_conn.return_value = conn
    result = metrics.get_inventory_health()
    assert result == {"checkin_pending": 1, "spare_alert_pending": 2, "unprofiled": 3}
