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
def test_get_item_by_id(mock_conn):
    conn, cursor = make_mock_conn(fetchone={"id": 7, "name": "Berida Garnele"})
    mock_conn.return_value = conn
    assert crud.get_item_by_id(7)["name"] == "Berida Garnele"
    assert cursor.execute.call_args[0][1] == (7,)


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
    assert crud.set_profile("Milk", shelf_life_days=7, purchase_type="essential") is True
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
    assert "shelf_life_days > 1" in cursor.execute.call_args[0][0]  # same-day items never alert


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


@patch("db.crud.get_connection")
def test_get_pending_profile_item_purchase_type_stage_first(mock_conn):
    conn, cursor = make_mock_conn()
    mock_conn.return_value = conn
    cursor.fetchone.side_effect = [None, {"name": "Sushi"}]
    assert crud.get_pending_profile_item() == ("Sushi", "purchase_type")
    assert cursor.execute.call_count == 2


@patch("db.crud.get_connection")
def test_get_pending_profile_item_falls_back_to_shelf_life_stage(mock_conn):
    conn, cursor = make_mock_conn()
    mock_conn.return_value = conn
    cursor.fetchone.side_effect = [None, None, {"name": "Milk"}]
    assert crud.get_pending_profile_item() == ("Milk", "shelf_life")
    assert cursor.execute.call_count == 3


@patch("db.crud.get_connection")
def test_get_pending_profile_item_none_when_nothing_pending(mock_conn):
    conn, cursor = make_mock_conn()
    mock_conn.return_value = conn
    cursor.fetchone.side_effect = [None, None, None]
    assert crud.get_pending_profile_item() is None


@patch("db.crud.get_connection")
def test_pending_profile_asks_naming_first(mock_conn):
    conn, cursor = make_mock_conn(fetchone={"name": "BIO aln.pfanne", "name_status": "name"})
    mock_conn.return_value = conn
    assert crud.get_pending_profile_item() == ("BIO aln.pfanne", "name")


@patch("db.crud.get_connection")
def test_get_alias_not_found(mock_conn):
    conn, _cursor = make_mock_conn(fetchone=None)
    mock_conn.return_value = conn
    assert crud.get_alias("BIO aln.pfanne") is None


@patch("db.crud.get_connection")
def test_rename_item_merges_into_existing(mock_conn):
    conn, cursor = make_mock_conn()
    cursor.fetchone.side_effect = [{"id": 2, "quantity": 1}, {"id": 1, "name": "Frozen mixed veg"}]
    mock_conn.return_value = conn
    assert crud.rename_item("BIO aln.pfanne", "frozen mixed veg") == ("Frozen mixed veg", True)
    sql = " ".join(call[0][0] for call in cursor.execute.call_args_list)
    assert "UPDATE item_expenses SET item_id" in sql and "DELETE FROM inventory_items" in sql


@patch("db.crud.get_connection")
def test_rename_item_plain_rename_asks_category_next(mock_conn):
    conn, cursor = make_mock_conn()
    cursor.fetchone.side_effect = [{"id": 2, "quantity": 1}, None]
    mock_conn.return_value = conn
    assert crud.rename_item("BIO aln.pfanne", "Frozen mixed veg") == ("Frozen mixed veg", False)
    assert "name_status = 'category'" in cursor.execute.call_args_list[-1][0][0]
