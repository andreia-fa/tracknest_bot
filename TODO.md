# TODO

## Deploy (decided — see DEPLOY_STRATEGY.md)
Target: existing Oracle Cloud free-tier VM, via Docker + GHCR. See `DEPLOY_STRATEGY.md`
for the full rationale and the checklist of unknowns that need verifying before this
is implementable (VM access, what's installed on it, MySQL native-vs-container, etc).

**⚠️ Start next session here:** we lost SSH access to the Oracle VM and recovery via
the serial console didn't work (boot's too fast to catch GRUB). See the "HIGH
PRIORITY" section at the top of `DEPLOY_STRATEGY.md` for what was tried and the
recommended fix (likely: terminate + recreate the instance with a fresh key).

## Local environment
- [ ] Fill in real values in `.env` (`BOT_TOKEN`, `DB_USER`, `DB_PASSWORD`) — currently placeholders copied from `.env.example`
- [ ] Run `setup_db.sql` against a local/remote MySQL instance — the bot can't actually run end-to-end until this exists
- [ ] Replicate the same venv setup on the other laptop (`python3 -m venv tracknest_bot_env && source tracknest_bot_env/bin/activate && pip install -r requirements.txt`) per `tracknest/docs/setup.md`
