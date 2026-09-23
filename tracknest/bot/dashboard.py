"""Password-gated web dashboard: relative-numbers-only view of the month.

Runs as an aiohttp app inside the same process as the Telegram bot (see
bot/main.py's run loop), reachable only via the Cloudflare Tunnel URL
bot/tunnel.py hands out — never a port published on the host. Every figure
here is a percentage; absolute euro amounts stay in /report. Session auth
is bot.auth's signed cookie, not a server-side session store.
"""

from datetime import datetime, timezone

from aiohttp import web

from bot import auth
from db import metrics

_SESSION_COOKIE = "tracknest_session"

# Mix segment colors are categorical slots 1/2/3 from the dataviz skill's
# validated default palette (references/palette.md) — fixed order, never
# reassigned — baked into render_dashboard_html's --mix-* CSS variables.


def build_dashboard_data() -> dict:
    """Gather every metric the dashboard shows, pre-converted to percentages.

    Pure aside from the DB reads metrics.py itself does — no request/response
    concerns — so it's testable without spinning up the web app.

    Returns:
        Dict with keys: month_abbr, budget_pct (float or None), month_pct,
        mix (list of {label, pct} for Essential/Treats/Necessity, only
        included if there's any classified spend), unclassified_pct (float
        or None), rising (list of {name, pct_change}), running_low (list of
        {name, pct_remaining}).
    """
    spending = metrics.get_spending_summary()
    pace = metrics.get_month_pace()
    budget = metrics.get_budget_status()

    total = spending["total"]
    classified = spending["essential"] + spending["luxury"] + spending["necessity"]
    mix = []
    if classified > 0:
        mix = [
            {"label": "Essential", "pct": spending["essential"] / classified * 100},
            {"label": "Treats", "pct": spending["luxury"] / classified * 100},
            {"label": "Necessity", "pct": spending["necessity"] / classified * 100},
        ]

    running_low = [
        {
            "name": item["name"],
            "pct_remaining": max(0.0, item["days_left"] / item["shelf_life_days"] * 100),
        }
        for item in metrics.get_running_low()
        if item["shelf_life_days"]
    ]

    return {
        "month_abbr": datetime.now(tz=timezone.utc).strftime("%b").upper(),
        "budget_pct": budget["pct"] if budget else None,
        "month_pct": pace["days_elapsed"] / pace["days_in_month"] * 100,
        "mix": mix,
        "unclassified_pct": (spending["unclassified"] / total * 100) if total > 0 else None,
        "rising": [
            {"name": t["name"], "pct_change": t["pct_change"]}
            for t in metrics.get_price_trends()
        ],
        "running_low": running_low,
    }


def _meter(label: str, pct: float | None) -> str:
    """A single labeled progress bar — track + fill, both from the blue ramp."""
    if pct is None:
        return ""
    clamped = max(0.0, min(100.0, pct))
    return f"""
    <div class="meter">
      <div class="meter-label"><span>{label}</span><span>{pct:.0f}%</span></div>
      <div class="meter-track"><div class="meter-fill" style="width:{clamped:.1f}%"></div></div>
    </div>
    """


def _mix_bar(mix: list[dict]) -> str:
    """The Essential/Treats/Necessity split as one stacked horizontal bar + legend."""
    if not mix:
        return "<p class='muted'>Nothing classified yet this month.</p>"
    segments = "".join(
        f'<div class="mix-segment" style="width:{m["pct"]:.1f}%;'
        f'background:var(--mix-{m["label"].lower()})"></div>'
        for m in mix
    )
    legend = "".join(
        f'<div class="legend-item"><span class="swatch" '
        f'style="background:var(--mix-{m["label"].lower()})"></span>'
        f'{m["label"]} {m["pct"]:.0f}%</div>'
        for m in mix
    )
    return f'<div class="mix-bar">{segments}</div><div class="legend">{legend}</div>'


def _rising_bars(rising: list[dict]) -> str:
    if not rising:
        return "<p class='muted'>Nothing creeping up right now.</p>"
    max_pct = max(r["pct_change"] for r in rising) or 1.0
    rows = "".join(
        f'<div class="bar-row"><span class="bar-name">⚠️ {r["name"]}</span>'
        f'<div class="bar-track"><div class="bar-fill warning" '
        f'style="width:{max(0.0, r["pct_change"]) / max_pct * 100:.1f}%"></div></div>'
        f'<span class="bar-value">+{r["pct_change"]:.0f}%</span></div>'
        for r in rising
    )
    return rows


