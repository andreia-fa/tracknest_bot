"""Telegram bot entry point and command handler registration for TrackNest."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from bot.parser import parse_line
from bot.receipt import parse_receipt
from config import BOT_TOKEN
from db import crud, expenses, metrics, settings, shopping_list
from db.database import init_db
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

_CHECKIN_INTERVAL = timedelta(hours=24)
_PRICE_SPIKE_THRESHOLD_PCT = 15
_PRICE_SPIKE_MIN_HISTORY = 2

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send the welcome message and remember this chat for proactive check-ins."""
    settings.set_chat_id(update.effective_chat.id)
    await update.message.reply_text(
        "Welcome to TrackNest!\n\n"
        "Just type whatever you need, one item per line, whenever you think of it:\n"
        "  Oat Milk\n"
        "  Oat Milk 3\n\n"
        "It goes straight onto your shopping list. Check it any time with /list.\n\n"
        "When you're done shopping, just send a photo of the receipt — it logs "
        "everything and clears matching items off your list.\n\n"
        "Other commands:\n"
        "  /list_items — Show current inventory\n"
        "  /update_item <name> <qty> — Set item quantity\n"
        "  /remove_item <name> — Remove an item\n"
        "  /my_expenses [item_name] — View spending history\n"
        "  /total_spent [item_name] — Total amount spent\n"
        "  /par_level [item_name] <1|2> — 1 = replace when low, 2 = always "
        "keep a spare. No item name sets the household default.\n"
        "  /set_budget <amount> — Set a monthly spending budget\n"
        "  /dashboard — Spending, alerts, and inventory health at a glance"
    )


_SHELF_LIFE_NA_WORDS = {"n/a", "na", "no", "none", "never", "doesn't spoil", "does not spoil"}
_LUXURY_WORDS = {"luxury", "lux", "treat", "l"}
_ESSENTIAL_WORDS = {"essential", "regular", "basic", "e"}


async def _ask_next_profile_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send the next queued item-profiling question, or clear state if the queue is empty."""
    queue = context.chat_data.get("profile_queue", [])
    if not queue:
        context.chat_data.pop("awaiting_profile", None)
        context.chat_data.pop("profile_queue", None)
        return
    name = queue[0]
    context.chat_data["awaiting_profile"] = {"item": name, "stage": "shelf_life"}
    await update.message.reply_text(
        f"Quick one — how many days does {name} usually last before it goes bad? "
        "Reply with a number, or 'n/a' if it doesn't really spoil (pantry items etc.)."
    )


async def _handle_profile_answer(update: Update, context: ContextTypes.DEFAULT_TYPE, pending: dict):
    """Interpret a plain-text reply as the answer to a pending item-profiling question."""
    text = update.message.text.strip().lower()
    name = pending["item"]
    if pending["stage"] == "shelf_life":
        if text in _SHELF_LIFE_NA_WORDS:
            days = 0
        else:
            try:
                days = int(text)
            except ValueError:
                await update.message.reply_text("Reply with a number of days, or 'n/a'.")
                return
        crud.set_profile(name, shelf_life_days=days)
        context.chat_data["awaiting_profile"] = {"item": name, "stage": "luxury"}
        await update.message.reply_text(
            f"Got it. Is {name} more of a luxury/treat purchase, or a regular essential? "
            "Reply 'luxury' or 'essential'."
        )
        return
    # stage == "luxury"
    if text in _LUXURY_WORDS:
        is_luxury = 1
    elif text in _ESSENTIAL_WORDS:
        is_luxury = 0
    else:
        await update.message.reply_text("Reply 'luxury' or 'essential'.")
        return
    crud.set_profile(name, is_luxury=is_luxury)
    queue = context.chat_data.get("profile_queue", [])
    if queue and queue[0] == name:
        queue.pop(0)
    context.chat_data["profile_queue"] = queue
    await _ask_next_profile_question(update, context)


_CHECKIN_NO_WORDS = {"no", "n", "ran out", "gone", "finished", "empty"}
_CHECKIN_YES_WORDS = {"yes", "y", "still good", "still lasts", "still have it"}
_CHECKIN_EXTEND_DAYS = 3


async def _handle_checkin_answer(update: Update, context: ContextTypes.DEFAULT_TYPE, item_name: str):
    """Interpret a plain-text reply to a pending shelf-life check-in.

    A "still good" answer means the estimate was too short — push it out a
    few days so the next check-in isn't immediate, while a real early
    repurchase (if one happens) will keep correcting it down via the usual
    signal in expenses.log_expense.
    """
    text = update.message.text.strip().lower()
    crud.mark_checkin_pending(item_name, pending=False)
    if text in _CHECKIN_NO_WORDS or "no" in text.split():
        await update.message.reply_text(f"Thanks — noted {item_name} ran out.")
        return
    if text in _CHECKIN_YES_WORDS or "yes" in text.split():
        crud.bump_shelf_life(item_name, _CHECKIN_EXTEND_DAYS)
        await update.message.reply_text(f"Good to know — I'll check back on {item_name} again later.")
        return
    await update.message.reply_text("Reply 'yes' or 'no'.")
    crud.mark_checkin_pending(item_name, pending=True)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle plain-text messages: one shopping list entry per line.

    If an item-profiling or shelf-life check-in question is pending for this
    chat, the message is treated as the answer to that instead of new
    shopping-list entries.
    """
    pending_profile = context.chat_data.get("awaiting_profile")
    if pending_profile:
        await _handle_profile_answer(update, context, pending_profile)
        return
    pending_checkin = crud.get_pending_checkin_item()
    if pending_checkin:
        await _handle_checkin_answer(update, context, pending_checkin)
        return
    lines = [line for line in update.message.text.splitlines() if line.strip()]
    replies = []
    for line in lines:
        try:
            name, qty, _unit_price = parse_line(line)
        except ValueError:
            replies.append(f"Couldn't understand: '{line}'")
            continue
        shopping_list.add_item(name, qty)
        replies.append(f"Added {qty}x {name} to your shopping list.")
    await update.message.reply_text("\n".join(replies))


