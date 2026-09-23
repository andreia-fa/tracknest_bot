"""Read-only aggregate queries backing /report and proactive alerts.

Deliberately Telegram-agnostic — every function returns plain dicts/lists so
a future web page can call the same functions instead of re-deriving these
numbers from a different layer.
"""

import calendar
from datetime import datetime, timedelta, timezone

from db.database import get_connection

# Extrapolating a month-end total from only a few days of spend is noise, not
# a forecast — get_month_pace returns no projection below this many days in.
_MIN_DAYS_FOR_PROJECTION = 5

# A shelf-life estimate starts as the user's cold guess and only becomes
# evidence once a real repurchase interval has tested it (log_expense corrects
# it down when a repurchase beats the estimate). Until then, dividing a price
# by it produces a figure that looks measured but isn't — so get_daily_cost
# withholds any item with fewer purchases than this.
# REVISIT 2026-11-20: raise toward 3 if single intervals still read as noisy,
# or lower if too few items ever qualify. See docs/expenses.md.
_MIN_PURCHASES_FOR_SHELF_LIFE_TRUST = 2


def _as_utc(value):
    """Parse an ISO date or datetime string from the DB as an aware UTC datetime.

    Rows logged before the `logged_at` column existed only carry a
    `purchase_date` ("YYYY-MM-DD"), which parses as naive — treat those as
    midnight UTC so date maths never mixes aware and naive values.
    """
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def get_spending_summary(year=None, month=None, top_n=3):
    """Return spend totals for a calendar month, broken down by item, category, and tier.

    Args:
        year: Calendar year to scope to. Defaults to the current month.
        month: Calendar month (1-12) to scope to. Defaults to the current month.
        top_n: How many top items/categories to include.

    Returns:
        Dict with keys: total (float), top_items (list of {name, total}),
        top_categories (list of {category, total}), luxury (float spent on
        items flagged as treats), essential (float spent on items flagged as
        essentials), necessity (float spent on same-day-consumed items), and
        unclassified (float spent on items never profiled — kept separate so
        an unanswered question never masquerades as an essential).
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
        SELECT i.purchase_type AS purchase_type,
               SUM(e.quantity_purchased * e.unit_price) AS total
        FROM item_expenses e
        JOIN inventory_items i ON i.id = e.item_id
        WHERE e.purchase_date LIKE ?
        GROUP BY i.purchase_type
    """, (f"{prefix}%",))
    by_tier = {row["purchase_type"]: float(row["total"]) for row in cursor.fetchall()}

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
    return {
        "total": total,
        "top_items": top_items,
        "top_categories": top_categories,
        "luxury": by_tier.get("luxury", 0.0),
        "essential": by_tier.get("essential", 0.0),
        "necessity": by_tier.get("necessity", 0.0),
        "unclassified": by_tier.get(None, 0.0),
    }


def get_month_pace(year=None, month=None):
    """Return a month's spend so far alongside a straight-line month-end projection.

    The projection is deliberately naive (spend per day so far × days in the
    month) and is withheld early in the month, when too few days have passed
    for extrapolation to mean anything.

    Args:
        year: Calendar year. Defaults to the current month.
        month: Calendar month (1-12). Defaults to the current month.

    Returns:
        Dict with keys spent, days_elapsed, days_in_month, and projected
        (None when the month is too young to extrapolate, or when looking at
        a month that has already finished — there, spent is the final figure).
    """
    now = datetime.now(tz=timezone.utc)
    year = year or now.year
    month = month or now.month
    days_in_month = calendar.monthrange(year, month)[1]
    is_current_month = (year, month) == (now.year, now.month)
    days_elapsed = now.day if is_current_month else days_in_month

    spent = get_spending_summary(year, month)["total"]
    if is_current_month and days_elapsed >= _MIN_DAYS_FOR_PROJECTION:
        projected = spent / days_elapsed * days_in_month
    else:
        projected = None
    return {
        "spent": spent,
        "days_elapsed": days_elapsed,
        "days_in_month": days_in_month,
        "projected": projected,
    }


def get_running_low(days_ahead=7):
    """Return essentials whose estimated run-out date falls within the next few days.

    Forward-looking counterpart to the shelf-life check-in, which only speaks
    up once an item is already due. Luxury items are excluded (a treat
    running out isn't a restocking need), and so are necessity items
    (same-day-consumed, never actually stocked — there's nothing to run low
    on).

    Args:
        days_ahead: How far ahead to look.

    Returns:
        List of dicts (name, days_left, run_out_date as an ISO date string,
        shelf_life_days), soonest first. Items already overdue are left out;
        those are the check-in flow's job.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT i.name AS name, i.shelf_life_days AS shelf_life_days,
               (SELECT COALESCE(MAX(e.logged_at), MAX(e.purchase_date))
                FROM item_expenses e WHERE e.item_id = i.id) AS last_purchase
        FROM inventory_items i
        WHERE i.purchase_type = 'essential' AND i.shelf_life_days > 0
    """)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    now = datetime.now(tz=timezone.utc)
    due = []
    for row in rows:
        if not row["last_purchase"]:
            continue
        run_out = _as_utc(row["last_purchase"]) + timedelta(days=row["shelf_life_days"])
        days_left = (run_out - now).days
        if 0 <= days_left <= days_ahead:
            due.append({
                "name": row["name"],
                "days_left": days_left,
                "run_out_date": run_out.date().isoformat(),
                "shelf_life_days": row["shelf_life_days"],
            })
    due.sort(key=lambda item: item["days_left"])
    return due


