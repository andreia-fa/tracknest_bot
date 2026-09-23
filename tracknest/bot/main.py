"""Telegram bot entry point and command handler registration for TrackNest."""

import asyncio
import logging
import signal
from datetime import datetime, timedelta, timezone

from aiohttp import web

from bot import dashboard
from bot.categorize import CATEGORY_NAMES, infer_category
from bot.parser import parse_line
from bot.tunnel import CloudflareTunnel
from config import BOT_TOKEN
from db import crud, expenses, metrics, receipt_queue, settings, shopping_list
from db.database import init_db
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

_DASHBOARD_PORT = 8080

_CHECKIN_INTERVAL = timedelta(hours=24)
_PRICE_SPIKE_THRESHOLD_PCT = 15
_PRICE_SPIKE_MIN_HISTORY = 2
_GOAL_DATE_PRESETS = {
    "3m": ("3 months", 91),
    "6m": ("6 months", 182),
    "1y": ("1 year", 365),
    "2y": ("2 years", 730),
}

_ONBOARD_PAR_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("Replace it when it runs low", callback_data="onboard_par:1")],
    [InlineKeyboardButton("Always keep a spare", callback_data="onboard_par:2")],
    [InlineKeyboardButton("Skip for now", callback_data="onboard_par:skip")],
])
_ONBOARD_BUDGET_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("Set one now", callback_data="onboard_budget:yes")],
    [InlineKeyboardButton("Skip for now", callback_data="onboard_budget:skip")],
])
_ONBOARD_GOAL_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("Set one now", callback_data="onboard_goal:yes")],
    [InlineKeyboardButton("Skip for now", callback_data="onboard_goal:skip")],
])
_NAME_KEEP_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("Keep this name", callback_data="name_keep")],
])


def _category_keyboard(suggested: str | None) -> InlineKeyboardMarkup:
    """Every category as a button, two per row; the suggested one is ticked."""
    buttons = [
        InlineKeyboardButton(
            f"✓ {name}" if name == suggested else name, callback_data=f"name_cat:{i}"
        )
        for i, name in enumerate(CATEGORY_NAMES)
    ]
    return InlineKeyboardMarkup([buttons[i:i + 2] for i in range(0, len(buttons), 2)])


_PROFILE_SHELF_KEYBOARD = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("1 day", callback_data="profile_shelf:1"),
        InlineKeyboardButton("4 days", callback_data="profile_shelf:4"),
    ],
    [
        InlineKeyboardButton("One week", callback_data="profile_shelf:7"),
        InlineKeyboardButton("Two weeks", callback_data="profile_shelf:14"),
    ],
    [InlineKeyboardButton("Doesn't spoil", callback_data="profile_shelf:na")],
    [InlineKeyboardButton("Other: insert", callback_data="profile_shelf:custom")],
])
_PROFILE_TYPE_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("Luxury / Treat", callback_data="profile_type:luxury")],
    [InlineKeyboardButton("Essential", callback_data="profile_type:essential")],
    [InlineKeyboardButton("Necessity (used up same-day)", callback_data="profile_type:necessity")],
])
_PROFILE_REMINDER_INTERVAL = timedelta(hours=3)

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send the welcome message, remember this chat, and onboard true first-time users.

    Onboarding (household policy, budget, goal — see _start_onboarding) only
    fires when no chat id has ever been saved before; a repeat /start just
    shows the command list. /setup re-runs onboarding manually any time.
    """
    is_first_run = settings.get_chat_id() is None
    settings.set_chat_id(update.effective_chat.id)
    if is_first_run:
        await _start_onboarding(update, context)
        return
    await update.message.reply_text(
        "Welcome to TrackNest!\n\n"
        "Just type whatever you need, one item per line, whenever you think of it:\n"
        "  Oat Milk\n"
        "  Oat Milk 3\n\n"
        "It goes straight onto your shopping list. Check it any time with /list.\n\n"
        "Changed your mind about something? Put a - in front of it to take it "
        "back off the list:\n"
        "  - Oat Milk\n\n"
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
        "  /set_goal — Optional: walks you through setting a savings goal "
        "(name, amount, date), shown in /report\n"
        "  /setup — Re-run the welcome questions (policy, budget, goal)\n"
        "  /report — Spending, price trends, and what needs your attention"
    )


async def setup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /setup — re-run the onboarding conversation any time."""
    await _start_onboarding(update, context)


