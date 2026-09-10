# Staging

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
