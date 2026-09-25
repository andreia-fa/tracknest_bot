from bot.receipt_lines import classify_line, clean_name


def test_deposits_and_discounts_are_adjustments():
    assert classify_line("PFAND 0,25 EURO") == "adjustment"
    assert classify_line("LEERGUT EINWEG") == "adjustment"
    assert classify_line("Rabatt 20%") == "adjustment"


def test_price_info_lines_are_ignored():
    assert classify_line("Normalpreis") == "info"
    assert classify_line("NORMALPREIS 2,49") == "info"
    assert classify_line("SUMME") == "info"
    assert classify_line("MwSt 19%") == "info"


def test_real_products_are_items():
    assert classify_line("BIO aln.pfanne") == "item"
    assert classify_line("Käsescheiben") == "item"
    assert classify_line("Summer Rolls") == "item"


def test_clean_name_strips_leading_count():
    assert clean_name("1 weleda Handcream SA") == "weleda Handcream SA"
    assert clean_name("2x Milch") == "Milch"
    assert clean_name("3 Stk Eier") == "Eier"


def test_clean_name_keeps_real_numbers_and_letters():
    assert clean_name("dmBio schoko. Himbeeren 150g*") == "dmBio schoko. Himbeeren 150g*"
    assert clean_name("Jessa SE Cotton Normal") == "Jessa SE Cotton Normal"
    assert clean_name("Pampers 4") == "Pampers 4"
    assert clean_name("7Up") == "7Up"
    assert clean_name("5") == "5"
