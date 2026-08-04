# Decibyl — engineering handover

Everything built on `claude/admin-dashboard-billing-759aws`, written for a
developer who has not seen any of it and has to take it forward.

**Read this first, then the file it points you at.** This document is a map and
a set of rules; the detail lives next to the code:

| For | Read |
|---|---|
| How a call is priced, and what every dashboard number means | `DASHBOARD.md` |
| Deploying, environment variables, the EC2 runbook | `DEPLOY.md` |
| Data protection: what is built and why it is built that way | `PRIVACY.md` |
| ROPA, DPA annex, privacy notice facts, trust page | `compliance/` |
| Open problems and their root causes | `KNOWN_ISSUES.md` |
| Repo conventions, layering, org-scoping rule | `AGENTS.md`, `api/AGENTS.md`, `ui/AGENTS.md` |

---

## 1. What this branch is

The upstream project was an open-source voice-agent platform with **no billing
of its own** — it called out to a hosted metering service that this fork does
not have access to. Everything that makes it a commercial product was built
here:

1. **A pricing and cost engine** — what a call cost us, what it earns, and the
   margin, computed from a rate card that is editable at runtime.
2. **Prepaid billing** — Razorpay payments, a credit ledger, and a balance gate
   that refuses calls an account cannot pay for.
3. **Indian GST** — place of supply, CGST/SGST vs IGST, zero-rated export under
   LUT, receipt vouchers and monthly tax invoices.
4. **Telephony KYC** — the two-stage state machine carriers require before
   issuing a number.
5. **An admin dashboard** — eight screens over all of the above.
6. **Data protection** — retention, erasure, export, access logging, recording
   disclosure, sub-processor derivation, breach scoping.
7. **Observability** — latency percentiles, token efficiency, context growth.

43 commits. The commit messages are long on purpose and explain *why* each
decision was made; `git log` is a legitimate part of this handover.

---

## 2. Architecture

Layering is enforced by convention and reviewed for. See `api/AGENTS.md`.

```
routes/      thin — parse, resolve auth + organization_id, delegate, shape
services/    all domain logic; importable from tasks/, mcp_server/, other routes
db/          data access only; one client module per domain
tasks/       background jobs (ARQ), scheduled in tasks/arq.py
```

**The rule that matters most: organization scoping.** Every read or write of an
org-scoped row filters by `organization_id`. A foreign key proves a row exists,
not that the caller may reference it — so writing an FK that points at another
org-scoped resource requires fetching that resource *with the caller's org* and
rejecting with 404 if it does not belong. This is tenant isolation, not style.

### Billing services

| Module | Responsibility |
|---|---|
| `services/billing/money.py` | All arithmetic. Integer paise, millipaise, micro-USD. Round-half-up, never floats. |
| `services/billing/rates.py` | Resolving which rate was in force at a moment (effective-dated) |
| `services/billing/rate_card.py` | Editing rates. Supersede-by-closing, never destructive update. |
| `services/billing/cost_engine.py` | Turning measured usage into an itemised cost |
| `services/billing/costing.py` | Costing a completed call; writes `call_cost_items` and the ledger |
| `services/billing/estimator.py` | Per-minute prediction before a call is placed |
| `services/billing/reservations.py` | The balance gate — holds credit for an in-flight call |
| `services/billing/payments.py` | Razorpay orders and the signature-verified webhook |
| `services/billing/tax.py` | GST computation and place of supply |
| `services/billing/documents.py` | Receipt vouchers and tax invoices, gap-free numbering |
| `services/billing/rollup.py` | Daily per-org aggregation the dashboard reads |
| `services/billing/kpis.py` | Unit economics |
| `services/billing/default_rates.py` | The starter vendor price book |

### Privacy services

`services/privacy/` — `retention.py`, `erasure.py`, `export.py`,
`access_log.py`, `subprocessors.py`, `metrics.py`.

---

## 3. The money rules

**Break any of these and the invoices stop reconciling.** They are not
preferences.

