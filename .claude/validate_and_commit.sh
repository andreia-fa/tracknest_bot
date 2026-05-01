#!/usr/bin/env bash
# Runs after Claude stops. Validates code, then commits and pushes if clean.
set -euo pipefail

PROJECT_ROOT="/home/afa/my_projects/tracknest_bot"
VENV="$PROJECT_ROOT/tracknest_bot_env/bin/activate"

cd "$PROJECT_ROOT"

# Nothing to validate if working tree is clean
if git diff --quiet && git diff --staged --quiet && [ -z "$(git ls-files --others --exclude-standard)" ]; then
  exit 0
fi

source "$VENV"

# --- Run tests ---
TEST_OUTPUT=$(
  cd tracknest
  BOT_TOKEN=dummy DB_USER=dummy DB_PASSWORD=dummy pytest tests/ -q 2>&1
) && TEST_OK=1 || TEST_OK=0

# --- Run lint ---
LINT_OUTPUT=$(ruff check tracknest/ 2>&1) && LINT_OK=1 || LINT_OK=0

# --- Report failures and block ---
if [ "$TEST_OK" -eq 0 ] || [ "$LINT_OK" -eq 0 ]; then
  REPORT=""
  [ "$TEST_OK" -eq 0 ] && REPORT="TESTS FAILED:\n${TEST_OUTPUT}\n"
  [ "$LINT_OK" -eq 0 ] && REPORT="${REPORT}LINT FAILED:\n${LINT_OUTPUT}"

  python3 - <<PYEOF
import json
report = """$REPORT"""
print(json.dumps({
    "decision": "block",
    "reason": "Validation failed — fix before completing:\n\n" + report.strip()
}))
PYEOF
  exit 0
fi

# --- Commit and push ---
git add -A

CHANGED=$(git diff --cached --name-only | head -6 | tr '\n' ',' | sed 's/,/, /g; s/, $//')

git commit -m "$(cat <<EOF
chore: $CHANGED

- tests: passed
- lint: passed

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"

PUSH_MSG="committed (no remote configured)"
if git remote get-url origin &>/dev/null; then
  git push && PUSH_MSG="committed and pushed" || PUSH_MSG="committed (push failed — check credentials)"
fi

python3 -c "import sys; print('{\"systemMessage\": \"Validation passed — ' + sys.argv[1] + '.\"}')" "$PUSH_MSG"
