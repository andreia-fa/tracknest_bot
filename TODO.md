# TODO

## Deploy (decided — see DEPLOY_STRATEGY.md)
Target: existing Oracle Cloud free-tier VM, via Docker + GHCR. See `DEPLOY_STRATEGY.md`
for the full rationale and the checklist of unknowns.

**SSH access resolved (2026-08-09):** `charmeleon` had lost access, but `Lapras`
already has a working key (`~/ssh-key-2026-05-22.key`, config alias
`oracle-tracknest`). VM confirmed bare (no Docker/MySQL installed), 2 OCPU /
~956Mi RAM. See "RESOLVED" section at the top of `DEPLOY_STRATEGY.md`.

**Next up:** MySQL will be a native install on the VM (decided 2026-08-09 — RAM's
too tight for a MySQL container on top of the bot container). Write the
`Dockerfile` (bot only), fill in the real CD steps in
`.github/workflows/ci_cd.yml`, add `SSH_HOST`/`SSH_USER`/`SSH_PRIVATE_KEY` GH
Actions secrets.

- [ ] Fix `charmeleon`'s SSH access to the VM (copy the key from `Lapras`, or add a
      `charmeleon`-specific key to the VM's `authorized_keys` from `Lapras`)

## Local environment
- [ ] Fill in real values in `.env` (`BOT_TOKEN`, `DB_USER`, `DB_PASSWORD`) — currently placeholders copied from `.env.example`
- [ ] Run `setup_db.sql` against a local/remote MySQL instance — the bot can't actually run end-to-end until this exists
- [ ] Replicate the same venv setup on the other laptop (`python3 -m venv tracknest_bot_env && source tracknest_bot_env/bin/activate && pip install -r requirements.txt`) per `tracknest/docs/setup.md`
