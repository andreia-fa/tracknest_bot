import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot import receipt_worker


def _make_bot():
    bot = MagicMock()
    telegram_file = MagicMock()
    telegram_file.download_as_bytearray = AsyncMock(return_value=bytearray(b"jpeg-bytes"))
    bot.get_file = AsyncMock(return_value=telegram_file)
    return bot


@pytest.mark.asyncio
@patch("bot.receipt_worker.parse_receipt")
@patch("bot.receipt_worker._remote_call")
async def test_process_one_success_finishes_receipt(mock_remote_call, mock_parse):
    mock_remote_call.side_effect = lambda op, args=None: {
        "get_shopping_list_names": ["Milk"],
        "finish_receipt": {"ok": True},
    }[op]
    mock_parse.return_value = {"items": [], "reconciled": True}
    bot = _make_bot()
    receipt = {"id": 1, "chat_id": 42, "telegram_file_id": "file-abc"}

    await receipt_worker._process_one(bot, receipt)

    bot.get_file.assert_awaited_once_with("file-abc")
    mock_parse.assert_called_once_with(b"jpeg-bytes", ["Milk"])
    finish_call = [c for c in mock_remote_call.call_args_list if c[0][0] == "finish_receipt"][0]
    assert finish_call[0][1] == {"receipt_id": 1, "chat_id": 42, "parsed": mock_parse.return_value}


@pytest.mark.asyncio
@patch("bot.receipt_worker.parse_receipt")
@patch("bot.receipt_worker._remote_call")
async def test_process_one_failure_calls_fail_receipt(mock_remote_call, mock_parse):
    mock_remote_call.side_effect = lambda op, args=None: (
        ["Milk"] if op == "get_shopping_list_names" else {"ok": True}
    )
    mock_parse.side_effect = RuntimeError("Ollama server did not start in time")
    bot = _make_bot()
    receipt = {"id": 2, "chat_id": 99, "telegram_file_id": "file-xyz"}

    await receipt_worker._process_one(bot, receipt)

    fail_call = [c for c in mock_remote_call.call_args_list if c[0][0] == "fail_receipt"][0]
    assert fail_call[0][1] == {"receipt_id": 2, "chat_id": 99}


@pytest.mark.asyncio
@patch("bot.receipt_worker._process_one", new_callable=AsyncMock)
@patch("bot.receipt_worker._remote_call")
async def test_run_once_processes_every_pending_receipt(mock_remote_call, mock_process_one):
    mock_remote_call.return_value = [{"id": 1}, {"id": 2}]
    bot = MagicMock()

    await receipt_worker.run_once(bot)

    assert mock_process_one.await_count == 2
    mock_remote_call.assert_called_once_with("get_pending_receipts")


@pytest.mark.asyncio
@patch("bot.receipt_worker.parse_receipt")
@patch("bot.receipt_worker._remote_call")
async def test_process_one_finish_receipt_failure_calls_fail_receipt(mock_remote_call, mock_parse):
    """A remote-write failure on this receipt must not propagate — it would abort
    run_once's loop and block every receipt queued behind it (see run_once)."""

    def side_effect(op, args=None):
        if op == "get_shopping_list_names":
            return ["Milk"]
        if op == "finish_receipt":
            raise subprocess.CalledProcessError(1, ["ssh"], stderr="boom")
        return {"ok": True}

    mock_remote_call.side_effect = side_effect
    mock_parse.return_value = {"items": [], "reconciled": True}
    bot = _make_bot()
    receipt = {"id": 3, "chat_id": 7, "telegram_file_id": "file-def"}

    await receipt_worker._process_one(bot, receipt)

    fail_call = [c for c in mock_remote_call.call_args_list if c[0][0] == "fail_receipt"][0]
    assert fail_call[0][1] == {"receipt_id": 3, "chat_id": 7}


@pytest.mark.asyncio
@patch("bot.receipt_worker._process_one", new_callable=AsyncMock)
@patch("bot.receipt_worker._remote_call")
async def test_run_once_continues_past_a_failed_receipt(mock_remote_call, mock_process_one):
    """_process_one swallows its own errors, so one bad receipt must not stop
    run_once from reaching the ones queued after it."""
    mock_remote_call.return_value = [{"id": 1}, {"id": 2}, {"id": 3}]
    mock_process_one.side_effect = [None, None, None]
    bot = MagicMock()

    await receipt_worker.run_once(bot)

    assert mock_process_one.await_count == 3


def test_remote_call_logs_stderr_and_reraises_on_failure():
    error = subprocess.CalledProcessError(1, ["ssh"], stderr="remote traceback here")
    with patch("bot.receipt_worker.subprocess.run", side_effect=error):
        with patch("bot.receipt_worker.logger") as mock_logger:
            with pytest.raises(subprocess.CalledProcessError):
                receipt_worker._remote_call("finish_receipt", {"receipt_id": 1})
            mock_logger.error.assert_called_once()
            assert "remote traceback here" in mock_logger.error.call_args[0][3]
