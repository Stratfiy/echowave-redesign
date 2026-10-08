# Going live

> Routine deploys, rollback, environment parity and day-to-day operations
> are in **`OPS-RUNBOOK.md`**, which is reconciled against the workflows.
> This page is the first-box and configuration reference.

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
| *(nothing — leave `BALANCE_ENFORCEMENT_ENABLED` alone)* | Prepaid is on by default and a fresh account has zero credit, so your own test account cannot place a call until it has some. **Give it credit, do not remove the ceiling:** a staff credit adjustment from the admin dashboard costs nothing and leaves the gate standing. Setting this to `false` does not just unblock you — it unblocks everyone, on every account, permanently. `reserve()` returns immediately, `_hold_funds` reads that as success, and calls run to completion against accounts with nothing in them. We pay the providers in cash and hold a receivable nobody can collect |
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

**`DEPLOY_MODE=build` builds from this checkout; without it the stack pulls.**
It now pulls `${REGISTRY:-ghcr.io/stratfiy}/decibyl-api:latest`, which is where
this repository's CI publishes, so pulling is a real option rather than a trap.
The default used to be `decibylai` — a Docker Hub namespace nobody here
controls, holding upstream's build with none of the billing, prepaid, GST or
privacy work in it, and the failure was silent: containers started, the app
loaded, and nothing looked wrong. Build mode is still what you want when the
code on the box is meant to be the code in front of you. In build mode `setup_remote.sh` writes a
`docker-compose.override.yaml` that builds both images from the checkout, and
Compose picks it up automatically from then on. (`docker-compose.build.yaml` in
the repo root does the same job for a manual `docker compose -f … -f …` run —
use one or the other, never both.)

**The box needs to be able to pull at all.** Two credentials, neither of them
Decibyl's:

* **Docker Hub.** `postgres`, `redis`, `nginx`, `coturn` and the rest come
  from Docker Hub, and an unauthenticated daemon shares one small pull quota
  with every other machine on its IP. When that runs out the error names the
  image and not the cause — `pull access denied for redis, repository does not
  exist or may require 'docker login'` on an image that is public. `docker
  login` with any free account on the box fixes it. MinIO is the exception:
  `minio/minio` really is gone from Docker Hub, so compose builds it from
  `deploy/minio/Dockerfile` (Chainguard's server plus curl for the health
  check) on first `up`.
* **GHCR.** `decibyl-api`, `decibyl-ui` and `decibyl-sandbox` are published to
  `ghcr.io/stratfiy`. If those packages are private the box needs a read token:
  `echo "$TOKEN" | docker login ghcr.io -u <user> --password-stdin`. Making the
  packages public removes the step.

Check both with `docker compose pull` before a deploy depends on them. It is
the only check that means anything, and it fails with the registry's own words.

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
| `PLATFORM_PLIVO_LOW_BALANCE` | Low-water mark for that account's credits, in its own units. Unset, the balance screen reports the figure and flags it only at zero — which on the account that pays for every call is a warning arriving after the outage |
| `PLATFORM_PLIVO_APPLICATION_ID` | The Plivo Application whose `answer_url` is the inbound dispatcher. Numbers are bought with this `app_id` set, so there is no console step and no window where a number is rented but answers nowhere |
| `NUMBER_RENTAL_COST_PAISE` | What the carrier charges us, per number per month. Default 25000 (₹250) — an estimate, see the bottom of this file |
| `NUMBER_RENTAL_PRICE_PAISE` | What the customer pays for a number *beyond* whatever the account's plan includes. Default 55900 (₹559 net, ₹659.82 with GST). Stored alongside the cost so margin figures stop ignoring rental |
| `MANAGED_TELEPHONY_ENABLED=true` | Opens telephony verification to customers. Leave false until the Plivo reseller arrangement is approved — it gates document upload, and collecting identity records you cannot forward takes on DPDP custody for nothing |

### Hostnames

Set these to split one hostname into four. Setting **any one of them** switches
the deployment to subdomain mode; the rest are derived from whichever you
named, so in practice one line is enough:

```bash
DECIBYL_APP_HOST=app.decibyl.ai   # api., docs. and the apex follow from this
```

Leaving all four unset keeps the single-host config, which is right for a
self-hosted install on one name.

| Variable | Serves |
|---|---|
| `DECIBYL_APP_HOST` | The product |
| `DECIBYL_API_HOST` | API, WebSockets, MCP, the embed widget. The hostname customers integrate against, so it should not move later |
| `DECIBYL_DOCS_HOST` | Static documentation, served off disk |
| `DECIBYL_ROOT_HOST` | The apex. Served **empty**: a 404 with a pointer to the app until a built site lands in `./landing` |

