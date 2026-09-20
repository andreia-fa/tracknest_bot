from unittest.mock import MagicMock, patch

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
        '"category": "Dairy", "matched_shopping_list_item": ""}], "total_paid": 1.50}'
    )
    mock_client.chat.return_value = mock_response
    result = receipt.parse_receipt(b"fake-image", [])
    assert result["reconciled"] is True
    assert result["items_total"] == 1.50
    assert result["total_paid"] == 1.50


@patch("bot.receipt._ensure_server_running")
@patch("bot.receipt._client")
def test_parse_receipt_not_reconciled(mock_client, _mock_ensure):
    mock_response = MagicMock()
    mock_response.message.content = (
        '{"items": [{"name": "dmBio schoko. Himbeeren", "quantity": 2, "unit_price": 4.45, '
        '"category": "Snack", "matched_shopping_list_item": ""}], "total_paid": 5.90}'
    )
    mock_client.chat.return_value = mock_response
    result = receipt.parse_receipt(b"fake-image", [])
    assert result["reconciled"] is False
    assert result["items_total"] == 8.90
    assert result["total_paid"] == 5.90
