"""One-off (2026-09-24): move items logged before the current category list onto it.

Run inside the bot container, e.g.:
    ssh oracle-tracknest docker exec -i tracknest-bot python - < deploy/fix_stale_categories.py
"""

from db import crud

FIXES = {
    "PROTEINBROETCHEN": "Bread/Bakery",
    "Pfefferbreze": "Bread/Bakery",
    "BABYSPINAT BIO": "Fruits/Veg",
    "BANANE": "Fruits/Veg",
    "BIO Ain.Pfanne": "Fruits/Veg",
    "EIER BH M-L": "Dairy",
    "EIER MARM.": "Dairy",
    "Milram Käse Scheiben": "Dairy",
    "J.Tag Käseaufschnitt": "Dairy",
    "dmBio schoko. Himbeeren 150g*": "Snacks",
    "HP PUDDING": "Snacks",
    "HP PUDD. CHOCO V": "Snacks",
    "Jessa SE Cotton Normal": "Hygiene/Personal Care",
    "Haarprodukt": "Hygiene/Personal Care",
    "HAPPY CALIF. VEG": "Other",
}

for name, category in FIXES.items():
    item = crud.get_item(name)
    if item is None:
        print(f"  skipped (not found): {name}")
        continue
    crud.set_item_category(name, category)
    print(f"  {name}: {item['category']} -> {category}")
