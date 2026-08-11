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

**Two directories, and mixing them up is the commonest slip.** `.git` is at the
repository root (`echowave-redesign/`); `docker-compose.yaml`, `remote_up.sh`
and `api/` are one level down in `echowave/`. So **git commands run in the outer
directory and deploy commands run in the inner one.** Pulling from inside
`echowave/` works, but `cd echowave` first and then reaching for `git pull` is
how you end up rebuilding the code you already had.

```bash
# 1. The code. This repository — not upstream's.
git clone --recurse-submodules -b <your-branch> \
    https://github.com/Stratfiy/echowave-redesign.git
cd echowave-redesign/echowave

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

# 4. The documentation, if you are serving it from this box. It is static
#    HTML built from the .mdx tree; nothing builds it for you, and an unbuilt
#    docs/dist means the docs hostname answers 404.
cd docs && npm ci && npm run build && npm run check-links && cd ..
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

`GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` enable the
**Google Calendar** tool's "Connect Google Calendar" flow — register an OAuth
2.0 Web application client in Google Cloud Console with redirect URI
`https://<your-host>/api/v1/integrations/google-calendar/callback`, and enable
the Google Calendar API on that project. Left unset, the tool category is
simply unavailable to create; nothing else depends on it.
`GOOGLE_CALENDAR_DEFAULT_TIMEZONE` (default `Asia/Kolkata`) is the single
timezone every event is created in.

### Managed phone numbers

Only if you are selling numbers rather than having customers bring their own
carrier account. Leaving these unset changes nothing for a bring-your-own
deployment.

| Variable | Consequence |
|---|---|
| `PLATFORM_PLIVO_AUTH_ID`, `PLATFORM_PLIVO_AUTH_TOKEN` | Decibyl's *own* Plivo account — compliance applications are filed and numbers bought under it, never a customer's. Unset, forwarding a KYC application raises rather than quietly falling back to "a human will handle it" |
| `PLATFORM_PLIVO_APPLICATION_ID` | The Plivo Application whose `answer_url` is the inbound dispatcher. Numbers are bought with this `app_id` set, so there is no console step and no window where a number is rented but answers nowhere |
| `NUMBER_RENTAL_COST_PAISE` | What the carrier charges us, per number per month. Default 25000 (₹250) — an estimate, see the bottom of this file |
| `NUMBER_RENTAL_PRICE_PAISE` | What the customer pays. Default 39900 (₹399). Stored alongside the cost so margin figures stop ignoring rental |
| `MANAGED_TELEPHONY_ENABLED=true` | Opens telephony verification to customers. Leave false until the Plivo reseller arrangement is approved — it gates document upload, and collecting identity records you cannot forward takes on DPDP custody for nothing |

### Hostnames

Set these to split one hostname into four. Leaving `DECIBYL_APP_HOST` unset
keeps the single-host config, which is right for a self-hosted install on one
name.

| Variable | Serves |
|---|---|
| `DECIBYL_APP_HOST` | The product. **Setting this is what switches the deployment to subdomain mode** — `decibyl-init` renders the config on the next `up` |
| `DECIBYL_API_HOST` | API, WebSockets, MCP, the embed widget. The hostname customers integrate against, so it should not move later |
| `DECIBYL_DOCS_HOST` | Static documentation, served off disk |
| `DECIBYL_ROOT_HOST` | The apex. Optional — derived from the app host. Served **empty**: a 404 with a pointer to the app until a built site lands in `./landing` |

<!-- markdownlint-disable-next-line -->
> nginx serves the *first* block on a port to any hostname it does not
> recognise. Before this existed that role fell to the app, so every name
> pointed at the box — the apex, the docs subdomain, anything a stranger points
> at your IP — served the dashboard and redirected to a login page. There is now
> an explicit catch-all that closes the connection.

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
2. Grant staff access — on a Docker install, inside the api container:
   ```bash
   docker compose exec api python -m scripts.grant_superuser you@yourdomain.com
   docker compose exec api python -m scripts.grant_superuser --list   # confirm
   ```
   From a repository checkout with a venv instead:
   ```bash
   set -a && source api/.env && set +a
   python -m scripts.grant_superuser you@yourdomain.com
   ```
   If the container says `No module named scripts.grant_superuser`, the image
   predates the fix that ships it — grant the flag directly and rebuild later:
   ```bash
   docker compose exec postgres psql -U postgres \
     -c "UPDATE users SET is_superuser = true WHERE email = 'you@yourdomain.com';"
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
* If you split hostnames, check each one answers as itself. All four resolving
  to the same box is normal; all four *serving the same thing* means nginx
  never matched a `server_name` and fell through to the app:

  ```bash
  curl -s  https://api.<domain>/api/v1/health | jq -r .status   # ok
  curl -sI https://app.<domain>/            | head -1           # 307 to login
  curl -sI https://docs.<domain>/getting-started | head -1      # 200
  curl -s  https://<domain>/ | jq -r .detail                    # the pointer
  ```

  A `307` to `/auth/login` from the docs or apex host is the tell.
* Backups run nightly on their own (`BACKUP_ENABLED`, on by default). Check one
  has appeared, then rehearse the restore once —
  `./scripts/rehearse_restore.sh <backup-file>` builds a scratch database,
  restores into it and drops it again. The credit ledger is the only record of
  what every customer has paid, and an untested backup is a hypothesis.

---

## Updating a running box

Pull and rebuild in place:

```bash
# git in the OUTER directory, where .git is
cd echowave-redesign
git pull --recurse-submodules

