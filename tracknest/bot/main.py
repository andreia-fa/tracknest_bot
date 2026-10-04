"""Telegram bot entry point and command handler registration for TrackNest."""

import asyncio
import logging
import signal
from types import SimpleNamespace
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from aiohttp import web

from bot import dashboard
from bot.categorize import CATEGORY_NAMES, infer_category
from bot.list_match import canonical_product, choose_list_match, same_kind
from bot.parser import parse_line
from bot.profile_guess import guess_profile
from bot.receipt_lines import is_code_only
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

# Proactive messages (check-ins, spare/budget alerts, profiling reminders) go
# out in two fixed rounds a day, one message per round at most — never on
# startup, since every deploy restarts the bot.
_LOCAL_TZ = ZoneInfo("Europe/Berlin")
_PROACTIVE_ROUND_TIMES = (time(10, 0, tzinfo=_LOCAL_TZ), time(18, 0, tzinfo=_LOCAL_TZ))
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


def _product_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Yes", callback_data="product_ok"),
        InlineKeyboardButton("❌ No", callback_data="product_no"),
    ]])


_MAX_PRODUCT_CHOICES = 4
_MAX_CALLBACK_BYTES = 64  # Telegram's limit on a button's callback data


def _product_choices(name: str, rejected: str | None) -> list[str]:
    """Other things a new item could be, as buttons: the model's alternatives, then related products already bought.

    Each is mapped to the household's own word for it ("brezel" ->
    "pretzel"), and never repeats the guess just rejected.
    """
    known = crud.get_known_products(exclude_name=name)
    hint = rejected or name
    candidates = [*crud.get_product_options(name), *(p for p in known if same_kind(p, [name, hint]))]
    choices = []
    for candidate in candidates:
        candidate = canonical_product(candidate, known)
        fits = len(f"product_pick:{candidate}".encode()) <= _MAX_CALLBACK_BYTES
        if candidate != rejected and candidate not in choices and fits:
            choices.append(candidate)
    return choices[:_MAX_PRODUCT_CHOICES]


def _product_choices_keyboard(choices: list[str]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(choice, callback_data=f"product_pick:{choice}")] for choice in choices]
    rows.append([InlineKeyboardButton("✍️ Something else — I'll type it", callback_data="product_type")])
    return InlineKeyboardMarkup(rows)


def _category_keyboard(suggested: str | None) -> InlineKeyboardMarkup:
    """Every category as a button, two per row; the suggested one is ticked."""
    buttons = [
        InlineKeyboardButton(
            f"✓ {name}" if name == suggested else name, callback_data=f"name_cat:{i}"
        )
        for i, name in enumerate(CATEGORY_NAMES)
    ]
    return InlineKeyboardMarkup([buttons[i:i + 2] for i in range(0, len(buttons), 2)])


def _shelf_keyboard(prefix: str) -> InlineKeyboardMarkup:
    """The how-long-it-lasts choices, each button's callback data being prefix + its value.

    Values: "same" (same day), a number of days, "oneoff", or "custom" (type it).
    """
    def button(label: str, value: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(label, callback_data=f"{prefix}{value}")
    return InlineKeyboardMarkup([
        [button("Same day", "same"), button("4 days", "4")],
        [button("One week", "7"), button("Two weeks", "14")],
        [button("One month", "30"), button("Frozen (~3 months)", "90")],
        [button("🚫 One-off — don't remind me", "oneoff")],
        [button("Other: insert", "custom")],
    ])


def _lasts_from_choice(value: str) -> tuple[str, int | None]:
    """Turn a _shelf_keyboard value (other than "custom") into (lasts, days)."""
    if value == "same":
        return "same_day", None
    if value == "oneoff":
        return "one_off", None
    return "days", int(value)


_SAME_DAY_WORDS = {"same day", "same", "today", "1", "1 day"}
_ONE_OFF_WORDS = {"one-off", "one off", "oneoff", "never", "no", "none", "n/a", "na", "doesn't spoil"}


def _lasts_from_text(text: str) -> tuple[str, int | None] | None:
    """Read a typed how-long-it-lasts answer as (lasts, days), or None if it isn't one."""
    text = text.strip().lower()
    if text in _SAME_DAY_WORDS:
        return "same_day", None
    if text in _ONE_OFF_WORDS:
        return "one_off", None
    try:
        days = int(text)
    except ValueError:
        return None
    return ("days", days) if days >= 2 else None


def _shelf_change_keyboard(item_id: int) -> InlineKeyboardMarkup:
    """A single button to correct an item's shelf-life answer after the fact."""
    return InlineKeyboardMarkup([[InlineKeyboardButton("✏️ Change", callback_data=f"shelf_edit:{item_id}")]])


def _describe_lasts(lasts: str, days: int | None) -> str:
    if lasts == "same_day":
        return "used up the same day"
    if lasts == "one_off":
        return "one-off, no reminders"
    return f"lasts {days} day(s)"


_PROFILE_SHELF_KEYBOARD = _shelf_keyboard("profile_shelf:")
_PROFILE_KIND_KEYBOARD = InlineKeyboardMarkup([[
    InlineKeyboardButton("🍫 Treat", callback_data="profile_kind:treat"),
    InlineKeyboardButton("🧺 Need", callback_data="profile_kind:need"),
]])
_KIND_LABELS = {"treat": "🍫 treat", "need": "🧺 need"}

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
        "  /cleared — What recently came off your list, with a button to put it back\n"
        "  /list_items — Show current inventory\n"
        "  /update_item <name> <qty> — Set item quantity\n"
        "  /remove_item <name> — Remove an item\n"
        "  /my_expenses [item_name] — View spending history\n"
        "  /total_spent [item_name] — Total amount spent\n"
        "  /par_level [item_name] <1|2> — 1 = replace when low, 2 = always "
        "keep a spare. No item name sets the household default.\n"
        "  /rename <old name> = <new name> — Give an item a readable name\n"
        "  /note — Remember something about an item (e.g. a size)\n"
        "  /item <name> — See an item and change its category, treat/need, how long it lasts\n"
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


_TREAT_WORDS = {"treat", "t", "luxury"}
_NEED_WORDS = {"need", "n", "essential"}


async def send_pending_profile_question(bot, chat_id: int, force: bool = False):
    """Send the next item-profiling question, if any item still needs one — one at a time.

    The question already waiting for an answer is never sent again (four
    receipts in a row used to repeat it four times, on top of new ones):
    the next one only goes out once this one is answered. force re-sends it
    anyway — the once-a-day reminder.

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
    key = f"{name}|{stage}"
    if not force and settings.get_open_question() == key:
        return
    settings.set_open_question(key)
    if stage == "name":
        item = crud.get_item(name)
        guess = item.get("product") if item else None
        if guess:
            await bot.send_message(
                chat_id, f"🧾 New: {name}\nI think it's: {guess}", reply_markup=_product_confirm_keyboard(),
            )
        else:
            await bot.send_message(
                chat_id, f"🧾 New: {name}\nWhat is it?",
                reply_markup=_product_choices_keyboard(_product_choices(name, None)),
            )
    elif stage == "card":
        item = crud.get_item(name)
        await bot.send_message(chat_id, _card_text(item), reply_markup=_card_keyboard(item))
    elif stage == "category":
        item = crud.get_item(name)
        await bot.send_message(
            chat_id,
            f"Which category is {name}?",
            reply_markup=_category_keyboard(item["category"] if item else None),
        )
    elif stage == "lasts":
        await bot.send_message(
            chat_id,
            f"Quick one — how long does {name} last before you'd buy it again?",
            reply_markup=_PROFILE_SHELF_KEYBOARD,
        )
    else:
        await bot.send_message(
            chat_id,
            f"Got it. Is {name} a treat or a need?",
            reply_markup=_PROFILE_KIND_KEYBOARD,
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
        product = update.message.text.strip().lower()
        await _confirm_product(context.bot, update.effective_chat.id, name, product)
        return
    if stage == "category":
        await update.message.reply_text("Pick a category with the buttons above.")
        return
    text = update.message.text.strip().lower()
    if stage == "lasts":
        answer = _lasts_from_text(text)
        if answer is None:
            await update.message.reply_text("Reply with a number of days (2 or more), 'same day', or 'one-off'.")
            return
        crud.set_lasts(name, *answer)
        await _confirm_lasts(context.bot, update.effective_chat.id, name, *answer)
        await send_pending_profile_question(context.bot, update.effective_chat.id)
        return
    # stage == "treat_or_need"
    if text in _TREAT_WORDS:
        kind = "treat"
    elif text in _NEED_WORDS:
        kind = "need"
    else:
        await update.message.reply_text("Use the buttons above, or reply 'treat' or 'need'.")
        return
    crud.set_treat_or_need(name, kind)
    await send_pending_profile_question(context.bot, update.effective_chat.id)


async def _confirm_product(bot, chat_id: int, receipt_name: str, product: str) -> None:
    """Save what a new receipt item generically is, then file it from that — never before.

    Everything else follows from the product, so it's only used once the
    user has confirmed it: a SCHLAGCREME VEGA misread as "cheese" used to
    inherit the cheese's "need, lasts 7 days" before anyone checked.
    The product is mapped to the household's own word for it ("brezel" ->
    "pretzel"). A product already bought under another brand lends its
    profile, and the item is done; otherwise the filing card shows the
    bot's reading (treat or need, how long it lasts, category) to confirm
    with one tap.
    """
    product = canonical_product(product, crud.get_known_products(exclude_name=receipt_name))
    crud.set_item_product(receipt_name, product)
    crud.set_product_options(receipt_name, [])
    crud.copy_product_profile(receipt_name, product)
    item = crud.get_item(receipt_name) or {}
    guess = infer_category(product)
    category = guess if guess != "Other" else item.get("category")
    if category:
        crud.change_item_category(receipt_name, category)
    crud.save_alias(receipt_name, receipt_name, category, product)
    await _clear_list_entry_for_named_item(bot, chat_id, receipt_name, product)
    item = crud.get_item(receipt_name) or {}
    if _is_profiled(item) and category:
        # Filed like the same product's other brands (or answered before).
        crud.set_name_status(receipt_name, None)
        await bot.send_message(
            chat_id, f"{_card_title(item)}: {_profile_note(item)} · 🏷 {category}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✏️ Change", callback_data=f"fix:{item['id']}")]]),
        )
    else:
        _fill_guesses(receipt_name, None)
        crud.set_name_status(receipt_name, "card")
    await send_pending_profile_question(bot, chat_id)


async def _clear_list_entry_for_named_item(bot, chat_id: int, receipt_name: str, name: str) -> None:
    """Clear the shopping-list entry a just-named receipt item turns out to be.

    The receipt was matched against the list before the user said what the
    item is, so "PUSH UP" could miss "Soutien branco" then — the real name
    gives it a second chance.
    """
    list_names = [entry["name"] for entry in shopping_list.get_all_items()]
    match = choose_list_match(receipt_name, name, "", list_names)
    if not match:
        return
    history_id = shopping_list.remove_item(match, reason="receipt", source=receipt_name)
    if history_id:
        await bot.send_message(
            chat_id, f"Cleared '{match}' from your shopping list.",
            reply_markup=_put_back_keyboard([(history_id, match)]),
        )


def _pending_name_item() -> str | None:
    """The item whose what-is-it question is open, if any."""
    pending = crud.get_pending_profile_item()
    return pending[0] if pending and pending[1] == "name" else None


async def handle_product_ok(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle ✅ Yes on "I think it's: cheese"."""
    query = update.callback_query
    await query.answer()
    name = _pending_name_item()
    if not name:
        await query.edit_message_text("Already answered.")
        return
    product = (crud.get_item(name) or {}).get("product")
    if not product:
        await query.edit_message_text(f"🧾 {name}\nWhat is it?",
                                      reply_markup=_product_choices_keyboard(_product_choices(name, None)))
        return
    await query.edit_message_text(f"🧾 {name}: {product} ✓")
    await _confirm_product(context.bot, update.effective_chat.id, name, product)


