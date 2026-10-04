"""Password-gated web dashboard: the month at a glance, live from the DB.

Runs as an aiohttp app inside the same process as the Telegram bot (see
bot/main.py's run loop), reachable only via the Cloudflare Tunnel URL
bot/tunnel.py hands out — never a port published on the host. Session auth
is bot.auth's signed cookie, not a server-side session store.

Every number comes from db.metrics — this module only arranges it — so a
future non-Telegram frontend can reuse the same functions unchanged.
"""

from datetime import datetime, timezone
from html import escape

from aiohttp import web

from bot import auth
from bot.categorize import CATEGORY_NAMES, PURPOSES, purpose_of
from db import crud, metrics, shopping_list

_SESSION_COOKIE = "tracknest_session"

# Categories beyond this many fold into "Other". Set to the full category
# list: a cap of 6 folded €55.82 into an "Other" bigger than any real row,
# hiding exactly where the money went — and 12 labelled one-colour rows
# still read fine on a phone.
_MAX_CATEGORIES = len(CATEGORY_NAMES)


def _purposes(categories: list[dict]) -> list[dict]:
    """Category totals summed per purpose (Food, Personal & home, Leisure, Other), biggest first, empty ones left out."""
    totals = {purpose: 0.0 for purpose in PURPOSES}
    holds = {purpose: [] for purpose in PURPOSES}
    for c in categories:
        purpose = purpose_of(c["category"])
        totals[purpose] += c["total"]
        holds[purpose].append({"name": c["category"] or "Uncategorized", "total": c["total"]})
    return sorted(({"name": p, "total": t, "categories": holds[p]} for p, t in totals.items() if t),
                  key=lambda p: p["total"], reverse=True)


def build_month_data(year: int, month: int) -> dict:
    """Gather the metrics that belong to one calendar month.

    Returns:
        Dict with keys: key ("YYYY-MM"), is_current, month_label, day,
        days_in_month, spent, projected, budget (dict or None), mix ({need,
        treat, unknown} in euros), categories (list of {name, total}), trips
        fast_food ({meals, total, last_day}) and daily (euros per day).
    """
    now = datetime.now(tz=timezone.utc)
    is_current = (year, month) == (now.year, now.month)
    spending = metrics.get_spending_summary(year, month, top_n=50)
    pace = metrics.get_month_pace(year, month)

    categories = [{"name": c["category"], "total": c["total"]} for c in spending["top_categories"]]
    if len(categories) > _MAX_CATEGORIES:
        rest = categories[_MAX_CATEGORIES - 1:]
        categories = categories[:_MAX_CATEGORIES - 1] + [
            {"name": "Other", "total": sum(c["total"] for c in rest)}
        ]

    return {
        "key": f"{year:04d}-{month:02d}",
        "is_current": is_current,
        "month_label": datetime(year, month, 1).strftime("%B %Y"),
        "day": pace["days_elapsed"],
        "days_in_month": pace["days_in_month"],
        "spent": spending["total"],
        "projected": pace["projected"],
        # Only the current month: the budget is a single setting with no
        # history, so measuring a closed month against today's figure
        # (set after that month ended) would be made up.
        "budget": metrics.get_budget_status(year, month) if is_current else None,
        "mix": {k: spending[k] for k in ("need", "treat", "unknown")},
        "categories": categories,
        "purposes": _purposes(spending["top_categories"]),
        "trips": metrics.get_shopping_trips(year, month),
        "fast_food": metrics.get_fast_food(year, month),
        "daily": metrics.get_daily_spend(year, month),
    }


def build_dashboard_data() -> dict:
    """Gather every metric the dashboard shows: one view per month, plus what's true right now.

    Pure aside from the DB reads metrics.py itself does — no request/response
    concerns — so it's testable without spinning up the web app.

    Returns:
        The current month's build_month_data() keys at the top level, plus
        months (every month with purchases, oldest first, ending with the
        current one — switched between on the page without reloading),
        rising, running_low, shopping_list, health, readable (item name ->
        product, for display) and daily_cost.
    """
    months = [build_month_data(year, month) for year, month in metrics.get_months_with_spending()]
    health = metrics.get_inventory_health()
    running_low = metrics.get_running_low()
    shown_names = {n for group in health.values() for n in group} | {r["name"] for r in running_low}
    return {
        **months[-1],
        "months": months,
        "rising": metrics.get_price_trends(top_n=5),
        "running_low": running_low,
        "shopping_list": shopping_list.get_all_items(),
        "health": health,
        "readable": _readable_names(shown_names),
        "daily_cost": metrics.get_daily_cost(top_n=5),
    }


def _eur(value: float) -> str:
    return f"€{value:,.2f}"


def _share(part: float, whole: float) -> float:
    return part / whole * 100 if whole else 0.0


_PURPOSE_COLORS = {"Food": "var(--p-food)", "Personal & home": "var(--p-home)",
                   "Leisure": "var(--p-leisure)", "Other": "var(--mix-none)"}


# Illustrations: emoji rather than drawn icons — full colour, no image files,
# and every phone already has them.
_CARD_ICONS = {
    "Overview": "🧾", "Spending breakdown": "🍩", "Daily spending &amp; trips": "📅",
    "Spending by category": "🏷️", "Spending by store": "🏪", "Shopping list &amp; restock": "🛒",
    "Action items": "💬", "Price changes": "📈", "Cost per day of use": "⏳",
}
_CATEGORY_ICONS = {
    "Snacks": "🍫", "Ready Meals": "🍱", "Fruits/Veg": "🥦", "Dairy": "🧀", "Bread/Bakery": "🥨",
    "Meat/Fish": "🐟", "Pantry": "🍚", "Beverages": "🥤", "Hygiene/Personal Care": "🧴",
    "Household": "🧽", "Clothing": "👕", "Leisure": "🎟️", "Other": "📦",
}
_MAX_SLICES = 8


