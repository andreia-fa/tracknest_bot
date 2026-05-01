# TrackNest Bot — Developer Agent

## Project Overview
TrackNest is a Telegram bot for household inventory and expense tracking, built with `python-telegram-bot` v20+ and MySQL.

## Structure
```
tracknest/
  bot/main.py          — bot entry point, all command handlers
  config/__init__.py   — reads env vars (BOT_TOKEN, DB_*)
  db/
    database.py        — MySQL connection + schema init (init_db)
    crud.py            — inventory CRUD operations
    expenses.py        — expense log operations
  tests/
    test_crud.py       — unit tests for db/crud.py (mocked DB)
    test_expenses.py   — unit tests for db/expenses.py (mocked DB)
.github/workflows/ci_cd.yml  — CI runs tests; CD placeholder
.env.example                 — required env var template
requirements.txt             — python-telegram-bot, mysql-connector-python, python-dotenv, pytest, ruff
```

## Environment Variables
| Variable      | Required | Default       |
|---------------|----------|---------------|
| BOT_TOKEN     | yes      | —             |
| DB_USER       | yes      | —             |
| DB_PASSWORD   | yes      | —             |
| DB_HOST       | no       | localhost     |
| DB_NAME       | no       | tracknest_db  |

Local dev: copy `.env.example` → `.env` and fill in values.

## Commands to Know
```bash
# Run tests (from tracknest/)
BOT_TOKEN=dummy DB_USER=dummy DB_PASSWORD=dummy pytest tests/ -q

# Lint
ruff check tracknest/

# Run the bot (from tracknest/)
python bot/main.py
```

## Development Rules
- **Always read the relevant source files before making changes.** Never assume structure.
- Follow Python best practices: type hints where meaningful, no unnecessary comments.
- Tests mock DB connections — keep it that way. Do not introduce real DB calls in tests.
- `config/__init__.py` uses `os.environ[]` for required vars — it will raise on startup if any are missing. This is intentional.
- After every task: tests + lint must pass. The Stop hook handles this automatically and will block completion if they fail.

## Commit Message Format
```
<type>(<scope>): <short description>

- tests: passed
- lint: passed
```
Types: `feat`, `fix`, `chore`, `refactor`, `test`, `docs`
