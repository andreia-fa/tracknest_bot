from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from db import crud, database, expenses, metrics


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
            [
                {"treat_or_need": "need", "total": 25.5},
                {"treat_or_need": "treat", "total": 15.0},
                {"treat_or_need": "unknown", "total": 5.0},
            ],
            [{"name": "Milk", "total": 20.0}],
            [{"category": "Dairy", "total": 20.0}],
        ],
    )
    mock_conn.return_value = conn
    result = metrics.get_spending_summary(year=2026, month=4)
    assert result["total"] == 45.5
    assert result["top_items"] == [{"name": "Milk", "total": 20.0}]
    assert result["top_categories"] == [{"category": "Dairy", "total": 20.0}]
    assert result["treat"] == 15.0
    assert result["need"] == 25.5
    assert result["unknown"] == 5.0


@patch("db.metrics.get_connection")
def test_get_spending_summary_keeps_unsorted_out_of_needs(mock_conn):
    conn, _cursor = make_mock_conn(
        fetchone_side_effect=[(10.0,)],
        fetchall_side_effect=[[{"treat_or_need": "unknown", "total": 10.0}], [], []],
    )
    mock_conn.return_value = conn
    result = metrics.get_spending_summary(year=2026, month=4)
    assert result["unknown"] == 10.0
    assert result["need"] == 0.0
    assert result["treat"] == 0.0


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


@patch("db.metrics.get_spending_summary")
def test_get_month_pace_projects_from_days_elapsed(mock_summary):
    mock_summary.return_value = {"total": 100.0}
    now = datetime.now(tz=timezone.utc)
    result = metrics.get_month_pace()
    assert result["spent"] == 100.0
    assert result["days_elapsed"] == now.day
    if now.day >= 5:
        assert result["projected"] == pytest.approx(100.0 / now.day * result["days_in_month"])
    else:
        assert result["projected"] is None


@patch("db.metrics.get_spending_summary")
def test_get_month_pace_no_projection_for_finished_month(mock_summary):
    mock_summary.return_value = {"total": 80.0}
    result = metrics.get_month_pace(year=2024, month=1)
    assert result["projected"] is None
    assert result["days_elapsed"] == 31
    assert result["days_in_month"] == 31


@patch("db.metrics.get_connection")
def test_get_running_low_within_window(mock_conn):
    yesterday = (datetime.now(tz=timezone.utc) - timedelta(days=1)).isoformat()
    long_ago = (datetime.now(tz=timezone.utc) - timedelta(days=30)).isoformat()
    conn, _cursor = make_mock_conn(fetchall_side_effect=[[
        {"name": "Bread", "shelf_life_days": 6, "last_purchase": yesterday},
        {"name": "Peanut Butter", "shelf_life_days": 60, "last_purchase": yesterday},
        {"name": "Overdue Spinach", "shelf_life_days": 10, "last_purchase": long_ago},
        {"name": "Never Bought", "shelf_life_days": 5, "last_purchase": None},
    ]])
    mock_conn.return_value = conn
    result = metrics.get_running_low(days_ahead=7)
    assert [item["name"] for item in result] == ["Bread"]
    assert result[0]["days_left"] == 4


@patch("db.metrics.get_connection")
def test_get_running_low_handles_date_only_last_purchase(mock_conn):
    """Rows predating the logged_at column carry a bare YYYY-MM-DD date."""
    date_only = (datetime.now(tz=timezone.utc) - timedelta(days=2)).date().isoformat()
    conn, _cursor = make_mock_conn(fetchall_side_effect=[[
        {"name": "Bread", "shelf_life_days": 6, "last_purchase": date_only},
    ]])
    mock_conn.return_value = conn
    result = metrics.get_running_low()
    assert result[0]["name"] == "Bread"


@patch("db.metrics.get_connection")
def test_get_daily_cost_ranks_by_cost_per_day(mock_conn):
    conn, _cursor = make_mock_conn(fetchall_side_effect=[[
        {"name": "Peanut Butter", "shelf_life_days": 60, "treat_or_need": "need",
         "unit_price": 6.99, "purchase_count": 3},
        {"name": "Sushi", "shelf_life_days": 2, "treat_or_need": "treat",
         "unit_price": 10.99, "purchase_count": 2},
        {"name": "Never Bought", "shelf_life_days": 5, "treat_or_need": "need",
         "unit_price": None, "purchase_count": 0},
    ]])
    mock_conn.return_value = conn
    result = metrics.get_daily_cost()
    assert [item["name"] for item in result["items"]] == ["Sushi", "Peanut Butter"]
    assert result["items"][0]["cost_per_day"] == pytest.approx(5.495)
    assert result["ready"] == 2
    assert result["tracked"] == 3


@patch("db.metrics.get_connection")
def test_get_daily_cost_withholds_untested_shelf_life(mock_conn):
    """A single purchase means the shelf life is still just the user's guess."""
    conn, _cursor = make_mock_conn(fetchall_side_effect=[[
        {"name": "Sushi", "shelf_life_days": 2, "treat_or_need": "treat",
         "unit_price": 10.99, "purchase_count": 1},
    ]])
    mock_conn.return_value = conn
    result = metrics.get_daily_cost()
    assert result["items"] == []
    assert result["ready"] == 0
    assert result["tracked"] == 1


@patch("db.settings.get_financial_goal")
def test_get_goal_status_no_goal(mock_goal):
    mock_goal.return_value = None
    assert metrics.get_goal_status() is None