async def show_shopping_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /list — display the current shopping list."""
    items = shopping_list.get_all_items()
    if not items:
        await update.message.reply_text("Your shopping list is empty.")
        return
    lines = [f"• {i['name']} ({i['quantity']}x)" for i in items]
    await update.message.reply_text("Shopping list:\n" + "\n".join(lines))


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle a receipt photo: log purchases, and clear matching shopping list items."""
    logger.info("Receipt photo received, starting parse.")
    photo_file = await update.message.photo[-1].get_file()
    image_bytes = bytes(await photo_file.download_as_bytearray())
    current_list = [i["name"] for i in shopping_list.get_all_items()]
    try:
        # Runs off the event loop thread — parse_receipt is a blocking network
        # call to the local Ollama model that can take minutes on CPU-only
        # hardware, and would otherwise freeze the whole bot for everyone.
        items = await asyncio.to_thread(parse_receipt, image_bytes, current_list)
    except Exception:
        logger.exception("Receipt parsing failed.")
        await update.message.reply_text(
            "Sorry, I couldn't process that receipt (parsing error). Please try again."
        )
        return
    logger.info("Receipt parsed: %d item(s).", len(items))
    if not items:
        await update.message.reply_text("Couldn't find any items on that receipt.")
        return
    replies = []
    to_profile = []
    for item in items:
        name, qty, price = item["name"], item["quantity"], item["unit_price"]
        if expenses.is_duplicate_purchase(name, price):
            replies.append(
                f"• {qty}x {name} at €{price:.2f} each — skipped, this exact item/price "
                "was already logged in the last hour (looks like the same receipt sent twice)"
            )
            continue
        crud.add_item(name, qty, category=item.get("category") or None)
        delta = expenses.get_price_delta(name, price)
        expenses.log_expense(name, qty, price)
        current = crud.get_item(name)
        if current and current["shelf_life_days"] is None:
            to_profile.append(name)
        line = f"• {qty}x {name} at €{price:.2f} each"
        matched = item["matched_shopping_list_item"]
        if matched and shopping_list.remove_item(matched):
            line += " (cleared from your list)"
        if delta is not None:
            sign = "+" if delta["pct_change"] >= 0 else ""
            delta_text = f"{sign}{delta['pct_change']:.0f}% vs usual €{delta['avg_price']:.2f}"
            if delta["pct_change"] >= _PRICE_SPIKE_THRESHOLD_PCT and delta["n"] >= _PRICE_SPIKE_MIN_HISTORY:
                line += f" — ⚠️ {delta_text}, that's a jump"
            else:
                line += f" ({delta_text})"
        replies.append(line)
    await update.message.reply_text("Receipt processed:\n" + "\n".join(replies))
    if to_profile:
        queue = context.chat_data.setdefault("profile_queue", [])
        for name in to_profile:
            if name not in queue:
                queue.append(name)
        if "awaiting_profile" not in context.chat_data:
            await _ask_next_profile_question(update, context)


async def check_expiring_items(context: ContextTypes.DEFAULT_TYPE):
    """Daily job: proactively ask about any essential item past its estimated shelf life.

    Luxury items are excluded entirely (see get_checkin_candidates) — a
    treat bought on mood/budget doesn't follow a consumption schedule.
    """
    chat_id = settings.get_chat_id()
    if not chat_id:
        return
    now = datetime.now(tz=timezone.utc)
    for item in crud.get_checkin_candidates():
        if not item["last_purchase"]:
            continue
        last = datetime.fromisoformat(item["last_purchase"])
        if now >= last + timedelta(days=item["shelf_life_days"]):
            crud.mark_checkin_pending(item["name"])
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"Quick check — does {item['name']} still last, or did it run out? Reply 'yes' or 'no'.",
            )