async def handle_product_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle ❌ No on "I think it's: cheese": offer the other readings as buttons."""
    query = update.callback_query
    await query.answer()
    name = _pending_name_item()
    if not name:
        await query.edit_message_text("Already answered.")
        return
    rejected = (crud.get_item(name) or {}).get("product")
    choices = _product_choices(name, rejected)
    await query.edit_message_text(
        f"🧾 {name}\nNot {rejected} — what is it then?" if choices
        else f"🧾 {name}\nNot {rejected} — what is it then? Type it in a word or two.",
        reply_markup=_product_choices_keyboard(choices) if choices else None,
    )


async def handle_product_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle one of the other readings tapped after ❌ No."""
    query = update.callback_query
    await query.answer()
    name = _pending_name_item()
    if not name:
        await query.edit_message_text("Already answered.")
        return
    product = query.data.split(":", 1)[1]
    await query.edit_message_text(f"🧾 {name}: {product} ✓")
    await _confirm_product(context.bot, update.effective_chat.id, name, product)


async def handle_product_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle "✍️ Something else": the typed answer goes through the usual what-is-it path."""
    query = update.callback_query
    await query.answer()
    name = _pending_name_item()
    if not name:
        await query.edit_message_text("Already answered.")
        return
    await query.edit_message_text(f"🧾 {name}\nType what it is in a word or two (e.g. vegan cream).")


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


def _fill_guesses(name: str, product: str | None) -> None:
    """Answer a new item's profile questions from what's already known, where possible.

    The same product bought under another name lends its answers first
    (crud.copy_product_profile); the item's category default
    (bot.profile_guess) fills what's still unknown. Whatever neither covers
    stays 'unknown' and gets asked. Guesses are shown on the purchase line
    with a ✏️ button (see _fix_buttons), never silently.
    """
    if product:
        crud.copy_product_profile(name, product)
    item = crud.get_item(name) or {}
    kind, lasts = guess_profile(item.get("category"))
    if kind and item.get("treat_or_need", "unknown") == "unknown":
        crud.set_treat_or_need(name, kind)
    if lasts and item.get("lasts", "unknown") == "unknown":
        crud.set_lasts(name, *lasts)


def _profile_note(item: dict) -> str:
    """How an item is filed, for a purchase line: "🍫 treat · used up the same day", ❓ where unknown."""
    kind = _KIND_LABELS.get(item.get("treat_or_need"), "❓ treat or need")
    lasts = item.get("lasts", "unknown")
    how_long = "❓ how long it lasts" if lasts == "unknown" else _describe_lasts(lasts, item.get("shelf_life_days"))
    return f"{kind} · {how_long}"


def _fix_buttons(item_ids: list[int]) -> list[list[InlineKeyboardButton]]:
    """A "✏️ <name>" button per newly filed item, plus "✅ All good" — rows to add under a purchase reply."""
    rows = []
    for item_id in item_ids:
        item = crud.get_item_by_id(item_id)
        if item:
            rows.append([InlineKeyboardButton(f"✏️ {item['name']}", callback_data=f"fix:{item_id}")])
    if rows:
        rows.append([InlineKeyboardButton("✅ All good", callback_data="fixok")])
    return rows


def _fix_keyboard(item_id: int) -> InlineKeyboardMarkup:
    """Treat/need plus the how-long-it-lasts choices, for correcting one item, and its note."""
    kind_row = [
        InlineKeyboardButton("🍫 Treat", callback_data=f"kind_set:{item_id}:treat"),
        InlineKeyboardButton("🧺 Need", callback_data=f"kind_set:{item_id}:need"),
    ]
    extra_row = [
        InlineKeyboardButton("🏷 Category", callback_data=f"cat_edit:{item_id}"),
        InlineKeyboardButton("📝 Note", callback_data=f"note:{item_id}"),
    ]
    return InlineKeyboardMarkup([kind_row, *_shelf_keyboard(f"shelf_set:{item_id}:").inline_keyboard, extra_row])


async def handle_category_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle "🏷 Category" under an item: offer every category, the current one ticked."""
    query = update.callback_query
    await query.answer()
    item = crud.get_item_by_id(int(query.data.split(":", 1)[1]))
    if not item:
        await query.edit_message_text("That item no longer exists.")
        return
    buttons = [
        InlineKeyboardButton(f"✓ {name}" if name == item["category"] else name,
                             callback_data=f"cat_set:{item['id']}:{i}")
        for i, name in enumerate(CATEGORY_NAMES)
    ]
    await query.edit_message_text(
        f"Which category is {item['name']}?",
        reply_markup=InlineKeyboardMarkup([buttons[i:i + 2] for i in range(0, len(buttons), 2)]),
    )