async def _start_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the onboarding conversation: household policy, then budget, then goal.

    Every question is skippable — nothing here is required, and each one can
    also be set or changed later via /par_level, /set_budget, or /set_goal.
    """
    context.chat_data["onboarding"] = {"stage": "par_level"}
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=(
            "Hi! Thanks for joining TrackNest 👋\n\n"
            "Before we start, mind answering a couple of quick questions? "
            "It helps me be useful right away — skip anything you're not sure about.\n\n"
            "When something runs low, do you prefer to:"
        ),
        reply_markup=_ONBOARD_PAR_KEYBOARD,
    )


async def _ask_onboard_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ask the onboarding budget question."""
    context.chat_data["onboarding"] = {"stage": "budget_choice"}
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Want to set a monthly spending budget?",
        reply_markup=_ONBOARD_BUDGET_KEYBOARD,
    )


async def _ask_onboard_goal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ask the onboarding savings-goal question."""
    context.chat_data["onboarding"] = {"stage": "goal_choice"}
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Have a savings goal you'd like tracked alongside your spending?",
        reply_markup=_ONBOARD_GOAL_KEYBOARD,
    )


async def _finish_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Close out onboarding with a pointer to everyday usage."""
    context.chat_data.pop("onboarding", None)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=(
            "All set! I'll ask a couple more questions the first time you buy "
            "something new — for now, just send me what you need to buy, or a "
            "photo of a receipt.\n\n"
            "Type /start anytime to see the full command list, or /report to "
            "see how things are going."
        ),
    )


async def _handle_onboarding_text(update: Update, context: ContextTypes.DEFAULT_TYPE, pending: dict):
    """Interpret a plain-text reply during onboarding — currently only the budget amount."""
    text = update.message.text.strip()
    try:
        amount = float(text)
    except ValueError:
        await update.message.reply_text("Reply with a number, e.g. 250.")
        return
    settings.set_monthly_budget(amount)
    await update.message.reply_text(f"Budget set to €{amount:.2f}.")
    await _ask_onboard_goal(update, context)