1. **Money is integers.** Paise for rupees, millipaise for rates, micro-USD for
   dollar-denominated rates. No floats anywhere in a path that reaches an
   invoice. `Decimal` only inside conversion helpers.

2. **Round half up, away from zero.** Python's `round()` is banker's rounding
   and is wrong here. Use `money.round_half_up_div`.

3. **An invoice total is the sum of its rounded line items** — never the
   rounding of a summed total. The two differ by a paise often enough to
   generate support tickets.

4. **The credit ledger is GST-exclusive, everywhere, without exception.** The
   customer is charged gross at Razorpay; the ledger is credited net. Tax never
   enters the rate card, the cost engine, or any balance. This single invariant
   is what keeps GST from contaminating every calculation in the system.

5. **Rates are effective-dated and never updated in place.** Superseding a rate
   closes the old row (`effective_to`) and inserts a new one, so a historical
   call re-costs to the number that was actually charged.

6. **Usage with no rate on file is *uncosted*, not free.** The call still bills
   the platform fee, the missing provider cost is reported on the unit-economics
   screen, and margin is overstated until a rate is entered. Silently pricing at
   zero would make a misconfiguration look like a profitable month.

7. **The platform fee bills on 15-second pulses**, not whole minutes:
   `billed_seconds = ceil(seconds / pulse) * pulse`. At `pulse=60` this
   reproduces whole-minute billing exactly, which is how it stays comparable to
   competitors who bill that way.

8. **Only a signature-verified webhook credits an account.** The browser saying
   "paid" is not evidence of payment. Without `RAZORPAY_WEBHOOK_SECRET`, top-ups
   are refused outright — deliberately, because the alternative is charging a
   customer and crediting nobody.

---

## 4. Data model

Tables added by this branch. All migrations are in `api/alembic/versions/`.

### Pricing and cost

| Table | Purpose |
|---|---|
| `provider_rates` | Effective-dated vendor unit rates, keyed `(provider, model, component)`. `model=""` is the provider-wide fallback. |
| `organization_rate_history` | The platform rate per account, effective-dated |
| `platform_volume_tiers` | Volume discount bands |
| `usd_inr_rate_history` | FX, effective-dated. The platform rate is USD-denominated. |
| `call_cost_items` | One itemised line per component per call. `units` is the **raw** measured quantity — seconds, characters, **tokens** — never pre-divided. |
| `daily_organization_rollup` | Per-account per-day aggregate. Every dashboard headline reads this, not `workflow_runs`; that is what keeps pages fast at a million calls. |
| `call_turn_metrics` | Per-turn latency timeline and token usage |

### Money movement

| Table | Purpose |
|---|---|
| `credit_ledger` | Every credit movement. **The only record of what a customer has paid** — back it up. |
| `payments` | One row per top-up attempt, created before the customer pays. Makes the webhook idempotent; Razorpay retries at-least-once. |
| `billing_profiles` | GSTIN, legal name, address, state code per account |
| `tax_documents` | Issued receipt vouchers and tax invoices |
| `document_sequences` | Gap-free numbering per financial year, under a row lock |
| `billing_audit_log` | Who changed which rate, and when |

### KYC and provider keys

`organization_kyc`, `kyc_documents`, `platform_provider_credentials`
(Fernet-encrypted, no read path).

### Privacy

`data_retention_policies`, `data_access_log`, `erasure_requests`.

---

## 5. API reference

Everything is mounted under `/api/v1`.

### 5.1 Admin — staff only

**The whole router carries `Depends(get_superuser)` at router level**, so a new
endpoint added to `routes/billing_dashboard.py` is gated by default rather than
by the author remembering. Keep it that way.

Staff access is the `users.is_superuser` flag, granted by
`python -m scripts.grant_superuser <email>` (in Docker:
`docker compose exec api python -m scripts.grant_superuser <email>`). Nothing in
the signup flow sets it.

#### Overview and accounts

