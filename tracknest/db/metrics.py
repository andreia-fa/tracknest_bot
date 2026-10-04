"""Read-only aggregate queries backing /finance, /stock, the dashboard and proactive alerts.

Deliberately Telegram-agnostic — every function returns plain dicts/lists so
a future web page can call the same functions instead of re-deriving these
numbers from a different layer.
"""

import calendar
from datetime import datetime, timedelta, timezone

from db.database import get_connection

# Extrapolating a month-end total from only a few days of spend is noise, not
# a forecast — get_month_pace returns no projection below this many days in.
MIN_DAYS_FOR_PROJECTION = 5

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
        top_categories (list of {category, total}), treat (float spent on
        items flagged as treats), need (float spent on needs), and unknown
        (float spent on items not sorted yet — kept separate so an
        unanswered question never masquerades as a need).
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
        SELECT i.treat_or_need AS treat_or_need,
               SUM(e.quantity_purchased * e.unit_price) AS total
        FROM item_expenses e
        JOIN inventory_items i ON i.id = e.item_id
        WHERE e.purchase_date LIKE ?
        GROUP BY i.treat_or_need
    """, (f"{prefix}%",))
    by_kind = {row["treat_or_need"]: float(row["total"]) for row in cursor.fetchall()}

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
        "treat": by_kind.get("treat", 0.0),
        "need": by_kind.get("need", 0.0),
        "unknown": by_kind.get("unknown", 0.0),
    }


def get_category_purchases(year=None, month=None):
    """Return a month's purchases grouped by category — what each category bar is made of.

    Args:
        year: Calendar year. Defaults to the current month.
        month: Calendar month (1-12). Defaults to the current month.

    Returns:
        Dict of category (as get_spending_summary names it, 'Uncategorized'
        when unset) -> list of {day, name, store, total}, biggest first.
    """
    now = datetime.now(tz=timezone.utc)
    prefix = f"{year or now.year:04d}-{month or now.month:02d}"
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COALESCE(i.category, 'Uncategorized') AS category, e.purchase_date AS day,
               i.name AS name, e.store AS store, e.quantity_purchased * e.unit_price AS total
        FROM item_expenses e
        JOIN inventory_items i ON i.id = e.item_id
        WHERE e.purchase_date LIKE ?
        ORDER BY total DESC, e.purchase_date
    """, (f"{prefix}%",))
    grouped = {}
    for row in cursor.fetchall():
        grouped.setdefault(row["category"], []).append(
            {"day": row["day"], "name": row["name"], "store": row["store"], "total": float(row["total"])})
    cursor.close()
    conn.close()
    return grouped


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
    if is_current_month and days_elapsed >= MIN_DAYS_FOR_PROJECTION:
        projected = spent / days_elapsed * days_in_month
    else:
        projected = None
    return {
        "spent": spent,
        "days_elapsed": days_elapsed,
        "days_in_month": days_in_month,
        "projected": projected,
    }


def _stocked_needs():
    """Every stocked need with its run-out estimate, reasoned per product like the check-ins.

    Only the most recently bought item of each product counts, timed from
    that purchase — a new brand of cheese replaces the old one rather than
    the old one's date still saying "cheese runs out". shelf_life_days is
    per pack, so the run-out date is the purchase plus that many days per
    pack bought.

    Returns:
        List of dicts (id, name, product, category, shelf_life_days,
        last_quantity, days_left — negative once overdue —, run_out_date as
        an ISO date, last_purchase as an aware datetime, store: where this
        product is usually bought, or None),
        never-bought needs left out.
    """
    from db.crud import LATEST_PRODUCT_PURCHASE_JOIN

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"""
        SELECT i.id AS id, i.name AS name, i.product AS product, i.category AS category,
               i.shelf_life_days AS shelf_life_days,
               COALESCE(e.logged_at, e.purchase_date) AS last_purchase,
               e.quantity_purchased AS last_quantity,
               (SELECT e3.store FROM item_expenses e3 JOIN inventory_items j ON j.id = e3.item_id
                WHERE COALESCE(j.product, j.name) = COALESCE(i.product, i.name) AND e3.store IS NOT NULL
                GROUP BY e3.store ORDER BY COUNT(*) DESC, MAX(e3.purchase_date) DESC LIMIT 1) AS store
        FROM inventory_items i
        {LATEST_PRODUCT_PURCHASE_JOIN}
        WHERE i.treat_or_need = 'need' AND i.lasts = 'days' AND e.item_id = i.id
    """)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    now = datetime.now(tz=timezone.utc)
    needs = []
    for row in rows:
        packs = max(1, row["last_quantity"] or 1)
        run_out = _as_utc(row["last_purchase"]) + timedelta(days=row["shelf_life_days"] * packs)
        needs.append({
            "id": row["id"], "name": row["name"], "product": row["product"], "category": row["category"],
            "shelf_life_days": row["shelf_life_days"], "last_quantity": packs, "store": row["store"],
            "days_left": (run_out - now).days, "run_out_date": run_out.date().isoformat(),
            "last_purchase": _as_utc(row["last_purchase"]),
        })
    return needs


