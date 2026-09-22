from bot.categorize import infer_category


def test_fruit_veg_matches_across_languages():
    assert infer_category("Banane") == "Fruits/Veg"
    assert infer_category("bananas") == "Fruits/Veg"
    assert infer_category("Apfel") == "Fruits/Veg"


def test_clothing_matches_portuguese():
    assert infer_category("Soutien branco") == "Clothing"


def test_hygiene_matches_german():
    assert infer_category("Duschgel") == "Hygiene/Personal Care"


def test_dairy():
    assert infer_category("Milk") == "Dairy"


def test_unknown_item_falls_back_to_other():
    assert infer_category("Xyzzy Widget 3000") == "Other"


def test_matching_is_case_insensitive_and_whole_word():
    # "bra" must not match inside an unrelated word like "bracket"
    assert infer_category("Bracket") == "Other"
    assert infer_category("BRA") == "Clothing"