| Method | Path | Returns |
|---|---|---|
| `GET` | `/admin/billing/overview` | Headline figures + previous equal period, minutes/day, cost composition, top accounts, latency |
| `GET` | `/admin/billing/accounts` | All accounts with revenue, margin, balance. Filters: `account_type`, `status` |
| `GET` | `/admin/billing/accounts/{organization_id}` | One account: detail, daily series, cost composition, latency by language, rate history, credit ledger |
| `POST` | `/admin/billing/accounts/{organization_id}/credit` | Manual credit adjustment. Body `{delta_paise, note}`. **`note` is required** and audited. `delta_paise` must not be zero. |
| `PUT` | `/admin/billing/accounts/{organization_id}/platform-rate` | Set a negotiated rate for one account |

#### Calls

| Method | Path | Returns |
|---|---|---|
| `GET` | `/admin/billing/calls` | Paged call list. Filters: `organization_id`, `language`, `direction`, `search`, `page`, `limit` (max 200) |
| `GET` | `/admin/billing/calls/{workflow_run_id}` | Call receipt: metadata, itemised cost, per-turn latency, **latency summary** |

#### Metrics

| Method | Path | Returns |
|---|---|---|
| `GET` | `/admin/billing/latency` | Daily p50/p95, by language, stage medians, slowest turns, **`percentiles`** (per measure), **`headline`**, **`tools`**. Filters: `organization_id`, `language` |
| `GET` | `/admin/billing/tokens` | Token series, by model, **`context_growth`**. Params: `granularity` = `day\|week\|month`, `organization_id` |
| `GET` | `/admin/billing/unit-economics` | Cost per minute, margin per minute, pulse give-away, thinnest margins |
| `GET` | `/admin/billing/campaigns` | Campaign spend and concurrency |
| `GET` | `/admin/billing/campaigns/{campaign_id}/concurrency` | Real overlap, not a calls-started rate |

#### Rate card

| Method | Path | Body |
|---|---|---|
| `GET` | `/admin/billing/rate-card` | — |
| `PUT` | `/admin/billing/rate-card/platform` | Platform rate (USD micros + pulse seconds) |
| `PUT` | `/admin/billing/rate-card/providers` | `{provider, model, component, unit, rate_mpaise, effective_from?, note?}` |
| `DELETE` | `/admin/billing/rate-card/providers` | Retire a provider rate |
| `PUT` | `/admin/billing/rate-card/tiers` | Volume tier |
| `DELETE` | `/admin/billing/rate-card/tiers/{min_period_minutes}` | Retire a tier |
| `PUT` | `/admin/billing/rate-card/exchange-rate` | USD→INR |

`unit` is one of `minute`, `1k_chars`, `1k_tokens`. `component` is one of
`stt`, `llm`, `tts`, `telephony`. Both are validated against the enums at write
time — an unknown unit would otherwise be stored happily and fail at costing
time, on a live call.

#### KYC review

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/admin/kyc/queue` | Pending submissions |
| `GET` | `/admin/kyc/documents/{document_id}` | Signed URL for one document (**access-logged**) |
| `POST` | `/admin/kyc/{organization_id}/claim` | Claim for review |
| `POST` | `/admin/kyc/{organization_id}/approve` | Approve |
| `POST` | `/admin/kyc/{organization_id}/reject` | Reject with reason |
| `POST` | `/admin/kyc/{organization_id}/carrier-verdict` | Record the carrier's decision |

#### Provider keys

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/admin/provider-keys` | List. **Returns last four characters only** — there is no read path for a key. |
| `PUT` | `/admin/provider-keys` | Store `{component, provider, api_key, label?}`. Fernet-encrypted with `PLATFORM_CREDENTIAL_SECRET`. |
| `DELETE` | `/admin/provider-keys` | Remove |
| `POST` | `/admin/provider-keys/active` | Pause/resume without discarding |

