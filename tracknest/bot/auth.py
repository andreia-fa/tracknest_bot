"""Password check and signed session cookie for the /dashboard web login.

No session store, no new dependency: a session is a signed, self-contained
token (expiry + HMAC signature), verified statelessly on every request —
the same shape as a JWT but built on stdlib hmac/hashlib rather than
pulling in a library for something this small.
"""

import hashlib
import hmac
import time

from config import DASHBOARD_PASSWORD

SESSION_TTL_SECONDS = 7 * 24 * 60 * 60  # 1 week — re-login is rare, not never
_SIGNING_KEY = hashlib.sha256(f"tracknest-dashboard-session:{DASHBOARD_PASSWORD}".encode()).digest()


def check_password(candidate: str) -> bool:
    """Compare a submitted password against DASHBOARD_PASSWORD in constant time.

    Args:
        candidate: The password submitted via the login form.

    Returns:
        True if it matches.
    """
    return hmac.compare_digest(candidate, DASHBOARD_PASSWORD)


def create_session_token() -> str:
    """Return a new signed session token, valid for SESSION_TTL_SECONDS."""
    expires_at = int(time.time()) + SESSION_TTL_SECONDS
    signature = hmac.new(_SIGNING_KEY, str(expires_at).encode(), hashlib.sha256).hexdigest()
    return f"{expires_at}.{signature}"


def verify_session_token(token: str | None) -> bool:
    """Check a session token's signature and expiry.

    Args:
        token: The raw cookie value, or None if no cookie was sent.

    Returns:
        True if the token is well-formed, correctly signed, and unexpired.
    """
    if not token or "." not in token:
        return False
    expires_at_raw, _, signature = token.partition(".")
    if not expires_at_raw.isdigit():
        return False
    expected = hmac.new(_SIGNING_KEY, expires_at_raw.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return False
    return int(expires_at_raw) >= int(time.time())
