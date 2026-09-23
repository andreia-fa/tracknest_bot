"""Decide which shopping-list entry a receipt line clears, without trusting the model alone.

The vision model is asked for this match, but a small model misses
cross-language pairs (RAEUCHERLACHS vs "Salmon") and invents loose ones
(raspberry chocolate vs "morangos"). A fixed multilingual synonym list
(English / German / Portuguese — what this household types) both vetoes
the model's wrong matches and fills in the ones it missed.
"""

import re

# Each group names one thing in every language used here. Singular and
# plural are covered by matching (a term inside a longer word, or a
# receipt-truncated word that starts a term).
_SYNONYMS: list[tuple[str, ...]] = [
    ("salmon", "lachs", "salmão", "salmao"),
    ("tuna", "thunfisch", "atum"),
    ("prawn", "shrimp", "garnele", "gambas", "camarão", "camarao"),
    ("cod", "kabeljau", "bacalhau"),
    ("chicken", "hähnchen", "huhn", "frango"),
    ("beef", "rind", "vaca"),
    ("pork", "schwein", "porco"),
    ("ham", "schinken", "fiambre", "presunto"),
    ("sausage", "wurst", "salsicha"),
    ("mince", "hackfleisch", "carne picada"),
    ("turkey", "pute", "peru"),
    ("milk", "milch", "leite"),
    ("cheese", "käse", "queijo"),
    ("butter", "manteiga"),
    ("yogurt", "yoghurt", "joghurt", "iogurte"),
    ("cream", "sahne", "nata"),
    ("egg", "eier", "ei", "ovo"),
    ("bread", "brot", "pão", "pao"),
    ("roll", "brötchen", "semmel"),
    ("water", "wasser", "água", "agua"),
    ("juice", "saft", "sumo"),
    ("coffee", "kaffee", "café", "cafe"),
    ("tea", "tee", "chá", "cha"),
    ("beer", "bier", "cerveja"),
    ("wine", "wein", "vinho"),
    ("rice", "reis", "arroz"),
    ("pasta", "nudeln", "massa"),
    ("flour", "mehl", "farinha"),
    ("sugar", "zucker", "açúcar", "acucar"),
    ("oil", "öl", "óleo", "oleo"),
    ("peanut butter", "erdnussmus", "erdnussbutter", "manteiga de amendoim"),
    ("banana", "banane"),
    ("apple", "apfel", "äpfel", "maçã", "maca"),
    ("strawberry", "strawberries", "erdbeere", "morango"),
    ("raspberry", "raspberries", "himbeere", "framboesa"),
    ("blueberry", "blueberries", "heidelbeere", "blaubeere", "mirtilo"),
    ("grape", "traube", "uva"),
    ("pear", "birne", "pêra", "pera"),
    ("orange", "laranja"),
    ("lemon", "zitrone", "limão", "limao"),
    ("tomato", "tomate"),
    ("potato", "kartoffel", "batata"),
    ("onion", "zwiebel", "cebola"),
    ("garlic", "knoblauch", "alho"),
    ("carrot", "karotte", "möhre", "cenoura"),
    ("spinach", "spinat", "espinafre"),
    ("lettuce", "salat", "alface"),
    ("cucumber", "gurke", "pepino"),
    ("pepper", "paprika", "pimento"),
    ("broccoli", "brokkoli", "brócolos", "brocolos", "brócolis", "brocolis"),
    ("cauliflower", "blumenkohl", "couve flor"),
    ("mushroom", "pilz", "champignon", "cogumelo"),
    ("zucchini", "courgette", "curgete"),
    ("avocado", "abacate"),
    ("vegetable", "veg", "gemüse", "legumes"),
    ("chocolate", "schokolade", "schoko"),
    ("soap", "seife", "sabonete"),
    ("toothpaste", "zahnpasta", "pasta de dentes"),
    ("deodorant", "deo", "desodorante"),
    ("toilet paper", "klopapier", "toilettenpapier", "papel higiénico", "papel higienico"),
    ("shower gel", "duschgel", "gel de duche"),
    ("detergent", "waschmittel", "detergente"),
    ("sponge", "schwamm", "esponja"),
    ("bra", "soutien", "sutiã", "sutia", "bh", "push up"),
    ("socks", "socken", "meias"),
]

_MIN_INSIDE_LEN = 4      # a term this long may sit inside a compound word
_MIN_TRUNCATED_LEN = 5   # a receipt word this long may be a cut-off term


def _normalize(text: str) -> str:
    """Casefold, fold German umlauts to their receipt spelling, strip punctuation."""
    text = text.casefold()
    for umlaut, plain in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(umlaut, plain)
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def _mentions(name: str, term: str) -> bool:
    """Tell whether a normalized name mentions a normalized term.

    A whole word always counts; a longer term also counts inside a
    compound (RAEUCHERLACHS has "lachs"), and a longer receipt word counts
    when it's a cut-off start of the term ("broccol").
    """
    if re.search(rf"\b{re.escape(term)}s?\b", name):
        return True
    if len(term) >= _MIN_INSIDE_LEN and term in name:
        return True
    return any(len(word) >= _MIN_TRUNCATED_LEN and term.startswith(word) for word in name.split())


def _groups_of(name: str) -> list[tuple[str, ...]]:
    normalized = _normalize(name)
    return [
        group for group in _SYNONYMS
        if any(_mentions(normalized, _normalize(term)) for term in group)
    ]


def _same_thing(receipt_names: list[str], list_name: str) -> bool | None:
    """True/False if the synonym list can judge the pair, None if it knows neither side."""
    list_groups = _groups_of(list_name)
    if not list_groups:
        return None
    return any(group in list_groups for name in receipt_names for group in _groups_of(name))


def choose_list_match(
    receipt_name: str, canonical_name: str, model_match: str, list_names: list[str],
) -> str | None:
    """Pick the shopping-list entry this purchase clears, if any.

    Args:
        receipt_name: The line's wording on the receipt.
        canonical_name: The name it's logged under (the user's name for it,
            if they taught the bot one; otherwise the same as receipt_name).
        model_match: The vision model's suggested list entry ("" for none).
        list_names: Current shopping-list entries.

    Returns:
        The list entry to clear, exactly as it appears on the list, or None.
    """
    names = [receipt_name, canonical_name]
    by_key = {_normalize(entry): entry for entry in list_names}

    # The user's own name for the item is the strongest signal there is.
    exact = by_key.get(_normalize(canonical_name))
    if exact:
        return exact
    model_entry = by_key.get(_normalize(model_match)) if model_match else None
    if model_entry and _same_thing(names, model_entry) is not False:
        return model_entry
    for entry in list_names:
        if _same_thing(names, entry):
            return entry
    return None