### 5.2 Customer

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/billing/balance` | Balance, top-up limits, GST rate, whether the billing profile is complete |
| `POST` | `/billing/topup` | Create a Razorpay order. Returns `key_id` for checkout. |
| `GET`/`PUT` | `/billing/profile` | GSTIN, legal name, address, state |
| `GET` | `/billing/payments` | Payment history (net, gross and tax shown separately) |
| `GET` | `/billing/documents` | Tax documents |
| `GET` | `/billing/documents/{document_id}` | One document |
| `POST` | `/cost-estimate/per-minute` | Price a stack before the first call |
| `GET` | `/kyc`, `PUT /kyc/business-details`, `POST /kyc/documents`, `DELETE /kyc/documents/{id}`, `POST /kyc/submit` | Customer KYC flow |

### 5.3 Privacy — customer, own account only

Scoped to the caller's organization by design. We never act on a stranger's
request about an account they have not proved they belong to: confirming a
number appears in an account would itself be a disclosure.

| Method | Path | Purpose |
|---|---|---|
| `GET`/`PUT` | `/privacy/retention` | Retention windows. Minimum 1 day. |
| `POST` | `/privacy/erasure` | Erase one number across every call. Irreversible. |
| `GET` | `/privacy/erasure` | Erasure history — the evidence obligations were met |
| `GET` | `/privacy/export` | JSON export, per number or whole account |
| `GET` | `/privacy/access-log` | Who reached recordings and transcripts |
| `GET` | `/privacy/subprocessors` | Derived vendor list + grievance officer |
| `GET` | `/privacy/breach-report` | What was reached between two timestamps |
| `GET` | `/privacy/metrics` | Privacy health — see §7 |

### 5.5 Readiness — is this deployment actually fit to run

Two assessments in one shape (`services/readiness.py` holds the shared
vocabulary). Both split **configuration** — is the setting present — from
**evidence** — did the thing actually happen. Only the second kind catches a
deployment where the code is correct and the outcome is still wrong, and a
fresh install reports `unknown` rather than `ready` for those, because
reporting a pass on absence of evidence is the failure both exist to prevent.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/privacy/readiness` | Data-protection obligations. `needs_a_human` never clears. |
| `GET` | `/admin/billing/readiness` | Supplier identity, price book, **payments with no receipt voucher**, uncosted calls, worker liveness. Staff only. |
| `GET` | `/health/workers` | ARQ worker heartbeat age. Devops secret, like `/health/active-calls`. |

The billing check to watch is `payments_have_vouchers`. It is designed to read
zero; any other value is an accrued tax liability rather than a statistic.

### 5.4 Webhook

`POST /billing/razorpay/webhook` — **excluded from the OpenAPI schema on
purpose** (`include_in_schema=False`); it carries no session, and the HMAC
signature is the authentication. Do not IP-allowlist it: Razorpay publishes no
fixed source range.

---

## 6. Metric definitions

Get these wrong and the dashboard lies confidently.

| Metric | Definition |
|---|---|
| **TTFT** | `t_llm_first_token_ms − t_stt_final_ms`. The model's thinking time. |
| **TTFB** | `t_tts_first_byte_ms − t_llm_first_token_ms`. The voice's. |
| **Perceived latency** | Caller stops speaking → audio leaves. Neither of the above, and the only one a complaint is about. |
| **Tokens per minute** | LLM `units` ÷ conversation minutes. Flat as volume grows; moves when the agent design changes. |
| **Context growth** | Median `prompt_tokens` by turn index. At turn N this *is* the context size, because the whole conversation is resent. |
| **Cache hit rate** | `cached_tokens ÷ prompt_tokens`. `null` when unmeasured — "no cache" and "not reported" are different findings. |

Three rules the queries follow, all of which have already caused a bug:

1. **A turn missing a timestamp is excluded, never zeroed.** A zero drags a
   percentile *down*, so broken instrumentation would read as an improvement.
2. **Percentiles over a window are computed over the window.** The p95 of a
   month is not the mean of its daily p95s.
3. **Tokens and minutes bucket by the *call's* date**, not the costing job's.
   Otherwise a recosted call moves periods and the ratio divides one period's
   tokens by another period's conversation. Totals still reconcile, which is why
   this survived a first review.

