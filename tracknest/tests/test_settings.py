from unittest.mock import MagicMock, patch

from db import settings


def make_mock_conn(fetchone=None):
    cursor = MagicMock()
    cursor.fetchone.return_value = fetchone
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn, cursor


@patch("db.settings.get_connection")
def test_get_chat_id_found(mock_conn):
    conn, _cursor = make_mock_conn(fetchone={"value": "12345"})
    mock_conn.return_value = conn
    assert settings.get_chat_id() == 12345


@patch("db.settings.get_connection")
def test_get_chat_id_not_set(mock_conn):
    conn, _cursor = make_mock_conn(fetchone=None)
    mock_conn.return_value = conn
    assert settings.get_chat_id() is None


@patch("db.settings.get_connection")
def test_set_chat_id(mock_conn):
    conn, _cursor = make_mock_conn()
    mock_conn.return_value = conn
    settings.set_chat_id(6789)
    conn.commit.assert_called_once()


@patch("db.settings.get_connection")
def test_get_default_par_level_unset(mock_conn):
    conn, _cursor = make_mock_conn(fetchone=None)
    mock_conn.return_value = conn
    assert settings.get_default_par_level() == 1


@patch("db.settings.get_connection")
def test_get_default_par_level_set(mock_conn):
    conn, _cursor = make_mock_conn(fetchone={"value": "2"})
    mock_conn.return_value = conn
    assert settings.get_default_par_level() == 2


@patch("db.settings.get_connection")
def test_get_monthly_budget_unset(mock_conn):
    conn, _cursor = make_mock_conn(fetchone=None)
    mock_conn.return_value = conn
    assert settings.get_monthly_budget() is None


@patch("db.settings.get_connection")
def test_get_monthly_budget_set(mock_conn):
    conn, _cursor = make_mock_conn(fetchone={"value": "300.0"})
    mock_conn.return_value = conn
    assert settings.get_monthly_budget() == 300.0


@patch("db.settings.get_connection")
def test_get_budget_alert_state_unset(mock_conn):
    conn, cursor = make_mock_conn()
    mock_conn.return_value = conn
    cursor.fetchone.side_effect = [None, None]
    assert settings.get_budget_alert_state() == (None, None)


@patch("db.settings.get_connection")
def test_get_budget_alert_state_set(mock_conn):
    conn, cursor = make_mock_conn()
    mock_conn.return_value = conn
    cursor.fetchone.side_effect = [{"value": "2026-04"}, {"value": "80"}]
    assert settings.get_budget_alert_state() == ("2026-04", 80)


@patch("db.settings.get_connection")
def test_get_financial_goal_unset(mock_conn):
    conn, cursor = make_mock_conn()
    mock_conn.return_value = conn
    cursor.fetchone.side_effect = [None, None, None]
    assert settings.get_financial_goal() is None


@patch("db.settings.get_connection")
def test_get_financial_goal_set(mock_conn):
    conn, cursor = make_mock_conn()
    mock_conn.return_value = conn
    cursor.fetchone.side_effect = [
        {"value": "Japan trip"}, {"value": "2000.0"}, {"value": "2027-03-01"},
    ]
    assert settings.get_financial_goal() == {
        "name": "Japan trip", "amount": 2000.0, "target_date": "2027-03-01",
    }
