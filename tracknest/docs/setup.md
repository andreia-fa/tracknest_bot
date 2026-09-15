# TrackNest – Project Setup

TrackNest is a Telegram bot for household inventory and expense tracking, with support for:
- Inventory management (add, update, remove, list items)
- Expense logging tied to inventory items
- Low-stock alerts (planned)
- Image-based product detection using Azure (planned)



## 🔧 Requirements

- Python 3.9+
- Virtual environment (recommended)

No database server to install — TrackNest uses SQLite (Python's built-in
`sqlite3` module), a single file created automatically on first run.

## 🚀 Setup Steps

1. **Clone the repository**

```bash
git clone https://github.com/andreia-fa/tracknest_bot.git
cd tracknest_bot
```

## 🐍 Python env

```bash
python -m venv tracknest_bot_env
source tracknest_bot_env/bin/activate  # Windows: tracknest_bot_env\Scripts\activate
```

## 📦 Requirements Installation

```bash
pip install -r requirements.txt
```

## ⚙️ Configuration

No `.env` file — export this as a real shell environment variable (e.g. add
it to `~/.bashrc` so every terminal session has it automatically, no file to
manage):

```bash
export BOT_TOKEN=your_telegram_bot_token
```

`DB_PATH` is optional and defaults to `data/tracknest.db` (relative to
`tracknest/`, git-ignored) — only set it if you want the SQLite file
somewhere else.

## 🛢️ Repository structure

```
tracknest/
├── bot/
│   └── main.py              # bot entry point and all command handlers
│
├── config/
│   └── __init__.py          # loads environment variables from os.environ
│
├── db/
│   ├── crud.py              # inventory item CRUD operations
│   ├── expenses.py          # expense logging and reporting
│   └── database.py          # SQLite connection factory + schema init
│
├── docs/
│   ├── setup.md             # this file — full setup guide
│   └── expenses.md          # expense module commands and data model
│
└── tests/
    ├── test_crud.py
    ├── test_expenses.py
    └── __init__.py
```

## 🛢️ Database Creation

Nothing to do manually — `init_db()` in `db/database.py` creates the SQLite
file and both tables (`inventory_items`, `item_expenses`) automatically the
first time the bot runs. The full current schema (including `unit` and
`store` columns) lives in that function; treat it as the source of truth
rather than duplicating the DDL here.



## 🧠  Known setup issues/troubleshooting

```
- If you get a `ModuleNotFoundError`, make sure your virtual environment is activated.
- If you get a SQLite error, check `DB_PATH` (if set) points to a writable location.
```

