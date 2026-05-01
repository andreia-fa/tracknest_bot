"""Loads and exposes bot and database configuration from environment variables.

Required variables (raises KeyError on startup if missing): BOT_TOKEN, DB_USER, DB_PASSWORD.
Optional variables with defaults: DB_HOST (localhost), DB_NAME (tracknest_db).
"""

import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]
DB_NAME = os.environ.get("DB_NAME", "tracknest_db")
