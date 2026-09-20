"""Small key/value settings store for chat id, household par-level default, and budget."""

from db.database import get_connection

_DEFAULT_PAR_LEVEL = 1


def _get_setting(key):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM bot_settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row["value"] if row else None


def _set_setting(key, value):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO bot_settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
    """, (key, str(value)))
    conn.commit()
    cursor.close()
    conn.close()


def get_chat_id():
    """Return the saved Telegram chat id for proactive messages, or None if not yet set."""
    value = _get_setting("chat_id")
    return int(value) if value else None


def set_chat_id(chat_id):
    """Save the Telegram chat id to message proactively (e.g. shelf-life check-ins)."""
    _set_setting("chat_id", chat_id)


def get_default_par_level():
    """Return the household's default par level (1 = replace when low, 2 = keep a spare).

    Falls back to 1 if the household has never set one.
    """
    value = _get_setting("default_par_level")
    return int(value) if value else _DEFAULT_PAR_LEVEL


def set_default_par_level(level):
    """Save the household-wide default par level (1 or 2)."""
    _set_setting("default_par_level", level)


def get_monthly_budget():
    """Return the household's monthly spending budget, or None if not set."""
    value = _get_setting("monthly_budget")
    return float(value) if value else None


def set_monthly_budget(amount):
    """Save the household's monthly spending budget."""
    _set_setting("monthly_budget", amount)


def get_budget_alert_state():
    """Return the (month, threshold) of the last budget alert sent, or (None, None).

    month is an "YYYY-MM" string, threshold is the percentage (80 or 100)
    already alerted on for that month — used to avoid re-alerting on every
    daily check once a threshold has been crossed.
    """
    month = _get_setting("budget_alert_month")
    threshold = _get_setting("budget_alert_threshold")
    return month, (int(threshold) if threshold else None)


def set_budget_alert_state(month, threshold):
    """Record that a budget alert was sent for this month at this threshold."""
    _set_setting("budget_alert_month", month)
    _set_setting("budget_alert_threshold", threshold)


def get_financial_goal():
    """Return the household's financial goal, or None if not set.

    Opt-in only — set via /set_goal, never asked upfront.

    Returns:
        Dict with keys name, amount (float), target_date (ISO date string),
        or None if no goal has been set.
    """
    name = _get_setting("goal_name")
    amount = _get_setting("goal_amount")
    target_date = _get_setting("goal_target_date")
    if not name or not amount or not target_date:
        return None
    return {"name": name, "amount": float(amount), "target_date": target_date}


def set_financial_goal(name, amount, target_date):
    """Save the household's financial goal.

    Args:
        name: What the goal is for (e.g. "Japan trip").
        amount: Target amount.
        target_date: Target date as an ISO string ("YYYY-MM-DD").
    """
    _set_setting("goal_name", name)
    _set_setting("goal_amount", amount)
    _set_setting("goal_target_date", target_date)
