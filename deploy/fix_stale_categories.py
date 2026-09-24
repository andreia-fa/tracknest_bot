"""One-off (2026-09-24): move old items onto the current categories; fix Water Bottle's type.

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
    "HAPPY CALIF. VEG": "Ready Meals",
}

for name, category in FIXES.items():
    item = crud.get_item(name)
    if item is None:
        print(f"  skipped (not found): {name}")
        continue
    crud.set_item_category(name, category)
    print(f"  {name}: {item['category']} -> {category}")

# Water Bottle is drunk the day it's bought: a necessity, never a spare to stock.
crud.set_profile("Water Bottle", purchase_type="necessity", shelf_life_days=1)
crud.mark_spare_alert_pending("Water Bottle", pending=False)
print("  Water Bottle: essential -> necessity")
