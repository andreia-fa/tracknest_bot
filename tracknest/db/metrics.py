"""Read-only aggregate queries backing /report and proactive alerts.

Deliberately Telegram-agnostic — every function returns plain dicts/lists so
a future web page can call the same functions instead of re-deriving these
numbers from a different layer.
"""

from datetime import datetime, timezone

from db.database import get_connection


def get_spending_summary(year=None, month=None, top_n=3):
    """Return spend totals for a calendar month, broken down by item and category.

    Args:
        year: Calendar year to scope to. Defaults to the current month.
        month: Calendar month (1-12) to scope to. Defaults to the current month.
        top_n: How many top items/categories to include.

    Returns:
        Dict with keys: total (float), top_items (list of {name, total}),
        top_categories (list of {category, total}).
    """
    now = datetime.now(tz=timezone.utc)
    year = year or now.year
    month = month or now.month
    prefix = f"{year:04d}-{month:02d}"

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COALESCE(SUM(quantity_purchased * unit_price), 0)
        FROM item_expenses
        WHERE purchase_date LIKE ?
    """, (f"{prefix}%",))
    total = float(cursor.fetchone()[0])

    cursor.execute("""
        SELECT i.name AS name, SUM(e.quantity_purchased * e.unit_price) AS total
        FROM item_expenses e
        JOIN inventory_items i ON i.id = e.item_id
        WHERE e.purchase_date LIKE ?
        GROUP BY i.name
        ORDER BY total DESC
        LIMIT ?
    """, (f"{prefix}%", top_n))
    top_items = [dict(r) for r in cursor.fetchall()]

    cursor.execute("""
        SELECT COALESCE(i.category, 'Uncategorized') AS category,
               SUM(e.quantity_purchased * e.unit_price) AS total
        FROM item_expenses e
        JOIN inventory_items i ON i.id = e.item_id
        WHERE e.purchase_date LIKE ?
        GROUP BY category
        ORDER BY total DESC
        LIMIT ?
    """, (f"{prefix}%", top_n))
    top_categories = [dict(r) for r in cursor.fetchall()]

    cursor.close()
    conn.close()
    return {"total": total, "top_items": top_items, "top_categories": top_categories}


def get_budget_status():
    """Return this month's spend against the household budget, or None if no budget is set.

    Returns:
        Dict with keys: budget, spent, pct (0-100+, float), or None.
    """
    from db.settings import get_monthly_budget

    budget = get_monthly_budget()
    if budget is None:
        return None
    spent = get_spending_summary()["total"]
    pct = (spent / budget * 100) if budget else 0.0
    return {"budget": budget, "spent": spent, "pct": pct}


def get_goal_status():
    """Return the household's financial goal alongside the pace needed to hit it.

    TrackNest only tracks spending, not actual savings, so this doesn't
    measure real progress — it's an honest anchor number (how much you'd
    need to set aside per month, starting today, to hit the target by the
    target date), not a tracked balance.

    Returns:
        Dict with keys name, amount, target_date, days_left, and
        pace_per_month (None if the target date has already passed), or
        None if no goal is set.
    """
    from db.settings import get_financial_goal

    goal = get_financial_goal()
    if goal is None:
        return None
    target = datetime.strptime(goal["target_date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    days_left = (target - datetime.now(tz=timezone.utc)).days
    if days_left <= 0:
        pace_per_month = None
    else:
        months_left = days_left / 30.44
        pace_per_month = goal["amount"] / months_left
    return {**goal, "days_left": days_left, "pace_per_month": pace_per_month}


def get_price_trends(min_history=2, top_n=3):
    """Return the items whose latest price has crept up the most against their own history.

    Compares each item's most recent purchase price to the average of its
    earlier purchases — the same comparison used for the per-purchase price
    delta, but aggregated across all items so the dashboard can surface
    creeping inflation, not just one-off jumps.

    Args:
        min_history: Minimum number of purchases (including the latest)
            required before an item is considered.
        top_n: How many items to return, highest increase first.

    Returns:
        List of dicts: name, pct_change (signed), latest_price, avg_price.
        Only items with a positive pct_change are included.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT i.name AS name, e.unit_price AS unit_price
        FROM item_expenses e
        JOIN inventory_items i ON i.id = e.item_id
        ORDER BY i.name, e.id ASC
    """)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    prices_by_item = {}
    for row in rows:
        prices_by_item.setdefault(row["name"], []).append(row["unit_price"])

    trends = []
    for name, prices in prices_by_item.items():
        if len(prices) < min_history:
            continue
        *prior, latest = prices
        avg_price = sum(prior) / len(prior)
        if avg_price <= 0:
            continue
        pct_change = (latest - avg_price) / avg_price * 100
        if pct_change > 0:
            trends.append({
                "name": name, "pct_change": pct_change,
                "latest_price": latest, "avg_price": avg_price,
            })

    trends.sort(key=lambda t: t["pct_change"], reverse=True)
    return trends[:top_n]


def get_inventory_health():
    """Return items needing attention: pending alerts, missing profile data.

    Returns:
        Dict with keys checkin_pending, spare_alert_pending, unprofiled
        (items never asked about shelf-life/luxury status) — each a list of
        item names needing that kind of attention. An empty list means
        nothing of that kind needs attention right now.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM inventory_items WHERE checkin_pending = 1")
    checkin_pending = [row["name"] for row in cursor.fetchall()]
    cursor.execute("SELECT name FROM inventory_items WHERE spare_alert_pending = 1")
    spare_alert_pending = [row["name"] for row in cursor.fetchall()]
    cursor.execute("""
        SELECT name FROM inventory_items
        WHERE shelf_life_days IS NULL OR is_luxury IS NULL
    """)
    unprofiled = [row["name"] for row in cursor.fetchall()]
    cursor.close()
    conn.close()
    return {
        "checkin_pending": checkin_pending,
        "spare_alert_pending": spare_alert_pending,
        "unprofiled": unprofiled,
    }
