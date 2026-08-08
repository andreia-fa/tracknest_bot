# TODO

## Deploy (open — needs a decision)
- [ ] Pick a deploy target for the CD job in `.github/workflows/ci_cd.yml`:
  - SSH into an existing VPS and restart the bot process, or
  - Build & push a Docker image to GHCR, or
  - A PaaS (Railway / Render / Fly.io)
- [ ] Provision whatever that target needs (server, container registry, PaaS service)
- [ ] Add the resulting secrets (host/key, registry token, or PaaS token) to the GitHub repo's Actions secrets
- [ ] Replace the `Deploy (not configured yet)` placeholder step with the real deploy commands

## Local environment
- [ ] Fill in real values in `.env` (`BOT_TOKEN`, `DB_USER`, `DB_PASSWORD`) — currently placeholders copied from `.env.example`
- [ ] Run `setup_db.sql` against a local/remote MySQL instance — the bot can't actually run end-to-end until this exists
- [ ] Replicate the same venv setup on the other laptop (`python3 -m venv tracknest_bot_env && source tracknest_bot_env/bin/activate && pip install -r requirements.txt`) per `tracknest/docs/setup.md`