async def handle_category_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle a category tap from "🏷 Category" — Leisure, for instance, takes it out of shopping trips."""
    query = update.callback_query
    await query.answer()
    _prefix, item_id, index = query.data.split(":", 2)
    item = crud.get_item_by_id(int(item_id))
    if not item:
        await query.edit_message_text("That item no longer exists.")
        return
    category = CATEGORY_NAMES[int(index)]
    crud.change_item_category(item["name"], category)
    item = crud.get_item_by_id(int(item_id))
    await query.edit_message_text(
        f"{item['name']}: {category}. {_profile_note(item)}.", reply_markup=_fix_keyboard(item["id"])
    )


async def item_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /item <name> — show an item's answers with buttons to change them."""
    name = " ".join(context.args).strip()
    if not name:
        await update.message.reply_text("Send /item with the item's name, e.g. /item matcha")
        return
    item = crud.get_item(name)
    if not item:
        await update.message.reply_text(f"I don't have an item called '{name}' — /list_items shows them all.")
        return
    note = f"\n📝 {item['notes']}" if item.get("notes") else ""
    await update.message.reply_text(
        f"{item['name']} — {item.get('category') or 'no category'} · {_profile_note(item)}{note}\nChange it:",
        reply_markup=_fix_keyboard(item["id"]),
    )


def _related_notes(entry: str) -> str:
    """Notes of every item of the same kind as a list entry — favourites and don't-buys alike.

    "cheese" (or "Käse") on the list brings up every cheese with a note,
    whatever its brand. One line per item, ready to append to a reply.
    """
    lines = []
    related = [i for i in crud.get_noted_items() if same_kind(entry, [i["name"], i["product"]])]
    # ⭐ favourites first ("⭐ Favourite" before "⭐ Second favourite"), then 🚫 don't-buys, then the rest.
    order = {"⭐": 0, "🚫": 1}
    related.sort(key=lambda i: (order.get(i["notes"][:1], 2), i["notes"] if i["notes"][:1] == "⭐" else ""))
    for item in related:
        mark = "" if item["notes"][:1] in "⭐🚫" else "📝 "
        lines.append(f"\n  {mark}{item['name']} — {item['notes']}")
    return "".join(lines)


async def _ask_for_note(reply, context: ContextTypes.DEFAULT_TYPE, item: dict) -> None:
    """Start waiting for the typed note on an item (confirmed like any typed message)."""
    context.chat_data.pop("note_pick", None)
    context.chat_data["note_item"] = item["id"]
    current = f"\nRight now: {item['notes']}" if item.get("notes") else ""
    await reply(
        f"What should I remember about {item['name']}? E.g. a size or a brand. "
        f"Send '-' to remove the note.{current}"
    )


async def handle_note_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle "📝 Note" under an item: ask for the note."""
    query = update.callback_query
    await query.answer()
    item = crud.get_item_by_id(int(query.data.split(":", 1)[1]))
    if not item:
        await query.message.reply_text("That item no longer exists.")
        return
    await _ask_for_note(query.message.reply_text, context, item)


async def note_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /note [item] — write down something to remember about an item (a size, a brand)."""
    name = " ".join(context.args).strip()
    if name:
        item = crud.get_item(name)
        if not item:
            await update.message.reply_text(f"I don't have an item called '{name}' yet.")
            return
        await _ask_for_note(update.message.reply_text, context, item)
        return
    context.chat_data.pop("note_item", None)
    context.chat_data["note_pick"] = True
    await update.message.reply_text("Which item is the note for? Type its name.")


