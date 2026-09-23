from bot.list_match import choose_list_match


def test_finds_cross_language_match_the_model_missed():
    # Real miss, 2026-09-23: smoked salmon bought, "Salmon" stayed on the list.
    assert choose_list_match("RAEUCHERLACHS", "RAEUCHERLACHS", "", ["Soutien branco", "Salmon"]) == "Salmon"


def test_vetoes_a_model_match_the_synonyms_contradict():
    # Real bug: raspberry chocolate cleared "morangos" (strawberries).
    assert choose_list_match(
        "dmBio schoko. Himbeeren", "dmBio schoko. Himbeeren", "morangos", ["morangos"]
    ) is None


def test_keeps_a_model_match_the_synonyms_confirm():
    assert choose_list_match("PUSH UP", "PUSH UP", "Soutien branco", ["Soutien branco"]) == "Soutien branco"


def test_trusts_the_model_when_synonyms_know_nothing():
    assert choose_list_match("Lockenstab XL", "Lockenstab XL", "hair curler", ["hair curler"]) == "hair curler"


def test_users_own_name_matches_exactly():
    assert choose_list_match(
        "BIO aln.pfanne", "Frozen mixed veg", "", ["frozen mixed veg"]
    ) == "frozen mixed veg"


def test_truncated_and_umlaut_spellings():
    assert choose_list_match("Naturgut Broccol", "Naturgut Broccol", "", ["brócolos"]) == "brócolos"
    assert choose_list_match("Gouda Kaese", "Gouda Kaese", "", ["queijo"]) == "queijo"


def test_no_match_returns_none():
    assert choose_list_match("VOLVIC NATURELLE", "VOLVIC NATURELLE", "", ["Salmon"]) is None
