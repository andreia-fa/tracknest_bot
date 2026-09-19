"""Telegram bot entry point and command handler registration for TrackNest."""

import asyncio
import logging

from bot.parser import parse_line
from bot.receipt import parse_receipt
from config import BOT_TOKEN
from db import crud, expenses, shopping_list
from db.database import init_db
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send the welcome message explaining plain-text entry and the management commands."""
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
        "  /total_spent [item_name] — Total amount spent"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle plain-text messages: one shopping list entry per line."""
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
    for item in items:
        name, qty, price = item["name"], item["quantity"], item["unit_price"]
        if expenses.is_duplicate_purchase(name, price):
            replies.append(
                f"• {qty}x {name} at €{price:.2f} each — skipped, this exact item/price "
                "was already logged in the last hour (looks like the same receipt sent twice)"
            )
            continue
        crud.add_item(name, qty, category=item.get("category") or None)
        expenses.log_expense(name, qty, price)
        line = f"• {qty}x {name} at €{price:.2f} each"
        matched = item["matched_shopping_list_item"]
        if matched and shopping_list.remove_item(matched):
            line += " (cleared from your list)"
        replies.append(line)
    await update.message.reply_text("Receipt processed:\n" + "\n".join(replies))


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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_error_handler(handle_error)
    logger.info("TrackNest bot starting.")
    app.run_polling(timeout=30)


if __name__ == "__main__":
    main()