async def _handle_note_pick(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A confirmed typed item name after /note: ask for the note on it."""
    name = update.message.text.strip()
    item = crud.get_item(name)
    if not item:
        await update.message.reply_text(f"I don't have an item called '{name}'. Type its name as on /list_items.")
        return
    await _ask_for_note(update.message.reply_text, context, item)


async def _handle_note_answer(update, context: ContextTypes.DEFAULT_TYPE, item_id: int) -> None:
    """A confirmed typed note: save it ('-' removes it)."""
    item = crud.get_item_by_id(item_id)
    if not item:
        await update.message.reply_text("That item no longer exists.")
        return
    text = update.message.text.strip()
    note = None if text == "-" else text
    crud.set_item_note(item["name"], note)
    await update.message.reply_text(
        f"📝 Saved for {item['name']}: {note}" if note else f"Note removed from {item['name']}."
    )


def _card_title(item: dict) -> str:
    """ "Cheese (LEERDAMMER CAR.)" — the product first, the receipt wording only when it adds something."""
    product = item.get("product")
    if not product or product.casefold() in item["name"].casefold():
        return item["name"]
    return f"{product[0].upper()}{product[1:]} ({item['name']})"


def _card_lasts_line(item: dict) -> str:
    lasts = item.get("lasts", "unknown")
    if lasts == "same_day":
        return "🍽 Used up the same day"
    if lasts == "one_off":
        return "🎂 Bought once — no reminders"
    if lasts == "days":
        return f"⏳ Lasts about {item['shelf_life_days']} days"
    return "⏳ ❓ How long does it last?"


def _card_text(item: dict) -> str:
    """The filing card: how the bot would file a new item, one line per answer."""
    kind = {"treat": "🍫 Treat", "need": "🧺 Need"}.get(item.get("treat_or_need"), "❓ Treat or need?")
    category = item.get("category") or "❓ Category?"
    return (f"{_card_title(item)} — I'd file it as:\n"
            f"• {kind}\n• {_card_lasts_line(item)}\n• 🏷 {category}")


def _card_keyboard(item: dict) -> InlineKeyboardMarkup:
    """✅ once every line is answered; one button per line to change it."""
    item_id = item["id"]
    rows = []
    if _is_profiled(item) and item.get("category"):
        rows.append([InlineKeyboardButton("✅ Right", callback_data=f"card_ok:{item_id}")])
    kind = item.get("treat_or_need")
    if kind in ("treat", "need"):
        other = "need" if kind == "treat" else "treat"
        rows.append([InlineKeyboardButton(f"🔁 It's a {other}", callback_data=f"card_kind:{item_id}:{other}")])
    else:
        rows.append([InlineKeyboardButton("🍫 Treat", callback_data=f"card_kind:{item_id}:treat"),
                     InlineKeyboardButton("🧺 Need", callback_data=f"card_kind:{item_id}:need")])
    rows.append([InlineKeyboardButton("⏳ How long", callback_data=f"card_lasts:{item_id}"),
                 InlineKeyboardButton("🏷 Category", callback_data=f"card_cat:{item_id}")])
    return InlineKeyboardMarkup(rows)


async def handle_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the filing card's buttons: change one line and show the card again, or ✅ to finish."""
    query = update.callback_query
    await query.answer()
    action, item_id, *rest = query.data.split(":")
    item = crud.get_item_by_id(int(item_id))
    if not item:
        await query.edit_message_text("That item no longer exists.")
        return
    name = item["name"]
    if action == "card_ok":
        if item["name_status"] != "card":
            await query.edit_message_text(f"{_card_title(item)}: already filed.")
            return
        crud.set_name_status(name, None)
        await query.edit_message_text(f"✅ {_card_title(item)}: {_profile_note(item)} · 🏷 {item['category']}")
        await send_pending_profile_question(context.bot, query.message.chat_id)
        return
    if action == "card_lasts":
        await query.edit_message_text(f"How long does {item.get('product') or name} last?",
                                      reply_markup=_shelf_keyboard(f"card_setl:{item['id']}:"))
        return
    if action == "card_cat":
        buttons = [
            InlineKeyboardButton(f"✓ {category}" if category == item["category"] else category,
                                 callback_data=f"card_setc:{item['id']}:{i}")
            for i, category in enumerate(CATEGORY_NAMES)
        ]
        await query.edit_message_text(f"Which category is {item.get('product') or name}?",
                                      reply_markup=InlineKeyboardMarkup([buttons[i:i + 2] for i in range(0, len(buttons), 2)]))
        return
    if action == "card_kind":
        crud.set_treat_or_need(name, rest[0])
    elif action == "card_setl":
        if rest[0] == "custom":
            context.chat_data["shelf_edit_item"] = item["id"]
            await query.edit_message_text(f"How many days does {item.get('product') or name} last? Reply with a number.")
            return
        _apply_shelf_edit(name, *_lasts_from_choice(rest[0]))
    elif action == "card_setc":
        crud.change_item_category(name, CATEGORY_NAMES[int(rest[0])])
    item = crud.get_item_by_id(item["id"])
    await query.edit_message_text(_card_text(item), reply_markup=_card_keyboard(item))


async def handle_fix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle "✏️ <name>" under a purchase reply: offer that item's answers to change."""
    query = update.callback_query
    await query.answer()
    item = crud.get_item_by_id(int(query.data.split(":", 1)[1]))
    if not item:
        await query.message.reply_text("That item no longer exists.")
        return
    await query.message.reply_text(
        f"{item['name']}: {_profile_note(item)}. Change it:", reply_markup=_fix_keyboard(item["id"])
    )


async def handle_kind_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle 🍫 Treat / 🧺 Need on an item's change keyboard; how long it lasts stays changeable."""
    query = update.callback_query
    await query.answer()
    _prefix, item_id, kind = query.data.split(":", 2)
    item = crud.get_item_by_id(int(item_id))
    if not item:
        await query.edit_message_text("That item no longer exists.")
        return
    crud.set_treat_or_need(item["name"], kind)
    item = crud.get_item_by_id(int(item_id))
    await query.edit_message_text(
        f"{item['name']}: {_profile_note(item)}. Change it:", reply_markup=_fix_keyboard(item["id"])
    )


async def handle_fix_ok(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle "✅ All good": drop the ✏️ buttons, keep any ↩️ put-back ones."""
    query = update.callback_query
    await query.answer("Thanks!")
    remaining = [
        row for row in (query.message.reply_markup.inline_keyboard if query.message.reply_markup else [])
        if not row[0].callback_data.startswith(("fix:", "fixok"))
    ]
    await query.edit_message_reply_markup(InlineKeyboardMarkup(remaining) if remaining else None)


def _is_profiled(item: dict) -> bool:
    """Whether both profiling answers (treat or need, how long it lasts) are in."""
    return item.get("treat_or_need", "unknown") != "unknown" and item.get("lasts", "unknown") != "unknown"


async def handle_profile_kind_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle a tap on the treat-or-need profiling keyboard.

    Buttons from before the split ("profile_type:luxury" etc.) still in the
    chat just get the current question re-sent.
    """
    query = update.callback_query
    await query.answer()
    pending = crud.get_pending_profile_item()
    if query.data.startswith("profile_type:") or not pending or pending[1] != "treat_or_need":
        await query.edit_message_text("That question changed — here's the current one.")
        await send_pending_profile_question(context.bot, update.effective_chat.id)
        return
    name, _stage = pending
    kind = query.data.split(":", 1)[1]
    crud.set_treat_or_need(name, kind)
    await query.edit_message_text(f"{name}: {_KIND_LABELS[kind]}.")
    await send_pending_profile_question(context.bot, update.effective_chat.id)


async def handle_profile_shelf_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle a button press on the shelf-life profiling keyboard."""
    query = update.callback_query
    await query.answer()
    pending = crud.get_pending_profile_item()
    if not pending or pending[1] != "lasts":
        await query.edit_message_text("Already answered.")
        return
    name, _stage = pending
    choice = query.data.split(":", 1)[1]
    if choice == "custom":
        await query.edit_message_text(f"How many days does {name} usually last? Reply with a number.")
        return
    lasts, days = _lasts_from_choice(choice)
    crud.set_lasts(name, lasts, days)
    item = crud.get_item(name)
    await query.edit_message_text(
        f"{name}: {_describe_lasts(lasts, days)}.", reply_markup=_shelf_change_keyboard(item["id"])
    )
    await send_pending_profile_question(context.bot, update.effective_chat.id)


async def _confirm_lasts(bot, chat_id: int, name: str, lasts: str, days: int | None) -> None:
    """Echo a typed how-long-it-lasts answer back with a button to change it."""
    item = crud.get_item(name)
    await bot.send_message(
        chat_id, f"{name}: {_describe_lasts(lasts, days)}.", reply_markup=_shelf_change_keyboard(item["id"])
    )


def _apply_shelf_edit(name: str, lasts: str, days: int | None) -> None:
    """Save a corrected how-long-it-lasts and restart that item's alerts from it."""
    crud.set_lasts(name, lasts, days)
    crud.mark_spare_alert_pending(name, pending=False)
    crud.mark_checkin_pending(name, pending=False)


async def handle_shelf_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle "✏️ Change" under a shelf-life answer: offer the choices again."""
    query = update.callback_query
    await query.answer()
    item_id = int(query.data.split(":", 1)[1])
    item = crud.get_item_by_id(item_id)
    if not item:
        await query.edit_message_text("That item no longer exists.")
        return
    await query.edit_message_text(
        f"How long does {item['name']} usually last?", reply_markup=_shelf_keyboard(f"shelf_set:{item_id}:")
    )


async def handle_shelf_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle a choice on the "✏️ Change" shelf-life keyboard."""
    query = update.callback_query
    await query.answer()
    _prefix, item_id, choice = query.data.split(":", 2)
    item = crud.get_item_by_id(int(item_id))
    if not item:
        await query.edit_message_text("That item no longer exists.")
        return
    if choice == "custom":
        context.chat_data["shelf_edit_item"] = item["id"]
        await query.edit_message_text(f"How many days does {item['name']} usually last? Reply with a number.")
        return
    lasts, days = _lasts_from_choice(choice)
    _apply_shelf_edit(item["name"], lasts, days)
    await query.edit_message_text(
        f"{item['name']}: {_describe_lasts(lasts, days)}.", reply_markup=_shelf_change_keyboard(item["id"])
    )


async def _handle_shelf_edit_answer(update: Update, context: ContextTypes.DEFAULT_TYPE, item_id: int):
    """Interpret a typed number of days after "✏️ Change" → "Other: insert"."""
    item = crud.get_item_by_id(item_id)
    if not item:
        await update.message.reply_text("That item no longer exists.")
        return
    answer = _lasts_from_text(update.message.text)
    if answer is None:
        context.chat_data["shelf_edit_item"] = item_id
        await update.message.reply_text("Reply with a number of days (2 or more), 'same day', or 'one-off'.")
        return
    _apply_shelf_edit(item["name"], *answer)
    item = crud.get_item_by_id(item_id)
    if item.get("name_status") == "card":
        await update.message.reply_text(_card_text(item), reply_markup=_card_keyboard(item))
        return
    await update.message.reply_text(
        f"{item['name']}: {_describe_lasts(*answer)}.", reply_markup=_shelf_change_keyboard(item_id)
    )


_CHECKIN_NO_WORDS = {"no", "n", "ran out", "gone", "finished", "empty"}
_CHECKIN_YES_WORDS = {"yes", "y", "still good", "still lasts", "still have it"}
_CHECKIN_EXTEND_DAYS = 3


def _apply_checkin_answer(item: dict, still_have: bool) -> tuple[str, InlineKeyboardMarkup | None]:
    """Record a check-in answer and say what happened, in product terms ("cheese", not the brand).

    A "still have it" answer means the estimate was too short — push it out
    a few days so the next check-in isn't immediate, while a real early
    repurchase (if one happens) will keep correcting it down via the usual
    signal in expenses.log_expense. A "no" offers to put it on the list.
    """
    crud.mark_checkin_pending(item["name"], pending=False)
    need = _need_name(item)
    if still_have:
        crud.bump_shelf_life(item["name"], _CHECKIN_EXTEND_DAYS)
        return f"Good — I'll ask about {need} again later.", None
    return f"Noted — {need} ran out.", InlineKeyboardMarkup(
        [[InlineKeyboardButton(f"🛒 Add {need} to list", callback_data=f"spare_add:{item['id']}")]]
    )


async def _handle_checkin_answer(update: Update, context: ContextTypes.DEFAULT_TYPE, item_name: str):
    """Interpret a typed reply to a pending check-in (the ✅/❌ buttons are the usual way)."""
    text = update.message.text.strip().lower()
    item = crud.get_item(item_name)
    if text in _CHECKIN_NO_WORDS or "no" in text.split():
        still_have = False
    elif text in _CHECKIN_YES_WORDS or "yes" in text.split():
        still_have = True
    else:
        await update.message.reply_text("Reply 'yes' or 'no'.")
        return
    reply, markup = _apply_checkin_answer(item, still_have)
    await update.message.reply_text(reply, reply_markup=markup)


async def handle_checkin_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle ✅/❌ under "Do you still have cheese?"."""
    query = update.callback_query
    await query.answer()
    _prefix, answer, item_id = query.data.split(":", 2)
    item = crud.get_item_by_id(int(item_id))
    if not item or not item["checkin_pending"]:
        await query.edit_message_text("Already answered — thanks.")
        return
    reply, markup = _apply_checkin_answer(item, answer == "yes")
    await query.edit_message_text(reply, reply_markup=markup)


def _pending_question(context: ContextTypes.DEFAULT_TYPE) -> tuple[str, object, str] | None:
    """The open question a typed message could be answering, as (kind, key, description).

    key identifies the exact question (so a confirmation tapped after the
    question changed is caught), description says it in words for the
    confirmation prompt. A category question is buttons-only, so text never
    answers it.
    """
    note_item = context.chat_data.get("note_item")
    if note_item:
        item = crud.get_item_by_id(note_item) or {}
        return "note", note_item, f"the note for {item.get('name', 'that item')}"
    if context.chat_data.get("note_pick"):
        return "note_pick", True, "which item the note is for"
    onboarding = context.chat_data.get("onboarding")
    if onboarding:
        return "onboarding", onboarding.get("stage"), "your monthly budget"
    goal = context.chat_data.get("awaiting_goal")
    if goal:
        what = {"name": "what you're saving for", "amount": "how much you need"}.get(goal["stage"], "the goal date")
        return "goal", goal["stage"], what
    shelf_edit_item = context.chat_data.get("shelf_edit_item")
    if shelf_edit_item:
        item = crud.get_item_by_id(shelf_edit_item) or {}
        return "shelf_edit", shelf_edit_item, f"how many days {item.get('name', 'that item')} lasts"
    profile = crud.get_pending_profile_item()
    if profile and profile[1] not in ("category", "card"):
        name, stage = profile
        what = {
            "name": f'what "{name}" is',
            "lasts": f"how long {name} lasts",
        }.get(stage, f"whether {name} is a treat or a need")
        return "profile", profile, what
    checkin = crud.get_pending_checkin_item()
    if checkin:
        return "checkin", checkin, f"whether you still have {checkin}"
    return None


async def _answer_pending_question(update, context: ContextTypes.DEFAULT_TYPE, kind: str) -> None:
    """Hand a confirmed typed answer to the handler for that kind of question."""
    if kind == "note":
        await _handle_note_answer(update, context, context.chat_data.pop("note_item"))
    elif kind == "note_pick":
        context.chat_data.pop("note_pick", None)
        await _handle_note_pick(update, context)
    elif kind == "onboarding":
        await _handle_onboarding_text(update, context, context.chat_data["onboarding"])
    elif kind == "goal":
        await _handle_goal_answer(update, context, context.chat_data["awaiting_goal"])
    elif kind == "shelf_edit":
        await _handle_shelf_edit_answer(update, context, context.chat_data.pop("shelf_edit_item"))
    elif kind == "profile":
        await _handle_profile_answer(update, context, crud.get_pending_profile_item())
    else:
        await _handle_checkin_answer(update, context, crud.get_pending_checkin_item())


def _typed_purchase_store(name: str, unit_price: float) -> str | None:
    """The store a typed purchase came from, if its price singles one out (see expenses.guess_store)."""
    item = crud.get_item(name)
    return expenses.guess_store(name, item.get("product") if item else None, unit_price)


def _split_note(line: str) -> tuple[str, str | None]:
    """Split "Push Up Bra 35.90 // UK/USA 34B" into the line and its note (None without "//")."""
    line, sep, note = line.partition("//")
    return line.strip(), (note.strip() or None) if sep else None


def _preview_list_lines(text: str) -> list[str]:
    """Describe what _apply_list_lines would do with each line, without writing anything."""
    preview = []
    for line in (line.strip() for line in text.splitlines()):
        if not line:
            continue
        line, note = _split_note(line)
        if line.startswith("-"):
            name = line[1:].strip()
            preview.append(f"• remove {name} from your shopping list" if name else f"• skip '{line}' (not understood)")
            continue
        try:
            name, qty, unit_price = parse_line(line)
        except ValueError:
            preview.append(f"• skip '{line}' (not understood)")
            continue
        if unit_price is not None:
            store = _typed_purchase_store(name, unit_price)
            where = f" at {store} (same price as before)" if store else ""
            preview.append(f"• log a purchase: {qty}x {name} at €{unit_price:.2f} each{where}")
        else:
            preview.append(f"• add {qty}x {name} to your shopping list")
        if note:
            preview[-1] += f"\n  📝 new note: {note}"
        else:
            preview[-1] += _related_notes(name)
    return preview


def _apply_list_lines(text: str, purchase_date: str | None = None) -> tuple[list[str], list[int] | None, bool]:
    """Apply typed lines to the shopping list (or log priced lines as purchases).

    purchase_date (ISO date) logs the purchases on an earlier day — "📅 it
    was yesterday" — instead of now.

    Returns:
        (replies, new_item_ids, list_changed): one reply line per input
        line; the ids of items a purchase line created (to offer ✏️ on
        their guessed answers) — None if no line was a purchase at all;
        and whether any line added to or removed from the shopping list.
    """
    replies = []
    new_item_ids = None
    list_changed = False
    trip_key = f"typed:{datetime.now(tz=timezone.utc).isoformat()}"  # one visit per typed message
    for line in (line for line in text.splitlines() if line.strip()):
        line, note = _split_note(line)
        stripped = line.strip()
        if stripped.startswith("-"):
            name = stripped[1:].strip()
            if not name:
                replies.append(f"Couldn't understand: '{line}'")
                continue
            if shopping_list.remove_item(name, reason="manual"):
                replies.append(f"Removed {name} from your shopping list.")
                list_changed = True
            else:
                replies.append(f"'{name}' wasn't on your list.")
            continue
        try:
            name, qty, unit_price = parse_line(line)
        except ValueError:
            replies.append(f"Couldn't understand: '{line}'")
            continue
        if unit_price is not None:
            is_new = crud.get_item(name) is None
            line, _cleared_id = _log_purchase(
                name, qty, unit_price, store=_typed_purchase_store(name, unit_price),
                category=infer_category(name), matched_list_item=name, trip_key=trip_key,
                receipt_date=purchase_date,
            )
            if note:
                crud.set_item_note(name, note)
                line += f"\n  📝 {note}"
            replies.append(line)
            new_item_ids = new_item_ids or []
            if is_new and (logged := crud.get_item(name)):
                new_item_ids.append(logged["id"])
            continue
        shopping_list.add_item(name, qty, category=infer_category(name))
        list_changed = True
        reply = f"Added {qty}x {name} to your shopping list."
        if note:
            # Notes live on the item, which exists once it's been bought.
            reply += (f"\n  📝 {note}" if crud.set_item_note(name, note)
                      else "\n  (note not saved — I can keep notes once you've bought it)")
        replies.append(reply)
    return replies, new_item_ids, list_changed


def _could_be_an_answer(text: str, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Whether a typed message could answer an open question at all.

    A line with a price ("sesame ring 1,49") is always a purchase — no
    question is answered with a name and a price — and a message of several
    lines is a list. A note the user just asked to write can be anything.
    Asking "is this your answer to what SCHLAGCREME VEGA is?" there only
    put a real expense at risk of being lost.
    """
    if context.chat_data.get("note_item") or context.chat_data.get("note_pick"):
        return True  # a note can say anything, prices included
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) > 1:
        return False
    for line in lines:
        try:
            if parse_line(_split_note(line)[0])[2] is not None:
                return False
        except ValueError:
            pass
    return True


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle plain-text messages: show what they'd change, and wait for a tap to do it.

    Nothing typed is written to the DB straight away. Each line is a
    shopping-list entry ("bananas"), a removal ("- bananas"), or a purchase
    when it includes a price ("Matcha 2.50", see bot.parser.parse_line); the
    bot lists what it would do and asks to confirm. If a question is open
    (onboarding, /set_goal, item profiling, a shelf-life check-in), the
    message might be the answer to it instead, so both options are offered
    rather than guessing — typing "bananas" while a question was open used
    to silently become its answer.

    Only the latest typed message can be confirmed; tapping an older one's
    buttons says it expired.
    """
    text = update.message.text
    pending = _pending_question(context) if _could_be_an_answer(text, context) else None
    preview = _preview_list_lines(text)
    actionable = any(not line.startswith("• skip") for line in preview)
    token = context.chat_data.get("typed_token", 0) + 1
    context.chat_data["typed_token"] = token
    context.chat_data["typed"] = {"token": token, "text": text, "pending": pending[:2] if pending else None}
    buttons = []
    if pending:
        body = f"Is this your answer to {pending[2]}?"
        if actionable:
            body += "\n\nOr should I:\n" + "\n".join(preview)
        buttons.append([InlineKeyboardButton("💬 Yes, it's my answer", callback_data=f"typed:answer:{token}")])
        if actionable:
            buttons.append([InlineKeyboardButton("🛒 No, do the list changes", callback_data=f"typed:list:{token}")])
    elif actionable:
        body = "Just to be sure, I'll:\n" + "\n".join(preview)
        buttons.append([InlineKeyboardButton("✅ Yes, do it", callback_data=f"typed:list:{token}")])
        if any(line.startswith("• log a purchase") for line in preview):
            # Expenses without a receipt are often typed in the day after.
            buttons.append([InlineKeyboardButton("📅 Yes — it was yesterday",
                                                 callback_data=f"typed:yesterday:{token}")])
    else:
        context.chat_data.pop("typed", None)
        await update.message.reply_text("\n".join(f"Couldn't understand: '{line.strip()}'"
                                                 for line in text.splitlines() if line.strip()))
        return
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data=f"typed:cancel:{token}")])
    await update.message.reply_text(body, reply_markup=InlineKeyboardMarkup(buttons))


def _replayed_update(query, text: str):
    """A stand-in for the original text update, so answer handlers run unchanged on confirm.

    The handlers only read message.text, call message.reply_text and use
    effective_chat.id; replies go to the chat as new messages.
    """
    chat_id = query.message.chat_id
    bot = query.get_bot()

    async def reply_text(reply, **kwargs):
        return await bot.send_message(chat_id, reply, **kwargs)

    return SimpleNamespace(
        message=SimpleNamespace(text=text, reply_text=reply_text),
        effective_chat=SimpleNamespace(id=chat_id),
    )


async def handle_typed_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Carry out (or drop) a typed message once its confirmation button is tapped."""
    query = update.callback_query
    await query.answer()
    _prefix, action, token = query.data.split(":", 2)
    typed = context.chat_data.get("typed")
    if not typed or typed["token"] != int(token):
        await query.edit_message_text("That one expired — nothing was saved. Send it again if you still want it.")
        return
    context.chat_data.pop("typed")
    if action == "cancel":
        await query.edit_message_text("Cancelled — nothing saved.")
        return
    if action == "answer":
        pending = _pending_question(context)
        if not pending or pending[:2] != typed["pending"]:
            await query.edit_message_text("That question changed in the meantime — nothing was saved.")
            return
        await query.edit_message_text(f"💬 Answering {pending[2]}: {typed['text']}")
        await _answer_pending_question(_replayed_update(query, typed["text"]), context, pending[0])
        return
    purchase_date = None
    if action == "yesterday":
        purchase_date = (datetime.now(tz=_LOCAL_TZ).date() - timedelta(days=1)).isoformat()
    replies, new_item_ids, list_changed = _apply_list_lines(typed["text"], purchase_date)
    if purchase_date:
        replies.insert(0, f"📅 Logged for yesterday, {datetime.fromisoformat(purchase_date).strftime('%-d %b')}:")
    rows = _fix_buttons(new_item_ids or [])
    await query.edit_message_text("\n".join(replies), reply_markup=InlineKeyboardMarkup(rows) if rows else None)
    if list_changed:
        await context.bot.send_message(query.message.chat_id, _shopping_list_text())
    if new_item_ids is not None:
        await send_pending_profile_question(context.bot, query.message.chat_id)


def _shopping_list_text() -> str:
    """The current shopping list, grouped by category, as one message."""
    items = shopping_list.get_all_items()
    if not items:
        return "Your shopping list is empty."
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
        lines = [f"• {i['name']} ({i['quantity']}x){_related_notes(i['name'])}"
                 for i in by_category[category]]
        sections.append(f"{category}:\n" + "\n".join(lines))
    return "Shopping list:\n\n" + "\n\n".join(sections)


async def show_shopping_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /list — display the current shopping list, grouped by category."""
    await update.message.reply_text(_shopping_list_text())


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


def _purchased_at(receipt_date: str | None) -> datetime | None:
    """When a receipt's purchase happened, if it was before today (noon that day); None means now."""
    if not receipt_date or receipt_date >= datetime.now(tz=_LOCAL_TZ).date().isoformat():
        return None
    return datetime.combine(datetime.fromisoformat(receipt_date).date(), time(12, 0), tzinfo=timezone.utc)


def _log_purchase(
    name, qty, price, *, store=None, category=None, product=None, matched_list_item=None, ask_name=False,
    clear_reason="purchase", source=None, receipt_date=None, trip_key=None,
) -> tuple[str, int | None]:
    """Log one purchased item (inventory + expense) and describe it for a reply.

    Shared by receipt processing and a plain-text line that includes a
    price (see handle_text) — same underlying purchase, same bookkeeping,
    just a different source for name/qty/price/category.

    Returns:
        (line, cleared_id): a single "• ..." reply line, including a
        duplicate-purchase notice or a price-delta/spike note when
        applicable, and the shopping-list history id of the entry this
        purchase cleared (None if it cleared nothing) — so a caller can
        offer to put it back.
    """
    if expenses.is_duplicate_purchase(name, price, purchase_date=receipt_date):
        return (
            f"• {qty}x {name} at €{price:.2f} each — skipped, this exact item/price "
            "was already logged in the last hour (looks like the same receipt sent twice)"
        ), None
    existed = crud.get_item(name) is not None
    crud.add_item(name, qty, category=category, product=product)
    asking = ask_name and not existed
    if asking:
        # What it is gets asked first; nothing is guessed from an unconfirmed product.
        crud.mark_name_pending(name)
    elif not existed:
        _fill_guesses(name, product)
    delta = expenses.get_price_delta(name, price, store=store)
    expenses.log_expense(name, qty, price, store=store, purchased_at=_purchased_at(receipt_date), trip_key=trip_key)
    # Show what the bot understood the item to be, so a wrong guess is visible.
    product = (crud.get_item(name) or {}).get("product") or product
    shown = f"{name} ({product})" if product and product.casefold() not in name.casefold() else name
    line = f"• {qty}x {shown} at €{price:.2f} each"
    if asking:
        line += " — 🆕 I'll ask what it is"
    elif not existed:
        line += f" — {_profile_note(crud.get_item(name) or {})}"
    elif note := (crud.get_item(name) or {}).get("notes"):
        line += f"\n  📝 {note}"  # e.g. "don't buy again" — shown when it's bought again anyway
    cleared_id = (
        shopping_list.remove_item(matched_list_item, reason=clear_reason, source=source or name)
        if matched_list_item else None
    )
    if cleared_id:
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
    return line, cleared_id


def _resolve_receipt_name(item: dict) -> tuple[str, str | None, str | None, bool]:
    """Turn a receipt line's wording into (name, category, product, ask_name).

    A wording the user already named maps straight to their name, category
    and product (falling back to the model's product if the alias predates
    products). Otherwise the keyword list beats the model's category guess
    (deterministic, and it knows "Käse" is cheese), the product is the
    model's reading of what the item generically is, and the user gets asked
    what the item really is.
    """
    model_product = item.get("product") or None
    alias = crud.get_alias(item["name"])
    if alias:
        return alias["canonical_name"], alias["category"], alias.get("product") or model_product, False
    keyword = infer_category(item["name"])
    if keyword != "Other":
        return item["name"], keyword, model_product, True
    # The model's guess only counts if it's one of our own labels — it has
    # filled this field with VAT codes ("A", "B") before.
    guess = item.get("category")
    category = guess if guess in CATEGORY_NAMES and guess != "Other" else None
    return item["name"], category, model_product, True


def _put_back_keyboard(cleared: list[tuple[int, str]]) -> InlineKeyboardMarkup | None:
    """One "↩️ Put back" button per list entry a receipt cleared, or None if it cleared none."""
    if not cleared:
        return None
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"↩️ Put back {name}", callback_data=f"restore:{history_id}")]
        for history_id, name in cleared
    ])


