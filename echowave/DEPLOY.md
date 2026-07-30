# Going live

## Deploying to test it yourself first

Most of this document is about serving customers. If you are pushing to a box
purely to try the thing out, the list is much shorter — and several things that
look mandatory are not:

**You do not need**, for a private test: Razorpay merchant activation, live
payment keys, published policy pages, a filed LUT, real provider rate cards, or
backups. Test-mode Razorpay keys are enough to exercise the whole payment path,
and the balance gate can be switched off entirely.

**You do need**:

| | |
|---|---|
| `DATABASE_URL`, `REDIS_URL`, `PUBLIC_BASE_URL` | Nothing runs without them |
| TLS on your domain | The browser needs it for microphone access, and carriers will not post webhooks to plain HTTP |
| At least one working LLM/STT/TTS key | Either yours under **Provider keys**, or the customer's own under Models |
| `BALANCE_ENFORCEMENT_ENABLED=false` | **Otherwise your test account cannot make a single call.** Prepaid is on by default and a fresh account has zero credit. Turn it off for testing, or top yourself up with a staff credit adjustment from the admin dashboard |
| `MINIO_PUBLIC_BUCKET` left unset | Recordings are served by presigned URL |

### Putting it on an EC2 box, start to finish

Run this **on the server**, as root. Everything below is one path that works;
the traps it avoids are named underneath.

```bash
# 1. The code. Clone your own repo — not upstream's.
git clone --recurse-submodules -b <your-branch> \
    https://github.com/<you>/<your-repo>.git
cd <your-repo>/echowave

# 2. Setup: writes .env, a bootstrap certificate, the build override, and
#    brings the stack up. Both variables matter — see below.
sudo DEPLOY_MODE=build REPO_SOURCE=existing SERVER_IP=<your.elastic.ip> \
    ./scripts/setup_remote.sh

# 3. Your own configuration — none of it is prompted for. The template is
#    already filled in with the supplier identity and carries the three
#    CHANGE ME values (credential secret, Razorpay, grievance officer name).
cat deploy/decibyl.env.template | sudo tee -a .env >/dev/null
sudo nano .env          # replace every CHANGE ME

sudo ./remote_up.sh --build
```

**`REPO_SOURCE=existing` is not optional here.** The script decides whether to
build from the current directory by testing for `.git` beside
`docker-compose.yaml` — and in this repository `.git` sits one level up, at
`echowave-redesign/`, while the compose file is in `echowave/`. Left to guess it
would clone a *different* repository over the top of yours. Passing it
explicitly settles the question.

**`DEPLOY_MODE=build` is what makes it your code.** Without it the stack pulls
`${REGISTRY:-decibylai}/decibyl-api:latest` — a registry nobody here controls,
holding upstream's build with none of the billing, prepaid, GST or privacy work
in it. That failure is silent: containers start, the app loads, and nothing
looks wrong. In build mode `setup_remote.sh` writes a
`docker-compose.override.yaml` that builds both images from the checkout, and
Compose picks it up automatically from then on. (`docker-compose.build.yaml` in
the repo root does the same job for a manual `docker compose -f … -f …` run —
use one or the other, never both.)

**Step 3 is not optional either, and nothing warns you.** Compose reads `.env`
for interpolation; it does not put those values inside containers. Every
variable the compose file does not name explicitly used to be simply absent in
the API — which is why the api service now injects `.env` wholesale, with the
computed infrastructure values still winning. Anything you add to `.env` reaches
the app after a `remote_up.sh` re-run. Without `PLATFORM_CREDENTIAL_SECRET` in
particular, saving a provider key raises, so **there is no way to make a single
call**.

**TLS.** With a public IP and Docker present, `setup_remote.sh` issues a real
Let's Encrypt certificate for `<your-ip>.sslip.io` and serves the app there —
no DNS work, and a trusted certificate, which the browser requires before it
will hand over a microphone. Your own domain is a separate step:
`scripts/setup_custom_domain.sh` expects upstream's `decibyl/` subdirectory
layout and will not run against a repo checkout, so point the CNAME at the box
and issue the certificate with certbot yourself, then set `PUBLIC_HOST` and
`PUBLIC_BASE_URL` in `.env` and re-run `remote_up.sh`.

