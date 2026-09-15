# Deploy Strategy

Decided 2026-08-09. This is a living doc — update it as decisions firm up or change.

## ✅ RESOLVED (2026-09-15) — no `.env` anywhere, local or production

Secrets never live in a file, in any environment:

- **Local dev:** `BOT_TOKEN` is exported as a real shell environment variable
  (e.g. in `~/.bashrc`), so every terminal session has it automatically.
  `python-dotenv` has been removed from `requirements.txt`, and
  `config/__init__.py` no longer calls `load_dotenv()` — it only reads
  `os.environ[]`, so it doesn't care whether the value came from the shell,
  CI, or `docker run`. `.env.example` has been deleted. (`DB_PATH` is also
  read from the environment, but it's a file path, not a secret — see the
  SQLite section below.)
- **Production (the VM):** secrets live only in **GitHub Actions secrets**
  (`TRACKNEST_TELEGRAM_BOT_TOKEN` — the old `DB_USER`/`DB_PASSWORD`/`DB_HOST`/
  `DB_NAME` secrets are obsolete now that SQLite has landed) and are injected
  straight into the container via `docker run -e ...` in the CD job. No
  secrets file ever touches the VM's disk.

## ✅ RESOLVED (2026-09-15) — MySQL replaced by SQLite

The VM's ~956Mi RAM was flagged as tight for "bot + MySQL" (see confirmed VM
state below). Tuning native MySQL's memory footprint (small
`innodb_buffer_pool_size`, no `performance_schema`) was considered and would
have worked, but **SQLite was chosen instead** because it removes the RAM
question entirely rather than just shrinking it, and simplifies ops for a
solo-bot POC:

- No separate DB server process at all — SQLite is just a file, read/written
  in-process by the bot. Nothing to install natively on the VM, nothing to
  tune, nothing that can crash independently of the bot.
- No `DB_USER`/`DB_PASSWORD`/`DB_HOST`/`DB_NAME` secrets to manage anywhere —
  GitHub Actions only needs `BOT_TOKEN` going forward. Fewer secrets, fewer
  places for something to be misconfigured.
- Fits the current scale (single bot, single VM, low write concurrency) with
  room to move to a real server DB later without much friction — see
  "Migration path" below.

**New requirement this introduces:** containers are ephemeral — every redeploy
replaces the container, and anything written inside it is lost. Since the
SQLite file now holds all persistent data (previously MySQL's job, running
outside Docker on the VM), the container **must** mount a host path as a
volume (`docker run -v /home/ubuntu/tracknest-data:/app/data ...`) so the
`.db` file survives redeploys. This didn't apply to the MySQL plan, since
MySQL lived outside Docker already.

**Status: implemented and tested (2026-09-15).** `db/database.py` now opens a
`sqlite3` connection (with `row_factory = sqlite3.Row` and `PRAGMA
foreign_keys = ON`) instead of `mysql.connector`; `crud.py`/`expenses.py` use
`?` placeholders and `ON CONFLICT(...) DO UPDATE` instead of MySQL's `%s` and
`ON DUPLICATE KEY UPDATE`; `config/__init__.py` now has `DB_PATH` (optional,
defaults to `data/tracknest.db`) instead of `DB_USER`/`DB_PASSWORD`/`DB_HOST`/
`DB_NAME`. `mysql-connector-python` dropped from `requirements.txt`;
`setup_db.sql` deleted (SQLite needs no separate DB/user/grants — `init_db()`
creates the file and schema on first run). All 18 tests pass unchanged
(mocked DB, same interface), plus a manual smoke test against a real SQLite
file verified upsert, dict conversion, and cascading delete all behave
correctly. `Dockerfile` and CI/CD workflow updated too — see their line items
below.

## ✅ RESOLVED (2026-08-09, `Lapras` machine) — SSH access was never actually lost

The "lost SSH access" blocker below was specific to the `charmeleon` machine.
`Lapras` (this machine) already has a working key and config for the VM:

- Key: `~/ssh-key-2026-05-22.key` (RSA, generated 2026-05-22, predates the
  `charmeleon` incident)
- `~/.ssh/config` entry:
  ```
  Host oracle-tracknest
    HostName 92.5.103.47
    User ubuntu
    Port 22
    IdentityFile ~/ssh-key-2026-05-22.key
    IdentitiesOnly yes
  ```
- Verified working: `ssh oracle-tracknest` connects fine.

**No need to terminate/recreate the instance.** To get `charmeleon` (or any other
machine) working again, just copy `~/ssh-key-2026-05-22.key` /
`ssh-key-2026-05-22.key.pub` over from `Lapras` (or add `Lapras`'s pubkey to
`~/.ssh/authorized_keys` on the VM if each machine should have its own key per
the original cross-machine-access decision).

### Confirmed VM state (checked 2026-08-09 via `Lapras`)
- Public IP: `92.5.103.47` (unchanged)
- OS: Ubuntu 22.04.5 LTS
- Shape: 2 OCPU / ~956Mi RAM — this is the tighter AMD `E2.1.Micro`-class shape,
  **not** the roomier Ampere ARM one. Confirms the "tight for bot + MySQL
  together" concern flagged below.
- Disk: 45G total, 41G available
- **Docker: not installed. MySQL: not installed.** VM is bare — nothing valuable
  is at risk here, consistent with "nothing deployed yet."

