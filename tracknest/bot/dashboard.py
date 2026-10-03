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
from bot.categorize import CATEGORY_NAMES
from db import crud, metrics, shopping_list

_SESSION_COOKIE = "tracknest_session"

# Categories beyond this many fold into "Other". Set to the full category
# list: a cap of 6 folded €55.82 into an "Other" bigger than any real row,
# hiding exactly where the money went — and 12 labelled one-colour rows
# still read fine on a phone.
_MAX_CATEGORIES = len(CATEGORY_NAMES)


def build_month_data(year: int, month: int) -> dict:
    """Gather the metrics that belong to one calendar month.

    Returns:
        Dict with keys: key ("YYYY-MM"), is_current, month_label, day,
        days_in_month, spent, projected, budget (dict or None), mix ({need,
        treat, unknown} in euros), categories (list of {name, total}), trips
        and daily (euros per day).
    """
    now = datetime.now(tz=timezone.utc)
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
        "is_current": (year, month) == (now.year, now.month),
        "month_label": datetime(year, month, 1).strftime("%B %Y"),
        "day": pace["days_elapsed"],
        "days_in_month": pace["days_in_month"],
        "spent": spending["total"],
        "projected": pace["projected"],
        "budget": metrics.get_budget_status(year, month),
        "mix": {k: spending[k] for k in ("need", "treat", "unknown")},
        "categories": categories,
        "trips": metrics.get_shopping_trips(year, month),
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


def _empty(title: str, body: str) -> str:
    return f'<div class="card empty-card"><strong>{title}</strong>{body}</div>'


def _hero(data: dict) -> str:
    trips = data["trips"]
    if trips["count"]:
        sub = (
            f'{trips["count"]} shopping trip{"s" if trips["count"] != 1 else ""} · '
            f'average basket {_eur(trips["avg_basket"])}'
        )
    else:
        sub = "No purchases logged yet this month"
    if trips.get("not_shopping"):
        sub += f' · includes {_eur(trips["not_shopping"])} leisure (not a shopping trip)'
    current = data.get("is_current", True)
    if not current:
        forecast = "Month closed — this is the final total"
    elif data["projected"] is not None:
        forecast = f'On pace for <strong>{_eur(data["projected"])}</strong> by month end'
    else:
        forecast = "Month-end forecast appears from day 5"
    label = "Spent this month" if current else f'Spent in {data["month_label"].split()[0]}'
    return f"""
    <div class="card hero">
      <p class="label">{label}</p>
      <div class="hero-value">{_eur(data["spent"])}</div>
      <p class="hero-sub">{sub}</p>
      <p class="hero-sub">{forecast}</p>
    </div>"""


def _budget_kpi(data: dict) -> str:
    budget = data["budget"]
    month_pct = data["day"] / data["days_in_month"] * 100
    if not budget:
        return """
      <div class="card kpi empty">
        <div class="label">Budget</div>
        <div class="kpi-note">No budget yet — set one with <code>/set_budget</code> in Telegram.</div>
      </div>"""
    pct = budget["pct"]
    ahead = pct - month_pct
    if ahead > 5:
        note = f"⚠️ {ahead:.0f} pts ahead of the calendar"
    else:
        note = "✓ Within pace"
    return f"""
      <div class="card kpi">
        <div class="label">Budget</div>
        <div class="kpi-main">{pct:.0f}%<span class="kpi-of"> of {_eur(budget["budget"])}</span></div>
        <div class="meter" title="Tick = how far through the month we are ({month_pct:.0f}%)">
          <div class="meter-fill" style="width:{min(pct, 100):.1f}%"></div>
          <div class="meter-tick" style="left:{month_pct:.1f}%"></div>
        </div>
        <div class="kpi-note">{note}</div>
      </div>"""


def _treats_kpi(data: dict) -> str:
    mix = data["mix"]
    classified = mix["need"] + mix["treat"]
    if not classified:
        return """
      <div class="card kpi empty">
        <div class="label">Treats</div>
        <div class="kpi-note">Answer the bot's "treat or need?" questions to see this.</div>
      </div>"""
    # Share of *all* spend, matching the mix bar below — two different
    # denominators for the same word on one page read as a contradiction.
    pct = mix["treat"] / data["spent"] * 100 if data["spent"] else 0.0
    return f"""
      <div class="card kpi">
        <div class="label">Treats</div>
        <div class="kpi-main">{pct:.0f}%</div>
        <div class="kpi-note">{_eur(mix["treat"])} of this month's spend went on treats</div>
      </div>"""


