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

Purchases without a receipt are typed, one per line, and confirmed with a tap
(`bot.parser.parse_purchase`):

| Typed | Means |
|-------|-------|
| `sesame ring 1,49` | one item at €1.49, today |
| `avec, sesame ring 1,49` | bought at avec (matched to a known store's spelling, e.g. `rewe` → `REWE`) |
| `Lidl, milk + bread + eggs 5,40` | €5.40 is the **total** for all three |
| `3/10 avec, sesame ring 1,49` | bought on 3 October (`d/m` or `d/m/yyyy` — a slash, since `3.10` reads as a price) |
| `yesterday avec, sesame ring 1,49` | bought yesterday (also `gestern`, `ontem`) |

Without a date, the purchase is logged today; the confirmation also offers
"📅 Yes — it was yesterday". A shared total is split evenly in cents (the
last share takes the rounding, so they add up exactly) and stored with
`price_kind = 'share'`: it counts towards spending, but price deltas, price
trends, cost per day and store guessing only use real `'unit'` prices.

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
- `log_expense()` also auto-corrects a need's `shelf_life_days` estimate
  down towards the real gap when a repurchase comes sooner than expected —
  real repurchase timing is a better signal than the original guess.
- **Typos never create a new item** (`bot/name_match.py`, 2026-10-04). Before
  a typed purchase, a shopping-list line, a `- remove` line or an un-aliased
  receipt line is used, its name is matched against the items (and list
  entries) already known. Case, spacing, edge punctuation and umlaut/ß
  spellings never matter ("Pfeffer  Bretzel," is "Pfefferbretzel"); beyond
  that, names of 5+ letters within a 0.85 similarity ratio match, but only if
  the numbers in them agree ("Milch 1,5%" ≠ "Milch 3,5%") and no second
  candidate is about as close. Kept strict on purpose: a wrong merge mixes
  two items' prices, a miss only costs a question. When a typo was read as a
  known name, the reply says "(you typed '…')" so a wrong match is visible.
- A receipt is logged on its **printed date** (`purchased_at`), not the day
  it's processed, so a batch of old receipts lands in the right month; the
  duplicate check then means "same item and price on the same receipt date".

## Data Model

### `item_expenses` table

| Column | Type | Description |
|--------|------|-------------|
| `id` | INT AUTO_INCREMENT | Primary key |
| `item_id` | INT | Foreign key → `inventory_items.id` (CASCADE on delete) |
| `quantity_purchased` | INT | Units bought |
| `unit_price` | DECIMAL(10,2) | Price per unit |
| `total_cost` | DECIMAL(10,2) | Computed as `quantity_purchased × unit_price` |
| `purchase_date` | DATE | Date of the purchase (a receipt's printed date, or a typed one) |
| `logged_at` | TEXT (ISO datetime) | Exact timestamp of the log call, used for duplicate detection |
| `trip_key` | TEXT | Which visit it belongs to (`receipt:<id>`, or one per typed message) |
| `price_kind` | TEXT, `'unit'` / `'share'` (CHECK) | `'share'` = an even share of a typed total for several products — never compared as a price |

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

## Related: `/finance` and `/stock`, and why (`bot/main.py`, `db/metrics.py`)

Until 2026-10-04 everything lived in one `/report` message, which the user
found "too big and too messy": money, per-item insights, what's running out
and a to-do list of open questions, all at once. It was split by what the
user wants to know. `/report` stays as an alias of `/finance` until
2026-10-25 (see `TODO.md`).

Every line still has to answer something the user couldn't work out from
the receipt itself.

**`/finance`**: money only (`_finance_text`).

| Line | Function | Why it earns its place |
|------|----------|------------------------|
| Spent + month-end pace | `get_month_pace()` | Straight-line projection (spend/day × days in month). Withheld before `MIN_DAYS_FOR_PROJECTION` days, and says so, since extrapolating from 2 days is noise. |
| Budget used | `get_budget_status()` | % of the monthly budget. Once there's a projection that lands over budget, it names treats as the easiest cut. |
| Most on | `get_spending_summary(top_n=1)` | The single biggest category. |
| Treats vs. needs | `get_spending_summary()` (`treat`/`need`/`unknown`) | The user's own treat/need answers, totalled. `unknown` stays separate so an unanswered question never masquerades as a need. |
| Goal pace | `get_goal_status()` | Honest anchor, not a fake progress bar (no savings ledger exists). |

**`/stock`**: what runs out within 7 days (`get_running_low()`), named by
**product** (`_need_name`: "bananas", not "BANANE"), one line per product.
When two brands of the same product are both running out, the sooner one
wins. 🛒 *Add all to list* re-reads the list when tapped and skips anything
already on the shopping list, so tapping twice never bumps quantities.

**Moved to the dashboard only:** cost per day (`get_daily_cost()`) and rising
prices (`get_price_trends()`). They need room to be read properly.

**Dropped:** the "needs you" list (`get_inventory_health()`): items waiting
for a check-in, profiling or a spare alert. The reminders already ask those
one at a time, and repeating them as a list of brand names was noise. The
dashboard still shows it.

**Cut earlier, and why:** a "consumption tracking accuracy" metric (how many
shelf-life guesses had been corrected), which was an internal calibration
signal the user can't act on; and a frequency-based "most purchased item",
since euro totals already answer "where does my money go" better.

### How much to trust a shelf-life estimate

`shelf_life_days` starts life as the user's cold guess, made the first time
an item is bought, and only becomes evidence once a real repurchase interval
has tested it (`log_expense` corrects it downward when a repurchase beats the
estimate; a "still good" check-in reply bumps it up). The user made the point
concretely: sushi was declared a 2-day item but was still being eaten on day
three.

So the two shelf-life-derived figures are treated differently, on
purpose:

- **Cost per day is gated** behind `_MIN_PURCHASES_FOR_SHELF_LIFE_TRUST`
  (currently 2 purchases, i.e. at least one observed interval). A price
  divided by an untested guess *looks* like a measurement, and because the
  figure ranks items against each other, one bad estimate reorders the whole
  list. It is shown on the dashboard only.
- **Running out soon is not gated.** It's a cheap, self-correcting nudge
  built on a number the user supplied themselves — if it's wrong, they just
  don't buy bread. It's what `/stock` shows.

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
questions are asked upfront; everything else in the bot (treat or need,
how long it lasts) stays reactive — settled the first time an item is
actually purchased, since there's no meaningful answer before then. Even
then it's guessed before it's asked: the same product bought under another
name lends its answers, then a category default (`bot/profile_guess.py`);
the guess is shown with a ✏️ button, and only what neither covers becomes a
question — one at a time, the next only after the last is answered.

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
target date. `/finance` shows this, or that the target date has passed if
`pace_per_month` comes back `None`.

## Related: Price Trends (`db/metrics.py`)

The dashboard's price watch comes from `metrics.get_price_trends()`,
which compares each item's most recent purchase to the average of its earlier
ones (same idea as `get_price_delta`, but aggregated across the whole
inventory) and lists the items that have risen the most.
