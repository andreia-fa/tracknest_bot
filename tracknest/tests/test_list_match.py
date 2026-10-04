from bot.list_match import choose_list_match


def test_finds_cross_language_match_the_model_missed():
    # Real miss, 2026-09-23: smoked salmon bought, "Salmon" stayed on the list.
    assert choose_list_match("RAEUCHERLACHS", "RAEUCHERLACHS", ["Soutien branco", "Salmon"]) == "Salmon"


def test_raspberries_never_clear_strawberries():
    # Real bug: raspberry chocolate cleared "morangos" (strawberries).
    assert choose_list_match("dmBio schoko. Himbeeren", "dmBio schoko. Himbeeren", ["morangos"]) is None


def test_synonyms_match_across_languages():
    assert choose_list_match("PUSH UP", "PUSH UP", ["Soutien branco"]) == "Soutien branco"


def test_a_burger_king_line_never_clears_socks():
    # Real bug, 2026-10-04: the model, shown the list, named a line "Socks - decathlon".
    assert choose_list_match("Whopper", "Whopper", ["Socks - decathlon"], product="burger") is None


def test_the_product_matches_a_list_entry_written_that_way():
    assert choose_list_match("Lockenstab XL", "Lockenstab XL", ["hair curler"], product="hair curler") == "hair curler"


def test_users_own_name_matches_exactly():
    assert choose_list_match(
        "BIO aln.pfanne", "Frozen mixed veg", ["frozen mixed veg"]
    ) == "frozen mixed veg"


def test_truncated_and_umlaut_spellings():
    assert choose_list_match("Naturgut Broccol", "Naturgut Broccol", ["brócolos"]) == "brócolos"
    assert choose_list_match("Gouda Kaese", "Gouda Kaese", ["queijo"]) == "queijo"


def test_no_match_returns_none():
    assert choose_list_match("VOLVIC NATURELLE", "VOLVIC NATURELLE", ["Salmon"]) is None


def test_unrelated_entries_are_never_cleared():
    entries = ["Pfefferbretzel 1€", "Zimtschenecke 3.50€"]
    # Real bug, 2026-09-29: the model paired these and both entries got cleared.
    assert choose_list_match("Kuchenbeleg", "Kuchenbeleg", entries, product="topping (for cake/bread)") is None
    assert choose_list_match("Brotstücker", "Brotstücker", entries, product="bread pieces") is None


def test_pretzels_match_across_spellings():
    assert choose_list_match("LAUGENBREZEL", "LAUGENBREZEL", ["Pfefferbretzel"]) == "Pfefferbretzel"

