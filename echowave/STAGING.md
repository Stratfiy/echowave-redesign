# Staging

> Production/staging parity (same keys, different secrets, same deploy
> script) and how to check it: **`OPS-RUNBOOK.md`** section 3 and
> `python -m scripts.check_infra parity`. Capacity reviews with a recorded
> verdict: `python -m scripts.capacity_review` (runbook section 10).

A staging box exists to answer one question production must never be asked:
**how many concurrent calls does a node hold before it degrades?** The fleet in
`INFRASTRUCTURE.md` is sized on "5 concurrent calls per vCPU", which that
document itself flags as an estimate that has never been measured. Staging is
where it gets measured, with `scripts/load_ramp.py` and
`scripts/rehearse_concurrency.py`, against a box that bills no real money and
drops no real customer's call.

Staging is not a smaller architecture. It is the **same** `docker-compose.yaml`
on its own box with its own secrets and its own hostname. The stack is
self-contained — Postgres, Redis, MinIO, nginx, coturn and the tunnel are all
sibling containers — so there is nothing external to stand up alongside it.

## What you provision (the human part)

1. **One EC2 instance in `ap-south-1`.** Size it like *one production media
   node*, not smaller, or the per-vCPU number you measure will not extrapolate.
   A `c7i.2xlarge` matches the fleet in `INFRASTRUCTURE.md`; a `c7i.xlarge` is
   fine for a first pass. Give it an Elastic IP.
2. **A staging hostname** — `staging.decibyl.ai` — pointed at that IP (or a
   Cloudflare tunnel, same as prod).
3. **Razorpay test-mode keys.** Staging uses test keys and a test webhook, so a
   top-up in a load run moves no real rupee. Never paste live keys here.
4. **Fresh secrets.** Generate new `PLATFORM_CREDENTIAL_SECRET`,
   `OSS_JWT_SECRET`, `POSTGRES_PASSWORD`, `REDIS_PASSWORD` for staging. Never
   reuse a prod secret on a box whose whole job is to be hammered.

## Bring it up (the same path as prod)

On the box, as root — identical to `DEPLOY.md §"Putting it on an EC2 box"`,
with the staging values from above:

```bash
git clone --recurse-submodules -b <branch-under-test> \
    https://github.com/Stratfiy/echowave-redesign.git
cd echowave-redesign/echowave

sudo DEPLOY_MODE=build REPO_SOURCE=existing SERVER_IP=<staging.elastic.ip> \
    ./scripts/setup_remote.sh

cat deploy/decibyl.env.template | sudo tee -a .env >/dev/null
sudo nano .env    # staging hostname, Razorpay TEST keys, the four fresh secrets

sudo ./remote_up.sh --build
```

Confirm it is live:

```bash
curl -s https://staging.decibyl.ai/api/v1/health | jq .status   # "ok"
```

## Run the load test against it

From a machine with real bandwidth — a laptop or, better, two or three small
load-generator boxes, **not** a single proxied container — ramp the control
plane until it bends:

```bash
python -m scripts.load_ramp \
    --base-url https://staging.decibyl.ai \
    --levels 1,10,25,50,100,200,400 --rounds 4 \
    --knee-error-rate 0.10 --knee-p95-ms 8000
```

It stops at the first level that crosses the knee and prints the last healthy
concurrency. That number, divided by the box's vCPUs, is the real
"calls per vCPU" the fleet sizing has been guessing at.

Then, on the box itself, prove the spend ceiling holds under real concurrent
transactions (this places no calls and spends nothing):

```bash
docker compose exec api python -m scripts.rehearse_concurrency --calls 40
```

## What "green" means before we trust production at volume

- `load_ramp` reaches the target concurrency (one node's share of peak, ~40
  concurrent for the Telangana shape) with error rate 0 and p95 within budget.
- `rehearse_concurrency` passes all three checks.
- Redis and the Postgres pool stay healthy through the ramp (watch
  `docker stats` and the pool's checkout wait; if the pool saturates first,
  that is the ceiling, and the fix is pool/pgbouncer config, not app code).

Only then is the per-vCPU estimate a measurement, and only then does the fleet
in `INFRASTRUCTURE.md` rest on a tested number.

## Functional staging on the CI box

