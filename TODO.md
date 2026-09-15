# TODO

## ⚠️ HIGH PRIORITY — start next session here (2026-09-15)
Run the bot locally, for real, against Telegram — the SQLite migration has
only been proven with a direct smoke-test script, not through
`python-telegram-bot`'s actual handlers. You already have a bot token (it's
already in GitHub Actions secrets) — it just needs to also be exported in
your local shell, since GH Actions secrets and local shell env vars are two
separate places (production having it doesn't put it on your laptop).

1. `export BOT_TOKEN=...` in `~/.bashrc` (same token already in GH Actions)
2. `python -m bot.main` from `tracknest/` and try real commands (`/add_item`,
   `/log_expense`, `/list_items`, etc.) against a real chat
3. Confirm `data/tracknest.db` gets created and data persists across a restart

Once this is confirmed working, move on to the CD pipeline work below.

## ✅ Done (2026-09-15) — deploy-concepts walkthrough + secrets/DB decisions
The `.env`/secrets, `.dockerignore`, and full Docker flow (VM → Docker engine →
image → container → GHCR → CD pipeline) walkthrough flagged on 2026-08-09
happened this session, step by step. It led to two implemented decisions: no
`.env` anywhere (secrets are shell-exported locally, GitHub Actions secrets in
production), and MySQL replaced by SQLite (removes the VM's RAM concern
entirely, drops `DB_USER`/`DB_PASSWORD`/`DB_HOST`/`DB_NAME` down to nothing).
Both are implemented and tested — see `DEPLOY_STRATEGY.md` for full rationale.

## Deploy (decided — see DEPLOY_STRATEGY.md)
Target: existing Oracle Cloud free-tier VM, via Docker + GHCR. See `DEPLOY_STRATEGY.md`
for the full rationale and the checklist of unknowns.

**SSH access resolved (2026-08-09):** `charmeleon` had lost access, but `Lapras`
already has a working key (`~/ssh-key-2026-05-22.key`, config alias
`oracle-tracknest`). VM confirmed bare (no Docker/MySQL installed), 2 OCPU /
~956Mi RAM. See "RESOLVED" section at the top of `DEPLOY_STRATEGY.md`.

**Next up:** `Dockerfile` and the SQLite migration are both done. Still needed:
fill in the real CD steps in `.github/workflows/ci_cd.yml` (build image, push
to GHCR, SSH to VM, `docker run` with the `/app/data` volume mount), add
`SSH_HOST`/`SSH_USER`/`SSH_PRIVATE_KEY` GH Actions secrets.

- [ ] Fix `charmeleon`'s SSH access to the VM (copy the key from `Lapras`, or add a
      `charmeleon`-specific key to the VM's `authorized_keys` from `Lapras`)

## Local environment
- [ ] Replicate the same venv setup on the other laptop (`python3 -m venv tracknest_bot_env && source tracknest_bot_env/bin/activate && pip install -r requirements.txt`) per `tracknest/docs/setup.md`
