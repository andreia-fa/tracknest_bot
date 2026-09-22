from unittest.mock import patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot import dashboard


@pytest.mark.asyncio
async def test_dashboard_redirects_to_login_without_session():
    async with TestClient(TestServer(dashboard.build_app())) as client:
        resp = await client.get("/dashboard", allow_redirects=False)
        assert resp.status == 302
        assert resp.headers["Location"] == "/login"


@pytest.mark.asyncio
async def test_login_wrong_password_shows_error_and_no_cookie():
    async with TestClient(TestServer(dashboard.build_app())) as client:
        resp = await client.post("/login", data={"password": "nope"})
        assert resp.status == 401
        assert "Wrong password" in await resp.text()
        assert dashboard._SESSION_COOKIE not in resp.cookies


@pytest.mark.asyncio
@patch("bot.dashboard.build_dashboard_data")
async def test_login_correct_password_grants_dashboard_access(mock_build_data):
    mock_build_data.return_value = {
        "month_abbr": "SEP", "budget_pct": None, "month_pct": 10.0,
        "mix": [], "unclassified_pct": None, "rising": [], "running_low": [],
    }
    async with TestClient(TestServer(dashboard.build_app())) as client:
        login_resp = await client.post(
            "/login", data={"password": "dummy_password_for_tests"}, allow_redirects=False
        )
        assert login_resp.status == 302
        assert dashboard._SESSION_COOKIE in login_resp.cookies

        dashboard_resp = await client.get("/dashboard")
        assert dashboard_resp.status == 200
        assert "TrackNest" in await dashboard_resp.text()


@pytest.mark.asyncio
async def test_logout_clears_session_and_redirects():
    async with TestClient(TestServer(dashboard.build_app())) as client:
        client.session.cookie_jar.update_cookies({dashboard._SESSION_COOKIE: "whatever"})
        resp = await client.get("/logout", allow_redirects=False)
        assert resp.status == 302
        assert resp.headers["Location"] == "/login"