def _list_kpi(data: dict) -> str:
    count = len(data["shopping_list"])
    low = len(data["running_low"])
    low_note = f"{low} running low this week" if low else "Nothing running low this week"
    return f"""
      <div class="card kpi">
        <div class="label">Shopping list</div>
        <div class="kpi-main">{count}<span class="kpi-of"> item{"s" if count != 1 else ""}</span></div>
        <div class="kpi-note">{low_note}</div>
      </div>"""


def _daily_chart(data: dict) -> str:
    daily = data["daily"]
    if not any(daily):
        return ""
    width, height, gap = 600, 120, 2
    bar_w = width / len(daily) - gap
    peak = max(daily)
    bars = []
    for i, value in enumerate(daily):
        day = i + 1
        x = i * (width / len(daily))
        if value:
            h = max(3.0, value / peak * (height - 4))
            cls = "bar"
        else:
            h, cls = 3.0, "bar zero" if day <= data["day"] else "bar future"
        bars.append(
            f'<rect class="{cls}" x="{x:.1f}" y="{height - h:.1f}" width="{bar_w:.1f}" '
            f'height="{h:.1f}" rx="2"><title>{day} {data["month_label"].split()[0][:3]}: '
            f'{_eur(value)}</title></rect>'
        )
    busiest = max(range(len(daily)), key=lambda i: daily[i]) + 1
    spend_days = sum(1 for v in daily if v)
    return f"""
    <div class="card section">
      <p class="section-title">Spend by day</p>
      <p class="section-sub">You shopped on {spend_days} of {data["day"]} days{" so far" if data.get("is_current", True) else ""} ·
        biggest day was the {busiest}{_ordinal(busiest)} ({_eur(peak)})</p>
      <svg class="daily" viewBox="0 0 {width} {height}" preserveAspectRatio="none" role="img"
           aria-label="Spend per day this month">{"".join(bars)}</svg>
      <div class="axis"><span>1</span><span>{len(daily) // 2}</span><span>{len(daily)}</span></div>
    </div>"""


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _mix_card(data: dict) -> str:
    mix = data["mix"]
    total = sum(mix.values())
    if not total:
        return ""
    parts = [
        ("Needs", mix["need"], "var(--mix-need)"),
        ("Treats", mix["treat"], "var(--mix-treats)"),
        ("Not sorted yet", mix["unknown"], "var(--mix-none)"),
    ]
    parts = [p for p in parts if p[1] > 0]
    segments = "".join(
        f'<div style="width:{v / total * 100:.2f}%;background:{c}" '
        f'title="{label}: {_eur(v)} ({v / total * 100:.0f}%)"></div>'
        for label, v, c in parts
    )
    legend = "".join(
        f'<div class="legend-item"><span class="swatch" style="background:{c}"></span>'
        f'{label} <strong>{v / total * 100:.0f}%</strong> <span class="muted">{_eur(v)}</span></div>'
        for label, v, c in parts
    )
    return f"""
    <div class="card section">
      <p class="section-title">What kind of spending</p>
      <div class="mix-bar">{segments}</div>
      <div class="legend">{legend}</div>
    </div>"""


def _categories_card(data: dict) -> str:
    categories = data["categories"]
    if not categories:
        return ""
    total = sum(c["total"] for c in categories) or 1.0
    peak = max(c["total"] for c in categories) or 1.0
    rows = "".join(
        f'<div class="bar-row"><span class="bar-name">{escape(c["name"])}</span>'
        f'<div class="bar-track"><div class="bar-fill" style="width:{c["total"] / peak * 100:.1f}%"></div></div>'
        f'<span class="bar-value">{_eur(c["total"])}<span class="bar-pct">{c["total"] / total * 100:.0f}%</span></span></div>'
        for c in categories
    )
    return f"""
    <div class="card section">
      <p class="section-title">Spending by category</p>
      {rows}
    </div>"""


