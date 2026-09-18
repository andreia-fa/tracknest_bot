"""Receipt photo parsing via a local Ollama vision model (free, fully offline)."""

import json
import subprocess
import time

import ollama

_MODEL = "minicpm-v4.5"
_client = ollama.Client()

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
                        "description": "Item name as printed on the receipt, cleaned up into a readable product name",
                    },
                    "quantity": {"type": "integer", "description": "Units purchased"},
                    "unit_price": {"type": "number", "description": "Price per single unit, not the line total"},
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
                "required": ["name", "quantity", "unit_price", "matched_shopping_list_item"],
            },
        },
    },
    "required": ["items"],
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


def parse_receipt(image_bytes: bytes, shopping_list_names: list[str]) -> list[dict]:
    """Extract purchased items from a receipt photo, matched against the shopping list.

    Args:
        image_bytes: Raw JPEG bytes of the receipt photo (Telegram always sends
            photos as JPEG).
        shopping_list_names: Current shopping list item names, so the model can
            match receipt lines to them across languages, abbreviations, and typos.

    Returns:
        List of dicts: name, quantity, unit_price, matched_shopping_list_item
        (empty string when nothing matched).
    """
    _ensure_server_running()
    shopping_list_text = "\n".join(shopping_list_names) if shopping_list_names else "(empty)"
    prompt = (
        "Read this grocery receipt and record every purchased item: its "
        "name, quantity, and price per unit (not the line total).\n\n"
        "The shopper's current shopping list is:\n"
        f"{shopping_list_text}\n\n"
        "For each receipt item, set matched_shopping_list_item to the exact "
        "text of the shopping list entry it corresponds to, if any — "
        "matching may cross languages, abbreviations, or typos. Otherwise "
        "leave it as an empty string."
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
    return json.loads(response.message.content)["items"]
