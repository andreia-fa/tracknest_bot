"""Heuristic shopping-list categorization from a small multilingual keyword list.

No AI involved on purpose — this runs inline in the cloud bot's plain-text
handler, which has to stay instant (no Ollama, no local worker round trip).
Covers English, German, and Portuguese, since that's the mix this household
actually types in. Anything not recognized falls into "Other" rather than
guessing.
"""

import re

_OTHER = "Other"

# Checked in this order — first matching keyword wins. Order only matters
# for words that could plausibly belong to more than one bucket.
_CATEGORIES: list[tuple[str, list[str]]] = [
    ("Fruits/Veg", [
        "banana", "banane", "apple", "apfel", "maçã", "maca", "tomato", "tomate",
        "potato", "kartoffel", "batata", "onion", "zwiebel", "cebola", "carrot",
        "karotte", "cenoura", "spinach", "spinat", "espinafre", "lettuce", "salat",
        "alface", "cucumber", "gurke", "pepino", "pepper", "paprika", "pimento",
        "garlic", "knoblauch", "alho", "lemon", "zitrone", "limão", "limao",
        "orange", "laranja", "ginger", "ingwer", "gengibre", "salad", "obst",
        "gemüse", "gemuse", "fruta", "legume", "avocado", "abacate",
    ]),
    ("Dairy", [
        "milk", "milch", "leite", "cheese", "käse", "kase", "queijo", "yogurt",
        "joghurt", "iogurte", "butter", "manteiga", "cream", "sahne", "nata",
        "quark",
    ]),
    ("Bread/Bakery", [
        "bread", "brot", "pão", "pao", "brötchen", "broetchen", "roll",
        "pretzel", "brezel", "breze", "bakery", "padaria", "bolo", "cake",
    ]),
    ("Meat/Fish", [
        "meat", "fleisch", "carne", "chicken", "hähnchen", "haehnchen", "frango",
        "fish", "fisch", "peixe", "beef", "rind", "sausage", "wurst", "salsicha",
        "salmon", "lachs", "salmão", "salmao", "shrimp", "camarão", "camarao",
    ]),
    ("Pantry", [
        "rice", "reis", "arroz", "pasta", "nudeln", "massa", "flour", "mehl",
        "farinha", "sugar", "zucker", "açúcar", "acucar", "salt", "salz", "sal",
        "oil", "öl", "ol", "óleo", "oleo", "peanut butter", "erdnussmus",
        "manteiga de amendoim",
    ]),
    ("Beverages", [
        "water", "wasser", "água", "agua", "juice", "saft", "sumo", "coffee",
        "kaffee", "café", "cafe", "tea", "tee", "chá", "cha", "beer", "bier",
        "cerveja", "wine", "wein", "vinho",
    ]),
    ("Snacks", [
        "chocolate", "schokolade", "chocolate", "candy", "süßigkeiten",
        "susigkeiten", "doces", "chips", "biscuit", "keks", "biscoito",
        "pudding", "snack",
    ]),
    ("Hygiene/Personal Care", [
        "soap", "seife", "sabonete", "shampoo", "toothpaste", "zahnpasta",
        "pasta de dentes", "deodorant", "desodorante", "tampon", "tampão",
        "tampao", "pad", "duschgel", "shower gel", "gel de duche", "toilet paper",
        "klopapier", "papel higiénico", "papel higienico", "cotton",
    ]),
    ("Household", [
        "detergent", "waschmittel", "detergente", "cleaner", "reiniger",
        "sponge", "schwamm", "esponja", "trash bag", "müllbeutel", "muellbeutel",
        "saco do lixo", "battery", "batterie", "pilha",
    ]),
    ("Clothing", [
        "soutien", "sutiã", "sutia", "bra", "bh", "shirt", "hemd", "camisa",
        "socks", "socken", "meias", "underwear", "unterwäsche", "unterwaesche",
        "roupa interior",
    ]),
]


def infer_category(name: str) -> str:
    """Guess a shopping-list category from an item name via keyword matching.

    Args:
        name: The (already bullet-stripped) item name as typed by the user.

    Returns:
        A category label, or "Other" if nothing matched.
    """
    normalized = re.sub(r"[^\w\s]", " ", name.lower())
    for category, keywords in _CATEGORIES:
        for keyword in keywords:
            # Trailing "s?" so an English plural (bananas, apples) matches
            # a singular keyword without listing every plural by hand.
            if re.search(rf"\b{re.escape(keyword)}s?\b", normalized):
                return category
    return _OTHER
