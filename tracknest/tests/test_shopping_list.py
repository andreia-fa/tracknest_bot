from unittest.mock import MagicMock, patch

from db import shopping_list


def make_mock_conn(fetchall=None):
    cursor = MagicMock()
    cursor.fetchall.return_value = fetchall or []
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn, cursor


@patch("db.shopping_list.get_connection")
def test_add_item(mock_conn):
    conn, _cursor = make_mock_conn()
    mock_conn.return_value = conn
    shopping_list.add_item("Oat Milk", 2)
    conn.commit.assert_called_once()


@patch("db.shopping_list.get_connection")
def test_get_all_items(mock_conn):
    rows = [{"id": 1, "name": "Oat Milk", "quantity": 2, "added_at": "2026-09-18"}]
    conn, _cursor = make_mock_conn(fetchall=rows)
    mock_conn.return_value = conn
    result = shopping_list.get_all_items()
    assert result == rows


@patch("db.shopping_list.get_connection")
def test_get_all_items_empty(mock_conn):
    conn, _cursor = make_mock_conn(fetchall=[])
    mock_conn.return_value = conn
    assert shopping_list.get_all_items() == []


@patch("db.shopping_list.get_connection")
def test_remove_item_found(mock_conn):
    conn, cursor = make_mock_conn()
    cursor.rowcount = 1
    mock_conn.return_value = conn
    assert shopping_list.remove_item("Oat Milk") is True
    conn.commit.assert_called_once()


@patch("db.shopping_list.get_connection")
def test_remove_item_not_found(mock_conn):
    conn, cursor = make_mock_conn()
    cursor.rowcount = 0
    mock_conn.return_value = conn
    assert shopping_list.remove_item("Ghost") is False


@patch("db.shopping_list.get_connection")
def test_remove_item_case_insensitive(mock_conn):
    conn, cursor = make_mock_conn()
    cursor.rowcount = 1
    mock_conn.return_value = conn
    assert shopping_list.remove_item("bananas") is True
    cursor.execute.assert_called_once_with(
        "DELETE FROM shopping_list_items WHERE name = ? COLLATE NOCASE", ("bananas",)
    )
