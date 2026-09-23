from bot.receipt_lines import classify_line


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
