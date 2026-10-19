"""/dashboard sends the fixed private dashboard address from DASHBOARD_URL."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot import main


def _update():
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    return update


@pytest.mark.asyncio
async def test_dashboard_buttons_open_the_fixed_address():
    update = _update()
    with patch.dict("os.environ", {"DASHBOARD_URL": "https://tracknest.example.ts.net/"}):
        await main.dashboard_cmd(update, MagicMock())
    keyboard = update.message.reply_text.call_args.kwargs["reply_markup"].inline_keyboard
    assert keyboard[0][0].web_app.url == "https://tracknest.example.ts.net/login"
    assert keyboard[1][0].url == "https://tracknest.example.ts.net/login"


@pytest.mark.asyncio
async def test_dashboard_without_an_address_says_so(monkeypatch):
    monkeypatch.delenv("DASHBOARD_URL", raising=False)
    update = _update()
    await main.dashboard_cmd(update, MagicMock())
    update.message.reply_text.assert_awaited_once_with("The dashboard isn't set up on this server.")
