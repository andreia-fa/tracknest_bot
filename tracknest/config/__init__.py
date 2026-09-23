"""Loads and exposes bot and database configuration from environment variables.

Required variables (raises KeyError on startup if missing): BOT_TOKEN.
Optional variables with defaults: DB_PATH (data/tracknest.db).

DASHBOARD_PASSWORD is deliberately not read here: this module is shared
with the local receipt worker, which has no use for it. bot/auth.py (cloud
bot only) reads it instead.
"""

import os

BOT_TOKEN = os.environ["BOT_TOKEN"]
DB_PATH = os.environ.get("DB_PATH", "data/tracknest.db")