def get_daily_cost(top_n=3):
    """Return items ranked by what they cost per day of use, once that's trustworthy.

    Latest unit price divided by the item's shelf-life estimate — the one
    thing a receipt can never show you, since it separates "expensive to
    buy" from "expensive to keep around". Treats are included: that's
    usually where the spread shows up.

    An item only qualifies once it has been purchased at least
    _MIN_PURCHASES_FOR_SHELF_LIFE_TRUST times, so its shelf life has been
    tested against a real repurchase interval instead of resting on the
    original guess. A quotient of a real price and an untested guess reads
    as a measurement while being nothing of the kind, and this figure is
    used to rank items against each other, so an error in one estimate
    reorders the whole list.

    Args:
        top_n: How many items to return, most expensive per day first.

    Returns:
        Dict with keys:
        - items: list of {name, cost_per_day, unit_price, shelf_life_days,
          purchase_type}, most expensive per day first (at most top_n).
        - ready: how many items currently qualify.
        - tracked: how many items have a shelf-life estimate at all, so a
          caller can say what it's still waiting on rather than silently
          showing nothing.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT i.name AS name, i.shelf_life_days AS shelf_life_days,
               i.purchase_type AS purchase_type,
               (SELECT e.unit_price FROM item_expenses e
                WHERE e.item_id = i.id ORDER BY e.id DESC LIMIT 1) AS unit_price,
               (SELECT COUNT(*) FROM item_expenses e
                WHERE e.item_id = i.id) AS purchase_count
        FROM inventory_items i
        WHERE i.shelf_life_days IS NOT NULL AND i.shelf_life_days > 0
    """)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    costs = [
        {
            "name": row["name"],
            "cost_per_day": row["unit_price"] / row["shelf_life_days"],
            "unit_price": row["unit_price"],
            "shelf_life_days": row["shelf_life_days"],
            "purchase_type": row["purchase_type"],
        }
        for row in rows
        if row["unit_price"] and row["purchase_count"] >= _MIN_PURCHASES_FOR_SHELF_LIFE_TRUST
    ]
    costs.sort(key=lambda item: item["cost_per_day"], reverse=True)
    return {"items": costs[:top_n], "ready": len(costs), "tracked": len(rows)}


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
        (items never asked about purchase type, or shelf life where that
        still applies) — each a list of item names needing that kind of
        attention. An empty list means nothing of that kind needs attention
        right now.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM inventory_items WHERE checkin_pending = 1")
    checkin_pending = [row["name"] for row in cursor.fetchall()]
    cursor.execute("SELECT name FROM inventory_items WHERE spare_alert_pending = 1")
    spare_alert_pending = [row["name"] for row in cursor.fetchall()]
    cursor.execute("""
        SELECT name FROM inventory_items
        WHERE purchase_type IS NULL
           OR (purchase_type IN ('luxury', 'essential') AND shelf_life_days IS NULL)
    """)
    unprofiled = [row["name"] for row in cursor.fetchall()]
    cursor.close()
    conn.close()
    return {
        "checkin_pending": checkin_pending,
        "spare_alert_pending": spare_alert_pending,
        "unprofiled": unprofiled,
    }


def get_shopping_trips(year=None, month=None):
    """Return a month's shopping trips — one per distinct (day, store) — and where they happened.

    A receipt carries no trip id, so a trip is approximated as every
    purchase logged on the same day at the same store. Purchases with no
    store (typed in by hand) count as one trip per day.

    Args:
        year: Calendar year. Defaults to the current month.
        month: Calendar month (1-12). Defaults to the current month.

    Returns:
        Dict with keys count, avg_basket (float, or None with no trips), and
        by_store (list of {store, trips, total}, biggest spend first; store
        is None for hand-typed purchases).
    """
    now = datetime.now(tz=timezone.utc)
    prefix = f"{year or now.year:04d}-{month or now.month:02d}"
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT purchase_date AS day, store AS store,
               SUM(quantity_purchased * unit_price) AS total
        FROM item_expenses
        WHERE purchase_date LIKE ?
        GROUP BY purchase_date, store
    """, (f"{prefix}%",))
    trips = cursor.fetchall()
    cursor.close()
    conn.close()

    by_store = {}
    for trip in trips:
        entry = by_store.setdefault(trip["store"], {"store": trip["store"], "trips": 0, "total": 0.0})
        entry["trips"] += 1
        entry["total"] += float(trip["total"])
    grand_total = sum(float(t["total"]) for t in trips)
    return {
        "count": len(trips),
        "avg_basket": grand_total / len(trips) if trips else None,
        "by_store": sorted(by_store.values(), key=lambda s: s["total"], reverse=True),
    }


def get_daily_spend(year=None, month=None):
    """Return spend per calendar day of a month, zero-filled.

    Args:
        year: Calendar year. Defaults to the current month.
        month: Calendar month (1-12). Defaults to the current month.

    Returns:
        List of floats, index 0 = day 1, one entry per day in the month.
    """
    now = datetime.now(tz=timezone.utc)
    year = year or now.year
    month = month or now.month
    days = [0.0] * calendar.monthrange(year, month)[1]
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT purchase_date AS day, SUM(quantity_purchased * unit_price) AS total
        FROM item_expenses
        WHERE purchase_date LIKE ?
        GROUP BY purchase_date
    """, (f"{year:04d}-{month:02d}%",))
    for row in cursor.fetchall():
        days[int(row["day"][8:10]) - 1] = float(row["total"])
    cursor.close()
    conn.close()
    return days
