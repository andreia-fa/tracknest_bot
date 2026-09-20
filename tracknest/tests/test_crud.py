from unittest.mock import MagicMock, patch

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
    assert args[3] == "Dairy"
    assert args[4] == 1


@patch("db.crud.get_connection")
def test_get_item_found(mock_conn):
    conn, _cursor = make_mock_conn(fetchone={"id": 1, "name": "Milk", "quantity": 3})
    mock_conn.return_value = conn
    result = crud.get_item("Milk")
    assert result["name"] == "Milk"
    assert result["quantity"] == 3


@patch("db.crud.get_connection")
def test_get_item_not_found(mock_conn):
    conn, _cursor = make_mock_conn(fetchone=None)
    mock_conn.return_value = conn
    assert crud.get_item("Ghost") is None


@patch("db.crud.get_connection")
def test_get_all_items(mock_conn):
    items = [
        {"id": 1, "name": "Milk", "quantity": 3},
        {"id": 2, "name": "Rice", "quantity": 1},
    ]
    conn, _cursor = make_mock_conn(fetchall=items)
    mock_conn.return_value = conn
    result = crud.get_all_items()
    assert len(result) == 2


@patch("db.crud.get_connection")
def test_get_all_items_empty(mock_conn):
    conn, _cursor = make_mock_conn(fetchall=[])
    mock_conn.return_value = conn
    assert crud.get_all_items() == []


@patch("db.crud.get_connection")
def test_update_item_quantity_found(mock_conn):
    conn, _cursor = make_mock_conn(rowcount=1)
    mock_conn.return_value = conn
    assert crud.update_item_quantity("Milk", 5) is True


@patch("db.crud.get_connection")
def test_update_item_quantity_not_found(mock_conn):
    conn, _cursor = make_mock_conn(rowcount=0)
    mock_conn.return_value = conn
    assert crud.update_item_quantity("Ghost", 5) is False


@patch("db.crud.get_connection")
def test_set_profile_found(mock_conn):
    conn, _cursor = make_mock_conn(rowcount=1)
    mock_conn.return_value = conn
    assert crud.set_profile("Milk", shelf_life_days=7, is_luxury=0) is True
    conn.commit.assert_called_once()


@patch("db.crud.get_connection")
def test_set_profile_not_found(mock_conn):
    conn, _cursor = make_mock_conn(rowcount=0)
    mock_conn.return_value = conn
    assert crud.set_profile("Ghost", shelf_life_days=7) is False


@patch("db.crud.get_connection")
def test_set_profile_partial_update(mock_conn):
    conn, cursor = make_mock_conn(rowcount=1)
    mock_conn.return_value = conn
    crud.set_profile("Milk", shelf_life_days=7)
    args = cursor.execute.call_args[0][1]
    assert args[0] == 7
    assert args[1] is None


@patch("db.crud.get_connection")
def test_get_checkin_candidates(mock_conn):
    rows = [{"name": "Milk", "shelf_life_days": 7, "last_purchase": "2026-04-01T00:00:00+00:00"}]
    conn, _cursor = make_mock_conn(fetchall=rows)
    mock_conn.return_value = conn
    result = crud.get_checkin_candidates()
    assert result == rows


@patch("db.crud.get_connection")
def test_get_pending_checkin_item_found(mock_conn):
    conn, _cursor = make_mock_conn(fetchone={"name": "Milk"})
    mock_conn.return_value = conn
    assert crud.get_pending_checkin_item() == "Milk"


@patch("db.crud.get_connection")
def test_get_pending_checkin_item_none(mock_conn):
    conn, _cursor = make_mock_conn(fetchone=None)
    mock_conn.return_value = conn
    assert crud.get_pending_checkin_item() is None


@patch("db.crud.get_connection")
def test_mark_checkin_pending(mock_conn):
    conn, cursor = make_mock_conn(rowcount=1)
    mock_conn.return_value = conn
    assert crud.mark_checkin_pending("Milk", pending=True) is True
    assert cursor.execute.call_args[0][1] == (1, "Milk")


@patch("db.crud.get_connection")
def test_bump_shelf_life(mock_conn):
    conn, _cursor = make_mock_conn(rowcount=1)
    mock_conn.return_value = conn
    assert crud.bump_shelf_life("Milk", 3) is True


@patch("db.crud.get_connection")
def test_set_par_level_found(mock_conn):
    conn, cursor = make_mock_conn(rowcount=1)
    mock_conn.return_value = conn
    assert crud.set_par_level("Toilet Paper", 2) is True
    assert cursor.execute.call_args[0][1] == (2, "Toilet Paper")


@patch("db.crud.get_connection")
def test_set_par_level_not_found(mock_conn):
    conn, _cursor = make_mock_conn(rowcount=0)
    mock_conn.return_value = conn
    assert crud.set_par_level("Ghost", 2) is False


@patch("db.crud.get_connection")
def test_get_par_alert_candidates(mock_conn):
    rows = [{"name": "Toilet Paper", "shelf_life_days": 20, "last_purchase": "2026-04-01T00:00:00+00:00"}]
    conn, cursor = make_mock_conn(fetchall=rows)
    mock_conn.return_value = conn
    result = crud.get_par_alert_candidates(default_par_level=1)
    assert result == rows
    assert cursor.execute.call_args[0][1] == (1,)


@patch("db.crud.get_connection")
def test_mark_spare_alert_pending(mock_conn):
    conn, cursor = make_mock_conn(rowcount=1)
    mock_conn.return_value = conn
    assert crud.mark_spare_alert_pending("Toilet Paper", pending=True) is True
    assert cursor.execute.call_args[0][1] == (1, "Toilet Paper")


@patch("db.crud.get_connection")
def test_delete_item_found(mock_conn):
    conn, _cursor = make_mock_conn(rowcount=1)
    mock_conn.return_value = conn
    assert crud.delete_item("Milk") is True


@patch("db.crud.get_connection")
def test_delete_item_not_found(mock_conn):
    conn, _cursor = make_mock_conn(rowcount=0)
    mock_conn.return_value = conn
    assert crud.delete_item("Ghost") is False
