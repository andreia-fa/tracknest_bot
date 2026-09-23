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
from db import metrics, shopping_list

_SESSION_COOKIE = "tracknest_session"

# Categories beyond this many fold into "Other" rather than stretching the
# list — a 9th row is noise on a phone screen.
_MAX_CATEGORIES = 6


def build_dashboard_data() -> dict:
    """Gather every metric the dashboard shows.

    Pure aside from the DB reads metrics.py itself does — no request/response
    concerns — so it's testable without spinning up the web app.

    Returns:
        Dict with keys: month_label, day, days_in_month, spent, projected,
        budget (dict or None), mix ({essential, luxury, necessity,
        unclassified} in euros), categories (list of {name, total}), trips,
        daily (euros per day), rising, running_low, shopping_list, health,
        daily_cost.
    """
    now = datetime.now(tz=timezone.utc)
    spending = metrics.get_spending_summary(top_n=50)
    pace = metrics.get_month_pace()

    categories = [{"name": c["category"], "total": c["total"]} for c in spending["top_categories"]]
    if len(categories) > _MAX_CATEGORIES:
        rest = categories[_MAX_CATEGORIES - 1:]
        categories = categories[:_MAX_CATEGORIES - 1] + [
            {"name": "Other", "total": sum(c["total"] for c in rest)}
        ]

    return {
        "month_label": now.strftime("%B %Y"),
        "day": pace["days_elapsed"],
        "days_in_month": pace["days_in_month"],
        "spent": spending["total"],
        "projected": pace["projected"],
        "budget": metrics.get_budget_status(),
        "mix": {k: spending[k] for k in ("essential", "luxury", "necessity", "unclassified")},
        "categories": categories,
        "trips": metrics.get_shopping_trips(),
        "daily": metrics.get_daily_spend(),
        "rising": metrics.get_price_trends(top_n=5),
        "running_low": metrics.get_running_low(),
        "shopping_list": shopping_list.get_all_items(),
        "health": metrics.get_inventory_health(),
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
    if data["projected"] is not None:
        forecast = f'On pace for <strong>{_eur(data["projected"])}</strong> by month end'
    else:
        forecast = "Month-end forecast appears from day 5"
    return f"""
    <div class="card hero">
      <p class="label">Spent this month</p>
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
    classified = mix["essential"] + mix["luxury"] + mix["necessity"]
    if not classified:
        return """
      <div class="card kpi empty">
        <div class="label">Treats</div>
        <div class="kpi-note">Answer the bot's "what kind of purchase?" questions to see this.</div>
      </div>"""
    # Share of *all* spend, matching the mix bar below — two different
    # denominators for the same word on one page read as a contradiction.
    pct = mix["luxury"] / data["spent"] * 100 if data["spent"] else 0.0
    return f"""
      <div class="card kpi">
        <div class="label">Treats</div>
        <div class="kpi-main">{pct:.0f}%</div>
        <div class="kpi-note">{_eur(mix["luxury"])} of this month's spend went on treats</div>
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
      <p class="section-sub">You shopped on {spend_days} of {data["day"]} days so far ·
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
        ("Essentials", mix["essential"], "var(--mix-essential)"),
        ("Treats", mix["luxury"], "var(--mix-treats)"),
        ("Same-day", mix["necessity"], "var(--mix-necessity)"),
        ("Not yet classified", mix["unclassified"], "var(--mix-none)"),
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
    peak = categories[0]["total"] or 1.0
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
            "Nothing getting pricier. Once you buy the same item twice, price rises show up here.",
        )
    rows = "".join(
        f'<li class="row"><span class="row-name">{escape(r["name"])}</span>'
        f'<span class="row-val warn">▲ {r["pct_change"]:.0f}%<span class="row-sub">'
        f'{_eur(r["avg_price"])} → {_eur(r["latest_price"])}</span></span></li>'
        for r in rising
    )
    return f'<div class="card"><p class="section-title pad">Price watch</p><ul class="rows">{rows}</ul></div>'


def _to_buy_card(data: dict) -> str:
    low = data["running_low"]
    items = data["shopping_list"]
    if not low and not items:
        return _empty("What to buy", "Your list is empty and nothing's running low.")
    low_rows = "".join(
        f'<li class="row"><span class="row-name">⏳ {escape(r["name"])}</span>'
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
    return f'<div class="card"><p class="section-title pad">What to buy</p><ul class="rows">{low_rows}{list_rows}{more}</ul></div>'


def _attention_card(data: dict) -> str:
    health = data["health"]
    rows = [
        ("Did these run out?", health["checkin_pending"]),
        ("Buy a spare", health["spare_alert_pending"]),
        ("Waiting for your answer", health["unprofiled"]),
    ]
    rows = [(label, names) for label, names in rows if names]
    if not rows:
        return _empty("Needs attention", "✓ All caught up — no open questions from the bot.")
    body = "".join(
        f'<li class="row-block"><div class="row"><span class="row-name">{label}</span>'
        f'<span class="row-val">{len(names)}</span></div>'
        f'<div class="row-sub">{escape(", ".join(names[:4]))}{"…" if len(names) > 4 else ""}</div></li>'
        for label, names in rows
    )
    return f'<div class="card"><p class="section-title pad">Needs attention</p><ul class="rows">{body}</ul></div>'


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
    --mix-essential: #2a78d6; --mix-treats: #eb6834; --mix-necessity: #1baf7a; --mix-none: #c3c2b7;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      color-scheme: dark;
      --page: #0d0d0d; --surface: #1a1a19; --surface-2: #232322;
      --ink: #ffffff; --ink-2: #c3c2b7; --ink-muted: #898781;
      --border: rgba(255,255,255,0.10); --track: #2c2c2a;
      --accent: #199e70; --warn: #fab219;
      --mix-essential: #3987e5; --mix-treats: #d95926; --mix-necessity: #199e70; --mix-none: #52514e;
    }
  }
  * { box-sizing: border-box; }
  body { margin: 0 auto; max-width: 760px; padding: 24px 16px; background: var(--page);
         color: var(--ink); font: 14px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif; }
  header { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 18px; }
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
    .kpi-row .card:first-child { grid-column: 1 / -1; }
    .split { grid-template-columns: 1fr; gap: 0; }
    .hero-value { font-size: 38px; }
    .bar-row { grid-template-columns: 96px 1fr 70px; }
  }
"""


def render_dashboard_html(data: dict) -> str:
    """Render the full dashboard page from build_dashboard_data()'s output."""
    updated = datetime.now(tz=timezone.utc).strftime("%d %b, %H:%M UTC")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TrackNest — {data['month_label']}</title>
<style>{_STYLE}</style>
</head>
<body>
  <header>
    <div class="brand"><span class="brand-mark"></span><h1>TrackNest</h1></div>
    <span class="period">{data['month_label']} · day {data['day']} of {data['days_in_month']}</span>
  </header>
  {_hero(data)}
  <div class="kpi-row">{_budget_kpi(data)}{_treats_kpi(data)}{_list_kpi(data)}</div>
  {_daily_chart(data)}
  {_mix_card(data)}
  {_categories_card(data)}
  <div class="split">{_stores_card(data)}{_prices_card(data)}</div>
  <div class="split">{_to_buy_card(data)}{_attention_card(data)}</div>
  {_daily_cost_card(data)}
  <footer>Live from TrackNest · {updated} · <a href="/logout">Log out</a></footer>
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