async def process_receipt_result(parsed: dict, receipt_id: int | None = None) -> tuple[str, InlineKeyboardMarkup | None]:
    """Log a parsed receipt's items and build the summary reply text.

    Used by the local receipt worker after it runs parse_receipt. New
    items are filed with a guess where one exists (_fill_guesses), shown on
    their line with a ✏️ button; anything still unknown is picked up by
    crud.get_pending_profile_item() — the caller should follow up with
    send_pending_profile_question() once this returns.

    Args:
        parsed: Output of bot.receipt.parse_receipt.

    Returns:
        The reply text to send back to the user.
    """
    items = parsed["items"]
    store = parsed.get("store") or None
    receipt_date = parsed.get("purchase_date")
    logger.info("Receipt parsed: %d item(s).", len(items))
    if not items:
        return "Couldn't find any items on that receipt.", None
    list_names = [entry["name"] for entry in shopping_list.get_all_items()]
    replies = []
    cleared = []
    new_ids = []
    known_products = crud.get_known_products()
    for item in items:
        name, category, product, ask_name = _resolve_receipt_name(item)
        is_new = crud.get_item(name) is None
        if ask_name and product:
            product = canonical_product(product, known_products)
        list_match = choose_list_match(
            item["name"], name, item["matched_shopping_list_item"], list_names, product=product,
        )
        if list_match:
            list_names.remove(list_match)
        line, cleared_id = _log_purchase(
            name, item["quantity"], item["unit_price"],
            store=store, category=category, product=product, matched_list_item=list_match, ask_name=ask_name,
            clear_reason="receipt", source=item["name"], receipt_date=receipt_date,
            trip_key=f"receipt:{receipt_id}" if receipt_id is not None else None,
        )
        if is_code_only(item["name"]):
            line += " ⚠️ no readable name on the receipt — check this one"
        replies.append(line)
        if cleared_id:
            cleared.append((cleared_id, list_match))
        logged = crud.get_item(name) if is_new else None
        if logged and logged.get("name_status"):
            crud.set_product_options(name, item.get("alternatives") or [])
        elif logged:
            new_ids.append(logged["id"])
    if not receipt_date:
        replies.append("⚠️ I couldn't read the date on this receipt, so it's logged as bought today.")
    if not parsed["reconciled"]:
        replies.append(
            f"⚠️ Heads up: item prices add up to €{parsed['items_total']:.2f} but the "
            f"receipt's total was €{parsed['total_paid']:.2f} — one of the amounts above "
            "is probably off. Worth double-checking against the paper receipt."
        )
    if cleared:
        replies.append("\nWrongly cleared something? Tap to put it back:")
    if new_ids:
        replies.append("\nNew items are filed with my best guess — tap ✏️ to change one.")
    rows = [*(_put_back_keyboard(cleared) or InlineKeyboardMarkup([])).inline_keyboard, *_fix_buttons(new_ids)]
    where = ", ".join(part for part in (
        store, datetime.fromisoformat(receipt_date).strftime("%-d %b") if receipt_date else None) if part)
    header = f"Receipt processed ({where}):" if where else "Receipt processed:"
    return header + "\n" + "\n".join(replies), InlineKeyboardMarkup(rows) if rows else None