def _spare_alert_lead_days(shelf_life_days: int) -> int:
    """Days before the estimated run-out date to alert a par=2 item, capped to the estimate itself."""
    return min(shelf_life_days, max(1, round(shelf_life_days * 0.15)))


async def check_spare_stock_alerts(context: ContextTypes.DEFAULT_TYPE):
    """Daily job: for par=2 items, alert ahead of the estimated run-out date so a spare gets bought in time.

    Unlike the par=1 check-in (which waits until the estimate says it's
    already out), a par=2 household wants the spare on hand before that
    point.
    """
    chat_id = settings.get_chat_id()
    if not chat_id:
        return
    now = datetime.now(tz=timezone.utc)
    default_par_level = settings.get_default_par_level()
    for item in crud.get_par_alert_candidates(default_par_level):
        if not item["last_purchase"]:
            continue
        last = datetime.fromisoformat(item["last_purchase"])
        lead_days = _spare_alert_lead_days(item["shelf_life_days"])
        alert_at = last + timedelta(days=item["shelf_life_days"] - lead_days)
        if now >= alert_at:
            crud.mark_spare_alert_pending(item["name"])
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"You're on a keep-a-spare policy for {item['name']} — might be time to "
                     "add it to your shopping list before the current one runs out.",
            )


_BUDGET_ALERT_THRESHOLDS = (100, 80)


async def check_budget_alert(context: ContextTypes.DEFAULT_TYPE):
    """Daily job: alert once per month when spending crosses 80% or 100% of the household budget."""
    chat_id = settings.get_chat_id()
    if not chat_id:
        return
    status = metrics.get_budget_status()
    if status is None:
        return
    now = datetime.now(tz=timezone.utc)
    current_month = f"{now.year:04d}-{now.month:02d}"
    alerted_month, alerted_threshold = settings.get_budget_alert_state()
    already_alerted = alerted_threshold if alerted_month == current_month else None
    for threshold in _BUDGET_ALERT_THRESHOLDS:
        if status["pct"] >= threshold and (already_alerted is None or already_alerted < threshold):
            settings.set_budget_alert_state(current_month, threshold)
            verb = "hit" if threshold == 100 else "crossed"
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"Budget alert: you've {verb} {threshold}% of this month's €{status['budget']:.2f} "
                     f"budget (€{status['spent']:.2f} spent so far).",
            )
            break


async def par_level_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /par_level [item_name] <1|2> — set the household default, or a per-item override."""
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /par_level [item_name] <1|2>")
        return
    level_str = args[-1]
    if level_str not in ("1", "2"):
        await update.message.reply_text("Level must be 1 (replace when low) or 2 (keep a spare).")
        return
    level = int(level_str)
    if len(args) == 1:
        settings.set_default_par_level(level)
        await update.message.reply_text(f"Household default par level set to {level}.")
        return
    name = " ".join(args[:-1])
    if crud.set_par_level(name, level):
        await update.message.reply_text(f"Par level for '{name}' set to {level}.")
    else:
        await update.message.reply_text(f"Item '{name}' not found.")


async def set_budget_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /set_budget <amount> — set the household's monthly spending budget."""
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("Usage: /set_budget <amount>")
        return
    try:
        amount = float(args[0])
    except ValueError:
        await update.message.reply_text("Amount must be a number.")
        return
    settings.set_monthly_budget(amount)
    await update.message.reply_text(f"Monthly budget set to €{amount:.2f}.")


