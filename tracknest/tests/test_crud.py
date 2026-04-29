from unittest.mock import patch, MagicMock
from db import crud


def make_mock_conn(fetchone=None, fetchall=None, rowcount=1):
    cursor = MagicMock()
    cursor.fetchone.return_value = fetchone
    cursor.fetchall.return_value = fetchall or []
    cursor.rowcount = rowcount
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn, cursor


@patch("db.crud.get_connection")
def test_add_item(mock_conn):
    conn, cursor = make_mock_conn()
    mock_conn.return_value = conn
    crud.add_item("Milk", 3)
    cursor.execute.assert_called_once()
    conn.commit.assert_called_once()


@patch("db.crud.get_connection")
def test_add_item_with_category(mock_conn):
    conn, cursor = make_mock_conn()
    mock_conn.return_value = conn
    crud.add_item("Milk", 3, category="Dairy", alert_threshold=1)
    args = cursor.execute.call_args[0][1]
    assert args[0] == "Milk"
    assert args[2] == "Dairy"
    assert args[3] == 1


@patch("db.crud.get_connection")
def test_get_item_found(mock_conn):
    conn, cursor = make_mock_conn(fetchone={"id": 1, "name": "Milk", "quantity": 3})
    mock_conn.return_value = conn
    result = crud.get_item("Milk")
    assert result["name"] == "Milk"
    assert result["quantity"] == 3


@patch("db.crud.get_connection")
def test_get_item_not_found(mock_conn):
    conn, cursor = make_mock_conn(fetchone=None)
    mock_conn.return_value = conn
    assert crud.get_item("Ghost") is None


@patch("db.crud.get_connection")
def test_get_all_items(mock_conn):
    items = [
        {"id": 1, "name": "Milk", "quantity": 3},
        {"id": 2, "name": "Rice", "quantity": 1},
    ]
    conn, cursor = make_mock_conn(fetchall=items)
    mock_conn.return_value = conn
    result = crud.get_all_items()
    assert len(result) == 2


@patch("db.crud.get_connection")
def test_get_all_items_empty(mock_conn):
    conn, cursor = make_mock_conn(fetchall=[])
    mock_conn.return_value = conn
    assert crud.get_all_items() == []


@patch("db.crud.get_connection")
def test_update_item_quantity_found(mock_conn):
    conn, cursor = make_mock_conn(rowcount=1)
    mock_conn.return_value = conn
    assert crud.update_item_quantity("Milk", 5) is True


@patch("db.crud.get_connection")
def test_update_item_quantity_not_found(mock_conn):
    conn, cursor = make_mock_conn(rowcount=0)
    mock_conn.return_value = conn
    assert crud.update_item_quantity("Ghost", 5) is False


@patch("db.crud.get_connection")
def test_delete_item_found(mock_conn):
    conn, cursor = make_mock_conn(rowcount=1)
    mock_conn.return_value = conn
    assert crud.delete_item("Milk") is True


@patch("db.crud.get_connection")
def test_delete_item_not_found(mock_conn):
    conn, cursor = make_mock_conn(rowcount=0)
    mock_conn.return_value = conn
    assert crud.delete_item("Ghost") is False
