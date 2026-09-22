"""Cloudflare Tunnel subprocess management for the /dashboard web app.

cloudflared connects outward from this VM to Cloudflare, which terminates
the public HTTPS side — so the dashboard is reachable over a real HTTPS URL
(required for a Telegram Web App button) without a domain and without
opening any inbound port on the VM's firewall.

Quick Tunnel mode (no Cloudflare account, no config): the assigned
https://*.trycloudflare.com URL changes every time this process restarts.
That's fine for how this is used — /dashboard always hands out the current
URL fresh via a button, nobody bookmarks it.
"""

import asyncio
import logging
import re

logger = logging.getLogger(__name__)

_URL_PATTERN = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
_STARTUP_TIMEOUT_SECONDS = 30


class CloudflareTunnel:
    """Owns one cloudflared subprocess and the URL it reports on startup.

    Simplification: if cloudflared crashes after a successful start, this
    doesn't detect it or restart it — get_url() keeps returning the last
    known URL, which would then be dead. Acceptable for a personal tool
    where a container restart is the normal recovery path for any failure;
    revisit if that turns out to be a real annoyance.
    """

    def __init__(self, local_port: int):
        self._local_port = local_port
        self._process: asyncio.subprocess.Process | None = None
        self._url: str | None = None
        self._url_ready = asyncio.Event()

    async def start(self) -> None:
        """Launch cloudflared and start watching its output for the assigned URL."""
        self._process = await asyncio.create_subprocess_exec(
            "cloudflared", "tunnel", "--url", f"http://localhost:{self._local_port}",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        asyncio.create_task(self._watch_stderr())

    async def _watch_stderr(self) -> None:
        """cloudflared prints its assigned trycloudflare.com URL to stderr, not stdout."""
        assert self._process is not None and self._process.stderr is not None
        async for line in self._process.stderr:
            if self._url_ready.is_set():
                continue
            match = _URL_PATTERN.search(line.decode(errors="replace"))
            if match:
                self._url = match.group(0)
                self._url_ready.set()
                logger.info("Cloudflare Tunnel ready: %s", self._url)

    async def get_url(self) -> str | None:
        """Return the current tunnel URL, waiting briefly if it's still starting up.

        Returns:
            The https://*.trycloudflare.com URL, or None if it didn't come
            up within _STARTUP_TIMEOUT_SECONDS.
        """
        try:
            await asyncio.wait_for(self._url_ready.wait(), timeout=_STARTUP_TIMEOUT_SECONDS)
        except TimeoutError:
            return None
        return self._url

    async def stop(self) -> None:
        """Terminate the subprocess, if running."""
        if self._process and self._process.returncode is None:
            self._process.terminate()
            await self._process.wait()
