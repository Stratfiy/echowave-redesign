# Go-live runbook — configure, test, take the first rupee

Written from a working checkout with the suite actually running, not from the
docs. Every command below was run; every claim about what a check does was read
out of the code that does it.

The order matters. Each phase assumes the one above it, and phases 2 and 3 are
where money starts moving — nothing there is reversible by editing a file.

---

## Phase 0 — Get the code running and the tests green

A fresh clone does **not** run the suite. Three things are missing and each
fails in a way that does not name itself.

```bash
# 1. The pipecat submodule. Without it, setup_requirements.sh fails with
#    "does not appear to be a Python project".
git submodule update --init --recursive

# 2. Python 3.12 or 3.13. The setup script refuses anything else, but only
#    after creating the venv, so the error looks like it came too late.
uv python install 3.13
cd echowave && uv venv --python 3.13 .venv
source .venv/bin/activate
./scripts/setup_requirements.sh --dev

# 3. Postgres needs pgvector -- one migration runs CREATE EXTENSION vector,
#    and plain postgres does not ship it.
apt-get install -y postgresql-16-pgvector   # or use pgvector/pgvector:pg17
service postgresql start
redis-server --daemonize yes --port 6379

# ts_validator shells out to node
cd api/mcp_server/ts_validator && npm install && cd -
```

Then, from `echowave/api`:

```bash
export DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/test_db"
export REDIS_URL="redis://localhost:6379/0"
export ENABLE_AWS_S3=false
export MINIO_PUBLIC_ENDPOINT=http://localhost:9000
export DEPLOYMENT_MODE=oss ENVIRONMENT=test LOG_LEVEL=WARNING
export PYTHONPATH=/path/to/echowave

pytest tests/ -q          # expect 4000+ passed, 10 skipped, 0 failed (~3.5 min)
```

UI, from `echowave/ui`:

```bash
npm ci
npx tsc --noEmit          # expect no output
npx vitest run            # expect all passed
npx next lint             # expect "No ESLint warnings or errors"
npx next build            # expect exit 0
```

**One thing to know about this suite.** `conftest.py` builds the test session
with `expire_on_commit=False`; production's `async_sessionmaker`
(`db/base_client.py`) takes the default, `True`. So any route that reads an ORM
row *after* committing passes every test and 500s in production. Six such
routes were found and fixed this way — see `tests/test_routes_survive_their_own_commit.py`,
which deliberately refuses the `db_session` fixture for exactly this reason.
**When you add a route, write at least one test that lets it open its own real
session.**

---

## Phase 1 — Configure the deployment (before any customer sees a price)

Set these in `.env` and **`docker compose up -d --force-recreate`** — plain
`restart` does not re-read `.env`.

### 1.1 Supplier identity — do this first

```bash
SUPPLIER_LEGAL_NAME="Your Company Private Limited"   # quote anything with a space
SUPPLIER_GSTIN=29ABCDE1234F1Z5
SUPPLIER_STATE_CODE=29          # defaults to the GSTIN's first two digits
SUPPLIER_ADDRESS="Full registered address"
SUPPLIER_PAN=ABCDE1234F
```

Without `SUPPLIER_LEGAL_NAME` **and** `SUPPLIER_GSTIN`, money is captured,
credit is issued, and **no receipt voucher is ever produced** — silently, with
one log line. Under GST an advance is taxable on receipt, so every such payment
is an accumulating liability. `SUPPLIER_STATE_CODE` is what decides CGST+SGST
versus IGST; absent, every supply is misclassified.

### 1.2 Razorpay

```bash
RAZORPAY_KEY_ID=rzp_live_...        # test keys work perfectly and take no money
RAZORPAY_KEY_SECRET=...
RAZORPAY_WEBHOOK_SECRET=...         # no secret => the route refuses every webhook
RAZORPAY_STARTER_PLAN_ID=plan_...   # see 1.3 -- the amount is the gross
PUBLIC_BASE_URL=https://your.domain # the webhook has to be reachable here
```

`topups_enabled` goes false when **either** the keys or the webhook secret are
missing, because checkout without a webhook charges the customer and credits
nobody. That is the honest failure and it is deliberate.

Webhook path: `POST /api/v1/billing/razorpay/webhook`, signed with
`X-Razorpay-Signature`.

### 1.3 Create the Razorpay plan at the **gross**, not the list price

| | |
|---|---:|
| Call balance | ₹2,500.00 |
| One number | ₹499.00 |
| **Net** | **₹2,999.00** |
| GST @ 18% | ₹539.82 |
| **Create the plan at** | **₹3,538.82** = `353882` paise |

Once pinned, the amount on that plan is what the bank collects and no code can
change it. A plan created at ₹2,999 collects **zero GST**, monthly, by standing
instruction. A guard now reads the pinned plan back and refuses to subscribe on
a mismatch — it stops the damage, it does not create the plan for you.

**Export/LUT accounts** are zero-rated and owe the net ₹2,999. A pinned plan
charges everyone the same, so they need their **own** Razorpay plan at the net
price, with its id on the plan row's export field. Until that exists, keep them
on prepaid.