The load-test box above is still the right place to measure capacity. Until it
exists, the CI box (the runner labelled `ci`) also runs a **functional**
staging: the same stack, to switch features on and try them with real keys
before production. Its database, secrets, hostname and Parameter Store path
are its own. It shares CPU with test runs, so never load-test it.

It does not collide with CI: the test jobs' Postgres and Redis are on 55432
and 56379; staging's are on the stack's usual loopback ports, and nginx takes
80/443.

### One-time setup on the CI box

1. **Public address (optional):** staging works without one: the runner is on
   the box and checks the API on loopback. To open it in a browser or ring it,
   give the box an Elastic IP, point `staging.decibyl.ai` at it, and open
   80/443 plus the TURN ports (UDP+TCP 3478 and 5349, UDP 49152-49200) in
   `decibyl-ci-sg`. Today the group has no inbound rules at all.
2. **Checkout:** no GitHub key on the box. Each deploy fetches with that
   job's own token. For the first copy, take the runner's checkout:
   ```bash
   sudo -u ubuntu cp -a /home/ubuntu/actions-runner/_work/echowave-redesign/echowave-redesign \
       /home/ubuntu/decibyl-staging
   cd /home/ubuntu/decibyl-staging
   git remote set-url origin https://github.com/Stratfiy/echowave-redesign.git
   git config --unset-all http.https://github.com/.extraheader || true
   cd echowave
   sudo SERVER_IP=$(hostname -I | awk '{print $1}') CERT_MODE=self-signed \
       DEPLOY_MODE=build REPO_SOURCE=existing FASTAPI_WORKERS=1 \
       ENABLE_TELEMETRY=false ./scripts/setup_remote.sh </dev/null
   cat deploy/decibyl.env.template | sudo tee -a .env >/dev/null
   ```
   With a public address, use `SERVER_IP=<elastic ip>` and drop
   `CERT_MODE`. Never run `./remote_up.sh` on a private IP here: it starts a
   public Cloudflare quick tunnel. The first deploy (step 7) starts the stack.
3. **`.env`:** setup already wrote fresh `OSS_JWT_SECRET`,
   `POSTGRES_PASSWORD` and `REDIS_PASSWORD`. Add a fresh
   `PLATFORM_CREDENTIAL_SECRET`, which must be a Fernet key
   (`python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`,
   or the same inside the api container; a hex string is refused the first
   time a key is saved) and Razorpay **test**
   keys; with a hostname, set `PUBLIC_BASE_URL=https://staging.decibyl.ai`.
   Never a production secret.
4. **Provider keys** (Super admin → Provider keys once it is up, or
   `/decibyl/staging/` in Parameter Store): Claude, Sarvam, the app connector,
   web search, a test phone number on the carrier, WhatsApp test credentials.
   Give the model keys a monthly spend cap.
5. **Switches:** turn on the features under test in Super admin → Flags for
   the staging workspace, or in `.env` (`PERSONAL_MEMORY_ENABLED=true` and so
   on). Staging may run ahead of production; production only follows a pass
   here.
6. **Test accounts:** two accounts in one workspace, `staging-a@example.com`
   and `staging-b@example.com` (B invited by A). Their passwords live only in
   `/home/ubuntu/decibyl-staging/check.env` (root, mode 600); the deploy
   workflow reads them from there when the GitHub secrets below are unset,
   and masks the passwords in its log.
7. **GitHub:** create an environment named `staging`, with variables
   `STAGING_URL` (only once there is a public address; the default is the
   API on loopback) and, if config should come from
   Parameter Store, `AWS_STAGING_ROLE_ARN`; and secrets `STAGING_EMAIL_A`,
   `STAGING_PASSWORD_A`, `STAGING_EMAIL_B`, `STAGING_PASSWORD_B` for two test
   accounts in one staging workspace (sign A up, invite B from A, once).
8. **First deploy:** Actions → *Deploy staging* → the branch to try. Or push
   to a branch named `staging`.

### Every deploy

`.github/workflows/deploy-staging.yml` runs the production deploy script
against the staging checkout (health check and rollback included), then
`scripts/staging_check.py`, which signs in as A and B and checks: a real model
reply, thread and draft privacy between the two, inviting a teammate, and the
screens the app opens on. It ends with the checks only a person can do (a call
ringing, a WhatsApp arriving, Gmail consent) as a list to tick.
