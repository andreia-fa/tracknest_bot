# Deploy Strategy

Decided 2026-08-09. This is a living doc — update it as decisions firm up or change.

## ⚠️ HIGH PRIORITY — start here next session (any machine)

**Blocker: we lost SSH access to the Oracle VM (`92.5.103.47`, user `ubuntu`,
region `eu-frankfurt-1`).** No original key/credentials for it could be found on
the `charmeleon` machine — check the other laptop too, in case it still has
whatever key was used originally.

What was tried on 2026-08-09 (charmeleon machine) and didn't work:
- Instance Console Connection (serial console) to interrupt GRUB and drop into a
  recovery shell to re-add a key. Connected fine (after two gotchas: Console
  Connections need an **RSA** key specifically, not ed25519; and you must add
  `-o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa` to *both*
  the outer ssh and the inner `ProxyCommand` ssh, or negotiation fails).
  But the VM boots too fast / GRUB's timeout is too short to catch — by the time
  the console session was live, cloud-init had already finished. Two reboot
  attempts both missed the window.

**Recommended next step**: don't keep chasing the GRUB timing race. Instead:
1. Confirm with the user whether anything valuable is actually on that VM (was
   MySQL ever manually installed there with real data?). Likely answer: no,
   nothing is deployed yet per the rest of this doc.
2. If it's bare: **terminate the instance and recreate it** via the Oracle
   Console, pasting a fresh SSH public key at creation time (Oracle's
   instance-creation flow accepts one or more public keys directly — this
   sidesteps the whole recovery problem). Add a public key for *each* machine
   that needs access (paste multiple, one per line) — see the per-device-key
   note further down.
3. If it turns out something valuable *is* there: don't terminate — instead use
   Oracle's boot-volume rescue procedure (stop instance → detach boot volume →
   attach to a temporary rescue instance → mount and edit
   `/home/ubuntu/.ssh/authorized_keys` directly on the disk → detach → reattach
   to original instance → start). More involved, not yet attempted.
4. Once back in, note the (possibly new) public IP here and in `TODO.md`, then
   resume the open items below.

Key material generated this session (local to the `charmeleon` machine, in
`~/.ssh/`, not committed to git):
- `id_rsa_oracle_console` / `.pub` — RSA key pair created for the console-connection
  attempt above. Reusable as the new VM's authorized key if we recreate the
  instance (paste the `.pub` contents at creation time). Each machine should
  still end up with its **own** key added to `authorized_keys` — see the
  cross-machine access decision above in this doc's history.

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