def _stores_card(data: dict) -> str:
    stores = data["trips"]["by_store"]
    if not stores:
        return _empty("Where you shop", "Send a receipt photo and your stores show up here.")
    rows = "".join(
        f'<li class="row"><span class="row-name">{escape(s["store"] or "Typed in by hand")}</span>'
        f'<span class="row-val">{_eur(s["total"])}<span class="row-sub">{s["trips"]} trip'
        f'{"s" if s["trips"] != 1 else ""} · {_eur(s["total"] / s["trips"])} avg</span></span></li>'
        for s in stores
    )
    return f'<div class="card"><p class="section-title pad">Where you shop</p><ul class="rows">{rows}</ul></div>'


def _prices_card(data: dict) -> str:
    rising = data["rising"]
    if not rising:
        return _empty(
            "Price watch",
            "Nothing getting pricier. Once you buy the same item twice at the same store, rises show up here.",
        )
    rows = "".join(
        f'<li class="row"><span class="row-name">{escape(r["name"])}</span>'
        f'<span class="row-val warn">▲ {r["pct_change"]:.0f}%<span class="row-sub">'
        f'{escape(r["store"]) + ": " if r.get("store") else ""}'
        f'{_eur(r["avg_price"])} → {_eur(r["latest_price"])}</span></span></li>'
        for r in rising
    )
    return f'<div class="card"><p class="section-title pad">Price watch</p><ul class="rows">{rows}</ul></div>'


def _readable_names(names: set[str]) -> dict:
    """Map item names to their product ("cheese") when known — receipt names like "LEERDAMMER CAR." read badly."""
    return {n: (crud.get_item(n) or {}).get("product") or n for n in names}


def _to_buy_card(data: dict) -> str:
    low = data["running_low"]
    items = data["shopping_list"]
    readable = data.get("readable", {})
    spares = sorted({readable.get(n, n) for n in data["health"]["spare_alert_pending"]})
    if not low and not items and not spares:
        return _empty("What to buy", "Your list is empty and nothing's running low.")
    spare_rows = "".join(
        f'<li class="row"><span class="row-name">🔁 {escape(p)}</span>'
        f'<span class="row-val muted">keep a spare</span></li>'
        for p in spares
    )
    low_rows = "".join(
        f'<li class="row"><span class="row-name">⏳ {escape(readable.get(r["name"], r["name"]))}</span>'
        f'<span class="row-val">{"today" if r["days_left"] == 0 else f"{r['days_left']}d left"}</span></li>'
        for r in low
    )
    shown = items[:8]
    list_rows = "".join(
        f'<li class="row"><span class="row-name">{escape(i["name"])}</span>'
        f'<span class="row-val muted">{i["quantity"]}×</span></li>'
        for i in shown
    )
    more = (
        f'<li class="row muted">+ {len(items) - len(shown)} more — <code>/list</code> in Telegram</li>'
        if len(items) > len(shown) else ""
    )
    return (f'<div class="card"><p class="section-title pad">What to buy</p>'
            f'<ul class="rows">{low_rows}{spare_rows}{list_rows}{more}</ul></div>')


def _attention_card(data: dict) -> str:
    """What the bot is waiting on you for — open questions and "did it run out?" check-ins."""
    health = data["health"]
    groups = [
        ("Did these run out?", "Answer in Telegram: yes or no", health["checkin_pending"]),
        ("Questions waiting for you", "The bot asks them one at a time in Telegram", health["unprofiled"]),
    ]
    groups = [g for g in groups if g[2]]
    readable = data.get("readable", {})
    if not groups:
        return _empty("Needs attention", "✓ All caught up — nothing waiting for you.")
    body = "".join(
        f'<div class="attn"><div class="attn-head"><span>{label}</span><span class="count">{len(names)}</span></div>'
        f'<div class="chips">{"".join(f"<span class=chip>{escape(readable.get(n, n))}</span>" for n in names)}</div>'
        f'<p class="attn-hint">{hint}</p></div>'
        for label, hint, names in groups
    )
    return f'<div class="card"><p class="section-title pad">Needs attention</p><div class="attn-body">{body}</div></div>'


