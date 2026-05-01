"""MySQL connection factory and schema initialisation for TrackNest."""

import mysql.connector
from config import DB_HOST, DB_USER, DB_PASSWORD, DB_NAME


def get_connection():
    """Return a new MySQL connection using credentials from config.

    A fresh connection is created on every call — there is no connection pool.
    Callers are responsible for closing the connection when done.
    """
    return mysql.connector.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME
    )


def init_db():
    """Create the required tables if they do not already exist (idempotent)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inventory_items (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(255) NOT NULL UNIQUE,
            quantity INT NOT NULL,
            category VARCHAR(100),
            alert_threshold INT,
            image_path TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS item_expenses (
            id INT AUTO_INCREMENT PRIMARY KEY,
            item_id INT,
            quantity_purchased INT,
            unit_price DECIMAL(10,2),
            total_cost DECIMAL(10,2),
            purchase_date DATE,
            FOREIGN KEY (item_id) REFERENCES inventory_items(id) ON DELETE CASCADE
        )
    """)
    conn.commit()
    cursor.close()
    conn.close()
