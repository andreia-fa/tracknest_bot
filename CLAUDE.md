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

## Commit Message Format
```
<type>(<scope>): <short description>

- tests: passed
- lint: passed
```
Types: `feat`, `fix`, `chore`, `refactor`, `test`, `docs`
