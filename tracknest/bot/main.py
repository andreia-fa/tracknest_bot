"""Telegram bot entry point and command handler registration for TrackNest."""

import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from config import BOT_TOKEN
from db.database import init_db
from db import crud, expenses

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send the welcome message listing all available commands."""
    await update.message.reply_text(
        "Welcome to TrackNest!\n\n"
        "Inventory:\n"
        "  /add_item <name> <qty> — Add or restock an item\n"
        "  /list_items — Show all inventory\n"
        "  /update_item <name> <qty> — Set item quantity\n"
        "  /remove_item <name> — Remove an item\n\n"
        "Expenses:\n"
        "  /log_expense <name> <qty> <unit_price> — Log a purchase\n"
        "  /my_expenses [item_name] — View spending history\n"
        "  /total_spent [item_name] — Total amount spent"
    )


async def add_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /add_item <name> <qty> — add a new item or restock an existing one."""
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /add_item <name> <qty>")
        return
    name, qty = args[0], args[1]
    if not qty.isdigit():
        await update.message.reply_text("Quantity must be a whole number.")
        return
    crud.add_item(name, int(qty))
    await update.message.reply_text(f"Added {qty}x {name} to inventory.")


async def list_items(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /list_items — display all inventory items with quantities."""
    items = crud.get_all_items()
    if not items:
        await update.message.reply_text("Inventory is empty.")
        return
    lines = [
        f"• {i['name']} — qty: {i['quantity']}" + (f" ({i['category']})" if i["category"] else "")
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


async def log_expense(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /log_expense <name> <qty> <unit_price> — record a purchase for an item."""
    args = context.args
    if len(args) < 3:
        await update.message.reply_text("Usage: /log_expense <name> <qty> <unit_price>")
        return
    name, qty_str, price_str = args[0], args[1], args[2]
    try:
        ok = expenses.log_expense(name, int(qty_str), float(price_str))
    except ValueError:
        await update.message.reply_text("qty must be an integer and unit_price a number.")
        return
    if ok:
        await update.message.reply_text(
            f"Logged: {qty_str}x {name} at €{float(price_str):.2f} each."
        )
    else:
        await update.message.reply_text(
            f"Item '{name}' not found. Add it first with /add_item."
        )


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
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add_item", add_item))
    app.add_handler(CommandHandler("list_items", list_items))
    app.add_handler(CommandHandler("update_item", update_item))
    app.add_handler(CommandHandler("remove_item", remove_item))
    app.add_handler(CommandHandler("log_expense", log_expense))
    app.add_handler(CommandHandler("my_expenses", my_expenses))
    app.add_handler(CommandHandler("total_spent", total_spent))
    app.add_error_handler(handle_error)
    logger.info("TrackNest bot starting.")
    app.run_polling(
        timeout=30,
        read_timeout=10,
        write_timeout=10,
        connect_timeout=10,
        pool_timeout=10,
    )


if __name__ == "__main__":
    main()