async def handle_restore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle "↩️ Put back": restore a cleared shopping-list entry and drop its button."""
    query = update.callback_query
    name = shopping_list.restore_item(int(query.data.split(":", 1)[1]))
    await query.answer(f"{name} is back on your list." if name else "Already back on your list.")
    remaining = [
        row for row in (query.message.reply_markup.inline_keyboard if query.message.reply_markup else [])
        if row[0].callback_data != query.data
    ]
    await query.edit_message_reply_markup(InlineKeyboardMarkup(remaining) if remaining else None)


_REASON_LABELS = {"manual": "you removed it", "receipt": "receipt", "purchase": "typed purchase"}


async def cleared_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /cleared — the last shopping-list removals, each with a put-back button."""
    history = shopping_list.get_history(limit=10)
    if not history:
        await update.message.reply_text("Nothing has been cleared from your list yet.")
        return
    lines = []
    for entry in history:
        why = _REASON_LABELS.get(entry["reason"], entry["reason"])
        if entry["reason"] != "manual" and entry["source"]:
            why += f": {entry['source']}"
        back = " (put back)" if entry["restored"] else ""
        lines.append(f"• {entry['name']} — {entry['removed_at'][:16]} UTC, {why}{back}")
    keyboard = _put_back_keyboard([(e["id"], e["name"]) for e in history if not e["restored"]])
    await update.message.reply_text("Recently cleared from your list:\n" + "\n".join(lines), reply_markup=keyboard)


