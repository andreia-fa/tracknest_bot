# TrackNest Bot — Developer Agent

## Project Overview
TrackNest is a Telegram bot for household inventory and expense tracking, built with `python-telegram-bot` v20+ and SQLite.

## Structure
```
tracknest/
  bot/main.py          — bot entry point, command + plain-text + photo handlers
  bot/parser.py        — parses plain-text entries (name/qty/unit_price)
  bot/receipt.py       — receipt photo parsing via local Ollama vision model
  config/__init__.py   — reads env vars (BOT_TOKEN, DB_PATH)
  db/
    database.py        — SQLite connection + schema init (init_db)
    crud.py            — inventory CRUD operations
    expenses.py        — expense log operations
    shopping_list.py   — shopping list CRUD operations
    metrics.py         — read-only aggregates for /report and alerts
    settings.py        — household settings (chat id, par level, budget)
  tests/
    test_crud.py       — unit tests for db/crud.py (mocked DB)
    test_expenses.py   — unit tests for db/expenses.py (mocked DB)
    test_shopping_list.py — unit tests for db/shopping_list.py (mocked DB)
    test_metrics.py    — unit tests for db/metrics.py (mocked DB)
    test_settings.py   — unit tests for db/settings.py (mocked DB)
    test_parser.py     — unit tests for bot/parser.py (pure, no DB)
    test_receipt.py    — unit tests for bot/receipt.py's pure reconciliation
    logic (_items_total) and parse_receipt with a mocked ollama client
.github/workflows/ci_cd.yml  — CI runs tests; CD placeholder
requirements.txt             — python-telegram-bot, pytest, ruff
```

## Environment Variables
| Variable        | Required | Default            |
|-----------------|----------|--------------------|
| BOT_TOKEN       | yes      | —                  |
| DB_PATH         | no       | data/tracknest.db  |

No `.env` file, in local dev or production. Export these as real shell
environment variables (e.g. in `~/.bashrc`) for local dev; in production it's
injected by the CD workflow from GitHub Actions secrets at `docker run` time.
Receipt photos need a local [Ollama](https://ollama.com) install with the
`minicpm-v4.5` model pulled — no env var, no API key, fully offline. The
systemd service is disabled (no boot autostart); `bot/receipt.py` starts
`ollama serve` itself on first use and leaves it running. The
shopping-list feature works fine even without Ollama installed at all —
only sending a receipt photo needs it.
`config/__init__.py` only ever reads `os.environ[]` — it doesn't care where
the values came from. `DB_PATH` is not a secret — it's just a file path, and
defaults to `data/tracknest.db` (git-ignored) if unset.

## Commands to Know
```bash
# Run tests (from tracknest/)
BOT_TOKEN=dummy pytest tests/ -q

# Lint
ruff check tracknest/

# Run the bot manually, for one-off local testing (from tracknest/)
# Must run as a module — bot/main.py uses absolute imports (from bot.x
# import y), so `python bot/main.py` fails with ModuleNotFoundError.
python -m bot.main
```

## Local autostart (temporary, until real CD deploy)

The bot normally runs as a `systemd --user` service, not manually — it
survives reboots and restarts on crash, so you don't need to remember to
start it. This is a local-dev stopgap (see `deploy/local/tracknest-bot.service`)
and should be removed once the Oracle VM + Docker + GitHub Actions CD
pipeline in `DEPLOY_STRATEGY.md` actually deploys the bot somewhere real.

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