def _daily_cost_card(data: dict) -> str:
    cost = data["daily_cost"]
    if not cost["items"]:
        if not cost["tracked"]:
            return ""
        return _empty(
            "Cost per day you own it",
            f"Unlocks once an item's been bought twice — 0 of {cost['tracked']} items ready yet. "
            "It separates expensive to buy from expensive to keep around.",
        )
    peak = cost["items"][0]["cost_per_day"] or 1.0
    rows = "".join(
        f'<div class="bar-row"><span class="bar-name">{escape(c["name"])}</span>'
        f'<div class="bar-track"><div class="bar-fill" style="width:{c["cost_per_day"] / peak * 100:.1f}%"></div></div>'
        f'<span class="bar-value">{_eur(c["cost_per_day"])}<span class="bar-pct">per day</span></span></div>'
        for c in cost["items"]
    )
    return f"""
    <div class="card section">
      <p class="section-title">Cost per day you own it</p>
      <p class="section-sub">Price ÷ how long it lasts — what's expensive to keep around, not just to buy</p>
      {rows}
    </div>"""


_STYLE = """
  :root {
    color-scheme: light;
    --page: #f9f9f7; --surface: #fcfcfb; --surface-2: #f2f1ec;
    --ink: #0b0b0b; --ink-2: #52514e; --ink-muted: #898781;
    --border: rgba(11,11,11,0.10); --track: #eceae2;
    --accent: #1baf7a; --warn: #b87700;
    --mix-need: #2a78d6; --mix-treats: #eb6834; --mix-none: #c3c2b7;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      color-scheme: dark;
      --page: #0d0d0d; --surface: #1a1a19; --surface-2: #232322;
      --ink: #ffffff; --ink-2: #c3c2b7; --ink-muted: #898781;
      --border: rgba(255,255,255,0.10); --track: #2c2c2a;
      --accent: #199e70; --warn: #fab219;
      --mix-need: #3987e5; --mix-treats: #d95926; --mix-none: #52514e;
    }
  }
  * { box-sizing: border-box; }
  body { margin: 0 auto; max-width: 760px; padding: 24px 16px; background: var(--page);
         color: var(--ink); font: 14px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif; }
  header { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 14px; }
  .brand { display: flex; align-items: center; gap: 8px; }
  .brand-mark { width: 10px; height: 10px; border-radius: 3px; background: var(--accent); }
  h1 { font-size: 19px; font-weight: 800; margin: 0; }
  .period, .muted { color: var(--ink-muted); }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 14px; margin-bottom: 14px; }
  .label { font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em;
           color: var(--ink-muted); margin: 0 0 6px; }
  .hero { padding: 22px; }
  .hero-value { font-size: 44px; font-weight: 700; line-height: 1; font-variant-numeric: tabular-nums; }
  .hero-sub { font-size: 13px; color: var(--ink-2); margin: 8px 0 0; }
  .kpi-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 14px; }
  .kpi-row .card { margin: 0; }
  .kpi-row.two { grid-template-columns: 1fr 1fr; }
  .month-radio { position: absolute; opacity: 0; pointer-events: none; }
  .month { display: none; }
  .tabs { display: flex; gap: 4px; padding: 3px; border-radius: 10px; background: var(--surface-2); }
  .tabs label { padding: 4px 12px; border-radius: 8px; border: 1px solid transparent; font-size: 13px;
                color: var(--ink-2); cursor: pointer; }
  .period { margin: 0 0 10px; font-size: 13px; }
  .cards > .card { margin-bottom: 14px; }
  .attn-body { padding: 10px 18px 16px; display: flex; flex-direction: column; gap: 14px; }
  .attn-head { display: flex; justify-content: space-between; font-size: 13px; color: var(--ink-2); }
  .count { font-weight: 700; color: var(--ink); font-variant-numeric: tabular-nums; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
  .chip { font-size: 12px; padding: 3px 9px; border-radius: 999px; background: var(--surface-2);
          border: 1px solid var(--border); color: var(--ink-2); max-width: 100%;
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .attn-hint { font-size: 11px; color: var(--ink-muted); margin: 6px 0 0; }
  @media (min-width: 900px) {
    body { max-width: 1100px; }
    .cards { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; align-items: start; }
    .cards > .card { margin: 0; }
    .cards { margin-bottom: 14px; }
  }
  .shared, .share-note { display: none; }
  #share:checked ~ .month .private, #share:checked ~ .right-now { display: none; }
  #share:checked ~ .month .shared { display: block; }
  #share:checked ~ .share-note { display: block; }
  #share:checked ~ header .share-toggle { background: var(--ink); color: var(--page); }
  .share-note { text-align: center; font-size: 12px; color: var(--ink-muted); margin: 18px 0 6px; }
  .header-tools { display: flex; align-items: center; gap: 8px; }
  .tool { border: 1px solid var(--border); background: var(--surface); color: var(--ink-2); border-radius: 8px;
          padding: 4px 9px; font-size: 14px; cursor: pointer; }
  .tool[hidden] { display: none; }
  .rows.tight { padding: 6px 0 4px; gap: 6px; }
  .group-title { font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em;
                 color: var(--ink-muted); margin: 22px 0 10px; }
  .kpi { padding: 16px; display: flex; flex-direction: column; gap: 8px; }
  .kpi.empty, .empty-card { border-style: dashed; }
  .kpi-main { font-size: 24px; font-weight: 700; font-variant-numeric: tabular-nums; }
  .kpi-of { font-size: 13px; font-weight: 500; color: var(--ink-muted); }
  .kpi-note { font-size: 12px; color: var(--ink-2); }
  .meter { position: relative; height: 8px; border-radius: 4px; background: var(--track); }
  .meter-fill { height: 100%; border-radius: 4px; background: var(--accent); }
  .meter-tick { position: absolute; top: -3px; width: 2px; height: 14px; background: var(--ink); }
  .section { padding: 18px 18px 12px; }
  .section-title { font-size: 14px; font-weight: 700; margin: 0 0 10px; }
  .section-title.pad { padding: 18px 18px 0; margin: 0; }
  .section-sub { font-size: 12px; color: var(--ink-2); margin: -4px 0 12px; }
  svg.daily { width: 100%; height: 120px; display: block; }
  .bar { fill: var(--accent); } .bar:hover { opacity: 0.75; }
  .bar.zero { fill: var(--track); } .bar.future { fill: var(--track); opacity: 0.4; }
  .axis { display: flex; justify-content: space-between; font-size: 11px; color: var(--ink-muted); margin-top: 4px; }
  .mix-bar { display: flex; gap: 2px; height: 14px; border-radius: 4px; overflow: hidden; }
  .legend { display: flex; flex-wrap: wrap; gap: 6px 16px; margin: 10px 0 4px; font-size: 13px; color: var(--ink-2); }
  .legend-item { display: flex; align-items: center; gap: 6px; }
  .swatch { width: 10px; height: 10px; border-radius: 2px; }
  .bar-row { display: grid; grid-template-columns: 120px 1fr 76px; align-items: center; gap: 10px; padding: 6px 0; }
  .bar-name { font-size: 13px; color: var(--ink-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .bar-track { height: 12px; border-radius: 4px; background: var(--track); }
  .bar-fill { height: 100%; border-radius: 4px; background: var(--accent); }
  .bar-value { font-size: 13px; font-weight: 600; text-align: right; font-variant-numeric: tabular-nums; }
  .bar-pct { display: block; font-size: 11px; font-weight: 400; color: var(--ink-muted); }
  .split { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  .split .card { margin-bottom: 14px; }
  .rows { list-style: none; margin: 0; padding: 10px 18px 16px; display: flex; flex-direction: column; gap: 10px; }
  .row { display: flex; justify-content: space-between; gap: 10px; font-size: 13px; }
  .row-name { color: var(--ink-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .row-val { font-weight: 700; text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
  .row-val.warn { color: var(--warn); }
  .row-sub { display: block; font-size: 11px; font-weight: 400; color: var(--ink-muted); }
  .empty-card { padding: 18px; font-size: 13px; color: var(--ink-2); }
  .empty-card strong { display: block; color: var(--ink); margin-bottom: 4px; }
  code { background: var(--surface-2); border-radius: 4px; padding: 1px 5px; font-size: 11px; }
  footer { text-align: center; font-size: 12px; color: var(--ink-muted); padding-top: 6px; }
  footer a { color: var(--ink-muted); }
  @media (max-width: 560px) {
    .kpi-row { grid-template-columns: 1fr 1fr; }
    .kpi-row:not(.two) .card:first-child { grid-column: 1 / -1; }
    .split { grid-template-columns: 1fr; gap: 0; }
    .hero-value { font-size: 38px; }
    .bar-row { grid-template-columns: 96px 1fr 70px; }
  }
"""


