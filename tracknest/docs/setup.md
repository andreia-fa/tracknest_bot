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

Receipt photos are read by a local [Ollama](https://ollama.com) vision
model — fully offline, no API key, no billing. One-time setup:

```bash
curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl disable ollama && sudo systemctl stop ollama  # no boot autostart
ollama pull minicpm-v4.5
```

The systemd service is disabled on purpose — `bot/receipt.py` starts
`ollama serve` itself the first time a receipt photo is sent, and leaves it
running afterward. The bot starts and the shopping-list feature works fine
even without Ollama installed at all.

## 🛢️ Repository structure

```
tracknest/
├── bot/
│   ├── main.py              # bot entry point and command/message/photo handlers
│   ├── parser.py            # parses plain-text entries (name/qty/unit_price)
│   └── receipt.py           # receipt photo parsing via local Ollama vision model
│
├── config/
│   └── __init__.py          # loads environment variables from os.environ
│
├── db/
│   ├── crud.py              # inventory item CRUD operations
│   ├── expenses.py          # expense logging and reporting
│   ├── metrics.py           # read-only aggregates for /dashboard and alerts
│   ├── settings.py          # household settings (chat id, par level, budget)
│   ├── shopping_list.py     # shopping list CRUD operations
│   └── database.py          # SQLite connection factory + schema init
│
├── docs/
│   ├── setup.md             # this file — full setup guide
│   └── expenses.md          # expense module commands and data model
│
└── tests/
    ├── test_crud.py
    ├── test_expenses.py
    ├── test_metrics.py
    ├── test_settings.py
    ├── test_shopping_list.py
    ├── test_parser.py
    ├── test_receipt.py
    └── __init__.py
```

## 🛢️ Database Creation

Nothing to do manually — `init_db()` in `db/database.py` creates the SQLite
file and its tables (`inventory_items`, `item_expenses`, `shopping_list_items`,
`bot_settings`) automatically the first time the bot runs. The full current
schema lives in that function; treat it as the source of truth rather than
duplicating the DDL here. Notable `inventory_items` columns beyond the
obvious: `shelf_life_days`/`is_luxury` (item profile, asked conversationally),
`checkin_pending` (awaiting a "did it run out" reply), `par_level` (per-item
override of the household's replenishment policy — NULL defers to the
`default_par_level` household setting), `spare_alert_pending` (awaiting a
par=2 "buy a spare" alert), and `shelf_life_corrected` (set once a real
repurchase has corrected the original shelf-life guess).



## 🧠  Known setup issues/troubleshooting

```
- If you get a `ModuleNotFoundError`, make sure your virtual environment is activated.
- If you get a SQLite error, check `DB_PATH` (if set) points to a writable location.
```

