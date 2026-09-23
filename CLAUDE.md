# TrackNest Bot — Developer Agent

## Project Overview
TrackNest is a Telegram bot for household inventory and expense tracking, built with `python-telegram-bot` v20+ and SQLite.

## Structure
```
tracknest/
  bot/main.py          — cloud bot entry point: command + plain-text handlers,
                         and handle_photo (just queues the receipt, see below)
  bot/receipt_worker.py — LOCAL-ONLY entry point: polls the cloud bot's
                         receipt queue over SSH, runs Ollama, hands results
                         back via db/remote_cli.py
  bot/parser.py        — parses plain-text entries (name/qty/unit_price)
  bot/receipt.py       — receipt photo parsing via local Ollama vision model
                         (only ever called by receipt_worker.py now)
  bot/dashboard.py     — password-gated aiohttp web app (relative-numbers-only
                         dashboard), served over a Cloudflare Tunnel — see
                         bot/tunnel.py and the /dashboard command
  bot/auth.py          — dashboard password check + signed session cookie
  bot/tunnel.py        — manages the cloudflared subprocess, exposes the
                         current https://*.trycloudflare.com URL
  config/__init__.py   — reads env vars (BOT_TOKEN, DB_PATH); DASHBOARD_PASSWORD
                         is read in bot/auth.py instead, so the local worker
                         never needs it
  db/
    database.py        — SQLite connection + schema init (init_db)
    crud.py            — inventory CRUD operations
    expenses.py        — expense log operations
    shopping_list.py   — shopping list CRUD operations
    receipt_queue.py   — pending_receipts queue (cloud bot writes, worker reads/resolves)
    remote_cli.py       — `python -m db.remote_cli <op> <json>`: the only
                         thing receipt_worker.py invokes (via SSH + docker
                         exec) to touch the cloud DB — reuses the real
                         functions above rather than building SQL remotely
    metrics.py          — read-only aggregates for /report and alerts
    settings.py         — household settings (chat id, par level, budget)
  tests/                — one test_*.py per db/ and bot/ module above (mocked DB / mocked Bot)
.github/workflows/ci_cd.yml  — CI runs tests; CD placeholder
requirements.txt             — python-telegram-bot, pytest, ruff
```

## Environment Variables
| Variable           | Required | Default            |
|--------------------|----------|--------------------|
| BOT_TOKEN          | yes      | —                  |
| DB_PATH            | no       | data/tracknest.db  |
| DASHBOARD_PASSWORD | yes      | —                  |

No `.env` file, in local dev or production. Export these as real shell
environment variables (e.g. in `~/.bashrc`) for local dev; in production it's
injected by the CD workflow from GitHub Actions secrets at `docker run` time.

**Receipt photos are cloud-queued, locally processed.** The cloud bot
(`bot/main.py`, running on the Oracle VM — see `DEPLOY_STRATEGY.md`) can't
run Ollama (956Mi RAM VM), so `handle_photo` just stores the photo's
Telegram `file_id` in `pending_receipts` and replies immediately. This
laptop's `bot/receipt_worker.py` (started via `systemctl --user`, see
"Local autostart" below) polls that queue whenever it's running, downloads
the photo straight from Telegram, runs it through the local
[Ollama](https://ollama.com) install (`minicpm-v4.5` model), and hands the
result back to the cloud container via `db.remote_cli` over SSH. `bot/receipt.py`
starts `ollama serve` itself on first use and leaves it running — no env
var, no API key, fully offline. The shopping-list feature works
independently of all this — only receipt photos wait on the worker.
`config/__init__.py` only ever reads `os.environ[]` — it doesn't care where
the values came from. `DB_PATH` is not a secret — it's just a file path, and
defaults to `data/tracknest.db` (git-ignored) if unset.

**`/dashboard` (2026-09-23): password-gated web view, relative numbers only**
(budget %, month-elapsed %, essential/treats/necessity mix %, price-trend %,
shelf-life-remaining %) — `bot/report()`'s euro figures stay Telegram-only.
`bot/main.py`'s entrypoint runs three things in one asyncio event loop: PTB's
long-poller, an aiohttp server (`bot/dashboard.py`, bound to `127.0.0.1` only)
and a `cloudflared` subprocess (`bot/tunnel.py`) that tunnels it out to a
`https://*.trycloudflare.com` URL — no domain, no inbound port opened on the
VM. That URL changes on every restart; the `/dashboard` command always fetches
the current one fresh (`context.bot_data["tunnel"]`) rather than caching a
stale link. Auth is `bot/auth.py`'s signed session cookie (HMAC, stdlib only,
no session store) checked against `DASHBOARD_PASSWORD` — not real user
accounts, one shared household password.