def _pct_bars(rows: list[tuple[str, float, str]]) -> str:
    """Labelled one-colour bars from (name, percent, note) rows, scaled to the largest — no amounts."""
    peak = max((pct for _, pct, _ in rows), default=0) or 1.0
    return "".join(
        f'<div class="bar-row"><span class="bar-name">{escape(name)}</span>'
        f'<div class="bar-track"><div class="bar-fill" style="width:{pct / peak * 100:.1f}%"></div></div>'
        f'<span class="bar-value">{pct:.0f}%<span class="bar-pct">{escape(note)}</span></span></div>'
        for name, pct, note in rows
    )


def _share_view(month: dict, previous: dict | None = None) -> str:
    """One month in relative terms only, for a screenshot to share with friends.

    Built separately from the private view rather than by hiding its euro
    figures, so no amount is in this part of the page at all, not even
    hidden. Store and category names are fine to share (the user's call).
    """
    total = month["spent"] or 0.0
    trips = month["trips"]
    days = month["daily"]
    shopped = sum(1 for v in days if v)
    budget = month["budget"]
    budget_line = f'<p class="hero-sub">{budget["pct"]:.0f}% of the monthly budget used</p>' if budget else ""
    hero = f"""
    <div class="card hero">
      <p class="label">Shopping trips</p>
      <div class="hero-value">{trips["count"]}</div>
      <p class="hero-sub">on {shopped} of {month["day"]} days</p>{budget_line}
    </div>"""
    if not total:
        return f'<p class="period">{escape(month["month_label"])}</p>{hero}'

    peak = max(days) or 1.0
    width, height = 600, 90
    bars = "".join(
        f'<rect class="{"bar" if v else "bar zero"}" x="{i * width / len(days):.1f}" '
        f'y="{height - max(3.0, v / peak * (height - 4)):.1f}" width="{width / len(days) - 2:.1f}" '
        f'height="{max(3.0, v / peak * (height - 4)):.1f}" rx="2"><title>Day {i + 1}: '
        f'{v / total * 100:.0f}% of the month</title></rect>'
        for i, v in enumerate(days)
    )
    mix = month["mix"]
    mix_parts = [(label, mix[key] / total * 100, color) for label, key, color in (
        ("Needs", "need", "var(--mix-need)"), ("Treats", "treat", "var(--mix-treats)"),
        ("Not sorted yet", "unknown", "var(--mix-none)")) if mix[key]]
    segments = "".join(f'<div style="width:{pct:.2f}%;background:{c}" title="{label}: {pct:.0f}%"></div>'
                       for label, pct, c in mix_parts)
    legend = "".join(f'<div class="legend-item"><span class="swatch" style="background:{c}"></span>'
                     f'{label} <strong>{pct:.0f}%</strong></div>' for label, pct, c in mix_parts)
    categories = _pct_bars([(c["name"], c["total"] / total * 100, "") for c in month["categories"]])
    stores = _pct_bars([
        (st["store"] or "Typed in by hand", st["total"] / total * 100, f'{st["trips"]} trip{"s" if st["trips"] != 1 else ""}')
        for st in trips["by_store"]
    ])
    return f"""
    <p class="period">{escape(month["month_label"])}</p>
    {hero}
    <div class="cards">
      {_trips_card(month, previous, share=True)}
      <div class="card section"><p class="section-title">When the shopping happened</p>
        <svg class="daily" viewBox="0 0 {width} {height}" preserveAspectRatio="none" role="img"
             aria-label="Share of the month's spending per day">{bars}</svg>
        <div class="axis"><span>1</span><span>{len(days) // 2}</span><span>{len(days)}</span></div></div>
      <div class="card section"><p class="section-title">What kind of spending</p>
        <div class="mix-bar">{segments}</div><div class="legend">{legend}</div></div>
      <div class="card section"><p class="section-title">Spending by category</p>{categories}</div>
      <div class="card section"><p class="section-title">Where the shopping happens</p>{stores}</div>
    </div>"""


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


