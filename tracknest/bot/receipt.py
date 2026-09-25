"""Receipt photo parsing via a local Ollama vision model (free, fully offline)."""

import json
import logging
import subprocess
import time

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
                            "beside it."
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
                        "description": (
                            "The most general everyday word for this item, lowercase "
                            "English, as someone would write it on a shopping list. Drop "
                            "brand, variety, flavour, fat level, age and size: any variety "
                            "of cheese is 'cheese', any pasta shape is 'pasta', any cow's "
                            "milk is 'milk' — but a different thing stays different (oat "
                            "milk is 'oat milk', not 'milk'). Brands are not products: "
                            "'LEERDAMMER CAR' -> 'cheese', 'Rama Original' -> 'margarine', "
                            "'Tempo Taschent.' -> 'tissues'. Empty string if you "
                            "genuinely can't tell — never guess."
                        ),
                    },
                    "matched_shopping_list_item": {
                        "type": "string",
                        "description": (
                            "The exact text of the shopping list entry this item corresponds "
                            "to, if any — even if written in a different language, "
                            "abbreviated, or misspelled there. Empty string if it matches "
                            "nothing on the list."
                        ),
                    },
                },
                "required": ["name", "quantity", "unit_price", "category", "product", "matched_shopping_list_item"],
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
    },
    "required": ["items", "total_paid", "store"],
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


def _items_total(items: list[dict]) -> float:
    """Sum quantity * unit_price across all parsed items, rounded to cents."""
    return round(sum(item["quantity"] * item["unit_price"] for item in items), 2)


def parse_receipt(image_bytes: bytes, shopping_list_names: list[str]) -> dict:
    """Extract purchased items from a receipt photo, matched against the shopping list.

    Args:
        image_bytes: Raw JPEG bytes of the receipt photo (Telegram always sends
            photos as JPEG).
        shopping_list_names: Current shopping list item names, so the model can
            match receipt lines to them across languages, abbreviations, and typos.

    Returns:
        Dict with keys:
        - items: list of dicts (name, quantity, unit_price, category,
          product — what it generically is, e.g. "cheese", empty if unknown —
          and matched_shopping_list_item — empty string when nothing matched).
        - total_paid: the receipt's printed total, as read by the model.
        - items_total: quantity*unit_price summed across items.
        - reconciled: True if items_total matches total_paid within a cent
          or two — False means a line's price is probably wrong (e.g. a
          multi-unit line's total mistaken for its per-unit price) and the
          caller should warn the user rather than log it silently.
        - store: the store/supplier name as read by the model, empty string
          if illegible.
    """
    _ensure_server_running()
    shopping_list_text = "\n".join(shopping_list_names) if shopping_list_names else "(empty)"
    prompt = (
        "Read this grocery receipt and record every purchased item: its "
        "name, quantity, and price per unit (not the line total). Also read "
        "the receipt's final total paid and the store or supplier name "
        "printed on it (e.g. REWE, dm, Amazon).\n\n"
        "A small number or letter printed immediately next to an item "
        "(e.g. '1', '2', 'A', 'B') is very often a VAT/tax-rate category "
        "code, not a quantity — German receipts print one of these next to "
        "almost every line. Only treat a number as the quantity if the "
        "line clearly shows a multiplier (e.g. '2 x', '2 Stk', 'Menge: 2') "
        "or the same item appears as separate repeated lines. Otherwise "
        "default quantity to 1.\n\n"
        "Before answering, check your work: multiply each item's quantity "
        "by its unit_price and add them up — this sum must equal the "
        "receipt's total paid. If it doesn't, first check whether you "
        "mistook a tax-code digit for a quantity (see above); only if that "
        "isn't the cause have you likely confused a line's total price "
        "with its per-unit price (e.g. a genuine '2 x €4.45' line where "
        "€4.45 is the total for both units, not €4.45 each). Find the "
        "mismatched line and correct it so the numbers reconcile before "
        "giving your final answer.\n\n"
        "The shopper's current shopping list is:\n"
        f"{shopping_list_text}\n\n"
        "For each receipt item, set matched_shopping_list_item to the exact "
        "text of the shopping list entry it corresponds to, if any — "
        "matching may cross languages, abbreviations, or typos. Otherwise "
        "leave it as an empty string.\n\n"
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
    total_paid = result["total_paid"]
    store = result.get("store") or ""
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
    }