---

## 7. Privacy and compliance

Full detail in `PRIVACY.md`; drafts for legal review in `compliance/`.

Built: retention with a nightly purge, erasure (number-format-insensitive),
export, access logging, spoken recording disclosure, derived sub-processor list,
breach-window report, grievance officer.

**Two design decisions worth preserving:**

- **Objects are deleted before rows are cleared.** Clearing the row and leaving
  the audio in a bucket looks exactly like success. If an object cannot be
  deleted the row keeps its pointer so the next sweep retries.
- **Erasure requests store a SHA-256 hash of the number, never the number.** A
  register of people who asked to be forgotten is itself personal data.

`GET /privacy/metrics` returns a headline designed to be **zero**: recordings
past their retention window that still exist. Any other value is an incident,
not a statistic — it means the purge job stopped and nothing said so.

---

## 8. Operations

### Scheduled jobs — `api/tasks/arq.py`

| Job | Schedule |
|---|---|
| `sweep_webhook_deliveries` | every 5 min |
| `refresh_billing_rollups` | every 10 min |
| `poll_kyc_carrier_status` | every 15 min |
| `sweep_credit_reservations` | every 5 min |
| `purge_expired_call_data` | daily 19:00 UTC (00:30 IST) |
| `issue_monthly_tax_invoices` | 1st of month, 20:30 UTC |

| `record_worker_heartbeat` | every minute |

**One container runs everything** — `start_services_docker.sh` starts uvicorn,
the ARQ workers, the ARI manager and the campaign orchestrator together. There
is no separate worker to deploy, but it also means **if the ARQ worker dies,
calls silently stop being costed and invoices stop being issued** while the API
keeps answering.

`GET /health/workers` is the signal for exactly that, and
`background_worker_alive` on `/admin/billing/readiness` reads the same
heartbeat. **Alert on it** — nothing else in the system notices, because the
absence of costing is indistinguishable from a quiet night. See
`services/worker_health.py` for why the heartbeat is positive rather than
inferred, and why `alive` is tri-state.

### Scripts

| Script | Purpose |
|---|---|
| `scripts/grant_superuser.py` | Grant/revoke staff access. `--list`, `--revoke`. |
| `scripts/seed_provider_rates.py` | Load the starter price book. `--confirm` to write, `--force` to overwrite. |
| `scripts/seed_billing_demo.py` | Demo data for development only |
| `scripts/dump_docs_openapi.py` | Regenerate `docs/api-reference/openapi.json` |
| `scripts/generate_sdk.sh` | Regenerate typed SDKs — CI asserts the diff is empty |
| `scripts/format.sh` | Format and lint — CI asserts no drift |

### Deployment

See `DEPLOY.md`. Two traps that have already cost time:

- **`DEPLOY_MODE=build REPO_SOURCE=existing`** — without the first, the stack
  pulls upstream's image and runs cleanly with none of this work in it. Without
  the second, the setup script clones a different repository over yours.
- **Compose reads `.env` for interpolation only.** The api service now injects
  the file wholesale (`env_file:`), with `environment:` still winning for the
  values it computes from container hostnames. Adding a setting needs no second
  edit.

---

## 9. Testing

```bash
source venv/bin/activate && set -a && source api/.env.test && set +a && python -m pytest api/tests
```

**1777 tests, green.** Real Postgres 16 + pgvector and real Redis — not
mocked, because the bugs that matter here are ones a mock cannot reproduce
(row locks, partial unique indexes, timezone truncation).

Tests worth reading before changing billing, because they encode invariants
rather than behaviour:

| File | Guards |
|---|---|
| `test_billing_money.py` | Rounding, currency conversion, pulses |
| `test_billing_concurrency.py` | Row locks under real contention — verified by *removing* the lock and watching 8/8 pass where 2 should |
| `test_billing_documents.py` | Gap-free numbering under concurrency |
| `test_billing_tax.py` | GST splits, place of supply, LUT |
| `test_privacy.py` | Storage objects actually deleted, not just dereferenced |
| `test_metrics.py` | Percentiles over the right rows; ratios over the same period |
| `test_default_rates.py` | Price book coherence — units match components |