# deploy from the INNER one, where the compose file is
cd echowave
sudo ./remote_up.sh --build

# Docs are a build artifact, not a container. A pull that changed .mdx files
# changes nothing on the docs host until this runs.
cd docs && npm ci && npm run build && cd ..
```

### Migrations do not run themselves on this path

Only the Helm chart runs `alembic upgrade head` as a hook. On the Docker/EC2
path above **nothing migrates the database for you** — the containers come up
against whatever schema is already there, and the failures that produces are
confusing rather than loud. Run it yourself, from the host, after the pull:

```bash
set -a && source api/.env && set +a
alembic -c api/alembic.ini upgrade head
alembic -c api/alembic.ini check     # must print "No new upgrade operations detected"
```

### Coming from a box that predates managed numbers

Four migrations land in one go, and they are additive — new tables and nullable
columns, no data rewritten, no column dropped:

| Revision | What it adds |
|---|---|
| `c7a1f4e93b28` | managed numbers, recurring charges, rental periods |
| `d3f5a81c62b7` | `call_cost_items.provider_cost_paise`, backfilled from `cost_paise` |
| `e5b27c0a91d4` | `payment_mandates`, plus the autopay columns on charges and periods |
| `f18a4d3c07e9` | `notifications`, the dedupe record behind the low-balance email |

The backfill in `d3f5a81c62b7` sets provider cost equal to what was charged for
every existing line, which is correct for history: everything billed before the
markup existed *was* billed at cost. Margin figures for those calls will
therefore read zero, and that is the truth rather than a gap.

Two settings change behaviour the moment this is deployed, so decide both
before you run it rather than after:

* **`REQUIRE_MANDATE_FOR_NUMBERS` defaults to `true`.** Every number purchase is
  refused with a 403 until Razorpay Subscriptions is activated and the customer
  has authorised a mandate. Set it `false` to keep the old prepaid-balance
  behaviour while you wait for that approval.
* **`MANAGED_PROVIDER_MARKUP_BPS` defaults to `13000`** — a 1.3x markup on STT,
  LLM and TTS bought with our keys, applied to calls from the moment it is
  deployed. `10000` charges at cost, exactly as before.

The UI gained pages (`/analytics`, `/numbers`), so it needs the rebuild that
`remote_up.sh --build` does. The API hostname is resolved at runtime rather than
baked in, so no rebuild is needed for a hostname change.

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

* **No generated PDF for tax documents.** They are issued, numbered, and
  readable as a printable page at `/billing` — a browser saves that as a PDF.
  What is missing is a PDF *byte stream*, which is what emailing one as an
  attachment would need.
* **No e-invoicing (IRN via the IRP).** Mandatory above ₹5 crore aggregate
  turnover.
* **No credit notes.** A refund is issued from the Razorpay dashboard and
  reflected with a staff credit adjustment. A credit note adjusts an invoice
  already filed, so it needs its own serial series and a decision about the GST
  already declared — not something to improvise under time pressure.
* **Low-balance email needs SMTP configured to do anything.** The job runs
  daily at 09:00 IST and logs one line saying it is off when `SMTP_HOST` is
  unset. Without it the dunning schedule suspends numbers in silence, which is
  the failure the email exists to prevent.
* **Autopay needs Razorpay Subscriptions activated.** That is their approval,
  not our configuration, and it can take days. **`REQUIRE_MANDATE_FOR_NUMBERS`
  defaults to `true`, so until Subscriptions is live every number purchase is
  refused with a 403.** Set it false to fall back to the prepaid balance and
  the dunning schedule while you wait.
* **Managed numbers have never run against Plivo's live API.** The endpoint
  shapes match the published Compliance API and the encoding is unit-tested,
  but no compliance application has been filed and no number bought for real.
  Budget for the first one going wrong.
* **`NUMBER_RENTAL_COST_PAISE` is an estimate, not a quote.** It defaults to
  ₹250/month from the launch plan. Confirm it against Plivo's live India price
  list before quoting anyone a margin — it is the input every rental margin
  figure rests on.

See `KNOWN_ISSUES.md` for anything open, and `DASHBOARD.md` for how a call is
priced and what every billing number means.