**Open the security group** for 80, 443, UDP+TCP 3478 and 5349, and UDP
49152–49200. Miss the UDP range and calls connect with no audio at all —
signaling succeeds, so it looks like a bug in the agent.

The first build is slow — a Next.js production bundle and a full Python
dependency tree. On a small EC2 instance give it a good twenty minutes and make
sure there is swap; the UI build is the memory-hungry one and an under-resourced
box kills it with an unhelpful error.

One container runs everything on the API side: `start_services_docker.sh` starts
uvicorn, the ARQ workers, the ARI manager and the campaign orchestrator
together. There is no separate worker to deploy — but it also means **if the
ARQ worker dies, calls silently stop being costed and invoices stop being
issued** while the API keeps answering.

**Check first, because it is new and unproven:** play back a recording. Object
storage moved from a public bucket to presigned URLs, and the signature covers
the hostname — if `MINIO_PUBLIC_ENDPOINT` does not match the host the browser
actually fetches from, every recording returns 403 while everything else looks
fine.

Then: sign up, grant yourself staff (below), make one real call, open the
recording, and look at Agent Runs to confirm it was costed.

---

The rest of this document is about serving customers. The order matters in two
places, and both are easy to get wrong once:

* **Create the admin account before disabling signup.** Nothing in the signup
  flow sets the staff flag, and with `ENABLE_SIGNUP=false` there is no way to
  create the first account at all. Do it in the order below and this is a
  non-event; do it backwards and you are editing the database by hand.
* **Set `RAZORPAY_WEBHOOK_SECRET` before taking a single payment.** Without it
  top-ups are refused outright — deliberately, because the alternative is
  charging a customer and crediting nobody.

---

## 1. Environment

Values containing spaces **must be quoted**, and so must anything containing
`<`, `>` or `&`. `set -a && source api/.env` is shell, so an unquoted
`SUPPLIER_LEGAL_NAME=Nautomation Labs Private Limited` silently sets the
variable to `Nautomation` and tries to run `Labs`.

### Required — the app misbehaves quietly without these

| Variable | Consequence if unset |
|---|---|
| `DATABASE_URL`, `REDIS_URL` | Nothing starts |
| `PUBLIC_BASE_URL` | Webhook and media URLs point at localhost |
| `PLATFORM_CREDENTIAL_SECRET` | Provider keys cannot be stored — saving raises. Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` | No top-up can be created |
| `RAZORPAY_WEBHOOK_SECRET` | **Top-ups refused.** Must match the value entered in the Razorpay dashboard exactly |
| `SUPPLIER_LEGAL_NAME`, `SUPPLIER_GSTIN` | No tax document is issued. Payments still credit, and the omission is logged as an error — a real payment is never rolled back over a missing variable |
| `SUPPLIER_ADDRESS`, `SUPPLIER_LUT_NUMBER` | Printed blank on documents |
| `SUPPLIER_HAS_LUT=true` | Every foreign customer's top-up is refused |

`SUPPLIER_STATE_CODE` defaults to the first two digits of the GSTIN and decides
CGST+SGST versus IGST for every domestic customer. It is the single most
consequential value here — get it wrong and every invoice carries the right
total split the wrong way, which is a filing correction rather than a bug.

### Required to keep things private

| Variable | Why |
|---|---|
| `MINIO_PUBLIC_BUCKET` | **Leave unset.** `true` grants the recordings bucket anonymous read, write *and* delete. Access is by presigned URL and needs no policy |
| `ENABLE_SIGNUP=false` | After creating your accounts, unless you want open registration |
| `CORS_ALLOWED_ORIGINS` | Only when `DEPLOYMENT_MODE != oss`. Behind one reverse proxy the UI and API are same-origin and CORS does not apply |

### Optional

`SENTRY_ORG` and `SENTRY_PROJECT` enable source-map upload so stack traces are
readable. Errors are reported either way.

---

## 2. Database

```bash
set -a && source api/.env && set +a
alembic -c api/alembic.ini upgrade head
alembic -c api/alembic.ini check     # must print "No new upgrade operations detected"
```

If `check` ever fails, **do not** run `--autogenerate` and apply what it
produces without reading it. It has proposed destructive column drops before.

---

## 3. The first admin

Signup must still be open for this step.

1. Sign up through the UI at `https://<your-host>/auth/signup`.
2. Grant staff access:
   ```bash
   set -a && source api/.env && set +a
   python -m scripts.grant_superuser you@yourdomain.com
   python -m scripts.grant_superuser --list      # confirm
   ```
