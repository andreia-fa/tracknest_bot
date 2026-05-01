-- Run once as root: mysql -u root -p < setup_db.sql

CREATE DATABASE IF NOT EXISTS tracknest_db;

CREATE USER IF NOT EXISTS 'tracknest_user'@'localhost' IDENTIFIED BY 'change_this_password';

GRANT SELECT, INSERT, UPDATE, DELETE ON tracknest_db.* TO 'tracknest_user'@'localhost';

FLUSH PRIVILEGES;

USE tracknest_db;

CREATE TABLE IF NOT EXISTS inventory_items (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
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