## Commands to Know
```bash
# Run tests (from tracknest/)
BOT_TOKEN=dummy DASHBOARD_PASSWORD=dummy_password_for_tests pytest tests/ -q

# Lint
ruff check tracknest/

# Run the full bot manually — only meaningful against a fresh/local DB,
# since the real one now runs in the cloud (from tracknest/)
# Must run as a module — bot/main.py uses absolute imports (from bot.x
# import y), so `python bot/main.py` fails with ModuleNotFoundError.
python -m bot.main

# Run the receipt worker manually, for one-off local testing (from tracknest/)
python -m bot.receipt_worker
```

## Local autostart (permanent — runs the receipt worker, not the full bot)

`deploy/local/tracknest-bot.service` runs `bot/receipt_worker.py` as a
`systemd --user` service — it survives reboots and restarts on crash, so
you don't need to remember to start it. This is **not** a stopgap: it's
the permanent home for receipt processing, since that's the one thing that
has to stay local (Ollama). The full bot (`bot/main.py`) no longer runs
here at all — it runs in the cloud (Oracle VM, see `DEPLOY_STRATEGY.md`).

```bash
systemctl --user status tracknest-bot.service   # is it running?
journalctl --user -u tracknest-bot.service -f   # live logs
systemctl --user restart tracknest-bot.service  # after a code change
```

Token lives in `~/.config/tracknest-bot.env` (`BOT_TOKEN=...`, `chmod 600`,
not in git) — `EnvironmentFile=` in the unit reads it directly instead of
`~/.bashrc`, since a non-interactive process like a systemd service doesn't
source `.bashrc` anyway. If the token is ever rotated, regenerate that file
from the new value and `systemctl --user restart tracknest-bot.service`.

The worker also needs a working `ssh oracle-tracknest` (see `~/.ssh/config`
and `DEPLOY_STRATEGY.md`) with `docker exec` permission on the VM (the
`ubuntu` user must be in the `docker` group) — that's how it reaches
`db.remote_cli` inside the running container.

## Development Rules
- **Always read the relevant source files before making changes.** Never assume structure.
- Follow Python best practices: type hints where meaningful, no unnecessary comments.
- Tests mock DB connections — keep it that way. Do not introduce real DB calls in tests.
- `config/__init__.py` uses `os.environ[]` for required vars — it will raise on startup if any are missing. This is intentional.
- After every task: tests + lint must pass. The Stop hook handles this automatically and will block completion if they fail.

## Documentation Standards

### Docstrings
Use **Google style** for all public functions and modules. Every public function must have a docstring.

```python
def function(arg: type) -> type:
    """One-line summary (imperative mood, no period).

    Only add a body when the behaviour is non-obvious from the signature.

    Args:
        arg: Description. Omit type — it's already in the signature.

    Returns:
        Description of the return value.

    Raises:
        ExceptionType: When and why it's raised.
    """
```

Rules:
- One-line summary only when Args/Returns are obvious from the name and signature.
- Never restate the argument name in its description ("name: the name of...").
- Omit `Raises` unless the function explicitly raises for a documented reason.
- Module-level docstrings: one sentence describing what the module provides.

### When to update `tracknest/docs/`
| File | Update when |
|------|-------------|
| `docs/setup.md` | Setup steps, env vars, or DB schema change |
| `docs/expenses.md` | Expense module commands, logic, or data model change |

### When to update `README.md`
- A new bot command is added or removed
- Setup steps change
- Project structure changes

### When to update `CLAUDE.md`
- A new module or layer is added to the project
- Development workflow changes (new tools, new rules)

## PM Agent (`tracknest/pm_agent/`)

Responsible for project quality metrics. Current modules:

### `pm_agent/token_tracker.py` — Token usage tracking
Wraps `anthropic.Anthropic` to record per-call and per-session token stats.

```python
from anthropic import Anthropic
from pm_agent.token_tracker import TokenTracker

tracker = TokenTracker(
    client=Anthropic(),
    model="claude-opus-4-7",
    inject_stats=True,   # prepend stats to system prompt for model self-optimisation
    print_summary=True,  # print usage table after each call
)
response = tracker.create(
    messages=[{"role": "user", "content": "Hello"}],
    system="You are a helpful assistant.",
    thinking={"type": "adaptive"},
)
```

After each call it prints:
```
── Token usage (call #1) ──────────────────
  Input:              823  tokens
  Output:             412  tokens
  Cache hit:          600  tokens
──────────────────────────────────────────
  Session input:      823  / 1,000,000
  Context used:      0.08%
  Remaining:      999,177  tokens
──────────────────────────────────────────
```

Key constants: `CONTEXT_WINDOW = 1_000_000` (claude-opus-4-7).  
`tracker.reset_session()` clears cumulative stats.  
`session.context_injection()` returns a compact stats string for system prompt injection.

### When to update `pm_agent/`
- A new metric type is added (latency, cost, error rate)
- The model or context window changes

## Commit Message Format
```
<type>(<scope>): <short description>

- tests: passed
- lint: passed
```
Types: `feat`, `fix`, `chore`, `refactor`, `test`, `docs`