async def remind_pending_profile(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Re-send the current item-profiling question, at most once a day.

    A newly-bought item's profile (treat or need, and how long it lasts) is
    asked once right after logging it — but a message
    sent once is easy to miss or dismiss, and an unanswered item silently
    stays unprofiled forever otherwise (excluded from check-ins, "running
    out soon", and the treats/needs split). One nudge a day
    gets it answered eventually without turning into a stream of messages.

    Returns:
        True if a reminder was sent.
    """
    chat_id = settings.get_chat_id()
    if not chat_id or crud.get_pending_profile_item() is None:
        return False
    today = datetime.now(tz=_LOCAL_TZ).date().isoformat()
    if settings.get_profile_reminded_on() == today:
        return False
    settings.set_profile_reminded_on(today)
    await send_pending_profile_question(context.bot, chat_id, force=True)
    return True


async def check_expiring_items(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Ask about the first need past its estimated shelf life, if any.

    One item per call — the rest stay due and come up in later rounds.
    Treats are excluded entirely (see get_checkin_candidates) — a treat
    bought on mood/budget doesn't follow a consumption schedule.

    Returns:
        True if a check-in was sent.
    """
    chat_id = settings.get_chat_id()
    if not chat_id:
        return False
    now = datetime.now(tz=timezone.utc)
    for item in crud.get_checkin_candidates():
        if not item["last_purchase"]:
            continue
        last = datetime.fromisoformat(item["last_purchase"])
        if now >= last + timedelta(days=item["shelf_life_days"]):
            crud.mark_checkin_pending(item["name"])
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"Do you still have {_need_name(item)}?",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Yes", callback_data=f"checkin:yes:{item['id']}"),
                    InlineKeyboardButton("❌ No", callback_data=f"checkin:no:{item['id']}"),
                ]]),
            )
            return True
    return False


def _spare_alert_lead_days(shelf_life_days: int) -> int:
    """Days before the estimated run-out date to alert a par=2 item, capped to the estimate itself."""
    return min(shelf_life_days, max(1, round(shelf_life_days * 0.15)))


def _spare_alert_at(last_purchase: datetime, shelf_life_days: int, quantity: int | None) -> datetime:
    """When a par=2 item's spare needs buying: shortly before the pack in use is the last one.

    Buying 3 packs means two spares already sit in the cupboard, so the
    alert waits until the second-to-last one is used up; buying 1 or 2 alerts
    near the end of the first pack. shelf_life_days is per unit.
    """
    packs_before_last = max(1, (quantity or 1) - 1)
    return last_purchase + timedelta(
        days=packs_before_last * shelf_life_days - _spare_alert_lead_days(shelf_life_days)
    )


def _need_name(item: dict) -> str:
    """What the household needs, brand aside: the product if known, else the item's own name."""
    return item.get("product") or item["name"]


def _spare_alert_text(item: dict) -> str:
    need = _need_name(item)
    return f"Running low on {need} — you'll be on your last one soon. Add {need} to the shopping list?"


def _spare_alert_keyboard(item_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 Add to list", callback_data=f"spare_add:{item_id}")],
        [InlineKeyboardButton("👍 Still have plenty", callback_data=f"spare_plenty:{item_id}")],
        [InlineKeyboardButton("✏️ It lasts longer than that", callback_data=f"shelf_edit:{item_id}")],
        [InlineKeyboardButton("🔕 Stop spare alerts for this", callback_data=f"spare_stop:{item_id}")],
        [InlineKeyboardButton("📝 Note", callback_data=f"note:{item_id}")],
    ])