This resolves several of the open items further down (VM access, what's
installed, shape). Remaining open items: firewall rules, GH Actions secrets,
Dockerfile, CD steps.

## Guiding principle

TrackNest is currently a POC: optimize for **$0 running cost**, but choose a shape
that doesn't need a rewrite when it's time to scale past the POC stage.

## Decision

**Target: the existing Oracle Cloud "Always Free" VM.** Deploy to it via **Docker +
GHCR** rather than running the bot as a bare `python bot/main.py` process.

## Why

- The Oracle free-tier VM is already provisioned, so hosting cost is $0 either way —
  the only real choice is *how* we deploy to it.
- Containerizing now means the future "scale past POC" step is *point a new host at
  the same GHCR image* — no deploy-pipeline rewrite, no re-learning how the app is
  packaged. Bare-metal `systemd` deploys don't give you that.
- The DB is SQLite (decided 2026-09-15, see RESOLVED section above) — a single
  file on a mounted volume, no separate DB server to run or manage. A managed
  DB service isn't justified at this scale/cost either way.

## Target architecture

- **CI** (already working): run tests + lint on every push/PR.
- **CD** (to build), on push to `main`:
  1. Build the bot's Docker image.
  2. Push to GHCR (`ghcr.io/andreia-fa/tracknest_bot`), auth via the built-in
     `GITHUB_TOKEN` — no extra registry secret needed.
  3. SSH into the Oracle VM using `SSH_HOST` / `SSH_USER` / `SSH_PRIVATE_KEY`
     GitHub Actions secrets.
  4. `docker pull` the new image, stop/remove the old container, `docker run` the
     new one with `BOT_TOKEN` passed as an env var and a volume mount
     (`-v /home/ubuntu/tracknest-data:/app/data`) so the SQLite file persists
     across redeploys.
- **Database**: SQLite (decided 2026-09-15, replaces the earlier native-MySQL
  plan) — a single file on the mounted volume above. No separate DB server,
  no DB credentials, nothing installed on the VM beyond Docker itself.

## Open items before this is implementable

- [x] VM access: public IP `92.5.103.47`, user `ubuntu`, key `~/ssh-key-2026-05-22.key`
      works from `Lapras` — confirmed 2026-08-09
- [x] What's installed: neither `docker` nor `mysql`/`mysqld` present — VM is bare
- [x] Confirm the VM's shape/resources: 2 OCPU / ~956Mi RAM (AMD `E2.1.Micro`-class,
      **not** the roomier Ampere shape) — tight for bot + MySQL together, keep an eye
      on memory once both are running
- [x] ~~MySQL: native install~~ — **superseded 2026-09-15**, replaced by SQLite
      (see RESOLVED section above). No native DB install on the VM at all now.
- [ ] Firewall / security-list rules: SSH inbound already works (confirmed by the
      successful connection above); still need to confirm no other rule changes are
      needed once the bot/Docker are added (bot itself needs no inbound port — it
      long-polls Telegram)
- [ ] Add GitHub Actions secrets: `SSH_HOST`, `SSH_USER`, `SSH_PRIVATE_KEY`
      (private key content is `~/ssh-key-2026-05-22.key` on `Lapras`). `BOT_TOKEN`
      already exists per `TODO.md`; the old `DB_USER`/`DB_PASSWORD`/`DB_HOST`/
      `DB_NAME` secrets are no longer needed now that SQLite has landed — safe
      to delete from the repo settings.
- [x] Write the `Dockerfile` — done 2026-08-09, updated 2026-09-15 for SQLite:
      creates `/app/data`, sets `ENV DB_PATH=/app/data/tracknest.db`, declares
      `VOLUME ["/app/data"]`. No MySQL client libs needed (never were — it's a
      pure-Python driver either way).
- [ ] Write the actual CD steps in `.github/workflows/ci_cd.yml` (currently a
      placeholder) — include the `-v /home/ubuntu/tracknest-data:/app/data`
      volume mount for the SQLite file
- [x] **SQLite migration (2026-09-15 decision) — implemented and tested.** See
      the "Status" note in the RESOLVED section above for the full rundown.

## Local machine notes

- **`Lapras`**: has working Oracle VM SSH access (`~/ssh-key-2026-05-22.key` +
  `~/.ssh/config` entry `oracle-tracknest`). Use this machine for VM work until
  `charmeleon` is fixed.
- **`charmeleon`**: lost SSH access to the VM (see resolved section above for
  what was tried). Fix by copying the key from `Lapras`, or adding a
  `charmeleon`-specific key to the VM's `authorized_keys` while logged in from
  `Lapras`. Not yet done.

## Migration path (post-POC, future)

Once this needs to scale past a single free VM: keep the same GHCR image, just
point a new target at it (bigger VPS, a PaaS, k8s). Only the deploy step's
*destination* changes — the image and CI stay the same.

SQLite specifically stops being the right fit once there's real concurrent
write traffic (multiple processes/instances writing at once) — it's a
single-file, single-writer-at-a-time database. If TrackNest ever needs that
(e.g. multiple bot instances, a web dashboard writing alongside the bot),
migrate to a managed server DB (MySQL via PlanetScale/RDS, or Postgres) at
that point. Not a concern at current scale (one bot process, one writer).
