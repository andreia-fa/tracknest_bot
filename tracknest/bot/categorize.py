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
    # Before Fruits/Veg so a flavoured treat ("schoko. Himbeeren") counts
    # as a snack, not fruit.
    ("Snacks", [
        "chocolate", "schokolade", "schoko", "candy", "süßigkeiten",
        "susigkeiten", "doces", "chips", "biscuit", "keks", "biscoito",
        "pudding", "snack",
    ]),
    ("Fruits/Veg", [
        "banana", "banane", "apple", "apfel", "maçã", "maca", "tomato", "tomate",
        "potato", "kartoffel", "batata", "onion", "zwiebel", "cebola", "carrot",
        "karotte", "cenoura", "spinach", "spinat", "espinafre", "lettuce", "salat",
        "alface", "cucumber", "gurke", "pepino", "pepper", "paprika", "pimento",
        "garlic", "knoblauch", "alho", "lemon", "zitrone", "limão", "limao",
        "orange", "laranja", "ginger", "ingwer", "gengibre", "salad", "obst",
        "gemüse", "gemuse", "fruta", "legume", "avocado", "abacate",
        "broccoli", "brokkoli", "brócolos", "brocolos", "brócolis", "brocolis",
        "cauliflower", "blumenkohl", "couve", "cabbage", "kohl", "zucchini",
        "courgette", "curgete", "abobrinha", "eggplant", "aubergine", "beringela",
        "pumpkin", "kürbis", "kurbis", "abóbora", "abobora", "mushroom", "pilz",
        "champignon", "cogumelo", "leek", "lauch", "alho francês", "alho frances",
        "celery", "sellerie", "aipo", "peas", "erbsen", "ervilhas", "beans",
        "bohnen", "feijão", "feijao", "corn", "milho", "asparagus",
        "spargel", "espargos", "radish", "radieschen", "rabanete", "beetroot",
        "rote bete", "beterraba", "sweet potato", "süßkartoffel", "batata doce",
        "strawberry", "strawberries", "erdbeere", "erdbeeren", "morango",
        "raspberry", "raspberries", "himbeere", "himbeeren", "framboesa",
        "blueberry", "blueberries", "heidelbeere", "heidelbeeren", "mirtilo",
        "grape", "traube", "trauben", "uva", "pear", "birne", "pêra", "pera",
        "peach", "pfirsich", "pêssego", "pessego", "plum", "pflaume", "ameixa",
        "cherry", "cherries", "kirsche", "kirschen", "cereja", "melon", "melone",
        "melão", "melao", "watermelon", "wassermelone", "melancia", "pineapple",
        "ananas", "abacaxi", "mango", "manga", "kiwi", "lime", "limette",
        "tangerine", "mandarine", "tangerina", "clementine", "clementina",
        "herbs", "kräuter", "krauter", "parsley", "petersilie",
        "coriander", "koriander", "coentros", "basil", "basilikum", "manjericão",
        "manjericao",
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
        "tuna", "thunfisch", "atum", "cod", "kabeljau", "bacalhau", "sardine",
        "sardinen", "sardinha", "trout", "forelle", "truta", "prawn", "garnelen",
        "gambas", "mackerel", "makrele", "cavala", "octopus", "polvo", "squid",
        "tintenfisch", "lula", "turkey", "pute", "peru", "pork", "schwein",
        "porco", "ham", "schinken", "fiambre", "presunto", "bacon", "speck",
        "mince", "hackfleisch", "carne picada", "lamb", "lamm", "borrego",
        "steak", "bife", "chouriço", "chourico", "salami",
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