**If the documentation site is showing a login page, this is why.** With none
of these set, every hostname lands on the single default server block, which
proxies to the app, whose middleware redirects anything unauthenticated to
`/auth/login`. The docs build, the DNS record and the certificate can all be
correct and you still get a login form. One `DECIBYL_APP_HOST` line and a
`remote_up.sh` fixes it; `decibyl-init` now also warns when it finds a built
`docs/dist` and no host set.

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
     -c "UPDATE users SET staff_role = 'superadmin' WHERE email = 'you@yourdomain.com';"
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

## 7. Claude and other models through AWS (optional)

Everything here is off until configured, and nothing in it needs an AWS key
in `.env`: on EC2 the instance role signs every request (the standard AWS
credential chain). Staff see each part's state, and the exact step still
missing, at `GET /api/v1/superuser/aws-gateway`; a workspace sees "Needs setup"
on Settings → Models for a choice that is listed but not ready. Code:
`api/services/aws_gateway/`.

### Where Claude runs: `CLAUDE_BACKEND`

| Value | What it means | Also set |
| --- | --- | --- |
| `anthropic` (default) | Today: Anthropic's API on the platform key | — |
| `aws_platform` | Claude Platform on AWS: Anthropic-operated, full API parity, IAM and AWS billing. Recommended | `CLAUDE_AWS_REGION` (or `AWS_REGION`), `ANTHROPIC_AWS_WORKSPACE_ID` |
| `bedrock` | Amazon Bedrock: a feature subset (no server-side web search or fetch, batches, Files API or MCP connector; our own web tools still run) | `BEDROCK_REGION` (or `AWS_REGION`), `BEDROCK_CLAUDE_MODEL_IDS`, `BEDROCK_ENABLED_MODELS` |

It moves the platform's own Claude everywhere it is used: Decibyl chat, the
builder, Auto's routing and the call pipeline's managed brain (on Bedrock the
call pipeline uses its existing Bedrock provider). A workspace that brought
its own Anthropic key keeps going to Anthropic with it.

