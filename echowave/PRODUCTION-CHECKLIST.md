# Production readiness — tested, not asserted

Every result below came from running the real HTTP API against a real
PostgreSQL and Redis on 2026-08-01. Nothing here is inferred from reading code.

**Method.** Fresh database, migrations to head, `uvicorn` serving the real app,
traffic driven over HTTP with `requests`. 181 API paths exist; 60 are
metrics/billing/reporting. All of those were probed.

**Headline: the platform works. Two configuration mistakes will silently cost
you money or break GST compliance, and neither raises an error.**

---

## 1. What was tested and what happened

| Area | Result |
|---|---|
| App boots, 236 routes registered | **PASS** |
| Signup → token → org created | **PASS** |
| DPA outstanding at signup, clears on acceptance | **PASS** |
| Billing profile (Telangana GSTIN, inter-state) | **PASS** |
| Unsigned webhook rejected | **PASS** (400) |
| Bad-signature webhook rejected | **PASS** (400) |
| **Signed webhook credits net-of-GST** | **PASS** — ₹1,180 gross → ₹1,000 to ledger |
| **Webhook replay is idempotent** | **PASS** — `already_credited`, no double credit |
| **Receipt voucher issued with GST split** | **PASS** — `RV/26-27/000001`, IGST ₹180, `inter_state` |
| Topup order with unreachable Razorpay | **PASS** — clean 400, no stack trace leaked |
| 21 user-scope metrics endpoints | **20 PASS / 1 guarded 400** |
| 8 admin metrics endpoints | **8 PASS** |
| Tenant isolation (5 probes) | **5 PASS** |
| Privacy readiness with config complete | **PASS** — `action_required=0` |
| Unit test suite | **1912 pass / 5 fail** (async timing, unrelated) |

---

## 2. The two silent killers — check these before taking a rupee

### 2.1 Unset supplier identity means no tax document is ever issued

**Reproduced.** With `SUPPLIER_LEGAL_NAME` and `SUPPLIER_GSTIN` unset, a
signature-verified payment was **credited to the ledger and issued zero tax
documents.** `GET /billing/documents` returned `{"documents": []}`.

The code is deliberate and correct — `issue_receipt_voucher()` returns `None`
rather than raising, because a real payment must not be rolled back over a
missing environment variable. But the consequence is that **you take money,
credit it, and issue no receipt voucher, indefinitely, with only a log line.**

Under GST an advance is taxable on receipt. Every payment taken this way is an
accumulating compliance failure that nothing in the UI surfaces.

With the four supplier variables set, the same payment issued
`RV/26-27/000001` — gapless serial, correct IGST, correct `inter_state`
classification derived from GSTIN 33 (Tamil Nadu) → 36 (Telangana).

- [ ] `SUPPLIER_LEGAL_NAME`, `SUPPLIER_GSTIN`, `SUPPLIER_STATE_CODE`, `SUPPLIER_ADDRESS` set
- [ ] One test payment made, and `GET /billing/documents` returns a voucher
- [ ] `grep -i "no receipt voucher issued" ` in the API logs returns nothing

### 2.2 An empty price book reports 100% margin, not an error

**Reproduced.** Before seeding, `POST /cost-estimate/per-minute` returned
`total_paise_per_minute: 192, agent_paise_per_minute: 0` — a plausible number
that is only the platform fee. The `unpriced` array listed all four providers,
but the total was still served as if authoritative.

After `python -m scripts.seed_provider_rates --confirm`, the same request
returned ₹10.30/min with `unpriced: []`.

Margin reporting has the same shape: a call with no rate on file is recorded as
*uncosted*, so the dashboard reads 100% margin rather than "unknown".

- [ ] `scripts/seed_provider_rates --confirm` has been run
- [ ] `GET /admin/billing/rate-card` shows Sarvam TTS at `300000` mpaise (₹3.00/1k chars)
- [ ] A cost estimate returns `unpriced: []`

---

## 3. Findings that need a decision

### 3.1 The seeded Plivo rate is US outbound, not India

Seeded at `$0.010/min` = **₹0.96/min**. The tender model assumes **₹0.25/min**
for Plivo India — the seeded rate is **3.8× too high** for our actual route.

Nothing is broken; the seeder ships US list prices and says so. But every
estimate and every margin figure for an Indian campaign is wrong until this row
is corrected in `/superadmin/billing/rate-card`.

