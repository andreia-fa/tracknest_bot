"""Shared test setup."""

from unittest.mock import patch

import pytest

from db import database


@pytest.fixture(autouse=True)
def _no_real_database(tmp_path):
    """Point every test at an empty throwaway DB file, never the developer's real one.

    A test that touches the database without meaning to then fails here
    exactly as it does in CI (which has no database), instead of quietly
    reading local data and passing. Tests that want a real database patch
    DB_PATH again and run init_db themselves.
    """
    with patch.object(database, "DB_PATH", str(tmp_path / "unexpected.db")):
        yield