async def check_spare_stock_alerts(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """For par=2 items, alert (one item per call) ahead of the estimated run-out date so a spare gets bought in time.

    Unlike the par=1 check-in (which waits until the estimate says it's
    already out), a par=2 household wants the spare on hand before that
    point. The message shows the reasoning (when it was bought, how long one
    lasts) so a bad estimate is obvious, with buttons to act on it.

    Returns:
        True if an alert was sent.
    """
    chat_id = settings.get_chat_id()
    if not chat_id:
        return False
    now = datetime.now(tz=timezone.utc)
    default_par_level = settings.get_default_par_level()
    for item in crud.get_par_alert_candidates(default_par_level):
        if not item["last_purchase"]:
            continue
        last = datetime.fromisoformat(item["last_purchase"])
        if now >= _spare_alert_at(last, item["shelf_life_days"], item["last_quantity"]):
            crud.mark_spare_alert_pending(item["name"])
            await context.bot.send_message(
                chat_id=chat_id,
                text=_spare_alert_text(item),
                reply_markup=_spare_alert_keyboard(item["id"]),
            )
            return True
    return False


_SPARE_PLENTY_MIN_EXTEND_DAYS = 3


async def handle_spare_alert_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the buttons under a keep-a-spare alert.

    "Add to list" leaves the alert pending — the next purchase clears it.
    "Still have plenty" means the estimate was too short: stretch it by a
    quarter (at least a few days) and restart the cycle from the new
    estimate. "Stop" switches just this item to replace-when-low.
    """
    query = update.callback_query
    await query.answer()
    action, item_id = query.data.split(":", 1)
    item = crud.get_item_by_id(int(item_id))
    if not item:
        await query.edit_message_text("That item no longer exists.")
        return
    name = item["name"]
    if action == "spare_add":
        need = _need_name(item)
        shopping_list.add_item(need, 1, category=item.get("category") or infer_category(need))
        await query.edit_message_text(f"Added {need} to your shopping list.")
        await context.bot.send_message(query.message.chat_id, _shopping_list_text())
    elif action == "spare_plenty":
        extend = max(_SPARE_PLENTY_MIN_EXTEND_DAYS, round(item["shelf_life_days"] * 0.25))
        crud.bump_shelf_life(name, extend)
        crud.mark_spare_alert_pending(name, pending=False)
        await query.edit_message_text(
            f"Got it — {name} now counts as lasting about {item['shelf_life_days'] + extend} days "
            "each. I'll check again later.",
            reply_markup=_shelf_change_keyboard(item["id"]),
        )
    elif action == "spare_stop":
        crud.set_par_level(name, 1)
        crud.mark_spare_alert_pending(name, pending=False)
        await query.edit_message_text(
            f"No more spare alerts for {name} — you'll just get a check-in once it's probably run out."
        )


_BUDGET_ALERT_THRESHOLDS = (100, 80)


async def check_budget_alert(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Alert once per month when spending crosses 80% or 100% of the household budget.

    Returns:
        True if an alert was sent.
    """
    chat_id = settings.get_chat_id()
    if not chat_id:
        return False
    status = metrics.get_budget_status()
    if status is None:
        return False
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
            return True
    return False


async def proactive_round(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled job: send the single most important proactive message, if any.

    Runs at each of _PROACTIVE_ROUND_TIMES, so the bot volunteers at most
    two messages a day; whatever didn't fit stays due for a later round.
    """
    for job in (check_budget_alert, check_expiring_items, check_spare_stock_alerts, remind_pending_profile):
        if await job(context):
            return


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


async def rename_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /rename <old name> = <new name> — give an item a readable name at any time.

    Receipt abbreviations ("LEERDAMMER CAR") stick once the naming question
    has passed. The old wording is remembered as an alias so future receipts
    land on the new name, and the item's category and profile carry over.
    """
    old_name, sep, new_name = " ".join(context.args).partition("=")
    old_name, new_name = old_name.strip(), new_name.strip()
    if not sep or not old_name or not new_name:
        await update.message.reply_text("Usage: /rename <old name> = <new name>")
        return
    item = crud.get_item(old_name)
    if not item:
        await update.message.reply_text(f"Item '{old_name}' not found — see /list_items for exact names.")
        return
    final_name, merged = crud.rename_item(old_name, new_name)
    if not merged and item["category"]:
        crud.set_item_category(final_name, item["category"])
    crud.save_alias(old_name, final_name, item["category"], item.get("product"))
    if merged:
        await update.message.reply_text(f"Merged '{old_name}' into your existing {final_name}.")
    else:
        await update.message.reply_text(f"Renamed '{old_name}' to {final_name}.")
        await send_pending_profile_question(context.bot, update.effective_chat.id)


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
                    f"(€{spending['treat']:.2f} so far) to land under."
                )
            else:
                budget_line += " — well under pace, nice work 🎉"
        lines.append(budget_line)

    # The split the user's own treat/need answers add up to — nobody
    # totals this for themselves, and it reframes the month more than the
    # headline number does.
    if spending["total"] > 0 and (spending["treat"] or spending["need"]):
        treat_pct = spending["treat"] / spending["total"] * 100
        lines.append(
            f"Treats €{spending['treat']:.2f} ({treat_pct:.0f}%) · "
            f"Needs €{spending['need']:.2f}"
        )
    if spending["unknown"]:
        lines.append(f"Not sorted yet: €{spending['unknown']:.2f}")
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
    # Telegram Desktop opens mini apps in a small fixed popup; the browser
    # button gives the same (password-protected) page a full-size window.
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Open in Telegram", web_app=WebAppInfo(url=f"{url}/login"))],
        [InlineKeyboardButton("🖥 Open in browser", url=f"{url}/login")],
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
    app.add_handler(CommandHandler("cleared", cleared_cmd))
    app.add_handler(CommandHandler("list_items", list_items))
    app.add_handler(CommandHandler("update_item", update_item))
    app.add_handler(CommandHandler("remove_item", remove_item))
    app.add_handler(CommandHandler("my_expenses", my_expenses))
    app.add_handler(CommandHandler("total_spent", total_spent))
    app.add_handler(CommandHandler("par_level", par_level_cmd))
    app.add_handler(CommandHandler("rename", rename_cmd))
    app.add_handler(CommandHandler("note", note_cmd))
    app.add_handler(CommandHandler("item", item_cmd))
    app.add_handler(CommandHandler("set_budget", set_budget_cmd))
    app.add_handler(CommandHandler("set_goal", set_goal_cmd))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("setup", setup_cmd))
    app.add_handler(CommandHandler("dashboard", dashboard_cmd))
    app.add_handler(CallbackQueryHandler(handle_goal_date_choice, pattern=r"^goal_date:"))
    app.add_handler(CallbackQueryHandler(handle_onboarding_choice, pattern=r"^onboard_"))
    app.add_handler(CallbackQueryHandler(handle_profile_kind_choice, pattern=r"^profile_(kind|type):"))
    app.add_handler(CallbackQueryHandler(handle_product_ok, pattern=r"^product_ok$"))
    app.add_handler(CallbackQueryHandler(handle_product_no, pattern=r"^product_no$"))
    app.add_handler(CallbackQueryHandler(handle_product_pick, pattern=r"^product_pick:"))
    app.add_handler(CallbackQueryHandler(handle_product_type, pattern=r"^product_type$"))
    app.add_handler(CallbackQueryHandler(handle_card, pattern=r"^card_(ok|kind|lasts|cat|setl|setc):"))
    app.add_handler(CallbackQueryHandler(handle_restore, pattern=r"^restore:"))
    app.add_handler(CallbackQueryHandler(handle_fix, pattern=r"^fix:"))
    app.add_handler(CallbackQueryHandler(handle_fix_ok, pattern=r"^fixok$"))
    app.add_handler(CallbackQueryHandler(handle_kind_set, pattern=r"^kind_set:"))
    app.add_handler(CallbackQueryHandler(handle_note_button, pattern=r"^note:"))
    app.add_handler(CallbackQueryHandler(handle_category_edit, pattern=r"^cat_edit:"))
    app.add_handler(CallbackQueryHandler(handle_category_set, pattern=r"^cat_set:"))
    app.add_handler(CallbackQueryHandler(handle_name_category, pattern=r"^name_cat:"))
    app.add_handler(CallbackQueryHandler(handle_profile_shelf_choice, pattern=r"^profile_shelf:"))
    app.add_handler(CallbackQueryHandler(handle_shelf_edit, pattern=r"^shelf_edit:"))
    app.add_handler(CallbackQueryHandler(handle_shelf_set, pattern=r"^shelf_set:"))
    app.add_handler(CallbackQueryHandler(handle_spare_alert_choice, pattern=r"^spare_(add|plenty|stop):"))
    app.add_handler(CallbackQueryHandler(handle_typed_choice, pattern=r"^typed:"))
    app.add_handler(CallbackQueryHandler(handle_checkin_choice, pattern=r"^checkin:(yes|no):"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_error_handler(handle_error)
    if app.job_queue is not None:
        for round_time in _PROACTIVE_ROUND_TIMES:
            app.job_queue.run_daily(proactive_round, time=round_time)
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
