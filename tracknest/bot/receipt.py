"""Receipt photo parsing via a local Ollama vision model (free, fully offline)."""

import json
import logging
import re
import subprocess
import time
from datetime import date, datetime, timedelta

import ollama

from bot.categorize import CATEGORY_NAMES
from bot.receipt_lines import classify_line, clean_name

logger = logging.getLogger(__name__)

_MODEL = "minicpm-v4.5"
_client = ollama.Client()
_RECONCILE_TOLERANCE = 0.02
# German VAT rates. A receipt's VAT breakdown (Netto / MwSt / Brutto) prints
# the pre-tax amount right next to the real total, and the model sometimes
# reads that instead — items then sum to exactly total_paid × (1 + rate).
_VAT_RATES = (0.07, 0.19)
# A receipt date older than this is more likely a misread than a real
# receipt being caught up on.
_MAX_RECEIPT_AGE = timedelta(days=366)

_PRODUCT_RULES = (
    "The most general everyday word for this item, lowercase English, as "
    "someone would write it on a shopping list. Drop brand, variety, flavour, "
    "fat level, age and size: any variety of cheese is 'cheese', any pasta "
    "shape is 'pasta', any cow's milk is 'milk' — but a different thing stays "
    "different (oat milk is 'oat milk', not 'milk'). Brands are not products: "
    "'LEERDAMMER CAR' -> 'cheese', 'Rama Original' -> 'margarine', 'Tempo "
    "Taschent.' -> 'tissues'. Empty string if you genuinely can't tell — "
    "never guess."
)

_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "Item name as printed on the receipt, cleaned up into a "
                            "readable product name. Only the product itself — never "
                            "the quantity, price, or tax-code digit/letter printed "
                            "beside it. Empty string if you can't read it — never "
                            "guess or invent a name."
                        ),
                    },
                    "unsure": {
                        "type": "boolean",
                        "description": (
                            "True if any part of this line (name, quantity or price) "
                            "was hard to read, so the shopper should check it."
                        ),
                    },
                    "quantity": {"type": "integer", "description": "Units purchased"},
                    "unit_price": {"type": "number", "description": "Price per single unit, not the line total"},
                    "category": {
                        "type": "string",
                        # Constrained to the bot's own labels: left free-form, the
                        # model copied the VAT code printed beside each price ("A",
                        # "B") into this field.
                        "enum": CATEGORY_NAMES,
                        "description": (
                            "Grocery category this item belongs to, inferred from its "
                            "name even if abbreviated or written in another language "
                            "(e.g. 'Proteinbrötchen' is German for a protein bread "
                            "roll -> Bread/Bakery). Never the single letter or digit "
                            "printed next to the price — that is a VAT code. Use "
                            "Other if unsure."
                        ),
                    },
                    "product": {
                        "type": "string",
                        "description": _PRODUCT_RULES,
                    },
                    "alternatives": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 3,
                        "description": (
                            "Up to 3 OTHER things this item could be, if the product "
                            "reading might be wrong — same style as product (general, "
                            "lowercase English). E.g. 'SCHLAGCREME VEGA' read as "
                            "'cheese' could also be 'vegan whipping cream' or 'cream'. "
                            "Empty if the product is certain."
                        ),
                    },
                },
                "required": ["name", "unsure", "quantity", "unit_price", "category", "product"],
            },
        },
        "total_paid": {
            "type": "number",
            "description": (
                "The final total amount paid, as printed on the receipt (e.g. "
                "'TOTAL', 'SUMME', 'TOTAL DUE', 'Brutto'), VAT included. Never "
                "the 'Netto'/net figure from the VAT breakdown table. Used to "
                "sanity-check the "
                "extracted item prices — see the reconciliation instruction "
                "in the prompt."
            ),
        },
        "store": {
            "type": "string",
            "description": (
                "The store or supplier name printed on the receipt (e.g. "
                "REWE, dm, Amazon). Empty string if illegible."
            ),
        },
        "purchase_date": {
            "type": "string",
            "description": (
                "The date the purchase was made, as printed on the receipt "
                "(German receipts print it like 22.09.2026 or 22.09.26, near "
                "the time or the payment details), written as YYYY-MM-DD. "
                "Empty string if no date is legible — never guess."
            ),
        },
    },
    "required": ["items", "total_paid", "store", "purchase_date"],
}


