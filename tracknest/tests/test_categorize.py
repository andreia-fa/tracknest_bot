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


def test_common_vegetables_and_berries():
    assert infer_category("Broccoli") == "Fruits/Veg"
    assert infer_category("Brokkoli") == "Fruits/Veg"
    assert infer_category("morangos") == "Fruits/Veg"


def test_flavoured_treat_is_a_snack_not_fruit():
    assert infer_category("dmBio schoko. Himbeeren 150g*") == "Snacks"


def test_common_fish_and_meat():
    assert infer_category("tuna") == "Meat/Fish"
    assert infer_category("Thunfisch in Öl") == "Meat/Fish"
    assert infer_category("atum") == "Meat/Fish"
    assert infer_category("Schinken") == "Meat/Fish"


def test_german_compounds_match_a_known_word_inside():
    assert infer_category("Käsescheiben") == "Dairy"
    assert infer_category("Käseaufschnitt") == "Dairy"
    assert infer_category("Salatgurke") == "Fruits/Veg"
    assert infer_category("Milchschokolade") == "Snacks"


def test_whole_word_match_beats_a_compound_match():
    assert infer_category("Zahnpasta") == "Hygiene/Personal Care"


def test_real_receipt_names_from_2026_09_23():
    assert infer_category("Naturgut Broccol") == "Fruits/Veg"   # cut off
    assert infer_category("Berida Garnele") == "Meat/Fish"
    assert infer_category("RAEUCHERLACHS") == "Meat/Fish"
    assert infer_category("Greenl. Erdbeere") == "Fruits/Veg"
    assert infer_category("Greenl. Gemüse") == "Fruits/Veg"


def test_eggs_are_dairy_even_when_labelled_bh():
    # "BH" on a German egg carton is Bodenhaltung (barn eggs), not a bra.
    assert infer_category("EIER BH M-L") == "Dairy"
    assert infer_category("EIER MARM.") == "Dairy"
    assert infer_category("eggs") == "Dairy"
    assert infer_category("BH schwarz") == "Clothing"


def test_ready_meals():
    assert infer_category("HAPPY CALIF. VEG") == "Ready Meals"
    assert infer_category("Sushi box") == "Ready Meals"
    assert infer_category("Tiefkühlpizza") == "Ready Meals"
    assert infer_category("Fertiggericht Lasagne") == "Ready Meals"
