# Expense Tracking Module

The expense module lets users log household purchases against existing inventory items and query their spending history.

## Bot Commands

| Command | Arguments | Description |
|---------|-----------|-------------|
| `/log_expense` | `<name> <qty> <unit_price>` | Record a purchase. Computes `total_cost = qty × unit_price`. |
| `/my_expenses` | `[item_name]` | List all expense records, or only those for a specific item. |
| `/total_spent` | `[item_name]` | Sum of all spending, or spending on one item. |

## Rules
- An item must exist in inventory before an expense can be logged against it.
- Deleting an inventory item cascades and removes its associated expense records.
- `purchase_date` is set to today automatically on each log call.

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

## Module API (`db/expenses.py`)

```python
log_expense(item_name, quantity_purchased, unit_price) -> bool
    # Returns False if the item does not exist.

get_expenses(item_name=None) -> list[dict]
    # Returns all records ordered by purchase_date DESC.
    # Pass item_name to filter to a single item.

get_total_spent(item_name=None) -> float
    # Returns 0.0 if no records match.
```
