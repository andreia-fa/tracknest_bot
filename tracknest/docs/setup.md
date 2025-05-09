# TrackNest – Project Setup

TrackNest is a personal home inventory tracker with support for:
- Manual item entry via CLI
- Low-stock alerts
- Telegram notifications
- Expense tracking (module in progress)
- Image-based product detection using Azure (planned)



## 🔧 Requirements

- Python 3.9+
- MySQL server
- Virtual environment (recommended)

## 🚀 Setup Steps

1. **Clone the repository**

```bash
git clone https://github.com/yourusername/tracknest_bot.git
cd tracknest_bot
```

## 🐍 Python env

```
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

## 📦 Requirements Installation

```
pip install -r requirements.txt

```


## ⚙️ Configuration File

```
DB_HOST=localhost
DB_USER=your_mysql_user
DB_PASSWORD=your_mysql_password
DB_NAME=tracknest_db
```

## 🛢️ Repository structure

```

tracknest/
├── bot/
│   ├── handlers/
│   ├── services/
│   ├── utils/
│   └── main.py
│
├── config/
│   └── settings.py          # loads environment variables
│
├── db/
│   ├── crud.py              # inventory item logic (add, update, delete, get)
│   ├── expenses.py          # expense tracking logic
│   └── database.py          # DB connection + schema creation
│
├── docs/
│   ├── setup.md             # full setup guide
│   └── expenses.md          # expense module design
│
├── tests/
│   ├── test_crud.py
│   ├── test_expenses.py
│   └── __init__.py
│
├── logs/                    # for scheduler/bot logs (if needed)
│
├── .env.example             # sample env file (you'll have .env locally)
├── .gitignore
├── LICENSE
├── README.md
└── requirements
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