async def handle_onboarding_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle a button press during the onboarding conversation."""
    query = update.callback_query
    await query.answer()
    pending = context.chat_data.get("onboarding")
    if not pending:
        return
    prefix, choice = query.data.split(":", 1)

    if prefix == "onboard_par":
        if choice in ("1", "2"):
            settings.set_default_par_level(int(choice))
            await query.edit_message_text("Got it — saved.")
        else:
            await query.edit_message_text("Skipped — set this anytime with /par_level.")
        await _ask_onboard_budget(update, context)
        return

    if prefix == "onboard_budget":
        if choice == "yes":
            pending["stage"] = "budget_amount"
            await query.edit_message_text("How much would you like to budget per month?")
            return
        await query.edit_message_text("Skipped — set this anytime with /set_budget.")
        await _ask_onboard_goal(update, context)
        return

    if prefix == "onboard_goal":
        if choice == "yes":
            context.chat_data.pop("onboarding", None)
            context.chat_data["awaiting_goal"] = {"stage": "name", "from_onboarding": True}
            await query.edit_message_text("What are you saving for?")
            return
        await query.edit_message_text("Skipped — set this anytime with /set_goal.")
        await _finish_onboarding(update, context)


_SHELF_LIFE_NA_WORDS = {"n/a", "na", "no", "none", "never", "doesn't spoil", "does not spoil"}
_LUXURY_WORDS = {"luxury", "lux", "treat", "l"}
_ESSENTIAL_WORDS = {"essential", "regular", "basic", "e"}
_NECESSITY_WORDS = {"necessity", "necessary", "n"}
_NECESSITY_SHELF_LIFE_DAYS = 1


async def send_pending_profile_question(bot, chat_id: int):
    """Send the next item-profiling question, if any item still needs one.

    Reads state from the DB (crud.get_pending_profile_item) rather than
    per-chat memory, so it works the same whether called from a live update
    handler or from the offline receipt worker (which has no chat_data).

    Args:
        bot: A telegram.Bot (or Application context's .bot) to send with.
        chat_id: Chat to send the question to.
    """
    pending = crud.get_pending_profile_item()
    if not pending:
        return
    name, stage = pending
    if stage == "name":
        await bot.send_message(
            chat_id,
            f"🧾 New on a receipt: \"{name}\". What is it? Reply with a short name "
            "(e.g. Frozen mixed veg) — I'll remember it for next time.",
            reply_markup=_NAME_KEEP_KEYBOARD,
        )
    elif stage == "category":
        item = crud.get_item(name)
        await bot.send_message(
            chat_id,
            f"Which category is {name}?",
            reply_markup=_category_keyboard(item["category"] if item else None),
        )
    elif stage == "shelf_life":
        await bot.send_message(
            chat_id,
            f"Quick one — how many days does {name} usually last before it goes bad?",
            reply_markup=_PROFILE_SHELF_KEYBOARD,
        )
    else:
        await bot.send_message(
            chat_id,
            f"Got it. What kind of purchase is {name}?",
            reply_markup=_PROFILE_TYPE_KEYBOARD,
        )


async def _handle_profile_answer(update: Update, context: ContextTypes.DEFAULT_TYPE, pending: tuple):
    """Interpret a plain-text reply as the answer to a pending item-profiling question.

    Buttons (see handle_profile_type_choice / handle_profile_shelf_choice)
    are the primary way to answer both questions now — this free-text path
    stays as a forgiving fallback for whichever stage is pending, and is
    still the only path for the shelf-life "Other: insert" follow-up.
    """
    name, stage = pending
    if stage == "name":
        await _rename_receipt_item(context.bot, update.effective_chat.id, name, update.message.text.strip())
        return
    if stage == "category":
        await update.message.reply_text("Pick a category with the buttons above.")
        return
    text = update.message.text.strip().lower()
    if stage == "shelf_life":
        if text in _SHELF_LIFE_NA_WORDS:
            days = 0
        else:
            try:
                days = int(text)
            except ValueError:
                await update.message.reply_text("Reply with a number of days, or 'n/a'.")
                return
        crud.set_profile(name, shelf_life_days=days)
        await send_pending_profile_question(context.bot, update.effective_chat.id)
        return
    # stage == "purchase_type"
    if text in _LUXURY_WORDS:
        purchase_type = "luxury"
    elif text in _ESSENTIAL_WORDS:
        purchase_type = "essential"
    elif text in _NECESSITY_WORDS:
        purchase_type = "necessity"
    else:
        await update.message.reply_text(
            "Use the buttons above, or reply 'luxury', 'essential', or 'necessity'."
        )
        return
    await _set_purchase_type(context.bot, update.effective_chat.id, name, purchase_type)


async def _rename_receipt_item(bot, chat_id: int, receipt_name: str, new_name: str) -> None:
    """Apply the user's real name for a receipt item and remember it as an alias.

    If the name already exists, the purchase merges into that item — its
    category and profile are already known, so nothing more is asked about it.
    """
    final_name, merged = crud.rename_item(receipt_name, new_name)
    item = crud.get_item(final_name)
    crud.save_alias(receipt_name, final_name, item["category"] if item else None)
    if merged:
        await bot.send_message(chat_id, f"Got it — added to your existing {final_name}.")
    else:
        # Re-guess from the real name ("Frozen mixed veg" says far more than
        # "BIO aln.pfanne" did) so the category question below arrives with
        # that guess pre-ticked — one tap to confirm.
        guess = infer_category(final_name)
        if guess != "Other":
            crud.set_item_category(final_name, guess)
            crud.keep_item_name(final_name)  # set_item_category finished naming; reopen it for the tap
    await send_pending_profile_question(bot, chat_id)


async def handle_name_keep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle "Keep this name" on a new receipt item's naming question."""
    query = update.callback_query
    await query.answer()
    pending = crud.get_pending_profile_item()
    if not pending or pending[1] != "name":
        await query.edit_message_text("Already answered.")
        return
    name, _stage = pending
    await query.edit_message_text(f"Keeping \"{name}\".")
    crud.keep_item_name(name)
    item = crud.get_item(name)
    crud.save_alias(name, name, item["category"] if item else None)
    await send_pending_profile_question(context.bot, update.effective_chat.id)


async def handle_name_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle a category button on a new receipt item's naming question."""
    query = update.callback_query
    await query.answer()
    pending = crud.get_pending_profile_item()
    if not pending or pending[1] != "category":
        await query.edit_message_text("Already answered.")
        return
    name, _stage = pending
    category = CATEGORY_NAMES[int(query.data.split(":", 1)[1])]
    await query.edit_message_text(f"{name}: {category}.")
    crud.set_item_category(name, category)
    await send_pending_profile_question(context.bot, update.effective_chat.id)


async def _set_purchase_type(bot, chat_id: int, name: str, purchase_type: str) -> None:
    """Save purchase_type, auto-filling shelf_life_days for a necessity item.

    A necessity is used up the same day it's bought — there's no shelf-life
    estimate to meaningfully ask for, so it's set to _NECESSITY_SHELF_LIFE_DAYS
    directly rather than prompting a question with an implied answer.
    """
    if purchase_type == "necessity":
        crud.set_profile(name, purchase_type=purchase_type, shelf_life_days=_NECESSITY_SHELF_LIFE_DAYS)
    else:
        crud.set_profile(name, purchase_type=purchase_type)
    await send_pending_profile_question(bot, chat_id)


async def handle_profile_type_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle a button press on the purchase-type profiling keyboard."""
    query = update.callback_query
    await query.answer()
    pending = crud.get_pending_profile_item()
    if not pending or pending[1] != "purchase_type":
        await query.edit_message_text("Already answered.")
        return
    name, _stage = pending
    choice = query.data.split(":", 1)[1]
    labels = {"luxury": "Luxury / Treat", "essential": "Essential", "necessity": "Necessity"}
    await query.edit_message_text(f"{name}: {labels[choice]}.")
    await _set_purchase_type(context.bot, update.effective_chat.id, name, choice)


async def handle_profile_shelf_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle a button press on the shelf-life profiling keyboard."""
    query = update.callback_query
    await query.answer()
    pending = crud.get_pending_profile_item()
    if not pending or pending[1] != "shelf_life":
        await query.edit_message_text("Already answered.")
        return
    name, _stage = pending
    choice = query.data.split(":", 1)[1]
    if choice == "custom":
        await query.edit_message_text(f"How many days does {name} usually last? Reply with a number.")
        return
    days = 0 if choice == "na" else int(choice)
    duration = "doesn't spoil" if days == 0 else f"{days} day(s)"
    await query.edit_message_text(f"{name}: {duration}.")
    crud.set_profile(name, shelf_life_days=days)
    await send_pending_profile_question(context.bot, update.effective_chat.id)


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
    """Handle plain-text messages: one shopping list entry (or purchase) per line.

    A line starting with "-" (e.g. "- bananas") removes that item from the
    shopping list instead of adding it. A line that includes a price (e.g.
    "Matcha 2.50") is treated as a purchase you're logging right now — no
    receipt needed — rather than a shopping-list addition; see
    bot.parser.parse_line for exactly what counts as a price.

    If onboarding, a /set_goal conversation, item-profiling, or shelf-life
    check-in question is pending for this chat, the message is treated as
    the answer to that instead of new shopping-list entries.
    """
    pending_onboarding = context.chat_data.get("onboarding")
    if pending_onboarding:
        await _handle_onboarding_text(update, context, pending_onboarding)
        return
    pending_goal = context.chat_data.get("awaiting_goal")
    if pending_goal:
        await _handle_goal_answer(update, context, pending_goal)
        return
    pending_profile = crud.get_pending_profile_item()
    if pending_profile:
        await _handle_profile_answer(update, context, pending_profile)
        return
    pending_checkin = crud.get_pending_checkin_item()
    if pending_checkin:
        await _handle_checkin_answer(update, context, pending_checkin)
        return
    lines = [line for line in update.message.text.splitlines() if line.strip()]
    replies = []
    logged_a_purchase = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("-"):
            name = stripped[1:].strip()
            if not name:
                replies.append(f"Couldn't understand: '{line}'")
                continue
            if shopping_list.remove_item(name):
                replies.append(f"Removed {name} from your shopping list.")
            else:
                replies.append(f"'{name}' wasn't on your list.")
            continue
        try:
            name, qty, unit_price = parse_line(line)
        except ValueError:
            replies.append(f"Couldn't understand: '{line}'")
            continue
        if unit_price is not None:
            replies.append(_log_purchase(
                name, qty, unit_price,
                category=infer_category(name), matched_list_item=name,
            ))
            logged_a_purchase = True
            continue
        shopping_list.add_item(name, qty, category=infer_category(name))
        replies.append(f"Added {qty}x {name} to your shopping list.")
    await update.message.reply_text("\n".join(replies))
    if logged_a_purchase:
        await send_pending_profile_question(context.bot, update.effective_chat.id)


async def show_shopping_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /list — display the current shopping list, grouped by category."""
    items = shopping_list.get_all_items()
    if not items:
        await update.message.reply_text("Your shopping list is empty.")
        return
    by_category: dict[str, list[dict]] = {}
    for item in items:
        category = item["category"]
        if not category or category == "Other":
            # Re-guess at display time so keyword-list additions also fix
            # items that were stored as "Other" before them.
            category = infer_category(item["name"])
        by_category.setdefault(category, []).append(item)
    sections = []
    for category in sorted(by_category, key=lambda c: (c == "Other", c)):
        lines = [f"• {i['name']} ({i['quantity']}x)" for i in by_category[category]]
        sections.append(f"{category}:\n" + "\n".join(lines))
    await update.message.reply_text("Shopping list:\n\n" + "\n\n".join(sections))


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle a receipt photo: queue it for the local Ollama worker to process.

    This bot instance doesn't have access to Ollama (it runs in the cloud) —
    it just remembers the photo and chat, and replies once it's actually
    processed and logged, whenever the local worker next runs.
    """
    file_id = update.message.photo[-1].file_id
    receipt_queue.queue_receipt(chat_id=update.effective_chat.id, telegram_file_id=file_id)
    logger.info("Receipt photo queued for local processing.")
    await update.message.reply_text(
        "Got your receipt — I'll read it and log the items once your computer's on."
    )


def _log_purchase(
    name, qty, price, *, store=None, category=None, matched_list_item=None, ask_name=False,
) -> str:
    """Log one purchased item (inventory + expense) and describe it for a reply.

    Shared by receipt processing and a plain-text line that includes a
    price (see handle_text) — same underlying purchase, same bookkeeping,
    just a different source for name/qty/price/category.

    Returns:
        A single "• ..." reply line, including a duplicate-purchase notice
        or a price-delta/spike note when applicable.
    """
    if expenses.is_duplicate_purchase(name, price):
        return (
            f"• {qty}x {name} at €{price:.2f} each — skipped, this exact item/price "
            "was already logged in the last hour (looks like the same receipt sent twice)"
        )
    is_new = ask_name and crud.get_item(name) is None
    crud.add_item(name, qty, category=category)
    if is_new:
        crud.mark_name_pending(name)
    delta = expenses.get_price_delta(name, price)
    expenses.log_expense(name, qty, price, store=store)
    line = f"• {qty}x {name} at €{price:.2f} each"
    if matched_list_item and shopping_list.remove_item(matched_list_item):
        # Name the list entry when it differs from the receipt's wording —
        # the vision model matches across languages ("PUSH UP" → "Soutien"),
        # and a bare "cleared" left no way to tell what actually came off.
        if matched_list_item.casefold() == name.casefold():
            line += " (cleared from your list)"
        else:
            line += f" (cleared '{matched_list_item}' from your list)"
    if delta is not None:
        sign = "+" if delta["pct_change"] >= 0 else ""
        delta_text = f"{sign}{delta['pct_change']:.0f}% vs usual €{delta['avg_price']:.2f}"
        if delta["pct_change"] >= _PRICE_SPIKE_THRESHOLD_PCT and delta["n"] >= _PRICE_SPIKE_MIN_HISTORY:
            line += f" — ⚠️ {delta_text}, that's a jump"
        else:
            line += f" ({delta_text})"
    return line


def _resolve_receipt_name(item: dict) -> tuple[str, str | None, bool]:
    """Turn a receipt line's wording into (name, category, ask_name).

    A wording the user already named maps straight to their name and
    category. Otherwise the keyword list beats the model's category guess
    (deterministic, and it knows "Käse" is cheese), and the user gets asked
    what the item really is.
    """
    alias = crud.get_alias(item["name"])
    if alias:
        return alias["canonical_name"], alias["category"], False
    keyword = infer_category(item["name"])
    category = keyword if keyword != "Other" else (item.get("category") or None)
    return item["name"], category, True


async def process_receipt_result(parsed: dict) -> str:
    """Log a parsed receipt's items and build the summary reply text.

    Used by the local receipt worker after it runs parse_receipt. Newly
    added items with no shelf-life estimate yet are picked up automatically
    by crud.get_pending_profile_item() — the caller should follow up with
    send_pending_profile_question() once this returns.

    Args:
        parsed: Output of bot.receipt.parse_receipt.

    Returns:
        The reply text to send back to the user.
    """
    items = parsed["items"]
    store = parsed.get("store") or None
    logger.info("Receipt parsed: %d item(s).", len(items))
    if not items:
        return "Couldn't find any items on that receipt."
    replies = [
        _log_purchase(
            name, item["quantity"], item["unit_price"],
            store=store, category=category,
            matched_list_item=item["matched_shopping_list_item"], ask_name=ask_name,
        )
        for item in items
        for name, category, ask_name in [_resolve_receipt_name(item)]
    ]
    if not parsed["reconciled"]:
        replies.append(
            f"⚠️ Heads up: item prices add up to €{parsed['items_total']:.2f} but the "
            f"receipt's total was €{parsed['total_paid']:.2f} — one of the amounts above "
            "is probably off. Worth double-checking against the paper receipt."
        )
    return "Receipt processed:\n" + "\n".join(replies)


async def remind_pending_profile(context: ContextTypes.DEFAULT_TYPE):
    """Recurring job: re-send the current item-profiling question until it's answered.

    A newly-bought item's profile (purchase type, and for luxury/essential
    items, shelf life) is asked once right after logging it — but a message
    sent once is easy to miss or dismiss, and an unanswered item silently
    stays unprofiled forever otherwise (excluded from check-ins, "running
    out soon", and the treats/essentials/necessities split). Re-nudging on
    an interval, same as the other proactive jobs, means it eventually gets
    answered without anyone having to remember to go back to it.
    """
    chat_id = settings.get_chat_id()
    if not chat_id:
        return
    await send_pending_profile_question(context.bot, chat_id)


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


async def set_goal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /set_goal — start a guided conversation to set an optional savings goal.

    Opt-in only — never asked upfront, run only if and when you want one.
    """
    context.chat_data["awaiting_goal"] = {"stage": "name"}
    await update.message.reply_text("What are you saving for?")


async def _handle_goal_answer(update: Update, context: ContextTypes.DEFAULT_TYPE, pending: dict):
    """Interpret a plain-text reply as the next step in the /set_goal conversation."""
    text = update.message.text.strip()
    if pending["stage"] == "name":
        pending["name"] = text
        pending["stage"] = "amount"
        await update.message.reply_text(f'How much do you need for "{text}"?')
        return
    if pending["stage"] == "amount":
        try:
            pending["amount"] = float(text)
        except ValueError:
            await update.message.reply_text("Reply with a number, e.g. 2000.")
            return
        pending["stage"] = "date"
        keys = list(_GOAL_DATE_PRESETS)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(_GOAL_DATE_PRESETS[k][0], callback_data=f"goal_date:{k}") for k in keys[:2]],
            [InlineKeyboardButton(_GOAL_DATE_PRESETS[k][0], callback_data=f"goal_date:{k}") for k in keys[2:]],
            [InlineKeyboardButton("Custom date", callback_data="goal_date:custom")],
        ])
        await update.message.reply_text("By when?", reply_markup=keyboard)
        return
    # stage == "custom_date"
    try:
        target = datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        await update.message.reply_text("Date must be in YYYY-MM-DD format.")
        return
    if target.date() <= datetime.now(tz=timezone.utc).date():
        await update.message.reply_text("Target date must be in the future.")
        return
    settings.set_financial_goal(pending["name"], pending["amount"], text)
    from_onboarding = pending.get("from_onboarding", False)
    context.chat_data.pop("awaiting_goal", None)
    await update.message.reply_text(f"Goal set: {pending['name']} — €{pending['amount']:.2f} by {text}.")
    if from_onboarding:
        await _finish_onboarding(update, context)


async def handle_goal_date_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle a button press on the /set_goal date-preset keyboard."""
    query = update.callback_query
    await query.answer()
    pending = context.chat_data.get("awaiting_goal")
    if not pending or pending.get("stage") != "date":
        return
    choice = query.data.split(":", 1)[1]
    if choice == "custom":
        pending["stage"] = "custom_date"
        await query.edit_message_text("What date? Reply with YYYY-MM-DD.")
        return
    label, days = _GOAL_DATE_PRESETS[choice]
    target_date = (datetime.now(tz=timezone.utc) + timedelta(days=days)).date().isoformat()
    settings.set_financial_goal(pending["name"], pending["amount"], target_date)
    from_onboarding = pending.get("from_onboarding", False)
    context.chat_data.pop("awaiting_goal", None)
    await query.edit_message_text(
        f"Goal set: {pending['name']} — €{pending['amount']:.2f} by {target_date} ({label} from today)."
    )
    if from_onboarding:
        await _finish_onboarding(update, context)


async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /report — what the receipts add up to that a receipt can't tell you.

    Numbers-first, compact format (2026-09-22): label + figure per line, no
    connecting sentences — the underlying insights are unchanged from the
    earlier prose version, only the density. Ordered so the surprising
    things come first (where the money went, what each habit costs per day,
    what's about to run out) and the reassuring "all clear" line comes last.
    """
    spending = metrics.get_spending_summary()
    pace = metrics.get_month_pace()
    budget = metrics.get_budget_status()

    month_abbr = datetime.now(tz=timezone.utc).strftime("%b").upper()
    lines = [f"📊 {month_abbr} · day {pace['days_elapsed']}/{pace['days_in_month']}"]
    spent_line = f"€{spending['total']:.2f} spent"
    if pace["projected"] is not None:
        spent_line += f" → ~€{pace['projected']:.0f} proj."
    lines.append(spent_line)
    if budget:
        budget_line = f"💰 Budget: €{budget['spent']:.2f} / €{budget['budget']:.2f} ({budget['pct']:.0f}%)"
        # Only once there's a real month-end projection to compare against —
        # same gate get_month_pace uses, so a nudge/compliment never fires
        # off 2 days of data. Treats are named as the lever because it's
        # the one category actually optional to cut, not because it's the
        # only cause of an overage.
        if pace["projected"] is not None:
            if pace["projected"] > budget["budget"]:
                budget_line += (
                    f" — on pace for €{pace['projected']:.0f}, cut treats "
                    f"(€{spending['luxury']:.2f} so far) to land under."
                )
            else:
                budget_line += " — well under pace, nice work 🎉"
        lines.append(budget_line)

    # The split the user's own luxury/essential/necessity answers add up
    # to — nobody totals this for themselves, and it reframes the month
    # more than the headline number does.
    if spending["total"] > 0 and (spending["luxury"] or spending["essential"] or spending["necessity"]):
        luxury_pct = spending["luxury"] / spending["total"] * 100
        lines.append(
            f"Treats €{spending['luxury']:.2f} ({luxury_pct:.0f}%) · "
            f"Essential €{spending['essential']:.2f} · "
            f"Necessity €{spending['necessity']:.2f}"
        )
    if spending["unclassified"]:
        lines.append(f"Unclassified: €{spending['unclassified']:.2f}")
    if spending["top_categories"]:
        lines.append("Top: " + ", ".join(
            f"{c['category']} €{c['total']:.2f}" for c in spending["top_categories"]
        ))

    # Held back until a repurchase has tested each item's shelf life — see
    # get_daily_cost. Says what it's waiting for rather than going quiet, so
    # the number doesn't look like it was dropped.
    daily = metrics.get_daily_cost()
    lines.append("\n💸 €/day")
    if daily["items"]:
        lines.append(" · ".join(f"{d['name']} €{d['cost_per_day']:.2f}" for d in daily["items"]))
    else:
        lines.append(f"Waiting on repeat purchases (0/{daily['tracked']} ready)")

    running_low = metrics.get_running_low()
    if running_low:
        lines.append("\n⏳ Running low")
        lines.append(" · ".join(f"{item['name']} {item['days_left']}d" for item in running_low))

    trends = metrics.get_price_trends()
    if trends:
        lines.append("\n📈 Rising")
        lines.append(" · ".join(f"{t['name']} +{t['pct_change']:.0f}%" for t in trends))

    goal = metrics.get_goal_status()
    if goal:
        if goal["pace_per_month"] is not None:
            lines.append(f"\n🎯 Goal: €{goal['pace_per_month']:.0f}/mo → {goal['name']} ({goal['days_left']}d left)")
        else:
            lines.append(f"\n🎯 Goal: {goal['name']} target passed ({goal['target_date']})")

    health = metrics.get_inventory_health()
    if not any(health.values()):
        lines.append("\n🟢 All clear")
    else:
        lines.append("\n🚦 Needs you")
        for name in health["unprofiled"]:
            lines.append(f"{name}: needs profiling")
        for name in health["checkin_pending"]:
            lines.append(f"{name}: check-in pending")
        for name in health["spare_alert_pending"]:
            lines.append(f"{name}: spare-stock alert")

    await update.message.reply_text("\n".join(lines))


async def dashboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /dashboard — send a button that opens the web dashboard inside Telegram.

    The URL comes from bot_data["tunnel"] (see main()) — it's whatever
    Cloudflare Tunnel is currently up, fetched fresh each time rather than
    cached anywhere, since it changes on every restart.
    """
    tunnel: CloudflareTunnel | None = context.bot_data.get("tunnel")
    url = await tunnel.get_url() if tunnel else None
    if not url:
        await update.message.reply_text("Dashboard is still starting up — try again in a few seconds.")
        return
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Open Dashboard", web_app=WebAppInfo(url=f"{url}/login"))]
    ])
    await update.message.reply_text("Your dashboard (password-protected):", reply_markup=keyboard)


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