def get_running_low(days_ahead=7):
    """Return needs whose estimated run-out date falls within the next few days.

    Forward-looking counterpart to the shelf-life check-in, which only speaks
    up once an item is already due. Treats are excluded (a treat running out
    isn't a restocking need), and so are same-day and one-off items (never
    actually stocked — there's nothing to run low on).

    Args:
        days_ahead: How far ahead to look.

    Returns:
        List of dicts (name, product, category, days_left, run_out_date as an
        ISO date string, shelf_life_days), soonest first. Items already overdue are left out;
        those are the check-in flow's job.
    """
    due = [{k: need[k] for k in ("name", "product", "category", "days_left", "run_out_date", "shelf_life_days")}
           for need in _stocked_needs() if 0 <= need["days_left"] <= days_ahead]
    due.sort(key=lambda item: item["days_left"])
    return due


def get_usual_store(product_or_name):
    """Return the store a product (or an item by name) is most often bought at, or None if never with a store."""
    conn = get_connection()
    row = conn.execute("""
        SELECT e.store FROM item_expenses e JOIN inventory_items i ON i.id = e.item_id
        WHERE (lower(COALESCE(i.product, '')) = lower(?) OR lower(i.name) = lower(?)) AND e.store IS NOT NULL
        GROUP BY e.store ORDER BY COUNT(*) DESC, MAX(e.purchase_date) DESC LIMIT 1
    """, (product_or_name, product_or_name)).fetchone()
    conn.close()
    return row["store"] if row else None


def get_shop_plan(horizon_days=14):
    """Return the needs a shop should think about: everything running out within horizon_days, or overdue.

    Args:
        horizon_days: How far ahead to look — the caller picks the shopping
            day and the week after it from these.

    Returns:
        Dict with keys items (see _stocked_needs, soonest first) and unknown
        (count of bought needs, or not-yet-sorted items, whose lifespan
        isn't known — they can't be predicted, only asked about).
    """
    items = sorted((need for need in _stocked_needs() if need["days_left"] <= horizon_days),
                   key=lambda need: need["days_left"])
    conn = get_connection()
    unknown = conn.execute("""
        SELECT COUNT(*) FROM inventory_items i
        WHERE i.treat_or_need != 'treat' AND i.lasts = 'unknown'
          AND EXISTS (SELECT 1 FROM item_expenses e WHERE e.item_id = i.id)
    """).fetchone()[0]
    conn.close()
    return {"items": items, "unknown": unknown}


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
          treat_or_need}, most expensive per day first (at most top_n). A
          same-day item counts as lasting 1 day.
        - ready: how many items currently qualify.
        - tracked: how many items have a shelf-life estimate at all, so a
          caller can say what it's still waiting on rather than silently
          showing nothing.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT i.name AS name,
               CASE i.lasts WHEN 'same_day' THEN 1 ELSE i.shelf_life_days END AS shelf_life_days,
               i.treat_or_need AS treat_or_need,
               (SELECT e.unit_price FROM item_expenses e
                WHERE e.item_id = i.id AND e.price_kind = 'unit' ORDER BY e.id DESC LIMIT 1) AS unit_price,
               (SELECT COUNT(*) FROM item_expenses e
                WHERE e.item_id = i.id) AS purchase_count
        FROM inventory_items i
        WHERE i.lasts IN ('same_day', 'days')
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
            "treat_or_need": row["treat_or_need"],
        }
        for row in rows
        if row["unit_price"] and row["purchase_count"] >= _MIN_PURCHASES_FOR_SHELF_LIFE_TRUST
    ]
    costs.sort(key=lambda item: item["cost_per_day"], reverse=True)
    return {"items": costs[:top_n], "ready": len(costs), "tracked": len(rows)}


