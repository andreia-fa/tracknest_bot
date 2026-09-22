from unittest.mock import patch

from bot import auth


def test_check_password_correct():
    assert auth.check_password("dummy_password_for_tests") is True


def test_check_password_wrong():
    assert auth.check_password("not-it") is False


def test_session_token_round_trips():
    token = auth.create_session_token()
    assert auth.verify_session_token(token) is True


def test_session_token_rejects_tampered_expiry():
    token = auth.create_session_token()
    expires_at, _, signature = token.partition(".")
    tampered = f"{int(expires_at) + 999999}.{signature}"
    assert auth.verify_session_token(tampered) is False


def test_session_token_rejects_tampered_signature():
    token = auth.create_session_token()
    expires_at, _, _signature = token.partition(".")
    assert auth.verify_session_token(f"{expires_at}.notthesignature") is False


def test_session_token_rejects_expired():
    with patch("bot.auth.time.time", return_value=1000.0):
        token = auth.create_session_token()
    with patch("bot.auth.time.time", return_value=1000.0 + auth.SESSION_TTL_SECONDS + 1):
        assert auth.verify_session_token(token) is False


def test_verify_session_token_rejects_missing_or_malformed():
    assert auth.verify_session_token(None) is False
    assert auth.verify_session_token("") is False
    assert auth.verify_session_token("no-dot-here") is False
    assert auth.verify_session_token("notanumber.somesignature") is False