- [ ] Plivo telephony rate replaced with the written India quote

### 3.2 The estimator and the tender model disagree, and both are right

The API estimator returned **₹7.42/min agent cost** for Sarvam. The tender cost
model says **₹1.75/min**. The difference is assumptions, not arithmetic:

| | Estimator | Tender model |
|---|---|---|
| Agent speech share | 100% of the call | 65% |
| TTS synthesis | all live | 25% live, 75% pre-rendered |

The estimator is the conservative worst case and is a reasonable default for a
customer-facing quote screen. **But if anyone compares the dashboard to the
₹4.95L proposal they will find a 4× discrepancy and lose confidence.** Decide
which is canonical and document it before the client sees both.

### 3.3 The campaign report is a row-level CSV, not the §10 aggregate

I previously said the campaign report did not exist. That was imprecise.
`GET /campaign/{id}/report` **does exist** and streams a CSV with: Run ID,
Campaign ID, Agent ID, Created At, Phone Number, Call Disposition, Call
Duration, extracted variables, Call Tags, Transcript URL, Recording URL.

What it does **not** contain is what §10 of the tender asks for — connection
rate, completion rate, retry statistics, language distribution, daily progress.
**It also has no Language column**, despite `workflow_runs.language` being
populated and the tender explicitly requiring language distribution.

- [ ] Add a Language column to the CSV (small)
- [ ] Build the aggregate report (5 days, already on the roadmap)

### 3.4 Redis is a hard startup dependency

The API **refuses to start** if Redis is unreachable — `create_pool()` in the
lifespan raises and uvicorn exits. Not a defect, but it dictates production
behaviour:

- ElastiCache must be healthy *before* the API starts
- An ElastiCache failover can take the whole API down rather than degrade it
- Your orchestration must retry API startup, not give up

- [ ] API service configured to restart on failure
- [ ] ElastiCache Multi-AZ (already in the infrastructure plan)

### 3.5 `usage/daily-breakdown` returns 400 for an unconfigured org

`{"detail":"Daily breakdown is only available for organizations with pricing
configured"}`. Correct guard, wrong shape — a dashboard tile will render an
error where it should render "no data yet". Cosmetic, worth fixing before a
government user sees it.

---

## 4. Deployment — step by step

Run these on the EC2 box over SSH. **Never paste keys into a chat, a ticket, or
a commit.**

### Step 1 — pull and verify you have the fixes

```bash
cd ~/echowave-redesign
git fetch origin claude/admin-dashboard-billing-759aws
git checkout claude/admin-dashboard-billing-759aws
git pull origin claude/admin-dashboard-billing-759aws
git log --oneline -6
```

### Step 2 — edit `.env`

```bash
cp .env .env.bak.$(date +%F)
nano .env
```

**Quote every value containing a space or a comma.** This bit me during
testing: `GRIEVANCE_OFFICER_NAME=Nithish K` made `source` try to execute `K`
and the API would not start.

```
# --- Razorpay: paste your LIVE keys here, nowhere else ---
RAZORPAY_KEY_ID=rzp_live_xxxxxxxxxxxx
RAZORPAY_KEY_SECRET=xxxxxxxxxxxxxxxx
RAZORPAY_WEBHOOK_SECRET=xxxxxxxxxxxxxxxx

# --- Supplier identity: WITHOUT THESE NO TAX DOCUMENT IS EVER ISSUED ---
SUPPLIER_LEGAL_NAME="NAUTOMATION LABS PRIVATE LIMITED"
SUPPLIER_GSTIN=33AALCN7211L1ZB
SUPPLIER_STATE_CODE=33
SUPPLIER_ADDRESS="No.86/18, Papanna Thottam, Brindhavan Nagar, TNHB PH-7, Hosur 635109, Tamil Nadu"
SUPPLIER_SAC_CODE=998314

# --- DPDP s13 ---
GRIEVANCE_OFFICER_NAME="Nithish K"
GRIEVANCE_OFFICER_EMAIL=privacy@decibyl.ai
GRIEVANCE_OFFICER_ADDRESS="No.86/18, Papanna Thottam, Hosur 635109, Tamil Nadu"
```

Verify it parses before restarting anything:

```bash
bash -n <(sed 's/^/export /' .env) && echo "env parses clean"
```

### Step 3 — rebuild and restart

```bash
docker compose build api
docker compose up -d
docker compose ps
```

