from datetime import date
from db.database import get_connection


def log_expense(item_name, quantity_purchased, unit_price):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id FROM inventory_items WHERE name = %s", (item_name,))
    item = cursor.fetchone()
    if not item:
        cursor.close()
        conn.close()
        return False
    total_cost = quantity_purchased * unit_price
    cursor.execute("""
        INSERT INTO item_expenses (item_id, quantity_purchased, unit_price, total_cost, purchase_date)
        VALUES (%s, %s, %s, %s, %s)
    """, (item["id"], quantity_purchased, unit_price, total_cost, date.today()))
    conn.commit()
    cursor.close()
    conn.close()
    return True


def get_expenses(item_name=None):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    if item_name:
        cursor.execute("""
            SELECT e.*, i.name FROM item_expenses e
            JOIN inventory_items i ON e.item_id = i.id
            WHERE i.name = %s
            ORDER BY e.purchase_date DESC
        """, (item_name,))
    else:
        cursor.execute("""
            SELECT e.*, i.name FROM item_expenses e
            JOIN inventory_items i ON e.item_id = i.id
            ORDER BY e.purchase_date DESC
        """)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows


def get_total_spent(item_name=None):
    conn = get_connection()
    cursor = conn.cursor()
    if item_name:
        cursor.execute("""
            SELECT COALESCE(SUM(e.total_cost), 0)
            FROM item_expenses e
            JOIN inventory_items i ON e.item_id = i.id
            WHERE i.name = %s
        """, (item_name,))
    else:
        cursor.execute("SELECT COALESCE(SUM(total_cost), 0) FROM item_expenses")
    total = cursor.fetchone()[0]
    cursor.close()
    conn.close()
    return float(total)
