import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.tunnel import CloudflareTunnel


class _FakeStderr:
    """Async-iterable stand-in for a subprocess's stderr stream."""

    def __init__(self, lines: list[bytes]):
        self._lines = lines

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


def _fake_process(stderr_lines):
    process = MagicMock()
    process.stderr = _FakeStderr(stderr_lines)
    process.returncode = None
    process.terminate = MagicMock()
    process.wait = AsyncMock()
    return process


@pytest.mark.asyncio
@patch("bot.tunnel.asyncio.create_subprocess_exec", new_callable=AsyncMock)
async def test_start_launches_cloudflared_with_local_port(mock_create):
    mock_create.return_value = _fake_process([])
    tunnel = CloudflareTunnel(local_port=8080)
    await tunnel.start()
    mock_create.assert_awaited_once_with(
        "cloudflared", "tunnel", "--url", "http://localhost:8080",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )


@pytest.mark.asyncio
@patch("bot.tunnel.asyncio.create_subprocess_exec", new_callable=AsyncMock)
async def test_get_url_returns_parsed_trycloudflare_url(mock_create):
    mock_create.return_value = _fake_process([
        b"some other cloudflared log line\n",
        b"2026-09-23T12:00:00Z INF |  https://random-words-here.trycloudflare.com  |\n",
    ])
    tunnel = CloudflareTunnel(local_port=8080)
    await tunnel.start()
    url = await tunnel.get_url()
    assert url == "https://random-words-here.trycloudflare.com"


@pytest.mark.asyncio
async def test_get_url_times_out_if_never_ready():
    tunnel = CloudflareTunnel(local_port=8080)
    with patch("bot.tunnel._STARTUP_TIMEOUT_SECONDS", 0.05):
        assert await tunnel.get_url() is None


@pytest.mark.asyncio
@patch("bot.tunnel.asyncio.create_subprocess_exec", new_callable=AsyncMock)
async def test_stop_terminates_running_process(mock_create):
    process = _fake_process([])
    mock_create.return_value = process
    tunnel = CloudflareTunnel(local_port=8080)
    await tunnel.start()
    await tunnel.stop()
    process.terminate.assert_called_once()
    process.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_is_a_noop_if_never_started():
    tunnel = CloudflareTunnel(local_port=8080)
    await tunnel.stop()  # must not raise
