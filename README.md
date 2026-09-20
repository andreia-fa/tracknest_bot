# 🏡 TrackNest Bot

**TrackNest** is a **Telegram-based household assistant** built in Python.

It helps you manage home inventory and track household expenses through a conversational chat interface — ideal for small households, roommates, or solo users who want a lightweight, on-demand tool.

---

## Features

### Shopping List
- ✅ `/start` – Welcome and usage guide
- ✅ Plain text, one item per line — `Oat Milk` or `Oat Milk 3` – add something you need to buy, anytime, mid-conversation
- ✅ `/list` – Show your current shopping list (check it as many times as you like while out shopping)
- ✅ Receipt photo — send a photo of your receipt, a local Ollama vision model reads it, bulk-adds items to inventory with quantity/price, logs the expenses, and clears matching items off the shopping list. The model is asked to reconcile its own item prices against the receipt's printed total before answering; if the numbers still don't add up (e.g. a multi-unit line's total mistaken for its per-unit price), the reply flags it instead of silently logging a wrong price.

### Inventory Management
- ✅ `/list_items` – Show current inventory
- ✅ `/remove_item <name>` – Remove an item
- ✅ `/update_item <name> <qty>` – Set item quantity to an absolute value
- ✅ `/par_level [item_name] <1|2>` – Household replenishment policy: 1 = replace
  right when an item runs low, 2 = always keep a spare on hand. No item name
  sets the household-wide default; with an item name, overrides it for that item.
- ✅ Proactive "buy a spare" alert for par=2 items, ahead of the estimated run-out date
- ✅ Category tagging (from receipt parsing)

### Expense Tracking
- ✅ `/my_expenses [item_name]` – View spending history
- ✅ `/total_spent [item_name]` – Total amount spent
- ✅ `/set_budget <amount>` – Set a monthly spending budget, with an alert at 80%/100%
- ✅ Every receipt item shows its % change vs. its own purchase history (not just
  spikes), flagged distinctly once it crosses +15%
- ✅ `/report` – Spending, budget, price trends, and a status line: a green light
  when nothing needs you, or specific items when something does (data layer in
  `db/metrics.py` is Telegram-agnostic, so a future web page can reuse it directly)

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

Receipt photos are read by a local [Ollama](https://ollama.com) vision
model (`minicpm-v4.5`) — fully offline, no API key, no billing. Install
Ollama and pull the model once:

```bash
curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl disable ollama && sudo systemctl stop ollama  # no boot autostart
ollama pull minicpm-v4.5
```

`bot/receipt.py` starts the Ollama server itself on first use (since the
systemd service is disabled) and leaves it running afterward.

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
│   ├── main.py               # bot entry point and command/message/photo handlers
│   ├── parser.py             # plain-text entry parsing (name/qty/price)
│   └── receipt.py            # receipt photo parsing via local Ollama vision model
├── config/
│   └── __init__.py          # loads environment variables
├── db/
│   ├── crud.py              # inventory CRUD operations
│   ├── expenses.py          # expense tracking logic
│   ├── metrics.py           # read-only aggregates for /report and alerts
│   ├── settings.py          # household settings (chat id, par level, budget)
│   ├── shopping_list.py     # shopping list CRUD operations
│   └── database.py          # DB connection and schema
├── docs/
│   ├── setup.md             # extended setup guide
│   └── expenses.md          # expense module design
└── tests/
    ├── test_crud.py
    ├── test_expenses.py
    ├── test_metrics.py
    ├── test_settings.py
    ├── test_shopping_list.py
    ├── test_parser.py
    └── test_receipt.py
```

---

## Troubleshooting

- **ModuleNotFoundError** — make sure the virtual environment is activated.
- **SQLite errors** — check `DB_PATH` (if set) points to a writable location; the file and schema are created automatically otherwise.

---

## License

MIT
