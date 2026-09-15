# 🏡 TrackNest Bot

**TrackNest** is a **Telegram-based household assistant** built in Python.

It helps you manage home inventory and track household expenses through a conversational chat interface — ideal for small households, roommates, or solo users who want a lightweight, on-demand tool.

---

## Features

### Inventory Management
- ✅ `/start` – Welcome and command list
- ✅ `/add_item <name> <qty>` – Add a new item or restock an existing one
- ✅ `/list_items` – Show current inventory
- ✅ `/remove_item <name>` – Remove an item
- ✅ `/update_item <name> <qty>` – Set item quantity to an absolute value
- 🧠 Planned: Low-stock alerts
- 🧠 Planned: Category tagging and expiration tracking
- 🧠 Planned: Image-based product detection (Azure)

### Expense Tracking
- ✅ `/log_expense <name> <qty> <unit_price>` – Log a purchase tied to an inventory item
- ✅ `/my_expenses [item_name]` – View spending history
- ✅ `/total_spent [item_name]` – Total amount spent
- 🧠 Planned: Monthly/category spending summaries

---

## Built With

- [Python 3.9+](https://www.python.org/)
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot)
- [SQLite](https://sqlite.org/) – persistent storage (stdlib `sqlite3`, no server to run)

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/andreia-fa/tracknest_bot.git
cd tracknest_bot
```

### 2. Create a virtual environment

```bash
python -m venv tracknest_bot_env
source tracknest_bot_env/bin/activate   # Windows: tracknest_bot_env\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

No `.env` file — export this as a real shell environment variable (e.g. add
it to `~/.bashrc` so every terminal session has it automatically):

```bash
export BOT_TOKEN=your_telegram_bot_token
```

`DB_PATH` is optional and defaults to `data/tracknest.db` (relative to
`tracknest/`, git-ignored) — only set it if you want the SQLite file
somewhere else.

### 5. Set up the database

Nothing to do — the SQLite file and its schema are created automatically the
first time the bot runs (`init_db()` in `db/database.py`).

### 6. Run the bot

```bash
python tracknest/bot/main.py
```

### 7. Run tests

```bash
pytest tracknest/tests/
```

---

## Project Structure

```
tracknest/
├── bot/
│   └── main.py              # bot entry point and command handlers
├── config/
│   └── __init__.py          # loads environment variables
├── db/
│   ├── crud.py              # inventory CRUD operations
│   ├── expenses.py          # expense tracking logic
│   └── database.py          # DB connection and schema
├── docs/
│   ├── setup.md             # extended setup guide
│   └── expenses.md          # expense module design
└── tests/
    ├── test_crud.py
    └── test_expenses.py
```

---

## Troubleshooting

- **ModuleNotFoundError** — make sure the virtual environment is activated.
- **SQLite errors** — check `DB_PATH` (if set) points to a writable location; the file and schema are created automatically otherwise.

---

## License

MIT