### 1.4 Exchange rate — the silent 7–8%

`DEFAULT_USD_INR_PAISE = 9600` (₹96). Every rate in the price book is quoted in
USD and settled in rupees, so an empty `usd_inr_rate_history` settles **every
charge on the platform 7–8% light** against a real ~₹104 — and quietly shrinks
the $5 signup bonus from ~₹520 to ~₹480.

The daily cron (`refresh_exchange_rate`, 02:30 UTC) has a real keyless feed
behind it, but a fetch that never succeeds writes nothing and logs a warning.

- Go to **/superadmin/billing/rate-card → Exchange rate**.
- If no rate is on file the panel now says so in amber and names the fallback.
- Press **Fetch now**. A rate lands, or a 502 names the upstream problem.
- Put the age of the newest rate on someone's morning check.

### 1.5 Price book and carriers

```bash
docker compose exec api python -m scripts.seed_provider_rates --confirm
```

Only **Plivo** is on the managed sell path — `MANAGED_CARRIER_ALLOWLIST =
frozenset({"plivo"})`, and `MANAGED_TELEPHONY_ENABLED` defaults false. So verify
Plivo's India rate and nothing else. Telnyx, Vonage, Cloudonix and Vobiz carry
US or placeholder rates; they cannot reach a customer's bill today, so fix them
**before you widen the allowlist**, not before launch.

### 1.6 Feature flags — leave these off until phase 3

```bash
BYOK_TIERED_FEE_ENABLED=false
ADDON_BILLING_ENABLED=false
MANAGED_TELEPHONY_ENABLED=false
```

---

## Phase 2 — Work the readiness screen to zero

**/superadmin/billing/readiness** is the go/no-go. It is not a summary of the
settings above — it splits *configuration* ("is the setting there") from
*evidence* ("did the thing actually happen"), and the evidence checks are the
ones that matter:

| Check | Reads zero when healthy |
|---|---|
| Supplier identity configured | — |
| Razorpay webhook secret set | — |
| Razorpay keys are **live** keys | checks the `rzp_live_` prefix specifically |
| Razorpay can reach the webhook | probes `PUBLIC_BASE_URL` + the webhook path |
| **Captured payments with no receipt voucher** | **any value but 0 is an incident** |
| Provider rates on file | — |
| Costed calls have a rate for every component | uncosted calls are reported, never billed at zero |
| Every carrier has a verified rate | — |
| Managed carriage only on enabled carriers | — |
| Background worker running | — |
| A real payment carried end to end | needs a human — see phase 3 |

Do not launch with any check at ACTION REQUIRED.

---

## Phase 3 — The first rupee

1. Sign up a fresh account. Confirm the **$5 bonus** lands: `/billing` should
   show ~₹480–520 as a `trial` ledger row. It is granted once per organization,
   enforced by a partial unique index, on both the local and Stack Auth signup
   paths.
2. Make one real call on that bonus. Confirm a receipt on `/usage` and that the
   line items sum to the total charged.
3. Top up for the **minimum ₹100**. Complete the real Razorpay checkout.
4. Confirm the balance moves **only after the webhook lands** — the browser
   callback deliberately credits nothing.
5. Confirm a numbered **receipt voucher** exists at `/billing` → Documents, and
   that the readiness screen's "captured payments with no voucher" still reads
   **0**.
6. Only now: subscribe one account to the plan, confirm the bank is asked for
   **₹3,538.82** and not ₹2,999.
7. Then turn on `BYOK_TIERED_FEE_ENABLED`, then `ADDON_BILLING_ENABLED`. Both
   change quoted prices, so do them one at a time.

---

## What the billing model actually is today

Worth stating plainly, because it decides what can leak:

- **Telephony on the customer's own carrier is not billed per minute.** Usage is
  recorded only when the key source is not `byok`, and *missing reads as byok* —
  the deliberate opposite default from the model components, because an
  unrecorded carrier means we cannot show we bought the minutes.
- **You charge:** platform fee (uplifted on BYOK model keys) + add-on fees for
  features the call used + provider costs × markup, and monthly rental on
  numbers bought through the platform.
- **Usage with no rate on file is `uncosted`, not free.** It is reported on the
  unit-economics screen rather than billed at zero.
- **`total_charged_paise` is exactly the sum of the line items**, never a
  separately rounded figure, so an invoice always reconciles against itself.
- **Number rentals** are billed at most once per period — a unique constraint on
  `(charge_id, period_start)` plus a partial unique index on the ledger — so the
  cron is safe to run twice, late, or over a month it already did. The first
  period prorates by whole days from purchase.

---

## The one number still unmeasured

No provider invoice has been reconciled against `provider_cost_paise`. Every
cost figure — and therefore every margin number on every screen — is a published
list price that was read, not an invoice that was checked. On BYO-telephony
accounts the AI components are essentially all of COGS, so this is the number to
close before quoting anyone a large contract.

## Still blocked on third parties

Razorpay Subscriptions approval, Plivo KYC, DLT registration for SMS
verification.