async def main():
    """Initialise the database schema and run the bot, dashboard, and tunnel together.

    Long polling, the /dashboard aiohttp server, and the Cloudflare Tunnel
    subprocess all share this one process/event loop — run_polling()'s usual
    blocking convenience method can't be combined with a second server, so
    this manages the bot's start/stop lifecycle manually instead (same thing
    run_polling() does internally, just alongside the other two).
    """
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
    app.add_handler(CommandHandler("set_goal", set_goal_cmd))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("setup", setup_cmd))
    app.add_handler(CommandHandler("dashboard", dashboard_cmd))
    app.add_handler(CallbackQueryHandler(handle_goal_date_choice, pattern=r"^goal_date:"))
    app.add_handler(CallbackQueryHandler(handle_onboarding_choice, pattern=r"^onboard_"))
    app.add_handler(CallbackQueryHandler(handle_profile_type_choice, pattern=r"^profile_type:"))
    app.add_handler(CallbackQueryHandler(handle_name_keep, pattern=r"^name_keep$"))
    app.add_handler(CallbackQueryHandler(handle_name_category, pattern=r"^name_cat:"))
    app.add_handler(CallbackQueryHandler(handle_profile_shelf_choice, pattern=r"^profile_shelf:"))
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
        app.job_queue.run_repeating(
            remind_pending_profile, interval=_PROFILE_REMINDER_INTERVAL, first=_PROFILE_REMINDER_INTERVAL
        )
    else:
        logger.warning("JobQueue unavailable (missing job-queue extra) — proactive alerts disabled.")

    tunnel = CloudflareTunnel(local_port=_DASHBOARD_PORT)
    app.bot_data["tunnel"] = tunnel

    web_runner = web.AppRunner(dashboard.build_app())
    await web_runner.setup()
    # Bound to localhost only — cloudflared (same container, same network
    # namespace) is the only thing ever allowed to reach this directly.
    site = web.TCPSite(web_runner, "127.0.0.1", _DASHBOARD_PORT)

    async with app:
        await app.start()
        await app.updater.start_polling(timeout=30)
        await site.start()
        await tunnel.start()
        logger.info("TrackNest bot starting.")

        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop_event.set)
        try:
            await stop_event.wait()
        finally:
            await tunnel.stop()
            await web_runner.cleanup()
            await app.updater.stop()
            await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
