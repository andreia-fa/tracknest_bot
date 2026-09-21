from unittest.mock import MagicMock, patch

from db import receipt_queue


def make_mock_conn(fetchone=None, fetchall=None, lastrowid=1):
    cursor = MagicMock()
    cursor.fetchone.return_value = fetchone
    cursor.fetchall.return_value = fetchall or []
    cursor.lastrowid = lastrowid
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn, cursor


@patch("db.receipt_queue.get_connection")
def test_queue_receipt_inserts_and_returns_id(mock_conn):
    conn, cursor = make_mock_conn(lastrowid=42)
    mock_conn.return_value = conn
    row_id = receipt_queue.queue_receipt(chat_id=123, telegram_file_id="file-abc")
    assert row_id == 42
    conn.commit.assert_called_once()
    insert_call = cursor.execute.call_args_list[0]
    assert "INSERT INTO pending_receipts" in insert_call[0][0]
    assert insert_call[0][1][0] == 123
    assert insert_call[0][1][1] == "file-abc"


@patch("db.receipt_queue.get_connection")
def test_get_pending_receipts_returns_dicts(mock_conn):
    rows = [
        {"id": 1, "chat_id": 123, "telegram_file_id": "a", "status": "pending"},
        {"id": 2, "chat_id": 123, "telegram_file_id": "b", "status": "pending"},
    ]
    conn, cursor = make_mock_conn(fetchall=rows)
    mock_conn.return_value = conn
    result = receipt_queue.get_pending_receipts()
    assert result == rows
    query = cursor.execute.call_args[0][0]
    assert "WHERE status = 'pending'" in query


@patch("db.receipt_queue.get_connection")
def test_resolve_receipt_updates_status(mock_conn):
    conn, cursor = make_mock_conn()
    mock_conn.return_value = conn
    receipt_queue.resolve_receipt(7, status="failed")
    conn.commit.assert_called_once()
    update_call = cursor.execute.call_args_list[0]
    assert "UPDATE pending_receipts" in update_call[0][0]
    assert update_call[0][1][0] == "failed"
    assert update_call[0][1][2] == 7
