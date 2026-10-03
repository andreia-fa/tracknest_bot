import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from bot import receipt


def test_items_total_sums_quantity_times_price():
    items = [
        {"quantity": 2, "unit_price": 2.225},
        {"quantity": 1, "unit_price": 1.45},
    ]
    assert receipt._items_total(items) == 5.90


def test_items_total_empty():
    assert receipt._items_total([]) == 0.0


@patch("bot.receipt._ensure_server_running")
@patch("bot.receipt._client")
def test_parse_receipt_reconciled(mock_client, _mock_ensure):
    mock_response = MagicMock()
    mock_response.message.content = (
        '{"items": [{"name": "Milk", "quantity": 1, "unit_price": 1.50, '
        '"category": "Dairy", "matched_shopping_list_item": ""}], "total_paid": 1.50, '
        '"store": "REWE"}'
    )
    mock_client.chat.return_value = mock_response
    result = receipt.parse_receipt(b"fake-image", [])
    assert result["reconciled"] is True
    assert result["items_total"] == 1.50
    assert result["total_paid"] == 1.50
    assert result["store"] == "REWE"


@patch("bot.receipt._ensure_server_running")
@patch("bot.receipt._client")
def test_parse_receipt_not_reconciled(mock_client, _mock_ensure):
    mock_response = MagicMock()
    mock_response.message.content = (
        '{"items": [{"name": "dmBio schoko. Himbeeren", "quantity": 2, "unit_price": 4.45, '
        '"category": "Snack", "matched_shopping_list_item": ""}], "total_paid": 5.90, '
        '"store": "dm"}'
    )
    mock_client.chat.return_value = mock_response
    result = receipt.parse_receipt(b"fake-image", [])
    assert result["reconciled"] is False
    assert result["items_total"] == 8.90
    assert result["total_paid"] == 5.90
    assert result["store"] == "dm"


@patch("bot.receipt._ensure_server_running")
@patch("bot.receipt._client")
def test_parse_receipt_missing_store_defaults_to_empty_string(mock_client, _mock_ensure):
    mock_response = MagicMock()
    mock_response.message.content = (
        '{"items": [], "total_paid": 0.0, "store": ""}'
    )
    mock_client.chat.return_value = mock_response
    result = receipt.parse_receipt(b"fake-image", [])
    assert result["store"] == ""


@patch("bot.receipt._ensure_server_running")
@patch("bot.receipt._client")
def test_parse_receipt_net_total_misread_is_not_a_mismatch(mock_client, _mock_ensure):
    # Real case: items €4.50, model read the VAT table's Netto €3.78 (= 4.50 / 1.19).
    mock_response = MagicMock()
    mock_response.message.content = (
        '{"items": [{"name": "Bread", "quantity": 1, "unit_price": 4.50, '
        '"category": "Bakery", "matched_shopping_list_item": ""}], "total_paid": 3.78, '
        '"store": "REWE"}'
    )
    mock_client.chat.return_value = mock_response
    result = receipt.parse_receipt(b"fake-image", [])
    assert result["reconciled"] is True
    assert result["total_paid"] == 4.50


def test_net_total_misread_only_matches_exact_vat_rates():
    assert receipt._is_net_total_misread(4.50, 3.78)       # 19%
    assert receipt._is_net_total_misread(10.70, 10.00)     # 7%
    assert not receipt._is_net_total_misread(8.90, 5.90)   # a real error
    assert not receipt._is_net_total_misread(11.20, 10.00)  # 12%: neither rate


@patch("bot.receipt._ensure_server_running")
@patch("bot.receipt._client")
def test_parse_receipt_drops_non_items_but_counts_deposits(mock_client, _mock_ensure):
    mock_response = MagicMock()
    mock_response.message.content = (
        '{"items": ['
        '{"name": "Water", "quantity": 1, "unit_price": 0.50, "category": "Beverages", "matched_shopping_list_item": ""},'
        '{"name": "PFAND 0,25 EURO", "quantity": 1, "unit_price": 0.25, "category": "", "matched_shopping_list_item": ""},'
        '{"name": "Normalpreis", "quantity": 1, "unit_price": 0.79, "category": "", "matched_shopping_list_item": ""}'
        '], "total_paid": 0.75, "store": "REWE"}'
    )
    mock_client.chat.return_value = mock_response
    result = receipt.parse_receipt(b"fake-image", [])
    assert [i["name"] for i in result["items"]] == ["Water"]
    assert result["reconciled"] is True


@pytest.mark.parametrize("raw, expected", [
    ("2026-09-22", "2026-09-22"),
    ("22.09.2026", "2026-09-22"),   # printed German form, copied as-is
    ("22.09.26", "2026-09-22"),
    ("", None),                     # illegible
    ("2026-10-04", None),           # in the future: a misread
    ("2024-09-22", None),           # over a year old: a misread
    ("31.02.2026", None),           # no such day
    ("sometime in September", None),
])
def test_receipt_date_is_trusted_only_when_plausible(raw, expected):
    assert receipt.parse_receipt_date(raw, date(2026, 10, 3)) == expected


@patch("bot.receipt._ensure_server_running")
@patch("bot.receipt._client")
def test_the_total_misread_as_a_product_is_dropped(mock_client, _mock_ensure):
    """Real misread, REWE 25 Sep: the €3.18 total came back as an item."""
    mock_response = MagicMock()
    mock_response.message.content = json.dumps({
        "items": [
            {"name": "HP PUDD.CHOCO V", "quantity": 1, "unit_price": 0.99, "category": "Snacks",
             "product": "pudding", "matched_shopping_list_item": ""},
            {"name": "SKYR STYLE MANGO", "quantity": 1, "unit_price": 2.19, "category": "Dairy",
             "product": "yogurt", "matched_shopping_list_item": ""},
            {"name": "Mango Dessert", "quantity": 1, "unit_price": 3.18, "category": "Snacks",
             "product": "", "matched_shopping_list_item": ""},
        ],
        "total_paid": 3.18, "store": "REWE", "purchase_date": "2026-09-25",
    })
    mock_client.chat.return_value = mock_response
    result = receipt.parse_receipt(b"fake-image", [])
    assert [i["name"] for i in result["items"]] == ["HP PUDD.CHOCO V", "SKYR STYLE MANGO"]
    assert result["reconciled"] is True