def _ensure_server_running():
    """Start the local Ollama server on demand if it isn't already running.

    The systemd service is intentionally disabled (no boot autostart) — this
    starts it lazily on first use instead, so it only ever runs when the bot
    actually needs it.
    """
    try:
        _client.list()
        return
    except Exception:
        pass
    subprocess.Popen(
        ["ollama", "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(30):
        time.sleep(1)
        try:
            _client.list()
            return
        except Exception:
            continue
    raise RuntimeError("Ollama server did not start in time")


def _is_net_total_misread(items_total: float, total_paid: float) -> bool:
    """Tell whether total_paid is the receipt's pre-VAT net rather than what was paid.

    Only an exact single-rate match counts: a gap anywhere in the 7-19%
    range would also swallow genuine extraction errors.
    """
    return any(
        abs(total_paid * (1 + rate) - items_total) <= _RECONCILE_TOLERANCE
        for rate in _VAT_RATES
    )


def parse_receipt_date(raw: str, today: date) -> str | None:
    """Turn the model's reading of the receipt date into an ISO date, or None if it can't be trusted.

    Accepts YYYY-MM-DD (as asked) and the printed German forms DD.MM.YYYY /
    DD.MM.YY (in case the model copies them). A date in the future or more
    than _MAX_RECEIPT_AGE old is treated as a misread.
    """
    raw = (raw or "").strip()
    for pattern, fmt in ((r"\d{4}-\d{2}-\d{2}", "%Y-%m-%d"),
                         (r"\d{1,2}\.\d{1,2}\.\d{4}", "%d.%m.%Y"),
                         (r"\d{1,2}\.\d{1,2}\.\d{2}", "%d.%m.%y")):
        if re.fullmatch(pattern, raw):
            try:
                parsed = datetime.strptime(raw, fmt).date()
            except ValueError:
                return None
            return parsed.isoformat() if today - _MAX_RECEIPT_AGE <= parsed <= today else None
    return None


def _items_total(items: list[dict]) -> float:
    """Sum quantity * unit_price across all parsed items, rounded to cents."""
    return round(sum(item["quantity"] * item["unit_price"] for item in items), 2)


def parse_receipt(image_bytes: bytes) -> dict:
    """Extract purchased items from a receipt photo.

    The model only reads the receipt. It is never shown the shopping list:
    when it was, it copied an entry ("Socks - decathlon") as the name of a
    line it couldn't read. Matching against the list happens afterwards, in
    bot.list_match.

    Args:
        image_bytes: Raw JPEG bytes of the receipt photo (Telegram always sends
            photos as JPEG).

    Returns:
        Dict with keys:
        - items: list of dicts (name, quantity, unit_price, category,
          and product — what it generically is, e.g. "cheese", empty if unknown).
        - total_paid: the receipt's printed total, as read by the model.
        - items_total: quantity*unit_price summed across items.
        - reconciled: True if items_total matches total_paid within a cent
          or two — False means a line's price is probably wrong (e.g. a
          multi-unit line's total mistaken for its per-unit price) and the
          caller should warn the user rather than log it silently.
        - store: the store/supplier name as read by the model, empty string
          if illegible.
        - purchase_date: the printed purchase date (ISO), or None if it was
          illegible or implausible — see parse_receipt_date.
    """
    _ensure_server_running()
    prompt = (
        "Read this grocery receipt and record every purchased item: its "
        "name, quantity, and price per unit (not the line total). Also read "
        "the receipt's final total paid and the store or supplier name "
        "printed on it (e.g. REWE, dm, Amazon), and the purchase date "
        "printed on it.\n\n"
        "A small number or letter printed immediately next to an item "
        "(e.g. '1', '2', 'A', 'B') is very often a VAT/tax-rate category "
        "code, not a quantity — German receipts print one of these next to "
        "almost every line. Only treat a number as the quantity if the "
        "line clearly shows a multiplier (e.g. '2 x', '2 Stk', 'Menge: 2') "
        "or the same item appears as separate repeated lines. Otherwise "
        "default quantity to 1.\n\n"
        "Weighed items (fruit, vegetables, cheese counter) print a second "
        "line with the weight and the price per kilo, e.g. 'BANANE  2,12' "
        "followed by '1,064 kg x 1,99 EUR/kg'. For these the unit_price is "
        "the amount printed on the item's own line (2,12) and the quantity "
        "is 1 — never the per-kilo price.\n\n"
        "Every product sits on its own line with its own price. Short codes "
        "printed between a name and its price (e.g. 'X01', 'B', 'A') are till "
        "markers: not products, and not part of any product's name — never "
        "join two lines' names into one.\n\n"
        "Before answering, check your work: multiply each item's quantity "
        "by its unit_price and add them up — this sum must equal the "
        "receipt's total paid. If it doesn't, first check whether you "
        "mistook a tax-code digit for a quantity (see above); only if that "
        "isn't the cause have you likely confused a line's total price "
        "with its per-unit price (e.g. a genuine '2 x €4.45' line where "
        "€4.45 is the total for both units, not €4.45 each). Find the "
        "mismatched line and correct it so the numbers reconcile before "
        "giving your final answer.\n\n"
        "Only write what is printed on this receipt. If you can't read a "
        "line's name, leave the name empty; if any part of a line is hard to "
        "read, set unsure to true. Never guess or fill in — the shopper will "
        "be asked about those lines.\n\n"
        "For each item, also say what it generically is (product): use what "
        "you know about brands and German/Portuguese/English grocery names, so "
        "a brand-only line like 'LEERDAMMER CAR' still becomes 'cheese'."
    )
    response = _client.chat(
        model=_MODEL,
        messages=[{
            "role": "user",
            "content": prompt,
            "images": [image_bytes],
        }],
        format=_RESPONSE_SCHEMA,
    )
    result = json.loads(response.message.content)
    lines = result["items"]
    for line in lines:
        line["name"] = clean_name(line["name"])
        line["product"] = (line.get("product") or "").strip().lower()
        line["alternatives"] = _clean_alternatives(line.get("alternatives"), line["product"])
    total_paid = result["total_paid"]
    store = result.get("store") or ""
    purchase_date = parse_receipt_date(result.get("purchase_date", ""), date.today())
    # A "product" costing exactly the receipt total, next to other products,
    # is the total line misread as an item (the footer "Kundenbeleg" was once
    # logged as a €3.18 "Kuchenbeleg" this way).
    if len(lines) > 1:
        lines = [line for line in lines
                 if abs(line["quantity"] * line["unit_price"] - result["total_paid"]) > _RECONCILE_TOLERANCE
                 or classify_line(line["name"]) != "item"]
    # Deposits/discounts still count toward what was paid; info lines
    # (Normalpreis, Summe, MwSt...) carry no money of their own.
    counted = [line for line in lines if classify_line(line["name"]) != "info"]
    items = [line for line in lines if classify_line(line["name"]) == "item"]
    items_total = _items_total(counted)
    reconciled = abs(items_total - total_paid) <= _RECONCILE_TOLERANCE
    if not reconciled and _is_net_total_misread(items_total, total_paid):
        logger.info(
            "Receipt total_paid %.2f is the pre-VAT net of %.2f — using the gross figure.",
            total_paid, items_total,
        )
        total_paid, reconciled = items_total, True
    if not reconciled:
        logger.warning(
            "Receipt reconciliation mismatch: items summed to %.2f but total_paid was %.2f",
            items_total, total_paid,
        )
    return {
        "items": items,
        "total_paid": total_paid,
        "items_total": items_total,
        "reconciled": reconciled,
        "store": store,
        "purchase_date": purchase_date,
    }


def _clean_alternatives(alternatives, product: str) -> list[str]:
    """Lowercase, de-duplicated other readings of an item, without the main one."""
    cleaned = []
    for alternative in alternatives or []:
        alternative = str(alternative).strip().lower()
        if alternative and alternative != product and alternative not in cleaned:
            cleaned.append(alternative)
    return cleaned[:3]


_GUESS_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "line": {"type": "string", "description": "The item line exactly as given"},
                    "product": {"type": "string", "description": _PRODUCT_RULES},
                },
                "required": ["line", "product"],
            },
        },
    },
    "required": ["items"],
}


def guess_products(names: list[str]) -> dict[str, str]:
    """Guess what each already-known item generically is, from its name alone.

    For items logged before products existed. Only a guess: the user
    confirms each one, since the model is confidently wrong on brand-only
    names it doesn't know.

    Returns:
        {name: product} for each name the model answered, product "" when
        it couldn't tell. Names it skipped or mangled are left out.
    """
    _ensure_server_running()
    prompt = (
        "Item names from a household's grocery receipts (German supermarkets "
        "mostly; food, drinks, household, toiletries or clothing). For each "
        "one, say what it generically is, using what you know about brands "
        "and German/Portuguese/English grocery names.\n\n" + "\n".join(names)
    )
    response = _client.chat(
        model=_MODEL,
        messages=[{"role": "user", "content": prompt}],
        format=_GUESS_SCHEMA,
        options={"temperature": 0},
    )
    wanted = set(names)
    return {
        line["line"]: (line.get("product") or "").strip().lower()
        for line in json.loads(response.message.content)["items"]
        if line.get("line") in wanted
    }
