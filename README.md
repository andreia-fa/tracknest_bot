# 🏡 TrackNest Bot

**TrackNest** is a **Telegram-based household assistant** built in Python.

It helps you manage home inventory and track household expenses through a conversational chat interface — ideal for small households, roommates, or solo users who want a lightweight, on-demand tool.

---

## Features

### Inventory Management
- ✅ `/start` – Welcome and setup
- ⏳ `/add_item <name> <qty>` – Add or update an inventory item
- ⏳ `/list_items` – Show current inventory
- ⏳ `/remove_item <name>` – Remove an item
- ⏳ `/update_item <name> <qty>` – Update quantity
- 🧠 Planned: Low-stock alerts
- 🧠 Planned: Category tagging and expiration tracking
- 🧠 Planned: Image-based product detection (Azure)

### Expense Tracking
- ⏳ Log purchases tied to inventory items (quantity, unit price, total cost)
- ⏳ View spending history per item
- 🧠 Planned: Monthly/category spending summaries

---

## Built With

- [Python 3.9+](https://www.python.org/)
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot)
- [MySQL](https://www.mysql.com/) – persistent storage
- `python-dotenv` – environment variable management

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/andreia-fa/tracknest_bot.git
cd tracknest_bot
```

### 2. Create a virtual environment

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Create a `.env` file in the project root:

```
BOT_TOKEN=your_telegram_bot_token
DB_HOST=localhost
DB_USER=your_mysql_user
DB_PASSWORD=your_mysql_password
DB_NAME=tracknest_db
```

### 5. Set up the database

```sql
CREATE DATABASE IF NOT EXISTS tracknest_db;

CREATE TABLE IF NOT EXISTS inventory_items (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    quantity INT NOT NULL,
    category VARCHAR(100),
    alert_threshold INT,
    image_path TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS item_expenses (
    id INT AUTO_INCREMENT PRIMARY KEY,
    item_id INT,
    quantity_purchased INT,
    unit_price DECIMAL(10,2),
    total_cost DECIMAL(10,2),
    purchase_date DATE,
    FOREIGN KEY (item_id) REFERENCES inventory_items(id) ON DELETE CASCADE
);
```

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
│   └── settings.py          # loads environment variables
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
- **MySQL connection error** — check your `.env` credentials and that the MySQL server is running.

---

## License

MIT
