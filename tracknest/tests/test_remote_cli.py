import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from db import remote_cli


@pytest.mark.asyncio
@patch("db.remote_cli.Bot")
@patch("db.remote_cli.receipt_queue")
@patch("bot.main.send_pending_profile_question", new_callable=AsyncMock)
@patch("bot.main.process_receipt_result", new_callable=AsyncMock)
async def test_finish_receipt_logs_resolves_and_replies(
    mock_process, mock_send_profile, mock_queue, mock_bot_cls
):
    mock_process.return_value = "Receipt processed:\n• 1x Milk at €1.50 each"
    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()
    mock_bot_cls.return_value = mock_bot

    parsed = {"items": [], "reconciled": True}
    result = await remote_cli._finish_receipt({"receipt_id": 5, "chat_id": 123, "parsed": parsed})

    mock_process.assert_awaited_once_with(parsed)
    mock_queue.resolve_receipt.assert_called_once_with(5, status="done")
    mock_bot.send_message.assert_awaited_once_with(123, mock_process.return_value)
    mock_send_profile.assert_awaited_once_with(mock_bot, 123)
    assert result == {"ok": True}


@pytest.mark.asyncio
@patch("db.remote_cli.Bot")
@patch("db.remote_cli.receipt_queue")
async def test_fail_receipt_resolves_failed_and_apologizes(mock_queue, mock_bot_cls):
    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()
    mock_bot_cls.return_value = mock_bot

    result = await remote_cli._fail_receipt({"receipt_id": 9, "chat_id": 321})

    mock_queue.resolve_receipt.assert_called_once_with(9, status="failed")
    mock_bot.send_message.assert_awaited_once()
    assert mock_bot.send_message.call_args[0][0] == 321
    assert result == {"ok": True}


@patch("db.remote_cli.receipt_queue")
def test_main_dispatches_sync_op(mock_queue, capsys):
    mock_queue.get_pending_receipts.return_value = [{"id": 1}]
    with patch("sys.argv", ["remote_cli.py", "get_pending_receipts"]), patch("sys.stdin.read", return_value=""):
        remote_cli.main()
    assert json.loads(capsys.readouterr().out) == [{"id": 1}]


def test_main_reads_args_from_stdin_not_argv():
    """Args must come from stdin: an ssh-invoked `docker exec` re-parses its
    trailing argv through the remote shell, which would mangle a JSON string
    passed as a plain argument (word-splits on spaces, strips quotes)."""
    with patch("db.remote_cli.shopping_list") as mock_shopping_list:
        mock_shopping_list.get_all_items.return_value = [{"name": "Milk"}, {"name": "Bretzel"}]
        with patch("sys.argv", ["remote_cli.py", "get_shopping_list_names"]), \
             patch("sys.stdin.read", return_value='{"unused": "value with spaces"}'):
            remote_cli.main()


def test_main_rejects_unknown_op():
    with patch("sys.argv", ["remote_cli.py", "not_a_real_op"]), patch("sys.stdin.read", return_value=""):
        with pytest.raises(SystemExit):
            remote_cli.main()