def _card(title: str, body: str, sub: str = "", cls: str = "") -> str:
    icon = _CARD_ICONS.get(title)
    title = f'<span class="h-icon" aria-hidden="true">{icon}</span>{title}' if icon else title
    sub_html = f'<p class="sub">{sub}</p>' if sub else ""
    return f'<section class="card {cls}"><h2>{title}</h2>{sub_html}{body}</section>'


# ── month view parts ──────────────────────────────────────────────

def _stats(pairs: list[tuple[str, str]]) -> str:
    return '<dl class="stats">' + "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in pairs) + "</dl>"


def _overview(month: dict, amounts: bool) -> str:
    """The month at a glance. Private: the total, forecast or closing note, budget meter and key averages.

    Share view: the same card without any amount — shopping days, biggest
    day, most-visited store, top category.
    """
    current = month.get("is_current", True)
    daily = month["daily"]
    trips = month["trips"]
    shopped = sum(1 for v in daily if v)
    busiest = max(range(len(daily)), key=lambda i: daily[i]) if any(daily) else None
    short = month["month_label"][:3]
    if not amounts:
        top_store = max(trips["by_store"], key=lambda st: st["trips"], default=None)
        top_cat = month["categories"][0] if month["categories"] else None
        pairs = [
            ("Shopping days", f'{shopped} of {month["day"]}'),
            ("Biggest day", f"{busiest + 1} {short}" if busiest is not None else "—"),
            ("Most visited", f'{escape(top_store["store"] or "typed in")} · {top_store["trips"]}×' if top_store else "—"),
            ("Top category", f'{escape(top_cat["name"])} · {_share(top_cat["total"], month["spent"]):.0f}%'
             if top_cat else "—"),
        ]
        return _card("Overview", _stats(pairs), sub=f'{month["month_label"]}{"" if current else " · closed"}')

    label = "Total spending" if current else f'Total spending · {month["month_label"].split()[0]}'
    if not current:
        status = "Month closed — final total"
    elif month["projected"] is not None:
        status = f'On pace for <strong>{_eur(month["projected"])}</strong> by month end'
    elif month["spent"]:
        status = "Month-end forecast from day 5"
    else:
        status = "No purchases logged yet this month"
    budget = month.get("budget")
    if budget:
        month_pct = month["day"] / month["days_in_month"] * 100
        ahead = budget["pct"] - month_pct
        state = f"⚠️ {ahead:.0f} pts ahead of the calendar" if ahead > 5 else "✓ Within pace"
        meter = f"""
        <div class="budget">
          <div class="budget-head"><span>Budget {_eur(budget["budget"])}</span><strong>{budget["pct"]:.0f}%</strong></div>
          <div class="meter" title="Tick = how far through the month we are ({month_pct:.0f}%)">
            <div class="meter-fill" style="width:{min(budget["pct"], 100):.1f}%"></div>
            <div class="meter-tick" style="left:{month_pct:.1f}%"></div>
          </div>
          <p class="note">{state}</p>
        </div>"""
    elif current:
        meter = '<p class="note">No budget yet — set one with <code>/set_budget</code> in Telegram.</p>'
    else:
        meter = ""
    stats = ""
    if month["spent"] and busiest is not None:
        first = next((i for i, v in enumerate(daily) if v), 0)
        tracked_days = max(month["day"] - first, 1)
        stats = _stats([
            ("Daily average", _eur(month["spent"] / tracked_days)),
            ("Average basket", _eur(trips["avg_basket"]) if trips.get("avg_basket") else "—"),
            ("Shopping days", f"{shopped} of {month['day']}"),
            ("Biggest day", f"{busiest + 1} {short} · {_eur(daily[busiest])}"),
        ])
    return f"""
    <section class="card total">
      <h2>{label}</h2>
      <div class="hero-value">{_eur(month["spent"])}</div>
      <p class="sub">{status}</p>{meter}{stats}
    </section>"""