def _running_low_bars(running_low: list[dict]) -> str:
    if not running_low:
        return "<p class='muted'>Nothing running low.</p>"
    rows = "".join(
        f'<div class="bar-row"><span class="bar-name">{r["name"]}</span>'
        f'<div class="bar-track"><div class="bar-fill" '
        f'style="width:{r["pct_remaining"]:.1f}%"></div></div>'
        f'<span class="bar-value">{r["pct_remaining"]:.0f}% left</span></div>'
        for r in running_low
    )
    return rows


def render_dashboard_html(data: dict) -> str:
    """Render the full dashboard page from build_dashboard_data()'s output."""
    unclassified = (
        f'<p class="muted">{data["unclassified_pct"]:.0f}% of spend not yet classified.</p>'
        if data["unclassified_pct"]
        else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TrackNest — {data['month_abbr']}</title>
<style>
  :root {{
    color-scheme: light;
    --surface-1: #fcfcfb; --page: #f9f9f7; --text-primary: #0b0b0b;
    --text-secondary: #52514e; --muted: #898781; --gridline: #e1e0d9;
    --blue-400: #3987e5; --blue-200: #9ec5f4;
    --warning: #fab219;
    --mix-essential: #2a78d6; --mix-treats: #eb6834; --mix-necessity: #1baf7a;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:where(:not([data-theme="light"])) {{
      color-scheme: dark;
      --surface-1: #1a1a19; --page: #0d0d0d; --text-primary: #ffffff;
      --text-secondary: #c3c2b7; --muted: #898781; --gridline: #2c2c2a;
      --blue-400: #3987e5; --blue-200: #184f95;
      --mix-essential: #3987e5; --mix-treats: #d95926; --mix-necessity: #199e70;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 16px; background: var(--page); color: var(--text-primary);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  }}
  .card {{
    background: var(--surface-1); border-radius: 12px; padding: 20px;
    margin-bottom: 16px; max-width: 480px; margin-left: auto; margin-right: auto;
  }}
  h1 {{ font-size: 18px; margin: 0 0 16px; }}
  h2 {{ font-size: 13px; text-transform: uppercase; letter-spacing: 0.04em;
        color: var(--text-secondary); margin: 0 0 12px; }}
  .muted {{ color: var(--muted); font-size: 14px; margin: 0; }}
  .meter {{ margin-bottom: 14px; }}
  .meter:last-child {{ margin-bottom: 0; }}
  .meter-label {{ display: flex; justify-content: space-between; font-size: 14px;
                   color: var(--text-secondary); margin-bottom: 4px; }}
  .meter-track {{ height: 20px; border-radius: 4px; background: var(--blue-200); }}
  .meter-fill {{ height: 100%; border-radius: 4px; background: var(--blue-400); }}
  .mix-bar {{ display: flex; height: 24px; border-radius: 4px; overflow: hidden; gap: 2px; }}
  .mix-segment {{ height: 100%; }}
  .legend {{ display: flex; gap: 16px; margin-top: 10px; flex-wrap: wrap; }}
  .legend-item {{ font-size: 13px; color: var(--text-secondary); display: flex; align-items: center; gap: 6px; }}
  .swatch {{ width: 10px; height: 10px; border-radius: 2px; display: inline-block; }}
  .bar-row {{ display: flex; align-items: center; gap: 10px; margin-bottom: 10px; font-size: 13px; }}
  .bar-row:last-child {{ margin-bottom: 0; }}
  .bar-name {{ flex: 0 0 90px; color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .bar-track {{ flex: 1; height: 16px; border-radius: 4px; background: var(--gridline); }}
  .bar-fill {{ height: 100%; border-radius: 4px; background: var(--blue-400); }}
  .bar-fill.warning {{ background: var(--warning); }}
  .bar-value {{ flex: 0 0 56px; text-align: right; color: var(--text-secondary); }}
</style>
</head>
<body>
  <div class="card">
    <h1>📊 TrackNest — {data['month_abbr']}</h1>
    {_meter("Month elapsed", data['month_pct'])}
    {_meter("Budget used", data['budget_pct'])}
  </div>
  <div class="card">
    <h2>Spending mix</h2>
    {_mix_bar(data['mix'])}
    {unclassified}
  </div>
  <div class="card">
    <h2>📈 Rising</h2>
    {_rising_bars(data['rising'])}
  </div>
  <div class="card">
    <h2>⏳ Running low</h2>
    {_running_low_bars(data['running_low'])}
  </div>
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
