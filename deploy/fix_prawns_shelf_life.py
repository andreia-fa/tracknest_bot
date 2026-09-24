"""One-off (2026-09-24): Berida Garnele are frozen — 90 days, not 1; clear the alert that fired.

Run inside the bot container, e.g.:
    ssh oracle-tracknest docker exec -i tracknest-bot python - < deploy/fix_prawns_shelf_life.py
"""

import sys

from db import crud

crud.set_profile("Berida Garnele", shelf_life_days=90)
crud.mark_spare_alert_pending("Berida Garnele", pending=False)
item = crud.get_item("Berida Garnele")
print(f"Berida Garnele: shelf life {item['shelf_life_days']} days", flush=True)
sys.stdout.flush()