def _donut(month: dict, amounts: bool) -> str:
    """Spending breakdown by purpose as a donut — a few parts of one whole, read at a glance."""
    total = month["spent"]
    purposes = month.get("purposes", [])
    if not total or not purposes:
        return _card("Spending breakdown", '<p class="note">Appears once something is logged this month.</p>', cls="empty")
    holds = {p["name"]: p.get("categories", []) for p in purposes}

    def inside(name: str) -> str:
        return ", ".join(
            f'{escape(c["name"])} {_eur(c["total"]) if amounts else f"{_share(c["total"], total):.0f}%"}'
            for c in holds[name])

    radius, width = 52, 20
    circumference = 2 * 3.141592653589793 * radius
    gap = 2.0 if len(purposes) > 1 else 0.0
    arcs, offset = [], 0.0
    for p in purposes:
        length = p["total"] / total * circumference
        arcs.append(
            f'<circle r="{radius}" cx="70" cy="70" fill="none" stroke="{_PURPOSE_COLORS[p["name"]]}" '
            f'stroke-width="{width}" stroke-dasharray="{max(length - gap, 0.5):.2f} {circumference:.2f}" '
            f'stroke-dashoffset="{-offset:.2f}" transform="rotate(-90 70 70)">'
            f'<title>{p["name"]} {_share(p["total"], total):.0f}% — {inside(p["name"])}</title></circle>'
        )
        offset += length
    lead = purposes[0]
    legend = "".join(
        f'<li title="{p["name"]}: {inside(p["name"])}"><span class="swatch" style="background:{_PURPOSE_COLORS[p["name"]]}"></span>'
        f'<span class="lg-name">{p["name"]}</span><strong>{_share(p["total"], total):.0f}%</strong>'
        + (f'<span class="lg-amt">{_eur(p["total"])} · ' if amounts else '<span class="lg-amt">')
        + ", ".join(escape(c["name"]) for c in holds[p["name"]]) + "</span></li>"
        for p in purposes
    )
    body = f"""
      <div class="donut-wrap">
        <svg class="donut" viewBox="0 0 140 140" role="img" aria-label="Spending breakdown">
          <circle r="{radius}" cx="70" cy="70" fill="none" stroke="var(--track)" stroke-width="{width}"/>
          {"".join(arcs)}
          <text x="70" y="68" text-anchor="middle" class="donut-value">{_share(lead["total"], total):.0f}%</text>
          <text x="70" y="86" text-anchor="middle" class="donut-label">{lead["name"].lower()}</text>
        </svg>
        <ul class="legend-list">{legend}</ul>
      </div>"""
    return _card("Spending breakdown", body)


def _tile(label: str, value: str, note: str = "", extra: str = "", icon: str = "") -> str:
    art = f'<span class="tile-icon" aria-hidden="true">{icon}</span>' if icon else ""
    return (f'<div class="card tile">{art}<h3>{label}</h3><div class="tile-value">{value}</div>{extra}'
            f'<p class="note">{note}</p></div>')


def _trips_per_week(month: dict | None) -> float | None:
    """Trips per week since the month's first purchase — tracking may start mid-month, so not since day 1."""
    if not month or not month["trips"]["count"]:
        return None
    first = next((i for i, day in enumerate(month["trips"].get("per_day") or []) if day), None)
    if first is None:
        first = next((i for i, v in enumerate(month["daily"]) if v), 0)
    return month["trips"]["count"] / max(month["day"] - first, 1) * 7


def _fast_food_tile(month: dict, previous: dict | None, amounts: bool) -> str:
    """Fast-food meals this month against last month, and how long since the last one — meant to stay rare."""
    food = month.get("fast_food") or {"meals": 0, "total": 0.0, "last_day": None}
    before = (previous or {}).get("fast_food")
    parts = []
    if before is not None:
        parts.append(f'{before["meals"]} in {previous["month_label"][:3]}')
    if amounts and food["meals"]:
        parts.append(_eur(food["total"]))
    if month.get("is_current", True) and food["last_day"]:
        days = (datetime.now(tz=timezone.utc).date() - datetime.fromisoformat(food["last_day"]).date()).days
        parts.append("last one today" if days <= 0 else f'last one {days} day{"s" if days != 1 else ""} ago')
    note = " · ".join(parts) or "None logged yet"
    unit = "meal" if food["meals"] == 1 else "meals"
    meals = food["meals"]
    slices = "🍕" * min(meals, _MAX_SLICES) + (f'<span class="of"> +{meals - _MAX_SLICES}</span>' if meals > _MAX_SLICES else "")
    row = (f'<div class="slices" aria-label="{meals} fast-food {unit}">{slices}</div>' if meals
           else '<div class="slices none" aria-hidden="true">🍕</div>')
    return _tile("Fast food", f'{meals}<span class="of"> {unit}</span>', note, extra=row, icon="🍔")


def _tiles(month: dict, previous: dict | None, amounts: bool) -> str:
    """Treats share, trips per week (against last month), top-up trips and fast food — the numbers to steer by."""
    total = month["spent"]
    mix = month["mix"]
    treat_pct = _share(mix["treat"], total)
    split = (f'<div class="split-bar"><div style="width:{_share(mix["need"], total):.1f}%;background:var(--mix-need)"></div>'
             f'<div style="width:{treat_pct:.1f}%;background:var(--mix-treats)"></div></div>') if total else ""
    need_pct = _share(mix["need"], total)
    treats = _tile(
        "Treats share", f"{treat_pct:.0f}%" if total else "—",
        (f'Needs {need_pct:.0f}% · Treats {treat_pct:.0f}%' + (f' · {_eur(mix["treat"])}' if amounts else ""))
        if total else "Nothing logged yet",
        split, icon="🍫",
    )
    trips = month["trips"]
    rate, before = _trips_per_week(month), _trips_per_week(previous)
    if rate is not None and before:
        change = _share(rate - before, before)
        if change <= -5:
            delta = f'<span class="good">▼ {abs(change):.0f}%</span> vs {previous["month_label"][:3]}'
        elif change >= 5:
            delta = f'<span class="bad">▲ {change:.0f}%</span> vs {previous["month_label"][:3]}'
        else:
            delta = f'about the same as {previous["month_label"][:3]}'
        note = f'{trips["count"]} trips · {delta}'
    else:
        note = f'{trips["count"]} trips this month' if trips["count"] else "No shopping trips yet"
    per_week = _tile("Trips per week", f"{rate:.1f}" if rate is not None else "—", note, icon="🛒")
    top_ups = trips.get("top_ups", [])
    if trips["count"]:
        top_note = (f'{_eur(sum(t["total"] for t in top_ups))} in trips under €5' if amounts
                    else "trips were small top-ups") if top_ups else "Every trip was a proper shop"
        top = _tile("Top-up trips", f'{len(top_ups)}<span class="of"> of {trips["count"]}</span>', top_note, icon="🛍️")
    else:
        top = _tile("Top-up trips", "—", "No shopping trips yet", icon="🛍️")
    return f'<div class="tiles">{treats}{per_week}{top}{_fast_food_tile(month, previous, amounts)}</div>'