async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /dashboard — spending, alerts, and inventory health at a glance."""
    spending = metrics.get_spending_summary()
    budget = metrics.get_budget_status()
    accuracy = metrics.get_consumption_accuracy()
    health = metrics.get_inventory_health()

    lines = ["📊 This month", f"  Spent: €{spending['total']:.2f}"]
    if budget:
        lines.append(f"  Budget: €{budget['spent']:.2f} / €{budget['budget']:.2f} ({budget['pct']:.0f}%)")
    if spending["top_items"]:
        lines.append("  Top items: " + ", ".join(
            f"{i['name']} (€{i['total']:.2f})" for i in spending["top_items"]
        ))
    if spending["top_categories"]:
        lines.append("  Top categories: " + ", ".join(
            f"{c['category']} (€{c['total']:.2f})" for c in spending["top_categories"]
        ))

    trends = metrics.get_price_trends()
    if trends:
        lines.append("\n📈 Creeping up")
        lines.extend(
            f"  {t['name']}: +{t['pct_change']:.0f}% (now €{t['latest_price']:.2f}, was ~€{t['avg_price']:.2f})"
            for t in trends
        )

    lines.append("\n🔍 Consumption tracking")
    lines.append(f"  {accuracy['tracked']} item(s) with a shelf-life estimate, "
                 f"{accuracy['corrected']} corrected from real repurchase timing")

    lines.append("\n📦 Inventory health")
    lines.append(f"  {health['checkin_pending']} check-in(s) pending")
    lines.append(f"  {health['spare_alert_pending']} spare-stock alert(s) pending")
    lines.append(f"  {health['unprofiled']} item(s) not yet profiled")

    await update.message.reply_text("\n".join(lines))


async def list_items(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /list_items — display all inventory items with quantities."""
    items = crud.get_all_items()
    if not items:
        await update.message.reply_text("Inventory is empty.")
        return
    lines = [
        f"• {i['name']} — {i['quantity']}{' ' + i['unit'] if i.get('unit') else 'x'}"
        + (f" ({i['category']})" if i["category"] else "")
        for i in items
    ]
    await update.message.reply_text("Inventory:\n" + "\n".join(lines))


async def update_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /update_item <name> <qty> — set an item's quantity to an absolute value."""
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /update_item <name> <qty>")
        return
    name, qty = args[0], args[1]
    if not qty.isdigit():
        await update.message.reply_text("Quantity must be a whole number.")
        return
    if crud.update_item_quantity(name, int(qty)):
        await update.message.reply_text(f"{name} quantity updated to {qty}.")
    else:
        await update.message.reply_text(f"Item '{name}' not found.")


async def remove_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /remove_item <name> — delete an item and its expense history."""
    if not context.args:
        await update.message.reply_text("Usage: /remove_item <name>")
        return
    name = context.args[0]
    if crud.delete_item(name):
        await update.message.reply_text(f"'{name}' removed from inventory.")
    else:
        await update.message.reply_text(f"Item '{name}' not found.")


async def my_expenses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /my_expenses [item_name] — show expense history, optionally filtered by item."""
    item_name = context.args[0] if context.args else None
    rows = expenses.get_expenses(item_name)
    if not rows:
        msg = f"No expenses found for '{item_name}'." if item_name else "No expenses logged yet."
        await update.message.reply_text(msg)
        return
    lines = [
        f"• {r['name']} — {r['quantity_purchased']}x €{r['unit_price']} = €{r['total_cost']} on {r['purchase_date']}"
        for r in rows
    ]
    await update.message.reply_text("Expenses:\n" + "\n".join(lines))


async def total_spent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /total_spent [item_name] — show cumulative spend, optionally scoped to one item."""
    item_name = context.args[0] if context.args else None
    total = expenses.get_total_spent(item_name)
    if item_name:
        await update.message.reply_text(f"Total spent on '{item_name}': €{total:.2f}")
    else:
        await update.message.reply_text(f"Total spent overall: €{total:.2f}")


async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log unhandled errors and notify the user."""
    logger.error("Update %s caused error: %s", update, context.error, exc_info=context.error)
    if isinstance(update, Update) and update.message:
        await update.message.reply_text("Something went wrong. Please try again.")


def main():
    """Initialise the database schema and start the bot with long polling."""
    init_db()
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .read_timeout(10)
        .write_timeout(10)
        .connect_timeout(10)
        .pool_timeout(10)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", show_shopping_list))
    app.add_handler(CommandHandler("list_items", list_items))
    app.add_handler(CommandHandler("update_item", update_item))
    app.add_handler(CommandHandler("remove_item", remove_item))
    app.add_handler(CommandHandler("my_expenses", my_expenses))
    app.add_handler(CommandHandler("total_spent", total_spent))
    app.add_handler(CommandHandler("par_level", par_level_cmd))
    app.add_handler(CommandHandler("set_budget", set_budget_cmd))
    app.add_handler(CommandHandler("dashboard", dashboard))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_error_handler(handle_error)
    if app.job_queue is not None:
        app.job_queue.run_repeating(
            check_expiring_items, interval=_CHECKIN_INTERVAL, first=timedelta(minutes=1)
        )
        app.job_queue.run_repeating(
            check_spare_stock_alerts, interval=_CHECKIN_INTERVAL, first=timedelta(minutes=1)
        )
        app.job_queue.run_repeating(
            check_budget_alert, interval=_CHECKIN_INTERVAL, first=timedelta(minutes=1)
        )
    else:
        logger.warning("JobQueue unavailable (missing job-queue extra) — proactive alerts disabled.")
    logger.info("TrackNest bot starting.")
    app.run_polling(timeout=30)


if __name__ == "__main__":
    main()
