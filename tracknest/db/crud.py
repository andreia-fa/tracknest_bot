from db.database import get_connection


def add_item(name, quantity, category=None, alert_threshold=None):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO inventory_items (name, quantity, category, alert_threshold)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE quantity = quantity + VALUES(quantity)
    """, (name, quantity, category, alert_threshold))
    conn.commit()
    cursor.close()
    conn.close()


def get_item(name):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM inventory_items WHERE name = %s", (name,))
    item = cursor.fetchone()
    cursor.close()
    conn.close()
    return item


def get_all_items():
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM inventory_items ORDER BY name")
    items = cursor.fetchall()
    cursor.close()
    conn.close()
    return items


def update_item_quantity(name, quantity):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE inventory_items SET quantity = %s WHERE name = %s",
        (quantity, name)
    )
    affected = cursor.rowcount
    conn.commit()
    cursor.close()
    conn.close()
    return affected > 0


def delete_item(name):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM inventory_items WHERE name = %s", (name,))
    affected = cursor.rowcount
    conn.commit()
    cursor.close()
    conn.close()
    return affected > 0