def _timeline(month: dict, amounts: bool) -> str:
    """Spending per day as columns, with one dot per shopping trip beneath — hollow for a top-up.

    HTML columns rather than an SVG, so the bars keep a fixed height and the
    dots stay round and readable from a phone to a wide screen.
    """
    daily = month["daily"]
    total = month["spent"]
    if not any(daily):
        return ""
    per_day = month["trips"].get("per_day") or [[] for _ in daily]
    short = month["month_label"][:3]
    peak = max(daily)
    columns = []
    for i, value in enumerate(daily):
        trips = per_day[i] if i < len(per_day) else []
        tops = sum(1 for t in trips if t["total"] < 5)
        what = _eur(value) if amounts else f"{_share(value, total):.0f}% of the month"
        tip = f"{i + 1} {short}: {what}" + (f" · {len(trips)} trip{'s' if len(trips) != 1 else ''}" if trips else "")
        tip += f" ({tops} top-up{'s' if tops != 1 else ''})" if tops else ""
        state = "" if value else (" zero" if i < month["day"] else " future")
        height = max(value / peak * 100, 3) if value else 3
        dots = "".join(
            f'<i class="{"tl-dot topup" if t["total"] < 5 else "tl-dot"}" '
            f'title="{i + 1} {short} · {escape(t["store"] or "typed in")}'
            + (f' · {_eur(t["total"])}' if amounts else "")
            + (" · top-up" if t["total"] < 5 else "") + '"></i>'
            for t in trips[:4]
        ) + (f'<span class="tl-more" title="{len(trips) - 4} more trips">+{len(trips) - 4}</span>' if len(trips) > 4 else "")
        columns.append(f'<div class="tl-day" title="{tip}"><div class="tl-bar-area">'
                       f'<div class="tl-bar{state}" style="height:{height:.1f}%"></div></div>'
                       f'<div class="tl-dots">{dots}</div></div>')
    busiest = max(range(len(daily)), key=lambda i: daily[i])
    shopped = sum(1 for v in daily if v)
    peak_txt = f" ({_eur(peak)})" if amounts else ""
    sub = f"Shopped on {shopped} of {month['day']} days · biggest day {busiest + 1} {short}{peak_txt}"
    top_ups = month["trips"].get("top_ups", [])
    details = ""
    if top_ups:
        rows = "".join(
            f'<tr><td>{datetime.fromisoformat(t["day"]).strftime("%-d %b")}</td>'
            f'<td class="store">{escape(t["store"] or "typed in")}</td>'
            + (f'<td class="num">{_eur(t["total"])}</td>' if amounts else "") + "</tr>"
            for t in top_ups
        )
        details = (f'<details><summary>Show the {len(top_ups)} top-up trip{"s" if len(top_ups) != 1 else ""}</summary>'
                   f'<table class="table">{rows}</table>'
                   f'<p class="note">One bigger shop can absorb these — the restock list shows what runs out when.</p>'
                   f'</details>')
    body = f"""
      <div class="timeline" role="img" aria-label="Daily spending and trips">{"".join(columns)}</div>
      <div class="axis"><span>1</span><span>{len(daily) // 2}</span><span>{len(daily)}</span></div>
      <p class="legend-inline"><span class="dot-key"></span>shopping trip <span class="dot-key topup"></span>top-up ({"under €5" if amounts else "small trip"})</p>
      {details}"""
    return _card("Daily spending &amp; trips", body, sub=sub)


def _categories(month: dict, amounts: bool) -> str:
    """One labelled bar per category, scaled to the largest — the names carry identity, so one colour."""
    categories = month["categories"]
    total = month["spent"]
    if not categories:
        return ""
    peak = max(c["total"] for c in categories) or 1.0
    rows = "".join(
        f'<div class="bar-row"><span class="bar-name"><span class="cat-icon" aria-hidden="true">'
        f'{_CATEGORY_ICONS.get(c["name"], "📦")}</span>{escape(c["name"])}</span>'
        f'<div class="bar-track"><div class="bar-fill" style="width:{c["total"] / peak * 100:.1f}%"></div></div>'
        f'<span class="bar-value">{_share(c["total"], total):.0f}%'
        + (f'<span class="bar-amt">{_eur(c["total"])}</span>' if amounts else "") + "</span></div>"
        for c in categories
    )
    return _card("Spending by category", rows)


def _stores(month: dict, amounts: bool) -> str:
    """Stores as a compact table — trips, share of spending, and (privately) the amount."""
    stores = month["trips"]["by_store"]
    total = sum(s["total"] for s in stores)
    if not stores:
        return ""
    head = "<tr><th>Store</th><th class='num'>Trips</th><th class='num'>Share</th>" + (
        "<th class='num'>Amount</th>" if amounts else "") + "</tr>"
    rows = "".join(
        f'<tr><td class="store">{escape(s["store"] or "Typed in by hand")}</td><td class="num">{s["trips"]}</td>'
        f'<td class="num">{_share(s["total"], total):.0f}%</td>'
        + (f'<td class="num">{_eur(s["total"])}</td>' if amounts else "") + "</tr>"
        for s in stores
    )
    avg = month["trips"].get("avg_basket")
    sub = f"Average basket {_eur(avg)}" if amounts and avg else ""
    return _card("Spending by store", f'<table class="table"><thead>{head}</thead><tbody>{rows}</tbody></table>', sub=sub)