def _trips_per_week(month: dict) -> float | None:
    """Trips per week since the month's first purchase — tracking may start mid-month, so not since day 1."""
    first = next((i for i, v in enumerate(month["daily"]) if v), None)
    if first is None or not month["trips"]["count"]:
        return None
    return month["trips"]["count"] / (month["day"] - first) * 7


def _trips_card(month: dict, previous: dict | None, share: bool = False) -> str:
    """How often the shopping happened — the goal is fewer trips — and which ones were small top-ups.

    share leaves out every amount (the top-up list shows days and stores only).
    """
    trips = month["trips"]
    if not trips["count"]:
        return ""
    rate = _trips_per_week(month)
    before = _trips_per_week(previous) if previous else None
    if rate is not None and before:
        change = (rate - before) / before * 100
        arrow = "✓ fewer" if change <= -5 else "more" if change >= 5 else "about the same"
        compare = (f'<p class="section-sub">{rate:.1f} trips a week — {arrow} than '
                   f'{previous["month_label"].split()[0]} ({before:.1f} a week)</p>')
    else:
        compare = f'<p class="section-sub">{rate:.1f} trips a week</p>' if rate else ""
    top_ups = trips.get("top_ups", [])
    if top_ups:
        rows = "".join(
            f'<li class="row"><span class="row-name">{datetime.fromisoformat(t["day"]).strftime("%-d %b")} · '
            f'{escape(t["store"] or "typed in")}</span>'
            + ("" if share else f'<span class="row-val muted">{_eur(t["total"])}</span>') + "</li>"
            for t in top_ups
        )
        total = "" if share else f' · {_eur(sum(t["total"] for t in top_ups))} together'
        top = (f'<p class="attn-head"><span>{len(top_ups)} of {trips["count"]} were small top-ups'
               f' (under €5){total}</span></p><ul class="rows tight">{rows}</ul>'
               f'<p class="attn-hint">These are the ones a single bigger shop can absorb — '
               f'"What to buy" shows what runs out when.</p>')
        if share:
            top = top.replace(" (under €5)", "")
    else:
        top = '<p class="attn-hint">No small top-up trips — every trip was a real shop. 👏</p>'
    return f"""
    <div class="card section">
      <p class="section-title">Shopping trips: {trips["count"]}</p>
      {compare}
      {top}
    </div>"""


