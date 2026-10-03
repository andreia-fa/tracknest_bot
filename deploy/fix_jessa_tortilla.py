"""One-off (2026-10-03): Jessa "SE" are Slipeinlagen (panty liners); VOLLKORNTORTILLA is a wrap.

Backs the DB up first and applies everything in one transaction. Run inside the bot container:
    ssh oracle-tracknest docker exec -i tracknest-bot python - < deploy/fix_jessa_tortilla.py
"""

import sqlite3
from datetime import datetime
from pathlib import Path

DB = Path("/app/data/tracknest.db")
BACKUP_DIR = DB.parent / "backups"

FIXES = {
    "Jessa SE Cotton Normal": "panty liners",
    "VOLLKORNTORTILLA": "wrap",
}

BACKUP_DIR.mkdir(exist_ok=True)
backup = BACKUP_DIR / f"tracknest-{datetime.now():%Y%m%d-%H%M%S}-before-jessa-tortilla.db"
src = sqlite3.connect(DB)
dst = sqlite3.connect(backup)
src.backup(dst)
dst.close()
print(f"Backup: {backup}")

with src:  # one transaction: commits on success, rolls back on any error
    for name, product in FIXES.items():
        old = src.execute("SELECT product FROM inventory_items WHERE name = ?", (name,)).fetchone()
        if old is None:
            print(f"  skipped (not found): {name}")
            continue
        src.execute("UPDATE inventory_items SET product = ? WHERE name = ?", (product, name))
        src.execute("UPDATE item_aliases SET product = ? WHERE canonical_name = ?", (product, name))
        print(f"  {name}: {old[0]!r} -> {product!r}")
src.close()
print("Done.")
