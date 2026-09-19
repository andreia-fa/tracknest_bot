"""Small key/value settings store — currently just the chat id for proactive messages."""

from db.database import get_connection


def get_chat_id():
    """Return the saved Telegram chat id for proactive messages, or None if not yet set."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM bot_settings WHERE key = 'chat_id'")
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return int(row["value"]) if row else None


def set_chat_id(chat_id):
    """Save the Telegram chat id to message proactively (e.g. shelf-life check-ins)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO bot_settings (key, value) VALUES ('chat_id', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
    """, (str(chat_id),))
    conn.commit()
    cursor.close()
    conn.close()