def get_budget_status(year=None, month=None):
    """Return a month's spend against the household budget, or None if no budget is set.

    Args:
        year: Calendar year. Defaults to the current month.
        month: Calendar month (1-12). Defaults to the current month.

    Returns:
        Dict with keys: budget, spent, pct (0-100+, float), or None.
    """
    from db.settings import get_monthly_budget

    budget = get_monthly_budget()
    if budget is None:
        return None
    spent = get_spending_summary(year, month)["total"]
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


_MIN_PRICE_RISE_PCT = 1.0
# A trip spending less than this is a top-up: the kind one bigger shop could absorb.
TOP_UP_TRIP_EUR = 5.0


def get_price_trends(min_history=2, top_n=3):
    """Return the items whose latest price has crept up the most against their own history, per store.

    Compares each item's most recent purchase price at a store to the
    average of its earlier purchases at that same store — the same
    comparison as the per-purchase price delta, aggregated so the dashboard
    can surface creeping inflation. Different stores charging different
    prices isn't inflation, so stores are never mixed, and purchases with no
    known store are left out. Rises under _MIN_PRICE_RISE_PCT are noise
    (float rounding, a cent) and are left out too.

    Args:
        min_history: Minimum number of purchases (including the latest)
            required before an item is considered.
        top_n: How many items to return, highest increase first.

    Returns:
        List of dicts: name, store, pct_change, latest_price, avg_price.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT i.name AS name, e.store AS store, e.unit_price AS unit_price
        FROM item_expenses e
        JOIN inventory_items i ON i.id = e.item_id
        WHERE e.store IS NOT NULL AND e.price_kind = 'unit'
        ORDER BY i.name, e.store, COALESCE(e.logged_at, e.purchase_date), e.id
    """)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    prices_by_item = {}
    for row in rows:
        prices_by_item.setdefault((row["name"], row["store"]), []).append(row["unit_price"])

    trends = []
    for (name, store), prices in prices_by_item.items():
        if len(prices) < min_history:
            continue
        *prior, latest = prices
        avg_price = sum(prior) / len(prior)
        if avg_price <= 0:
            continue
        pct_change = (latest - avg_price) / avg_price * 100
        if pct_change >= _MIN_PRICE_RISE_PCT:
            trends.append({
                "name": name, "store": store, "pct_change": pct_change,
                "latest_price": latest, "avg_price": avg_price,
            })

    trends.sort(key=lambda t: t["pct_change"], reverse=True)
    return trends[:top_n]


def get_months_with_spending(limit=12):
    """Return the (year, month) pairs that have purchases, plus the current month, oldest first.

    Args:
        limit: How many of the most recent months to return at most.
    """
    now = datetime.now(tz=timezone.utc)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT substr(purchase_date, 1, 7) AS ym FROM item_expenses WHERE purchase_date IS NOT NULL")
    months = {tuple(int(part) for part in row["ym"].split("-")) for row in cursor.fetchall()}
    cursor.close()
    conn.close()
    months.add((now.year, now.month))
    return sorted(m for m in months if m <= (now.year, now.month))[-limit:]


