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
- Also before logging, `handle_photo` calls `get_price_delta()`: with at least
  one prior purchase, the receipt reply always shows the % change vs. the
  item's historical average (e.g. "+8% vs usual €2.96"), not just when it's
  unusual — this makes everyday price creep visible, not only dramatic jumps.
  When the change is +15% or more (with at least 2 prior purchases), the line
  is flagged as a jump instead of a plain parenthetical. This must run before
  `log_expense()` inserts the new row, or the average would include the very
  price being compared.
- `log_expense()` also auto-corrects a non-luxury item's `shelf_life_days`
  estimate down to the real gap when a repurchase comes sooner than expected —
  real repurchase timing is a better signal than the original guess.

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

get_price_delta(item_name, new_price, min_history=1) -> dict | None
    # {avg_price, pct_change (signed), n} vs. purchase history, or None if
    # there isn't enough history yet. Call before log_expense() — see "Rules" above.
```

## Related: Household Replenishment Policy (`db/settings.py`, `db/crud.py`)

Each item has an effective **par level** — 1 (replace right when it runs low)
or 2 (always keep a spare) — resolved as the item's own `par_level` override
if set, otherwise the household default from `settings.get_default_par_level()`
(defaults to 1). Set via `/par_level [item_name] <1|2>`. Par=2 items get a
proactive "buy a spare" alert ahead of the estimated run-out date, computed
in `bot/main.py`'s `check_spare_stock_alerts` job — see `docs/setup.md` for
the new columns this relies on.

## Related: Budget Alerts (`db/settings.py`, `db/metrics.py`)

`/set_budget <amount>` sets a monthly spending cap. A daily job
(`bot/main.py`'s `check_budget_alert`) compares the current month's total
(`metrics.get_spending_summary()`) against it and alerts once when crossing
80% and again at 100%, tracked via `settings.get/set_budget_alert_state()` so
it doesn't repeat every day within the same month.

## Related: Financial Goal (`db/settings.py`, `db/metrics.py`)

`/set_goal <name> <amount> <YYYY-MM-DD>` is opt-in only — never asked upfront,
run only if and when the user wants it. `metrics.get_goal_status()` doesn't
track real progress (TrackNest has no savings ledger, only spending) — it
computes an honest anchor number instead: the amount per month needed from
today to hit the target by the target date. `/report` shows this, or that the
target date has passed if `pace_per_month` comes back `None`.

## Related: Price Trends (`db/metrics.py`)

`/report` includes a "Creeping up" section from `metrics.get_price_trends()`,
which compares each item's most recent purchase to the average of its earlier
ones (same idea as `get_price_delta`, but aggregated across the whole
inventory) and lists the items that have risen the most.