def _month_view(month: dict, previous: dict | None, amounts: bool) -> str:
    """One month on a 12-column grid: headline row, timeline, then categories beside stores."""
    if month.get("is_current", True):
        period = f'{month["month_label"]} · day {month["day"]} of {month["days_in_month"]}'
    else:
        period = f'{month["month_label"]} · closed'
    return f"""
    <p class="period">{period}</p>
    <div class="grid">
      <div class="span-4">{_overview(month, amounts)}</div>
      <div class="span-4">{_donut(month, amounts)}</div>
      <div class="span-4">{_tiles(month, previous, amounts)}</div>
      <div class="span-12">{_timeline(month, amounts)}</div>
      <div class="span-6">{_categories(month, amounts)}</div>
      <div class="span-6">{_stores(month, amounts)}</div>
    </div>"""


# ── right-now parts (private only) ────────────────────────────────

def _readable_names(names: set[str]) -> dict:
    """Map item names to their product ("cheese") when known — receipt names like "LEERDAMMER CAR." read badly."""
    return {n: (crud.get_item(n) or {}).get("product") or n for n in names}


def _to_buy(data: dict) -> str:
    low = data["running_low"]
    items = data["shopping_list"]
    readable = data.get("readable", {})
    spares = sorted({readable.get(n, n) for n in data["health"]["spare_alert_pending"]})
    if not low and not items and not spares:
        return _card("Shopping list &amp; restock", '<p class="note">List empty and nothing running low.</p>')
    rows = "".join(
        f'<tr><td>⏳ {escape(readable.get(r["name"], r["name"]))}</td>'
        f'<td class="num strong">{"today" if r["days_left"] == 0 else f"{r['days_left']}d left"}</td></tr>'
        for r in low
    ) + "".join(
        f'<tr><td>🔁 {escape(p)}</td><td class="num muted">keep a spare</td></tr>' for p in spares
    ) + "".join(
        f'<tr><td>🛒 {escape(i["name"])}</td><td class="num muted">{i["quantity"]}×</td></tr>' for i in items[:10]
    )
    more = f'<p class="note">+ {len(items) - 10} more — <code>/list</code> in Telegram</p>' if len(items) > 10 else ""
    return _card("Shopping list &amp; restock", f'<table class="table">{rows}</table>{more}',
                 sub="⏳ runs out soon · 🔁 keep-a-spare reminder · 🛒 on your list")


def _attention(data: dict) -> str:
    """What the bot is waiting on you for — shown only when there is something."""
    health = data["health"]
    readable = data.get("readable", {})
    groups = [(label, names) for label, names in (
        ("Did these run out?", health["checkin_pending"]),
        ("Open questions", health["unprofiled"]),
    ) if names]
    if not groups:
        return ""
    body = "".join(
        f'<div class="attn"><div class="attn-head"><span>{label}</span><strong>{len(names)}</strong></div>'
        f'<div class="chips">{"".join(f"<span class=chip>{escape(readable.get(n, n))}</span>" for n in names)}</div></div>'
        for label, names in groups
    )
    return _card("Action items", body, sub="Answer these in Telegram")


def _prices(data: dict) -> str:
    """Price rises at the same store — shown only when there are any."""
    rising = data["rising"]
    if not rising:
        return ""
    rows = "".join(
        f'<tr><td>{escape(r["name"])}<span class="cell-sub">{escape(r.get("store") or "")}</span></td>'
        f'<td class="num">{_eur(r["avg_price"])} → {_eur(r["latest_price"])}</td>'
        f'<td class="num bad">▲ {r["pct_change"]:.0f}%</td></tr>'
        for r in rising
    )
    return _card("Price changes", f'<table class="table">{rows}</table>', sub="Latest price vs. your usual, same store")


def _daily_cost(data: dict) -> str:
    cost = data["daily_cost"]
    if not cost["items"]:
        return ""
    peak = cost["items"][0]["cost_per_day"] or 1.0
    rows = "".join(
        f'<div class="bar-row"><span class="bar-name">{escape(i["name"])}</span>'
        f'<div class="bar-track"><div class="bar-fill" style="width:{i["cost_per_day"] / peak * 100:.1f}%"></div></div>'
        f'<span class="bar-value">{_eur(i["cost_per_day"])}<span class="bar-amt">per day</span></span></div>'
        for i in cost["items"]
    )
    return _card("Cost per day of use", rows, sub="Price ÷ how long it lasts — what's expensive to keep, not just to buy")


def _right_now(data: dict) -> str:
    cards = [c for c in (_to_buy(data), _attention(data), _prices(data), _daily_cost(data)) if c]
    cells = "".join(f'<div class="span-6">{c}</div>' for c in cards)
    return f'<p class="group-title right-now">Right now</p><div class="grid right-now">{cells}</div>'