def get_inventory_health():
    """Return items needing attention: pending alerts, missing profile data.

    Returns:
        Dict with keys checkin_pending, spare_alert_pending, unprofiled
        (items with a question still open: what it is, treat-or-need, or
        how long it lasts)
        — each a list of item names needing that kind of
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
        WHERE treat_or_need = 'unknown' OR lasts = 'unknown' OR name_status IS NOT NULL
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
    """Return a month's shopping trips — one per receipt or typed message — and where they happened.

    Purchases carry a trip_key (see expenses.log_expense); older ones
    without it fall back to one trip per store per day, which undercounts
    two visits to the same store on one day. Spending that isn't shopping
    (categorize.NOT_SHOPPING, e.g. a pool ticket) is never a trip: it's
    reported separately as not_shopping, and left out of everything else here.

    Args:
        year: Calendar year. Defaults to the current month.
        month: Calendar month (1-12). Defaults to the current month.

    Returns:
        Dict with keys count, avg_basket (float, or None with no trips),
        by_store (list of {store, trips, total}, biggest spend first; store
        is None for hand-typed purchases), top_ups (trips under
        TOP_UP_TRIP_EUR, as {day, store, total}), per_day (one list per
        day of the month, holding each of that day's trip totals) and
        not_shopping (euros). per_day entries are {store, total} per trip.
    """
    from bot.categorize import NOT_SHOPPING

    now = datetime.now(tz=timezone.utc)
    year, month = year or now.year, month or now.month
    prefix = f"{year:04d}-{month:02d}"
    placeholders = ", ".join("?" for _ in NOT_SHOPPING)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"""
        SELECT COALESCE(e.trip_key, e.purchase_date || '|' || COALESCE(e.store, '')) AS trip,
               MIN(e.purchase_date) AS day, e.store AS store,
               SUM(e.quantity_purchased * e.unit_price) AS total
        FROM item_expenses e
        JOIN inventory_items i ON i.id = e.item_id
        WHERE e.purchase_date LIKE ? AND COALESCE(i.category, '') NOT IN ({placeholders})
        GROUP BY trip, e.store
    """, (f"{prefix}%", *NOT_SHOPPING))
    trips = cursor.fetchall()
    cursor.execute(f"""
        SELECT COALESCE(SUM(e.quantity_purchased * e.unit_price), 0)
        FROM item_expenses e
        JOIN inventory_items i ON i.id = e.item_id
        WHERE e.purchase_date LIKE ? AND i.category IN ({placeholders})
    """, (f"{prefix}%", *NOT_SHOPPING))
    not_shopping = float(cursor.fetchone()[0])
    cursor.close()
    conn.close()

    by_store = {}
    for trip in trips:
        entry = by_store.setdefault(trip["store"], {"store": trip["store"], "trips": 0, "total": 0.0})
        entry["trips"] += 1
        entry["total"] += float(trip["total"])
    grand_total = sum(float(t["total"]) for t in trips)
    top_ups = sorted(
        ({"day": t["day"], "store": t["store"], "total": float(t["total"])}
         for t in trips if float(t["total"]) < TOP_UP_TRIP_EUR),
        key=lambda t: t["day"],
    )
    per_day = [[] for _ in range(calendar.monthrange(year, month)[1])]
    for t in trips:
        per_day[int(t["day"][8:10]) - 1].append({"store": t["store"], "total": float(t["total"])})
    return {
        "count": len(trips),
        "avg_basket": grand_total / len(trips) if trips else None,
        "by_store": sorted(by_store.values(), key=lambda s: s["total"], reverse=True),
        "top_ups": top_ups,
        "per_day": per_day,
        "not_shopping": not_shopping,
    }


def get_fast_food(year=None, month=None):
    """Return a month's fast-food meals — counted per trip, so burger, fries and a drink are one meal.

    What counts as fast food is categorize.is_fast_food (the store or the
    product). Trips group like get_shopping_trips: by trip_key, else one per
    store per day.

    Args:
        year: Calendar year. Defaults to the current month.
        month: Calendar month (1-12). Defaults to the current month.

    Returns:
        Dict with keys meals (int), total (euros) and last_day (ISO date of
        the most recent fast-food meal ever, not just this month; None if
        there's never been one).
    """
    from bot.categorize import is_fast_food

    now = datetime.now(tz=timezone.utc)
    prefix = f"{year or now.year:04d}-{month or now.month:02d}"
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COALESCE(e.trip_key, e.purchase_date || '|' || COALESCE(e.store, '')) AS trip,
               e.purchase_date AS day, e.store AS store, i.name AS name, i.product AS product,
               e.quantity_purchased * e.unit_price AS total
        FROM item_expenses e
        JOIN inventory_items i ON i.id = e.item_id
    """)
    rows = [r for r in cursor.fetchall() if is_fast_food(r["store"], r["name"], r["product"])]
    cursor.close()
    conn.close()
    in_month = [r for r in rows if r["day"].startswith(prefix)]
    return {
        "meals": len({r["trip"] for r in in_month}),
        "total": sum(float(r["total"]) for r in in_month),
        "last_day": max((r["day"] for r in rows), default=None),
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