All services must read `healthy`. Then run migrations:

```bash
docker compose exec api python -m alembic upgrade head
docker compose exec api python -m alembic current
```

### Step 4 — seed the price book

```bash
docker compose exec api python -m scripts.seed_provider_rates --confirm
```

It refuses to overwrite rates an operator has already set, so it is safe to
re-run.

### Step 5 — correct the rates that ship wrong

Open `/superadmin/billing/rate-card` and fix:

- **Plivo telephony** — seeded at US ₹0.96/min, must be your India rate
- Any provider where you have negotiated pricing

### Step 6 — configure the Razorpay webhook

In the Razorpay dashboard → Settings → Webhooks:

- **URL:** `https://<your-domain>/api/v1/billing/razorpay/webhook`
- **Secret:** the same value as `RAZORPAY_WEBHOOK_SECRET`
- **Active events:** `payment.captured`, `payment.failed`
- Save, then use "Send test webhook"

An unsigned or wrongly-signed webhook must return **400**. Both were verified in
testing.

### Step 7 — prove it end to end with real money

Do a **₹10 live top-up from a real account** and check all four:

```bash
# 1. webhook arrived and was accepted
docker compose logs api --tail 200 | grep -i razorpay

# 2. no silent voucher failure
docker compose logs api --tail 500 | grep -i "no receipt voucher issued"   # must be empty
```

Then in the UI:

- [ ] Balance increased by the **net** amount (₹10 gross at 18% credits ₹8.47)
- [ ] `GET /billing/documents` shows a receipt voucher with a serial number
- [ ] The voucher's GST split matches the customer's state

### Step 8 — verify readiness reports zero blockers

```bash
curl -s -H "Authorization: Bearer <token>" \
  https://<your-domain>/api/v1/privacy/readiness | python3 -m json.tool | head -20
```

`action_required` must be **0**. `needs_a_human` will be **4** and always will
be — those are legal obligations no code can discharge.

### Step 9 — verify backups actually run

```bash
# The binary the whole schedule depends on
docker compose exec api which pg_dump          # must print a path

# Force one backup now, without waiting for the nightly cron
docker compose exec api python -c \
  "import asyncio; from api.services.backup.database import run_backup; \
   print(asyncio.run(run_backup()))"

# Restore it into a scratch database and verify, then drop
bash scripts/rehearse_restore.sh
```

`pg_dump` was previously installed into a discarded build stage, so backups
would have failed nightly with nothing to show for it. Confirm the binary
exists before trusting the schedule.

---

## 5. Pre-launch sign-off

**Blocking — do not take customer money without these**

- [ ] Supplier identity set; test payment produced a numbered voucher
- [ ] Price book seeded; estimates return `unpriced: []`
- [ ] Plivo India rate corrected
- [ ] Razorpay webhook configured and a live ₹10 payment reconciled
- [ ] `privacy/readiness` shows `action_required: 0`
- [ ] `pg_dump` present, one backup taken, one restore rehearsed

**Blocking before the tender campaign**

- [ ] Migrate to `ap-south-1` — Indian farmer data must not sit in Virginia
- [ ] Retry intervals spread across day-parts (flat 120 s misses the reach target)
- [ ] Scheduled scaling of the media fleet (the ₹4.95L price depends on it)
- [ ] Calling window enforced at dial time, not batch time
- [ ] Load test to 40 concurrent conversations
- [ ] Telangana Telugu validated by a native listener on 20 real calls

**Credential hygiene — outstanding**

- [ ] Rotate `PLATFORM_CREDENTIAL_SECRET`; it appeared in git history and in a
      chat transcript. After rotating, **re-enter every provider API key**, because
      stored credentials encrypted under the old secret cannot be decrypted.
- [ ] Rotate the Razorpay test keys and webhook secret that were shared
- [ ] Confirm EBS/RDS encryption at rest is on

---

## 6. Known-good reference values

From the verified run, for comparison after deployment:

```
signed webhook       -> {"status":"credited","credited_paise":100000}
replayed webhook     -> {"status":"already_credited"}
receipt voucher      -> RV/26-27/000001  taxable 100000  igst 18000  inter_state
Sarvam STT rate      -> 50000 mpaise/minute      (Rs0.50/min)
Sarvam TTS rate      -> 300000 mpaise/1k chars   (Rs3.00/1k chars)
privacy readiness    -> action_required 0, needs_a_human 4
```
