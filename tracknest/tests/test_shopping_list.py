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
def test_remove_item_found_records_history(mock_conn):
    conn, cursor = make_mock_conn()
    cursor.fetchone.return_value = {"name": "Oat Milk", "quantity": 1, "category": "Dairy", "added_at": "x"}
    cursor.lastrowid = 7
    mock_conn.return_value = conn
    assert shopping_list.remove_item("Oat Milk", reason="receipt", source="HAFERDRINK") == 7
    sql = [call[0][0] for call in cursor.execute.call_args_list]
    assert any("INSERT INTO shopping_list_history" in q for q in sql)
    assert any("DELETE FROM shopping_list_items" in q for q in sql)
    conn.commit.assert_called_once()


@patch("db.shopping_list.get_connection")
def test_remove_item_not_found(mock_conn):
    conn, cursor = make_mock_conn()
    cursor.fetchone.return_value = None
    mock_conn.return_value = conn
    assert shopping_list.remove_item("Ghost") is None


@patch("db.shopping_list.get_connection")
def test_remove_item_case_insensitive(mock_conn):
    conn, cursor = make_mock_conn()
    cursor.fetchone.return_value = {"name": "Bananas", "quantity": 1, "category": None, "added_at": "x"}
    mock_conn.return_value = conn
    assert shopping_list.remove_item("bananas")
    cursor.execute.assert_any_call(
        "DELETE FROM shopping_list_items WHERE name = ? COLLATE NOCASE", ("bananas",)
    )


@patch("db.shopping_list.add_item")
@patch("db.shopping_list.get_connection")
def test_restore_item_puts_it_back_once(mock_conn, mock_add):
    conn, cursor = make_mock_conn()
    cursor.fetchone.return_value = {"name": "Tuna", "quantity": 1, "category": "Meat/Fish"}
    mock_conn.return_value = conn
    assert shopping_list.restore_item(3) == "Tuna"
    mock_add.assert_called_once_with("Tuna", 1, category="Meat/Fish")

    cursor.fetchone.return_value = None
    assert shopping_list.restore_item(3) is None
