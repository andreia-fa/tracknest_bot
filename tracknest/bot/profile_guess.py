"""Category defaults for treat-or-need and how long an item lasts, to ask fewer questions.

Used only for a brand-new item, after the stronger signal (the same product
already answered under another name, crud.copy_product_profile) found
nothing. A guess is always shown to the user with a way to change it, and a
category too mixed to guess (a loaf is a need, a Zimtschnecke a treat) gets
no default, so that question is still asked. Shelf-life guesses only need to
be roughly right: expenses.log_expense pulls an estimate towards the real
gap between purchases.
"""

# The user's rule (2026-10-03): a need is a raw staple you can't live without
# — vegetables, salmon, tofu, rice; pretzels, pastries, cake, ready meals and
# outings are treats.
_DEFAULTS: dict[str, tuple[str | None, tuple[str, int | None] | None]] = {
    "Fruits/Veg": ("need", ("days", 7)),
    "Meat/Fish": ("need", ("days", 4)),
    "Dairy": ("need", ("days", 10)),
    "Pantry": ("need", ("days", 60)),
    "Hygiene/Personal Care": ("need", ("days", 90)),
    "Household": ("need", ("days", 60)),
    "Snacks": ("treat", ("days", 7)),
    "Ready Meals": ("treat", ("same_day", None)),
    "Pastries": ("treat", ("same_day", None)),
    "Clothing": (None, ("days", 120)),
    "Leisure": ("treat", ("same_day", None)),
}


def guess_profile(category: str | None) -> tuple[str | None, tuple[str, int | None] | None]:
    """Return (treat_or_need, (lasts, days)) defaults for a category; None where it's too mixed to guess."""
    return _DEFAULTS.get(category or "", (None, None))