---

## 10. What is not built

Honest list. Priorities are mine; argue with them.

### Revenue-blocking

| Gap | Note |
|---|---|
| **No PDF for tax documents** | Issued, numbered and readable via API; a customer cannot download one |
| **No e-invoicing (IRN via IRP)** | Mandatory above ₹5 crore aggregate turnover |
| **No credit notes** | A refund is issued in the Razorpay dashboard and reflected with a staff credit adjustment |
| **No low-balance email** | The screen warns in amber and a refused run says why, but nobody is emailed |

### Product

| Gap | Note |
|---|---|
| **Number provisioning not built** | `is_platform_managed` is the flag the KYC gate keys on and nothing sets it. The gate is correct but dormant. |
| **No recost script** | `cost_workflow_run(recost=True)` exists; nothing exposes it. Calls placed before rates existed stay uncosted. |
| **Role model is one boolean** | `is_superuser`. There is no matrix. |

### Built since this list was written

| Was missing | Now |
|---|---|
| §10 aggregate campaign report | `GET /campaign/{id}/summary` — rates, retries, language distribution, daily progress in IST. Denominators documented in `services/reports/campaign_summary.py`. |
| Language on the run CSV | Added. The column was never selected; the data was always there. |
| Any signal for the two silent billing failures | `GET /admin/billing/readiness` |
| Any signal for a dead ARQ worker | `GET /health/workers` |

### Metrics not yet built

Ordered by value. Interruption rate and talk ratio need new pipeline
instrumentation; the rest are queries over data already stored.

1. **Interruption / barge-in rate** — rising rate means the agent is too slow or
   too verbose. Needs capture.
2. **Cost per completed outcome** — cost per *booking*, not per call. The number
   that decides whether the product pays.
3. **Agent vs user talk ratio**, **dead-air ratio** — quality signals.
4. **ASR (answer-seizure ratio)** — carriers judge you on it.
5. **Provider error / retry / circuit-breaker trip counts** — the breaker exists,
   nothing counts its trips.
6. **MOS, jitter, packet loss, post-dial delay** — carrier-side, needs telephony
   webhooks.

### Compliance

DPIA, SCCs for EEA transfers, incident-response runbook, tested backup restore.
The `compliance/` drafts mark every decision `[TO CONFIRM]`.

---

## 11. Traps

Bugs found on this branch that were invisible to every test. A new developer
will meet the same class again.

**The rebrand renamed columns inside an already-applied migration.** A database
created after the edit is correct — so every test, every fresh install and every
CI run passed. A database created *before* it keeps the old names forever, and
every query touching that table fails. Found on the first deployment that was an
upgrade rather than an install. Fixed by `f2b9d47c30ae`, which converges both
histories. **Never edit a migration that has run anywhere.**

**Erasure normalised the search term but not the stored value.** `+91 98765
43210` never contains `9876543210`, so erasure matched nothing and reported
successful erasure of zero calls. Both sides are now stripped with
`regexp_replace`.

**The cost estimate endpoint computed `pulse_seconds` and dropped it**, so the
UI rendered "billed in **s** pulses" — the product's differentiator displayed as
a typo.

**Compose reads `.env` for interpolation only.** Every variable the compose file
did not name explicitly was silently absent inside the container. Top-ups
refused, provider keys unsavable, invoices with a blank supplier — all looking
like application bugs.

**Tokens bucketed by the costing job's timestamp**, minutes by the call's. The
ratio divided one period's tokens by another period's conversation while totals
still reconciled.

The pattern: **the dangerous failures are the ones that produce a plausible
number rather than an error.** When adding a metric or a money path, ask what it
would show if the thing it measures were broken. If the answer is "something
that looks fine", write the test that distinguishes the two.
