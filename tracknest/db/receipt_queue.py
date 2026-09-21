"""Queue of receipt photos awaiting processing by the local Ollama worker."""

from datetime import datetime, timezone

from db.database import get_connection


def queue_receipt(chat_id: int, telegram_file_id: str) -> int:
    """Add a receipt photo to the pending queue.

    Args:
        chat_id: Telegram chat to reply to once the receipt is processed.
        telegram_file_id: Telegram file id, used to re-download the photo
            later from any machine with the bot token.

    Returns:
        The new pending_receipts row id.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO pending_receipts (chat_id, telegram_file_id, queued_at) VALUES (?, ?, ?)",
        (chat_id, telegram_file_id, datetime.now(tz=timezone.utc).isoformat()),
    )
    row_id = cursor.lastrowid
    conn.commit()
    cursor.close()
    conn.close()
    return row_id


def get_pending_receipts() -> list[dict]:
    """Return all receipts still awaiting processing, oldest first."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM pending_receipts WHERE status = 'pending' ORDER BY queued_at ASC")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return [dict(row) for row in rows]


def resolve_receipt(receipt_id: int, status: str = "done") -> None:
    """Mark a queued receipt as processed.

    Args:
        receipt_id: The pending_receipts row id.
        status: 'done' on success, 'failed' if parsing errored out.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE pending_receipts SET status = ?, resolved_at = ? WHERE id = ?",
        (status, datetime.now(tz=timezone.utc).isoformat(), receipt_id),
    )
    conn.commit()
    cursor.close()
    conn.close()
