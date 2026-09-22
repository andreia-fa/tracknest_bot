"""Loads and exposes bot and database configuration from environment variables.

Required variables (raises KeyError on startup if missing): BOT_TOKEN,
DASHBOARD_PASSWORD.
Optional variables with defaults: DB_PATH (data/tracknest.db).
"""

import os

BOT_TOKEN = os.environ["BOT_TOKEN"]
DB_PATH = os.environ.get("DB_PATH", "data/tracknest.db")
# Gates /dashboard's web login — never logged, never in a URL. The session
# cookie's signing key is derived from it (see bot/auth.py) rather than
# being a separate secret to manage.
DASHBOARD_PASSWORD = os.environ["DASHBOARD_PASSWORD"]
