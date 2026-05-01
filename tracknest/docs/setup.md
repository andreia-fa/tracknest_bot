# TrackNest – Project Setup

TrackNest is a Telegram bot for household inventory and expense tracking, with support for:
- Inventory management (add, update, remove, list items)
- Expense logging tied to inventory items
- Low-stock alerts (planned)
- Image-based product detection using Azure (planned)



## 🔧 Requirements

- Python 3.9+
- MySQL server
- Virtual environment (recommended)

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

## ⚙️ Configuration File

Copy `.env.example` to `.env` and fill in your values:

```
BOT_TOKEN=your_telegram_bot_token
DB_HOST=localhost
DB_USER=your_mysql_user
DB_PASSWORD=your_mysql_password
DB_NAME=tracknest_db
```

## 🛢️ Repository structure

```
tracknest/
├── bot/
│   └── main.py              # bot entry point and all command handlers
│
├── config/
│   └── __init__.py          # loads environment variables from .env / os.environ
│
├── db/
│   ├── crud.py              # inventory item CRUD operations
│   ├── expenses.py          # expense logging and reporting
│   └── database.py          # MySQL connection factory + schema init
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

**Database Initialization (DDL)**

If you prefer to create the database and tables manually, here are the SQL statements.

### Create the database

```sql
CREATE DATABASE IF NOT EXISTS tracknest_db;

```

### Create inventory_items table

```
CREATE TABLE IF NOT EXISTS inventory_items (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    quantity INT NOT NULL,
    category VARCHAR(100),
    alert_threshold INT,
    image_path TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

```

### Create item_expenses table

```
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

### Sample DML

```
INSERT INTO inventory_items (name, quantity, category, alert_threshold)
VALUES ('Oat Milk', 2, 'Drinks', 1);
```



## 🧠  Known setup issues/troubleshooting

```
- If you get a `ModuleNotFoundError`, make sure your virtual environment is activated.
- If you can't connect to MySQL, check your `.env` credentials and if the MySQL server is running.
```