@patch("db.settings.get_financial_goal")
def test_get_goal_status_future_target(mock_goal):
    future_date = (datetime.now(tz=timezone.utc) + timedelta(days=304)).strftime("%Y-%m-%d")
    mock_goal.return_value = {"name": "Japan trip", "amount": 3044.0, "target_date": future_date}
    result = metrics.get_goal_status()
    assert result["name"] == "Japan trip"
    assert result["days_left"] in (303, 304)
    assert result["pace_per_month"] == pytest.approx(304.0, rel=0.05)


@patch("db.settings.get_financial_goal")
def test_get_goal_status_past_target(mock_goal):
    past_date = (datetime.now(tz=timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d")
    mock_goal.return_value = {"name": "Old goal", "amount": 100.0, "target_date": past_date}
    result = metrics.get_goal_status()
    assert result["pace_per_month"] is None


@pytest.fixture
def db(tmp_path):
    with patch.object(database, "DB_PATH", str(tmp_path / "test.db")):
        database.init_db()
        yield


def _bought(name, prices, store, start_days_ago=30):
    if crud.get_item(name) is None:
        crud.add_item(name, 1)
    for i, price in enumerate(prices):
        when = datetime.now(tz=timezone.utc) - timedelta(days=start_days_ago - i)
        expenses.log_expense(name, 1, price, store=store, purchased_at=when)


def test_get_price_trends(db):
    _bought("Milk", [2.00, 2.20, 3.00], "REWE")
    _bought("Rice", [1.00, 0.90], "REWE")
    result = metrics.get_price_trends()
    assert [(r["name"], r["store"]) for r in result] == [("Milk", "REWE")]
    assert result[0]["pct_change"] == pytest.approx(42.857142857142854)
    assert result[0]["latest_price"] == 3.00
    assert result[0]["avg_price"] == pytest.approx(2.10)


def test_get_price_trends_insufficient_history(db):
    _bought("Milk", [2.00], "REWE")
    assert metrics.get_price_trends() == []


def test_another_stores_price_is_not_a_price_rise(db):
    """€1.00 pretzels at Yormas and €1.80 at a bakery are two prices, not +80%."""
    _bought("Pfefferbretzel", [1.00], "Yormas")
    _bought("Pfefferbretzel", [1.80], "Bäckerei", start_days_ago=10)
    _bought("Pfefferbretzel", [1.80], None, start_days_ago=5)  # typed, store unknown
    assert metrics.get_price_trends() == []


def test_rounding_noise_is_not_a_price_rise(db):
    _bought("Banane", [1.99, 1.9900000000000002], "REWE")
    assert metrics.get_price_trends() == []


def test_months_with_spending_include_the_current_one(db):
    _bought("Milk", [1.0], "REWE", start_days_ago=40)
    now = datetime.now(tz=timezone.utc)
    months = metrics.get_months_with_spending()
    assert months[-1] == (now.year, now.month)
    assert len(months) == len(set(months)) >= 2


@patch("db.metrics.get_connection")
def test_get_inventory_health(mock_conn):
    conn, _cursor = make_mock_conn(fetchall_side_effect=[
        [{"name": "Milk"}],
        [{"name": "Toilet Paper"}],
        [{"name": "dmBio schoko. Himbeeren 150g*"}],
    ])
    mock_conn.return_value = conn
    result = metrics.get_inventory_health()
    assert result == {
        "checkin_pending": ["Milk"],
        "spare_alert_pending": ["Toilet Paper"],
        "unprofiled": ["dmBio schoko. Himbeeren 150g*"],
    }


@patch("db.metrics.get_connection")
def test_get_inventory_health_all_clear(mock_conn):
    conn, _cursor = make_mock_conn(fetchall_side_effect=[[], [], []])
    mock_conn.return_value = conn
    result = metrics.get_inventory_health()
    assert result == {"checkin_pending": [], "spare_alert_pending": [], "unprofiled": []}


@patch("db.metrics.get_connection")
def test_get_shopping_trips_groups_by_day_and_store(mock_conn):
    conn, _cursor = make_mock_conn(fetchall_side_effect=[[
        {"day": "2026-09-20", "store": "REWE", "total": 12.0},
        {"day": "2026-09-22", "store": "REWE", "total": 8.0},
        {"day": "2026-09-22", "store": "dm", "total": 4.0},
    ]])
    mock_conn.return_value = conn

    trips = metrics.get_shopping_trips(2026, 9)

    assert trips["count"] == 3
    assert trips["avg_basket"] == 8.0
    assert trips["by_store"][0] == {"store": "REWE", "trips": 2, "total": 20.0}


@patch("db.metrics.get_connection")
def test_get_shopping_trips_empty_month(mock_conn):
    conn, _cursor = make_mock_conn(fetchall_side_effect=[[]])
    mock_conn.return_value = conn

    assert metrics.get_shopping_trips(2026, 9) == {"count": 0, "avg_basket": None, "by_store": []}


@patch("db.metrics.get_connection")
def test_get_daily_spend_zero_fills_the_month(mock_conn):
    conn, _cursor = make_mock_conn(fetchall_side_effect=[[
        {"day": "2026-09-03", "total": 5.5},
    ]])
    mock_conn.return_value = conn

    days = metrics.get_daily_spend(2026, 9)

    assert len(days) == 30
    assert days[2] == 5.5
    assert sum(days) == 5.5


def test_an_unanswered_what_is_it_question_needs_attention(db):
    crud.add_item("Koreanische Alge", 1)
    crud.set_treat_or_need("Koreanische Alge", "need")
    crud.set_lasts("Koreanische Alge", "days", 14)
    crud.mark_name_pending("Koreanische Alge")  # guessed, but "what is it?" still open
    assert metrics.get_inventory_health()["unprofiled"] == ["Koreanische Alge"]