_STYLE = """
  :root {
    color-scheme: light;
    --page: #f9f9f7; --surface: #fcfcfb; --surface-2: #f2f1ec;
    --ink: #0b0b0b; --ink-2: #52514e; --ink-muted: #898781;
    --border: rgba(11,11,11,0.10); --track: #eceae2;
    --accent: #1baf7a; --good: #0ca30c; --bad: #b87700;
    --mix-need: #2a78d6; --mix-treats: #eb6834; --mix-none: #c3c2b7;
    --p-food: #1baf7a; --p-home: #4a3aa7; --p-leisure: #eda100;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      color-scheme: dark;
      --page: #0d0d0d; --surface: #1a1a19; --surface-2: #232322;
      --ink: #ffffff; --ink-2: #c3c2b7; --ink-muted: #898781;
      --border: rgba(255,255,255,0.10); --track: #2c2c2a;
      --accent: #199e70; --good: #3fbf5f; --bad: #fab219;
      --mix-need: #3987e5; --mix-treats: #d95926; --mix-none: #52514e;
      --p-food: #199e70; --p-home: #9085e9; --p-leisure: #c98500;
    }
  }
  * { box-sizing: border-box; }
  body { margin: 0 auto; max-width: 1200px; padding: 24px 16px 32px; background: var(--page);
         color: var(--ink); font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
  header { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
  .brand { display: flex; align-items: center; gap: 8px; }
  .brand-mark { width: 10px; height: 10px; border-radius: 3px; background: var(--accent); }
  h1 { font-size: 19px; font-weight: 800; margin: 0; }
  .header-tools { display: flex; align-items: center; gap: 8px; }
  .tabs { display: flex; gap: 4px; padding: 3px; border-radius: 10px; background: var(--surface-2); }
  .tabs label { padding: 4px 12px; border-radius: 8px; border: 1px solid transparent; font-size: 13px;
                color: var(--ink-2); cursor: pointer; }
  .tool { border: 1px solid var(--border); background: var(--surface); color: var(--ink-2); border-radius: 8px;
          padding: 4px 10px; font-size: 13px; cursor: pointer; }
  .tool[hidden] { display: none; }
  .month-radio { position: absolute; opacity: 0; pointer-events: none; }
  .month, .shared, .share-note { display: none; }
  #share:checked ~ .month .private, #share:checked ~ .right-now { display: none; }
  #share:checked ~ .month .shared, #share:checked ~ .share-note { display: block; }
  #share:checked ~ header .share-toggle { background: var(--ink); color: var(--page); }
  .period { margin: 0 0 10px; font-size: 13px; color: var(--ink-muted); }
  .grid { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: 14px; margin-bottom: 14px; }
  .span-4 { grid-column: span 4; } .span-6 { grid-column: span 6; }
  .span-8 { grid-column: span 8; } .span-12 { grid-column: span 12; }
  .grid > div { display: flex; flex-direction: column; min-width: 0; }
  .grid > div > .card { flex: 1; }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 14px; padding: 18px; }
  .card.empty { border-style: dashed; }
  h2 { font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em;
       color: var(--ink-muted); margin: 0 0 10px; }
  h3 { font-size: 12px; font-weight: 600; color: var(--ink-muted); margin: 0; }
  .sub { font-size: 13px; color: var(--ink-2); margin: -4px 0 12px; }
  .note { font-size: 12px; color: var(--ink-muted); margin: 6px 0 0; }
  .hero-value { font-size: 48px; font-weight: 700; line-height: 1.05; letter-spacing: -0.02em; margin: 2px 0 8px; }
  .total .sub { margin: 0; }
  .budget { margin-top: 16px; }
  .stats { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 16px; margin: 18px 0 0; }
  .stats dt { font-size: 11px; color: var(--ink-muted); text-transform: uppercase; letter-spacing: 0.04em; }
  .stats dd { margin: 2px 0 0; font-size: 15px; font-weight: 600; }
  .budget-head { display: flex; justify-content: space-between; font-size: 13px; color: var(--ink-2); margin-bottom: 6px; }
  .meter { position: relative; height: 8px; border-radius: 4px; background: var(--track); }
  .meter-fill { height: 100%; border-radius: 4px; background: var(--accent); }
  .meter-tick { position: absolute; top: -3px; width: 2px; height: 14px; background: var(--ink); }
  .donut-wrap { display: flex; align-items: center; gap: 18px; }
  .donut { width: 156px; height: 156px; flex: none; }
  .donut-value { font-size: 24px; font-weight: 700; fill: var(--ink); }
  .donut-label { font-size: 11px; fill: var(--ink-muted); }
  .legend-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; flex: 1; min-width: 0; }
  .legend-list li { display: grid; grid-template-columns: 10px 1fr auto; column-gap: 8px; align-items: center; font-size: 13px; }
  .legend-list .lg-name { color: var(--ink-2); }
  .legend-list .lg-amt { grid-column: 2 / 4; font-size: 11px; color: var(--ink-muted); }
  .swatch { width: 10px; height: 10px; border-radius: 2px; }
  .tiles { display: grid; grid-template-rows: repeat(4, 1fr); gap: 14px; flex: 1; }
  .tile { position: relative; padding: 12px 16px; display: flex; flex-direction: column; justify-content: center; }
  .tile-icon { position: absolute; top: 10px; right: 12px; width: 34px; height: 34px; border-radius: 10px;
               display: grid; place-items: center; font-size: 19px; background: var(--surface-2); }
  .h-icon { margin-right: 6px; font-size: 14px; letter-spacing: 0; }
  .cat-icon { margin-right: 6px; }
  .slices { font-size: 18px; letter-spacing: 1px; line-height: 1.2; margin-top: 2px; }
  .slices.none { filter: grayscale(1); opacity: 0.35; }
  .tile h3, .tile .tile-value { padding-right: 40px; }
  .tile-value { font-size: 26px; font-weight: 700; line-height: 1.2; }
  .tile-value .of { font-size: 14px; font-weight: 500; color: var(--ink-muted); }
  .tile .note { margin-top: 2px; }
  .split-bar { display: flex; gap: 2px; height: 6px; border-radius: 3px; overflow: hidden; margin-top: 6px; }
  .good { color: var(--good); font-weight: 600; } .bad { color: var(--bad); font-weight: 600; }
  .timeline { display: flex; gap: 2px; }
  .tl-day { flex: 1; min-width: 0; display: flex; flex-direction: column; align-items: center; }
  .tl-bar-area { height: 120px; width: 100%; display: flex; align-items: flex-end; }
  .tl-bar { width: 100%; border-radius: 3px 3px 0 0; background: var(--accent); }
  .tl-bar.zero { background: var(--track); } .tl-bar.future { background: var(--track); opacity: 0.4; }
  .tl-day:hover .tl-bar { opacity: 0.75; }
  .tl-dots { display: flex; flex-direction: column; align-items: center; gap: 3px; padding-top: 6px; min-height: 26px; }
  .tl-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--ink-2); display: block; }
  .tl-dot.topup { background: var(--surface); box-shadow: inset 0 0 0 1.6px var(--bad); }
  .tl-dot { cursor: help; outline: 4px solid transparent; }  /* bigger hover target than the dot */
  .tl-dot:hover { transform: scale(1.5); }
  .tl-more { font-size: 9px; color: var(--ink-muted); }
  .legend-list li { cursor: help; }
  .axis { display: flex; justify-content: space-between; font-size: 11px; color: var(--ink-muted); margin-top: 2px; }
  .legend-inline { font-size: 12px; color: var(--ink-muted); margin: 8px 0 0; display: flex; align-items: center; gap: 6px; }
  .dot-key { width: 8px; height: 8px; border-radius: 50%; background: var(--ink-2); display: inline-block; margin-left: 8px; }
  .dot-key:first-child { margin-left: 0; }
  .dot-key.topup { background: transparent; border: 1.6px solid var(--bad); }
  details { margin-top: 12px; }
  summary { cursor: pointer; font-size: 13px; color: var(--ink-2); }
  .bar-row { display: grid; grid-template-columns: minmax(0, 150px) 1fr 84px; align-items: center; gap: 10px; padding: 5px 0; }
  .bar-name { font-size: 13px; color: var(--ink-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .bar-track { height: 10px; border-radius: 4px; background: var(--track); }
  .bar-fill { height: 100%; border-radius: 4px; background: var(--accent); }
  .bar-value { font-size: 13px; font-weight: 600; text-align: right; font-variant-numeric: tabular-nums; }
  .bar-amt { display: block; font-size: 11px; font-weight: 400; color: var(--ink-muted); }
  .table { width: 100%; border-collapse: collapse; font-size: 13px; }
  .table th { text-align: left; font-size: 11px; font-weight: 600; color: var(--ink-muted); text-transform: uppercase;
              letter-spacing: 0.04em; padding: 0 0 6px; border-bottom: 1px solid var(--border); }
  .table td { padding: 7px 0; border-bottom: 1px solid var(--border); color: var(--ink-2); }
  .table tr:last-child td { border-bottom: 0; }
  .table .num { text-align: right; font-variant-numeric: tabular-nums; padding-left: 12px; white-space: nowrap; }
  .table .strong { color: var(--ink); font-weight: 700; } .table .muted { color: var(--ink-muted); }
  .table .store { max-width: 0; width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .cell-sub { display: block; font-size: 11px; color: var(--ink-muted); }
  .attn + .attn { margin-top: 14px; }
  .attn-head { display: flex; justify-content: space-between; font-size: 13px; color: var(--ink-2); }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
  .chip { font-size: 12px; padding: 3px 9px; border-radius: 999px; background: var(--surface-2);
          border: 1px solid var(--border); color: var(--ink-2); }
  .group-title { font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em;
                 color: var(--ink-muted); margin: 22px 0 10px; }
  .share-note { text-align: center; font-size: 12px; color: var(--ink-muted); margin: 6px 0; }
  code { background: var(--surface-2); border-radius: 4px; padding: 1px 5px; font-size: 11px; }
  footer { text-align: center; font-size: 12px; color: var(--ink-muted); padding-top: 10px; }
  footer a { color: var(--ink-muted); }
  @media (max-width: 899px) {
    .grid { grid-template-columns: minmax(0, 1fr); }
    .span-4, .span-6, .span-8, .span-12 { grid-column: auto; }
    .tiles { grid-template-rows: none; grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .tile { padding: 10px 12px; }
    .tile h3 { font-size: 11px; }
    .tile-icon { width: 28px; height: 28px; font-size: 16px; top: 8px; right: 8px; }
    .tile-value { font-size: 22px; }
    .hero-value { font-size: 40px; }
    .bar-row { grid-template-columns: minmax(0, 104px) 1fr 70px; }
  }
  @media (max-width: 359px) {
    .tiles { grid-template-columns: minmax(0, 1fr); }
  }
  @media (max-width: 420px) {
    .donut-wrap { flex-direction: column; align-items: stretch; }
    .donut { align-self: center; }
  }
"""


