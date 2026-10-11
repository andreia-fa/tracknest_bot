"""Queue of receipt photos awaiting processing by the local Ollama worker, then the user's review."""

import json
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
        status: 'done' once saved, 'failed' if parsing errored out,
            'discarded' if the user threw the reading away.
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


def hold_for_review(receipt_id: int, parsed: dict) -> None:
    """Park a parsed receipt until the user confirms it — nothing is logged yet."""
    conn = get_connection()
    conn.execute("UPDATE pending_receipts SET status = 'review', parsed = ?, review_at = ? WHERE id = ?",
                 (json.dumps(parsed), datetime.now(tz=timezone.utc).isoformat(), receipt_id))
    conn.commit()
    conn.close()


def get_review(receipt_id: int) -> dict | None:
    """Return a receipt's parsed reading while it awaits review, or None once it's saved or discarded."""
    conn = get_connection()
    row = conn.execute("SELECT parsed FROM pending_receipts WHERE id = ? AND status = 'review'",
                       (receipt_id,)).fetchone()
    conn.close()
    return json.loads(row["parsed"]) if row and row["parsed"] else None


def update_review(receipt_id: int, parsed: dict) -> bool:
    """Store the user's corrections to a reading under review; False if it's no longer under review."""
    conn = get_connection()
    cursor = conn.execute("UPDATE pending_receipts SET parsed = ? WHERE id = ? AND status = 'review'",
                          (json.dumps(parsed), receipt_id))
    conn.commit()
    changed = cursor.rowcount == 1
    conn.close()
    return changed


def get_review_to_nudge(min_age_hours: int) -> dict | None:
    """Return the oldest reading that has waited for review at least this long and wasn't nudged yet.

    Returns:
        Dict with id and parsed (the reading), or None.
    """
    conn = get_connection()
    row = conn.execute("""
        SELECT id, parsed FROM pending_receipts
        WHERE status = 'review' AND nudged_at IS NULL
          AND datetime(COALESCE(review_at, queued_at)) <= datetime('now', ?)
        ORDER BY id LIMIT 1
    """, (f"-{min_age_hours} hours",)).fetchone()
    conn.close()
    return {"id": row["id"], "parsed": json.loads(row["parsed"])} if row else None


def mark_nudged(receipt_id: int) -> None:
    """Record that the user was reminded about this reading, so it's only nudged once."""
    conn = get_connection()
    conn.execute("UPDATE pending_receipts SET nudged_at = ? WHERE id = ?",
                 (datetime.now(tz=timezone.utc).isoformat(), receipt_id))
    conn.commit()
    conn.close()
