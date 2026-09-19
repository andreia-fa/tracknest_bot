# Expense Tracking Module

The expense module lets users log household purchases against existing inventory items and query their spending history.

## Bot Commands

| Command | Arguments | Description |
|---------|-----------|-------------|
| `/my_expenses` | `[item_name]` | List all expense records, or only those for a specific item. |
| `/total_spent` | `[item_name]` | Sum of all spending, or spending on one item. |

Expenses are logged in bulk from a receipt photo (local Ollama vision model,
see `bot/receipt.py`), which also bulk-adds the purchased items to inventory
and clears them off the shopping list.

## Rules
- An item must exist in inventory before an expense can be logged against it.
- Deleting an inventory item cascades and removes its associated expense records.
- `purchase_date` is set to today automatically on each log call; `logged_at` is
  the full timestamp of the log call itself, used only for duplicate detection.
- Before logging, `handle_photo` checks `is_duplicate_purchase()` per item: the
  same item name at the same `unit_price` logged within the last 60 minutes is
  treated as the same physical receipt processed twice (e.g. two photos of one
  receipt) rather than a real second purchase, and is skipped instead of
  double-counted.

## Data Model

### `item_expenses` table

| Column | Type | Description |
|--------|------|-------------|
| `id` | INT AUTO_INCREMENT | Primary key |
| `item_id` | INT | Foreign key → `inventory_items.id` (CASCADE on delete) |
| `quantity_purchased` | INT | Units bought |
| `unit_price` | DECIMAL(10,2) | Price per unit |
| `total_cost` | DECIMAL(10,2) | Computed as `quantity_purchased × unit_price` |
| `purchase_date` | DATE | Date the purchase was logged |
| `logged_at` | TEXT (ISO datetime) | Exact timestamp of the log call, used for duplicate detection |

`inventory_items.category` is populated from receipt parsing (`bot/receipt.py`)
— the vision model infers a short category (Bread, Dairy, Produce, Sushi/Prepared
Food, etc.) from the item name, including non-English or abbreviated names.

## Module API (`db/expenses.py`)

```python
log_expense(item_name, quantity_purchased, unit_price) -> bool
    # Returns False if the item does not exist.

get_expenses(item_name=None) -> list[dict]
    # Returns all records ordered by purchase_date DESC.
    # Pass item_name to filter to a single item.

get_total_spent(item_name=None) -> float
    # Returns 0.0 if no records match.

is_duplicate_purchase(item_name, unit_price, window_minutes=60) -> bool
    # True if this item/price was already logged within window_minutes.
```
