#!/usr/bin/env bash
# Weekly snapshot of the live TrackNest DB onto this laptop, replacing last
# week's. Run by tracknest-backup.timer; safe to run by hand any time.
#
# The snapshot is taken with SQLite's backup API inside the container (a
# plain file copy can catch a half-written DB), streamed over SSH, and only
# replaces the previous one if it passes an integrity check — a failed or
# broken pull never overwrites the last good copy.
set -euo pipefail

BACKUP_DIR="$HOME/tracknest-backups"
TARGET="$BACKUP_DIR/tracknest-weekly.db"
PYTHON=/home/afa/my_projects/tracknest_bot/tracknest_bot_env/bin/python

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
TMP="$(mktemp "$BACKUP_DIR/.incoming.XXXXXX")"
trap 'rm -f "$TMP"' EXIT

ssh -o BatchMode=yes oracle-tracknest docker exec -i tracknest-bot python - > "$TMP" <<'PY'
import os, sqlite3, sys, tempfile
fd, path = tempfile.mkstemp(suffix=".db")
os.close(fd)
src, dst = sqlite3.connect("/app/data/tracknest.db"), sqlite3.connect(path)
src.backup(dst)
dst.close()
src.close()
with open(path, "rb") as f:
    sys.stdout.buffer.write(f.read())
os.remove(path)
PY

"$PYTHON" - "$TMP" <<'PY'
import sqlite3, sys
conn = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
result = conn.execute("PRAGMA integrity_check").fetchone()[0]
items = conn.execute("SELECT COUNT(*) FROM inventory_items").fetchone()[0]
if result != "ok":
    sys.exit(f"Snapshot failed integrity check: {result}")
print(f"Snapshot ok: {items} items")
PY

chmod 600 "$TMP"
mv "$TMP" "$TARGET"
trap - EXIT
echo "Saved $TARGET"
