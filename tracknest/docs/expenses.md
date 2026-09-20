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

## Related: What `/report` shows, and why (`bot/main.py`, `db/metrics.py`)

Every line in `/report` has to answer something the user couldn't work out
from the receipt itself. Reporting the obvious ("you spent money", "you
bought sushi") was deliberately cut. What survived, and the function behind
each:

| Line | Function | Why it earns its place |
|------|----------|------------------------|
| Spent + month-end pace | `get_month_pace()` | Straight-line projection (spend/day × days in month). Withheld before `_MIN_DAYS_FOR_PROJECTION` days, since extrapolating from 2 days is noise. |
| Treats vs. essentials | `get_spending_summary()` (`luxury`/`essential`/`unclassified`) | The user's own luxury/essential answers, totalled — nobody sums this for themselves, and it reframes the month harder than the headline number. `unclassified` stays separate so an unanswered question never masquerades as an essential. |
| Cost per day you own it | `get_daily_cost()` | Latest unit price ÷ shelf life. Separates "expensive to buy" from "expensive to keep around" — invisible on a receipt. Treats included; that's where the spread usually is. |
| Running out soon | `get_running_low()` | Forward-looking counterpart to the check-in, which only speaks up once an item is *already* due. Essentials only, 7-day window, so one shopping trip can replace three. |
| Creeping up | `get_price_trends()` | Inflation per item vs. its own history. |
| Goal pace | `get_goal_status()` | Honest anchor, not a fake progress bar (no savings ledger exists). |
| Green light / needs you | `get_inventory_health()` | Named items and what they need, or a single 🟢 line. |

Ordering is deliberate: the surprising things first, the reassuring
"nothing needs your attention" last.

**Cut, and why:** a "consumption tracking accuracy" metric (how many
shelf-life guesses had been corrected) — an internal calibration signal the
user can't act on; and a frequency-based "most purchased item" — euro totals
already answer "where does my money go" better.

### How much to trust a shelf-life estimate

`shelf_life_days` starts life as the user's cold guess, made the first time
an item is bought, and only becomes evidence once a real repurchase interval
has tested it (`log_expense` corrects it downward when a repurchase beats the
estimate; a "still good" check-in reply bumps it up). The user made the point
concretely: sushi was declared a 2-day item but was still being eaten on day
three.

So the two shelf-life-derived report lines are treated differently, on
purpose:

- **Cost per day is gated** behind `_MIN_PURCHASES_FOR_SHELF_LIFE_TRUST`
  (currently 2 purchases, i.e. at least one observed interval). A price
  divided by an untested guess *looks* like a measurement, and because the
  figure ranks items against each other, one bad estimate reorders the whole
  list. When nothing qualifies, `/report` says what it's waiting for rather
  than dropping the section silently.
- **Running out soon is not gated.** It's a cheap, self-correcting nudge
  built on a number the user supplied themselves — if it's wrong, they just
  don't buy bread — and the report labels it "based on your own estimates"
  so the basis is visible.

The check-in and spare-stock alert jobs are deliberately *not* gated either:
asking early is how the estimate gets corrected in the first place, so
gating them would prevent the very data the gate is waiting for.

> **REVISIT 2026-11-20** (two months on, agreed with the user 2026-09-20):
> re-tune `_MIN_PURCHASES_FOR_SHELF_LIFE_TRUST` once real repurchase data
> exists — raise toward 3 if single intervals still read as noisy, lower if
> too few items ever qualify — and reconsider whether the run-out forecast
> has earned more or less prominence.

## Related: Onboarding (`bot/main.py`)

On a brand-new chat (no chat id ever saved — `settings.get_chat_id()` is
`None`), `start()` launches a short welcome questionnaire instead of the
usual help text: household replenishment policy, then budget, then savings
goal, each via inline-keyboard buttons with a "Skip for now" option — no
question is required, and none of it is asked again on a later `/start`.
`/setup` re-runs the same questionnaire manually any time (e.g. to fill in
something skipped). This is deliberately the only place these three
questions are asked upfront; everything else in the bot (shelf-life,
luxury/essential) stays reactive — asked the first time an item is actually
purchased, since there's no meaningful answer before then.

## Related: Household Replenishment Policy (`db/settings.py`, `db/crud.py`)

Each item has an effective **par level** — 1 (replace right when it runs low)
or 2 (always keep a spare) — resolved as the item's own `par_level` override
if set, otherwise the household default from `settings.get_default_par_level()`
(defaults to 1). Set during onboarding, or any time via
`/par_level [item_name] <1|2>`. Par=2 items get a proactive "buy a spare"
alert ahead of the estimated run-out date, computed in `bot/main.py`'s
`check_spare_stock_alerts` job — see `docs/setup.md` for the new columns
this relies on.

## Related: Budget Alerts (`db/settings.py`, `db/metrics.py`)

A monthly spending cap, set during onboarding or any time via
`/set_budget <amount>`. A daily job (`bot/main.py`'s `check_budget_alert`)
compares the current month's total (`metrics.get_spending_summary()`)
against it and alerts once when crossing 80% and again at 100%, tracked via
`settings.get/set_budget_alert_state()` so it doesn't repeat every day
within the same month.

## Related: Financial Goal (`db/settings.py`, `db/metrics.py`)

Offered (skippable) during onboarding, or set any time via `/set_goal` — a
short guided conversation (`bot/main.py`'s `_handle_goal_answer`, mirroring
the shelf-life/luxury profiling flow): what the goal is for, how much, then
a target date — either an inline-keyboard preset (3/6/12/24 months from
today, via `handle_goal_date_choice`) or a typed custom `YYYY-MM-DD`.
`metrics.get_goal_status()` doesn't track real progress (TrackNest has no
savings ledger, only spending) — it computes an honest anchor number
instead: the amount per month needed from today to hit the target by the
target date. `/report` shows this, or that the target date has passed if
`pace_per_month` comes back `None`.

## Related: Price Trends (`db/metrics.py`)

`/report` includes a "Creeping up" section from `metrics.get_price_trends()`,
which compares each item's most recent purchase to the average of its earlier
ones (same idea as `get_price_delta`, but aggregated across the whole
inventory) and lists the items that have risen the most.
