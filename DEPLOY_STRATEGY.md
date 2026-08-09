# Deploy Strategy

Decided 2026-08-09. This is a living doc — update it as decisions firm up or change.

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
- MySQL also runs on the same VM for now (native install or a sidecar container). A
  managed DB service isn't justified at this scale/cost.

## Target architecture

- **CI** (already working): run tests + lint on every push/PR.
- **CD** (to build), on push to `main`:
  1. Build the bot's Docker image.
  2. Push to GHCR (`ghcr.io/andreia-fa/tracknest_bot`), auth via the built-in
     `GITHUB_TOKEN` — no extra registry secret needed.
  3. SSH into the Oracle VM using `SSH_HOST` / `SSH_USER` / `SSH_PRIVATE_KEY`
     GitHub Actions secrets.
  4. `docker pull` the new image, stop/remove the old container, `docker run` the
     new one with `BOT_TOKEN`/`DB_*` passed as env vars.
- **MySQL**: runs on the same VM. Not yet decided whether native install or its own
  container with a named volume — see open items below.

## Open items before this is implementable

We don't yet know the actual state of the Oracle VM — none of this is confirmed:

- [ ] VM access: public IP, SSH user, whether a key pair already exists for it
- [ ] What's installed: check `cat /etc/os-release`, `docker --version`,
      `mysql --version` (or `mysqld --version`) on the VM
- [ ] MySQL: native install vs. its own Docker container — decide once we know
      what's already there
- [ ] Confirm the VM's shape/resources are enough for bot + MySQL together. Oracle's
      Always Free tier is either the AMD `VM.Standard.E2.1.Micro` (1 OCPU / 1GB RAM —
      tight for bot + MySQL) or an Ampere ARM shape (up to 4 OCPU / 24GB RAM, if that's
      what was provisioned) — worth checking which one this actually is
- [ ] Firewall / security-list rules: SSH inbound needs to be open; the bot itself
      needs no inbound port since it long-polls Telegram (unless we later switch to
      webhooks)
- [ ] Add GitHub Actions secrets: `SSH_HOST`, `SSH_USER`, `SSH_PRIVATE_KEY`
      (`BOT_TOKEN`/`DB_*` secrets already exist per `TODO.md`)
- [ ] Write the `Dockerfile`
- [ ] Write the actual CD steps in `.github/workflows/ci_cd.yml` (currently a
      placeholder)

## Migration path (post-POC, future)

Once this needs to scale past a single free VM: keep the same GHCR image, just
point a new target at it (bigger VPS, a PaaS, k8s, managed MySQL like PlanetScale/
RDS). Only the deploy step's *destination* changes — the image and CI stay the same.