_TELEGRAM_SCRIPT = """<script src="https://telegram.org/js/telegram-web-app.js"></script>
<script>
  // Inside Telegram: full height, and a full-screen button where the app supports it (Bot API 8.0+).
  document.addEventListener("DOMContentLoaded", () => {
    const app = window.Telegram && Telegram.WebApp;
    if (!app || !app.initData) return;
    app.expand();
    if (app.isVersionAtLeast && app.isVersionAtLeast("8.0") && app.requestFullscreen) {
      const button = document.getElementById("fullscreen");
      button.hidden = false;
      button.onclick = () => app.isFullscreen ? app.exitFullscreen() : app.requestFullscreen();
    }
  });
</script>"""


def _month_tabs_css(count: int) -> str:
    """Show the month whose radio is checked, and mark its tab — one rule pair per month."""
    return "".join(
        f"#m{i}:checked ~ .m{i} {{ display: block; }}"
        f"#m{i}:checked ~ header label[for=m{i}] {{ background: var(--surface); color: var(--ink); "
        f"border-color: var(--border); font-weight: 700; }}"
        for i in range(count)
    )


def render_dashboard_html(data: dict) -> str:
    """Render the full dashboard page from build_dashboard_data()'s output.

    Every month is in the page and the tabs are radio buttons, so switching
    months needs no request: inside Telegram the session cookie is often
    dropped, and a reload would bounce back to the login. Each month is
    rendered twice — private, and a share view built without any amount
    (not by hiding them), safe to screenshot.
    """
    updated = datetime.now(tz=timezone.utc).strftime("%d %b, %H:%M UTC")
    months = data.get("months") or [{**data, "key": "this-month"}]
    shown = len(months) - 1
    radios = "".join(
        f'<input type="radio" name="month" id="m{i}" class="month-radio"{" checked" if i == shown else ""}>'
        for i in range(len(months))
    )
    tabs = "" if len(months) < 2 else '<nav class="tabs" aria-label="Month">' + "".join(
        f'<label for="m{i}">{m["month_label"][:3]}</label>' for i, m in enumerate(months)
    ) + "</nav>"
    views = "".join(
        f'<section class="month m{i}"><div class="private">{_month_view(m, prev, amounts=True)}</div>'
        f'<div class="shared">{_month_view(m, prev, amounts=False)}</div></section>'
        for i, (m, prev) in enumerate(zip(months, [None, *months[:-1]]))
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TrackNest</title>
<style>{_STYLE}{_month_tabs_css(len(months))}</style>
{_TELEGRAM_SCRIPT}
</head>
<body>
  {radios}
  <input type="checkbox" id="share" class="month-radio">
  <header>
    <div class="brand"><span class="brand-mark"></span><h1>TrackNest</h1></div>
    <div class="header-tools">{tabs}<label for="share" class="tool share-toggle" title="Percentages only — safe to screenshot and share">👁 Share</label><button id="fullscreen" class="tool" hidden title="Full screen">⛶</button></div>
  </header>
  {views}
  <p class="share-note">👁 Share view — percentages only, no amounts. Tap 👁 again for your full view.</p>
  {_right_now(data)}
  <footer class="right-now">Live from TrackNest · {updated} · <a href="/logout">Log out</a></footer>
</body>
</html>"""


def _login_page(error: str = "") -> str:
    error_html = f'<p class="error">{error}</p>' if error else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TrackNest login</title>
<style>
  body {{ font-family: system-ui, -apple-system, sans-serif; background: #f9f9f7;
          display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
  form {{ background: #fcfcfb; padding: 24px; border-radius: 12px; width: 260px; }}
  input {{ width: 100%; padding: 10px; margin: 8px 0; border-radius: 6px; border: 1px solid #c3c2b7; box-sizing: border-box; }}
  button {{ width: 100%; padding: 10px; border-radius: 6px; border: none; background: #2a78d6; color: white; font-weight: 600; }}
  .error {{ color: #e34948; font-size: 14px; }}
</style>
</head>
<body>
  <form method="post" action="/login">
    <h2>TrackNest</h2>
    {error_html}
    <input type="password" name="password" placeholder="Password" autofocus>
    <button type="submit">Open dashboard</button>
  </form>
</body>
</html>"""


async def handle_login_get(request: web.Request) -> web.Response:
    return web.Response(text=_login_page(), content_type="text/html")


async def handle_login_post(request: web.Request) -> web.Response:
    form = await request.post()
    if not auth.check_password(str(form.get("password", ""))):
        return web.Response(text=_login_page("Wrong password."), content_type="text/html", status=401)
    # Render the dashboard right here instead of redirecting to /dashboard:
    # inside Telegram (web/desktop) the page is often embedded cross-site,
    # where browsers drop the session cookie, so the redirect bounced
    # straight back to /login. The cookie still spares a re-login wherever
    # the browser does keep it.
    response = web.Response(text=render_dashboard_html(build_dashboard_data()), content_type="text/html")
    response.set_cookie(
        _SESSION_COOKIE, auth.create_session_token(),
        max_age=auth.SESSION_TTL_SECONDS, httponly=True, samesite="Lax",
    )
    return response


async def handle_dashboard(request: web.Request) -> web.Response:
    if not auth.verify_session_token(request.cookies.get(_SESSION_COOKIE)):
        raise web.HTTPFound("/login")
    html = render_dashboard_html(build_dashboard_data())
    return web.Response(text=html, content_type="text/html")


async def handle_logout(request: web.Request) -> web.Response:
    response = web.HTTPFound("/login")
    response.del_cookie(_SESSION_COOKIE)
    raise response


def build_app() -> web.Application:
    """Assemble the aiohttp app — routes only, no server lifecycle here (see bot/main.py)."""
    app = web.Application()
    app.router.add_get("/login", handle_login_get)
    app.router.add_post("/login", handle_login_post)
    app.router.add_get("/dashboard", handle_dashboard)
    app.router.add_get("/logout", handle_logout)
    return app
