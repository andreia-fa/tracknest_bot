# TODO

## 💡 Future features (ideas from 2026-09-19 testing session, not yet built)

**Smart quantity-mismatch check.** Prompted by a real miss: a receipt logged
qty 2 for an item but only 1 was actually received. Quantities of ≤2 are easy
for a person to self-verify and shouldn't be flagged — the idea is to only
offer an optional, playful check when the *pattern* of a receipt looks worth
a second look, e.g.:
- A short receipt where most/all items have qty > 2 (like 4 of 4 items), or
- A long receipt where a handful of items are outliers (e.g. 3 of 25 items
  have qty > 2).
After processing, prompt something like "Want a fast check on your
groceries?" — opt-in, not forced on every receipt. Needs a concrete rule for
"pattern looks worth flagging" before building (the two examples above are a
starting point, not a spec).

**Shopping bag review.** A broader end-of-trip assessment of that day's
haul — spend, categories, any quantity flags — rather than just the
per-item confirmation lines sent during processing. Not yet scoped.

## ✅ Done (2026-09-19) — bot confirmed running live against Telegram
Ran `python -m bot.main` from `tracknest/` for real, tested the shopping-list
+ receipt-photo flow end to end against a live chat (`@tracknest_app_bot`),
confirmed `tracknest/data/tracknest.db` (note: relative `DB_PATH`, resolves
against the process's cwd) gets written to correctly. Also fixed two real
bugs found along the way: `handle_photo` was blocking the whole event loop
synchronously during receipt parsing (now off-thread via `asyncio.to_thread`),
and duplicate receipt submissions were double-counting expenses (now guarded
by `expenses.is_duplicate_purchase()`). Added item categorization to receipt
parsing for the future expenses dashboard.

## ⚠️ TODO — GitHub Actions secret is stale
The bot token has been rotated multiple times locally (2026-09-19, security
incident — see git history) but the `TRACKNEST_TELEGRAM_BOT_TOKEN` GitHub
Actions secret was only ever updated once, right after the first rotation.
CI's "Test Telegram connection" step (deploy job, `ci_cd.yml`) is failing on
every push to `main` as a result — expected, not a real bug, but worth fixing
so CI goes green again:
```
gh secret set TRACKNEST_TELEGRAM_BOT_TOKEN --repo andreia-fa/tracknest_bot
```
Not urgent — the actual deploy step is still a placeholder, so this doesn't
block anything functional yet.

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