def _month_view(month: dict, previous: dict | None = None) -> str:
    """Everything on the page that belongs to one month."""
    if month.get("is_current", True):
        period = f'{month["month_label"]} · day {month["day"]} of {month["days_in_month"]}'
        kpis = f'<div class="kpi-row">{_budget_kpi(month)}{_treats_kpi(month)}{_list_kpi(month)}</div>'
    else:
        period = f'{month["month_label"]} · closed'
        kpis = f'<div class="kpi-row two">{_budget_kpi(month)}{_treats_kpi(month)}</div>'
    return f"""
    <p class="period">{period}</p>
    {_hero(month)}
    {kpis}
    <div class="cards">
      {_trips_card(month, previous)}
      {_daily_chart(month)}
      {_mix_card(month)}
      {_categories_card(month)}
      {_stores_card(month)}
    </div>"""


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
    dropped, and a reload would bounce back to the login.
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
    # A month view also shows "right now" figures (the shopping-list card), which live on data.
    views = "".join(
        f'<section class="month m{i}"><div class="private">{_month_view({**data, **m}, prev)}</div>'
        f'<div class="shared">{_share_view(m, prev)}</div></section>'
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
  <p class="group-title right-now">Right now</p>
  <div class="cards right-now">
    {_to_buy_card(data)}
    {_attention_card(data)}
    {_prices_card(data)}
    {_daily_cost_card(data)}
  </div>
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