`BEDROCK_CLAUDE_MODEL_IDS` maps each Claude model the tiers name to its Bedrock
id, e.g. `claude-haiku-4-5=anthropic.claude-haiku-4-5-20251001-v1:0,claude-opus-5-5=anthropic.claude-opus-5-5`.
Every tier model needs an entry (Haiku, Sonnet, Opus and the builder's model).

**If the backend is chosen but not ready** (missing setting, model access not
granted), Claude stays on Anthropic's API, the API log says why once, and the
staff view shows "needs setup". It never fails a turn for that reason.

### Bedrock model access: `BEDROCK_ENABLED_MODELS`

Listing a region's models is not permission to use them. The read-only check
on 7 Oct 2026 found ap-south-1 lists Anthropic's models while access is
`NOT_AUTHORIZED` (agreement not accepted). So a Bedrock model is "needs setup"
until it is in `BEDROCK_ENABLED_MODELS` (comma-separated Bedrock ids), and goes
back to "needs setup" for ten minutes whenever AWS refuses it at runtime.

What the founder enables in AWS, per model, in the Bedrock console → Model
access (in the region set above): request access, accept the model's EULA or
agreement (Anthropic's use-case form for Claude), wait for "Access granted",
then add the id to `BEDROCK_ENABLED_MODELS` and restart.

### The gateway beyond Claude (each behind its own flag)

| Flag | What it does | Settings |
| --- | --- | --- |
| `AWS_FALLBACK_BRAIN_ENABLED` | When the platform's Claude errors or times out (after the usual vendor fallbacks), a Bedrock model answers the turn. The reply ends with "(Claude was unavailable just now, so a backup model answered this one.)"; usage is recorded as `aws_bedrock` under feature `<feature>:fallback` | `BEDROCK_FALLBACK_MODEL` (e.g. Amazon Nova Pro or an open-weight model), `BEDROCK_FALLBACK_TIMEOUT_SECONDS` (60) |
| `AWS_CHEAP_TIER_ENABLED` | A small model sorts work for Auto in Laya's place; the rules still decide when it abstains. It sits under the same ops guardrails as Laya: the hard deadline and circuit breaker (`laya_guardrails`), and `laya_rollback` silences it | `BEDROCK_CHEAP_MODEL` (Nova Micro or Lite), `BEDROCK_CHEAP_TIMEOUT_MS` (1500) |
| `AWS_EMBEDDINGS_ENABLED` | "Multilingual (AWS)" knowledge search on Settings → Models. The vector column holds 1536 numbers, so only a model that returns 1536 is offered (Cohere Embed v4); documents are re-read for the new model | `BEDROCK_EMBEDDING_MODEL`, `BEDROCK_EMBEDDING_DIMENSIONS` (1536) |
| `AWS_NOVA_SONIC_ENABLED` | Nova Sonic speech-to-speech as the `nova` tier, for Hindi and Indian English only (any other language runs on the natural tier). Sarvam stays the default for Indian-language voice | `NOVA_SONIC_MODEL`, `NOVA_SONIC_REGION`, `NOVA_SONIC_VOICE` |

Nova Sonic is served from a few regions only, none of them in India today
(pipecat lists us-east-1, us-west-2 and ap-northeast-1 for Nova 2 Sonic), so
call audio on that tier is processed outside India. Decide that before
switching it on.

### The instance role's policy

Least privilege: only the actions and model ARNs in use. Replace the region,
account and ids with yours; keep only the statements for what you enable.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ClaudePlatformOnAWS",
      "Effect": "Allow",
      "Action": [
        "aws-external-anthropic:CreateInference",
        "aws-external-anthropic:CountTokens",
        "aws-external-anthropic:GetModel",
        "aws-external-anthropic:ListModels",
        "aws-external-anthropic:GetWorkspace"
      ],
      "Resource": "arn:aws:aws-external-anthropic:ap-south-1:<account-id>:workspace/<wrkspc_id>"
    },
    {
      "Sid": "BedrockModels",
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": [
        "arn:aws:bedrock:ap-south-1::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
        "arn:aws:bedrock:ap-south-1::foundation-model/anthropic.claude-opus-5-5",
        "arn:aws:bedrock:ap-south-1::foundation-model/<fallback-model-id>",
        "arn:aws:bedrock:ap-south-1::foundation-model/<cheap-model-id>",
        "arn:aws:bedrock:ap-south-1::foundation-model/<embedding-model-id>"
      ]
    },
    {
      "Sid": "NovaSonic",
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModelWithBidirectionalStream"],
      "Resource": "arn:aws:bedrock:<nova-sonic-region>::foundation-model/<nova-sonic-model-id>"
    }
  ]
}
```

Notes:

* `bedrock:InvokeModel` covers Converse and `InvokeModelWithResponseStream`
  covers ConverseStream; both are needed for streaming chat.
* A model reached through a cross-region inference profile (`apac.`,
  `global.` ids) also needs the profile's ARN
  (`arn:aws:bedrock:<region>:<account-id>:inference-profile/<id>`) and the
  foundation-model ARN in every destination region. Its id also needs its own
  row on the rate card (Super admin → Billing → Rate card), or its usage shows
  as unpriced.
* The AWS-managed `AnthropicInferenceAccess` policy also works for Claude
  Platform on AWS but grants read access to every resource in the workspace;
  the statement above is narrower.
* Check the Nova Sonic action name against the Bedrock IAM reference when you
  add it; it is the one statement here not exercised by the read-only check.

The fallback brain's spend, like every direct model call, counts toward the
ops cost stop (`cost_stop`), which now reads `model_usage` priced on the rate
card beside call receipts.

### Prices

Rows for `anthropic_aws` (Claude Platform on AWS) and `aws_bedrock` (Bedrock:
Claude, Nova, Nova 2 Sonic and Cohere Embed v4) are in
`api/services/billing/default_rates.py`, all marked provisional until an AWS
invoice confirms them. Run `scripts.seed_provider_rates` after deploying.

### Rollback

Set `CLAUDE_BACKEND=anthropic` (or remove it) and switch the four
`AWS_*_ENABLED` flags off (environment or Super admin → Flags), then restart.
Claude goes back to Anthropic's API on the platform key. A workspace that
chose "Multilingual (AWS)" runs on standard knowledge search from its next
request (documents embedded by the Bedrock model show as needing to be read
again), and a bundle on the `nova` tier runs on the natural tier. Nothing
stored has to be edited.

## Updating a running box

**Routinely, you do not do this by hand.** A merge to `main` runs
`.github/workflows/deploy.yml`, which hands `scripts/ci_deploy.sh` to the box
over SSM: fetch, build, up, migrate, seed rates, build docs, health check,
roll back on failure (`OPS-RUNBOOK.md` section 4). The manual path below is for
a box that is not wired to the workflow yet, and for break-glass.

Pull and rebuild in place:

```bash
# git in the OUTER directory, where .git is
cd echowave-redesign
git pull --recurse-submodules

# deploy from the INNER one, where the compose file is
cd echowave
sudo ./remote_up.sh --build

# Docs are a build artifact, not a container. ci_deploy.sh builds them on
# every workflow deploy; on this manual path, this is what builds them.
cd docs && npm ci && npm run build && cd ..
```

### Migrations run at container start, and again in the deploy

`scripts/start_services_docker.sh` runs `alembic upgrade head` when the api
container starts (roles `all` and `control`; a `media` node skips it), and
`ci_deploy.sh` runs it once more after `up`. So both the workflow and
`remote_up.sh` migrate. Check the result rather than assuming it, from the
host or with the `migrate-status` action of `.github/workflows/ops.yml`:

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
* **`MANAGED_PROVIDER_MARKUP_BPS` defaults to `17000`** — a 1.7x markup on STT,
  LLM and TTS bought with our keys, applied to calls from the moment it is
  deployed. `10000` charges at cost, exactly as before. This fallback is meant
  to track the live value in `managed_markup_history`; when the markup is
  raised through the OTP flow, raise this to match.

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
