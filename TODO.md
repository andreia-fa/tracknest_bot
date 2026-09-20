# TODO

## ⏰ TODO (due 2026-11-20) — Revisit /report's shelf-life confidence gate
`/report`'s "Cost per day you own it" line divides an item's latest price by
its `shelf_life_days` estimate — but that estimate starts as the user's cold
guess and only becomes evidence once a real repurchase interval has tested
it. Concrete example that prompted this: sushi was declared a 2-day item,
then was still being eaten on day three, making the naive €/day figure ~33%
off — and since the figure also ranks items against each other, one bad
estimate reorders the whole list.

Fixed 2026-09-20 by gating the line behind
`_MIN_PURCHASES_FOR_SHELF_LIFE_TRUST` (currently 2 purchases per item) in
`tracknest/db/metrics.py` — see the `REVISIT 2026-11-20` comment on that
constant and the "How much to trust a shelf-life estimate" section in
`tracknest/docs/expenses.md` for the full reasoning.

**When this date arrives:**
- [ ] Run `metrics.get_daily_cost()` and check `ready` vs `tracked` — if
      `ready` is still 0 after two months, the gate is too strict to ever
      deliver the insight and needs rethinking, not just tuning.
- [ ] Raise the threshold toward 3 if single intervals still produce noisy
      figures; lower it if too few items ever qualify (slow-moving items
      like a 60-day peanut butter may take 6 months to earn 2 purchases).
- [ ] Reconsider whether "Running out soon" (deliberately left ungated,
      since it's a cheap self-correcting nudge rather than an analytical
      claim) has earned more or less prominence.

## ✅ Done (2026-09-19) — local autostart via systemd, no more manual restarts
The bot was being started manually (`python -m bot.main &` + `disown`) every
session, which meant a reboot or closed terminal silently killed it until
someone remembered to restart it. Replaced with a `systemd --user` service
(`deploy/local/tracknest-bot.service`, installed to
`~/.config/systemd/user/`) — `Restart=on-failure`, enabled + `loginctl
enable-linger afa` so it starts at boot without needing an active login
session. Token moved out of `~/.bashrc` reliance into
`~/.config/tracknest-bot.env` (`EnvironmentFile=`, not in git, `chmod 600`),
since a non-interactive systemd service never sources `.bashrc`. See
`CLAUDE.md`'s "Local autostart" section for the day-to-day commands
(`systemctl --user status/restart`, `journalctl --user -f`).

**This is explicitly temporary** — remove `deploy/local/` and the systemd
unit once the real Oracle VM + Docker + CD pipeline below actually deploys
the bot somewhere. It's also machine-specific (hardcoded `/home/afa/...`
paths), so replicating this setup on another laptop needs the paths in
`deploy/local/tracknest-bot.service` adjusted first.

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