3. Open `/superadmin`. It is deliberately absent from the sidebar — the whole
   area is gated at router level, so a customer who guesses the URL gets
   nothing.
4. Set `ENABLE_SIGNUP=false` and restart, if you do not want open registration.

`--revoke` takes staff access away again.

---

## 4. Razorpay

In the Razorpay dashboard, Settings → Webhooks:

| Field | Value |
|---|---|
| URL | `https://<your-host>/api/v1/billing/razorpay/webhook` |
| Secret | You choose it. Put the identical value in `RAZORPAY_WEBHOOK_SECRET` |
| Events | `payment.captured`, `payment.failed` |

The endpoint must be publicly reachable over HTTPS with a valid certificate —
Razorpay will not deliver to a self-signed one. Do not IP-allowlist it: Razorpay
publishes no fixed source range, and the signature is the authentication.

Verify with a test-mode payment before switching to live keys. A captured
payment should credit the account **net of GST** and issue a receipt voucher
numbered `RV/<FY>/000001`.

---

## 5. Prices

Everything is set in the admin dashboard under **Billing → Rate card** — the
platform rate, volume tiers, the USD→INR rate, and every provider rate. Nothing
is hardcoded and nothing needs a deploy to change.

Set provider rates before the first call. Usage with no rate on file is recorded
as **uncosted, not free**: the call still bills the platform fee, the provider
cost is reported as missing on the unit-economics screen, and margin is
overstated until you fill it in.

---

## 6. Before opening the doors

* Make one real end-to-end call. Everything in this repo is tested against a
  real database, but no test places an actual call through a carrier.
* Confirm `https://<your-host>/api/v1/health` responds and reports the auth
  provider you expect.
* Check that the MinIO endpoint is **not** reachable from the internet, or that
  you are on S3 (`ENABLE_AWS_S3=true`).
* Take a backup, and check you can restore it. The credit ledger is the only
  record of what every customer has paid.

---

## Updating a running box

Pull and rebuild in place:

```bash
cd <your-repo>/echowave
git pull --recurse-submodules
sudo ./remote_up.sh --build
```

**Do not use `scripts/update_remote.sh`.** It hardcodes `decibyl-hq/decibyl` and
fetches a compose file and tagged images from there — an upstream that is not
yours. Its whole purpose is upgrading an install that tracks upstream releases,
which this is not.

The same applies to `setup_local.sh` and the bootstrap `curl` at the top of each
setup script: they fall back to raw.githubusercontent.com if
`scripts/lib/setup_common.sh` is missing. Deploying from a full clone means it
never is, and the fallback never fires.

---

## What is still missing

Listed here rather than discovered later:

* **No PDF for tax documents.** They are issued, numbered and readable through
  the API; a customer cannot download one.
* **No e-invoicing (IRN via the IRP).** Mandatory above ₹5 crore aggregate
  turnover.
* **No credit notes.** A refund is issued from the Razorpay dashboard and
  reflected with a staff credit adjustment.
* **No low-balance email.** The Billing screen warns in amber and a refused run
  says what the fix is, but nobody is emailed.
* **Number provisioning is not built.** `is_platform_managed` is the flag the
  KYC gate keys on and nothing sets it yet, so the gate is correct but dormant.

See `KNOWN_ISSUES.md` for anything open, and `DASHBOARD.md` for how a call is
priced and what every billing number means.
