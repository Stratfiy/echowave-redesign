# Operations manual

Everything an operator does after the platform is deployed: set prices, take
payments, add provider keys, read the numbers, and diagnose a failed call.

`DEPLOY.md` gets it running. This is what you do next, and every week after.

> **Nothing here is optional on a fresh install.** A deployment with no provider
> rates does not fail — it quietly reports the platform fee as the whole cost of
> a call and margin as near 100%. See §2.

---

## 1. Getting into the admin panel

The billing screens are at `/superadmin/billing` and need a superuser flag,
which nothing in the signup flow sets. Create the account first, then grant it:

```bash
docker compose exec api python -m scripts.grant_superuser you@yourdomain.com
docker compose exec api python -m scripts.grant_superuser --list   # confirm
```

If the script is missing from the image, the fallback is direct SQL:

```bash
docker compose exec postgres psql -U postgres -c \
  "UPDATE users SET staff_role = 'superadmin' WHERE lower(email) = 'you@yourdomain.com';"
```

Sign out and back in — the flag is read at login.

### The screens

| Screen | What it answers |
|---|---|
| **Overview** | Revenue, minutes, margin, this month against last |
| **Accounts** | Per-customer balance, spend, and their negotiated rate |
| **Calls** | Every call with its itemised cost, latency and disposition |
| **Campaigns** | Cost and outcome per campaign |
| **Latency** | TTFT/TTFB percentiles — p50, p90, p95, p99 |
| **Tokens** | Consumption per minute, and context growth per turn |
| **Realtime** | What a speech-to-speech minute costs, by model |
| **Unit economics** | Margin per minute, uncosted call share |
| **Rate card** | **Every price the platform charges or pays.** §2 and §3 |

---

## 2. Seed the price book — do this first

**On a fresh install `provider_rates` is empty.** Nothing populates it, and an
empty price book is not an error state: the cost engine simply reports every
provider as unpriced, the estimator shows customers the platform fee alone, and
the margin column reads near 100% because measured provider cost is zero.

Check whether you have this problem:

```bash
docker compose exec postgres psql -U postgres -c "SELECT count(*) FROM provider_rates;"
```

Zero means seed it:

```bash
docker compose exec api python -m scripts.seed_provider_rates            # dry run
docker compose exec api python -m scripts.seed_provider_rates --confirm  # write
```

Nothing is written without `--confirm`, and a rate you have already set by hand
is never overwritten unless you add `--force`.

**The seeded numbers are approximate published list prices and they will be
wrong for you.** They exist so the machinery works on day one, not as a
statement about what anything costs. Correct them in the rate card — §3 — and
check them against your actual invoices before quoting a customer.

---

## 3. Rate card — every price, and what each one does

`/superadmin/billing/rate-card`. Four separate things live here and they are
easy to confuse.

### 3.1 Platform rate — your revenue

The fee you charge on top of provider cost, quoted **per minute in USD** and
converted at the stored FX rate. This is the only line that is margin; every
other rate is money you pay somebody else.

Set alongside it:

- **Pulse seconds** — the billing increment. 15 by default, which is the
  differentiator: a 62-second call bills 75 seconds, not 120. Set it to 60 to
  reproduce ordinary per-minute billing exactly.

### 3.2 Provider rates — your cost

One row per **provider + model + component**, each with a unit:

| Component | Unit | Notes |
|---|---|---|
| `stt` | `minute` | Billed on audio duration |
| `llm` | `1k_tokens` | **Blended** — see below |
| `tts` | `1k_chars` | Usually the largest single line |
| `telephony` | `minute` | Domestic and international differ by an order of magnitude |

**LLM rates are blended.** Vendors price input and output tokens separately and
this schema carries one number, because a call's split is unknown until it
happens. The seeded blend assumes 70% input, which is right for voice agents
that resend a growing transcript each turn. If your traffic differs, the rate
should differ.

**A model with no row falls back to the provider-wide row** (the one with an
empty model). Provider-wide rates are seeded at the *cheapest* common model, so
an unpriced model under-reports rather than over-reports — a surprise on the
invoice should be a pleasant one.

**Speech-to-speech models** are recorded under the provider name derived from
the service class — `decibylgeminilive`, not `google_realtime`. Their rate is a
blend across audio-in, re-sent context and audio-out at a three-minute
reference call. Realtime billing has four prices and this schema has one field.

### 3.3 Exchange rate

USD→INR, stored with an effective date. Every invoice snapshots the rate that
applied when the call happened, so re-reading an old invoice reproduces the
original number rather than today's.

Update it when the rate moves materially. Do not update it retroactively.

### 3.4 Per-account rates

`/superadmin/billing/accounts` → a customer → set their platform rate. Overrides
the global rate for that account only, and is how you pilot a new price without
touching existing customers.

Volume tiers exist in the schema (`platform_volume_tiers`) and are settable from
the rate card, but nothing currently applies them automatically.

### How changes take effect

Rates are **effective-dated**. Saving a new rate closes the old row's
`effective_to` and opens a new one — it never edits history. A call costed
yesterday keeps yesterday's price forever, which is what makes an old invoice
reproducible.

**So: changing a rate never re-prices past calls.** If you need that, it is a
deliberate re-costing job, not a rate edit.

---

## 4. Provider keys

`/superadmin/provider-keys`. One key per component per provider, encrypted at
rest with `PLATFORM_CREDENTIAL_SECRET`.

Two things that bite:

- **Without `PLATFORM_CREDENTIAL_SECRET` set, saving a key raises** and no call
  can be placed at all. This is deliberate — the alternative is storing
  provider credentials in plaintext.
- **Rotating that secret makes every stored key undecryptable.** They are not
  deleted; they simply stop working, and the failure is logged as *"cannot be
  decrypted — re-enter the key"*. After any rotation, re-enter every key.

---

## 5. Razorpay — taking payments

### 5.1 Keys

Test and live mode have **separate keys and separate webhook secrets**. In
`.env`:

```
RAZORPAY_KEY_ID=rzp_live_xxxxx
RAZORPAY_KEY_SECRET=xxxxx
RAZORPAY_WEBHOOK_SECRET=xxxxx
```

### 5.2 The webhook — the part people get wrong

`RAZORPAY_WEBHOOK_SECRET` is **not issued by Razorpay**. You invent it, and you
paste the identical string into both `.env` and the Razorpay dashboard.

In the Razorpay dashboard → Settings → Webhooks → Add:

| Field | Value |
|---|---|
| URL | `https://<your-host>/api/v1/billing/razorpay/webhook` |
| Secret | the string you generated |
| Active events | `payment.captured` and `payment.failed` — **only these two** |

Generate the secret without it touching your shell history:

```bash
WHS=$(openssl rand -hex 24)
sed -i.bak "s|^RAZORPAY_WEBHOOK_SECRET=.*|RAZORPAY_WEBHOOK_SECRET=${WHS}|" .env
echo "$WHS"        # paste into Razorpay, then clear the screen
unset WHS && rm .env.bak
```

Then `docker compose restart api`.

### 5.3 Why the strictness

**Only a signature-verified webhook credits an account** — never the browser
reporting success, which a customer can forge. If the secret is missing or
mismatched, top-ups are **refused outright** rather than credited, deliberately:
the alternative is charging a customer and crediting nobody.

So a mismatch looks like "payments stopped working", not like "free credit".

### 5.4 Verifying it end to end

```bash
BASE_URL=https://<your-host> RAZORPAY_WEBHOOK_SECRET=<same-string> \
  python -m scripts.e2e_smoke
```

Run this against **staging**, never production — it creates an account and posts
a payment webhook. It walks signup → agreement acceptance → billing profile →
top-up → webhook signature rejection → credit → tax documents → readiness.

### 5.5 Money model, briefly

- Accounts are **prepaid**. Credit is bought up front; usage draws it down.
- GST is added **on top** of the credit price and never enters the ledger, which
  stays tax-exclusive end to end.
- Each payment produces a **receipt voucher**; each month a **tax invoice** for
  actual usage. Numbering is gap-free per financial year.
- Supply outside India is zero-rated under LUT — which requires
  `SUPPLIER_HAS_LUT=true` and a LUT number, or foreign top-ups are refused
  rather than charged 18% they should not pay.

---

## 6. Diagnosing a failed call

### 6.1 `pipeline_error`

The run state says the speech pipeline threw. The detail is now written to
`workflow_runs.extra.pipeline_error` — read it first:

```bash
docker compose exec postgres psql -U postgres -c \
  "SELECT id, state, extra->'pipeline_error' FROM workflow_runs
   WHERE state = 'pipeline_error' ORDER BY id DESC LIMIT 5;"
```

**The most common cause by far is a missing or undecryptable provider key.**
The pipeline cannot construct its STT/LLM/TTS service, and fails at start-up.
Check:

```bash
docker compose logs api --tail 500 | grep -i "cannot be decrypted\|no platform.*key\|api key"
```

If you see *"cannot be decrypted"*, `PLATFORM_CREDENTIAL_SECRET` changed since
the keys were stored. Re-enter every key in `/superadmin/provider-keys`.

### 6.2 No recording or transcript

Three causes, in order of likelihood:

1. **The call never produced audio** — it failed before connecting. The run
   state tells you; a `pipeline_error` run has nothing to record.
2. **MinIO/S3 unreachable.** Look for connection-refused to the storage endpoint
   in the api logs.
3. **Retention purged it.** Recordings age out at 90 days by default. A purged
   run keeps its row and its billing figures, and the recording pointer is
   replaced with a marker.

### 6.3 The numbers look wrong

| Symptom | Almost always |
|---|---|
| Margin near 100% | Provider rates not seeded — §2 |
| Cost estimate = platform fee only | Same |
| Dashboard shows zero | `refresh_billing_rollups` not running — check the arq worker |
| Costing stale by hours | arq worker dead. Nothing monitors this. |
| Realtime calls cost nothing | No rate for `decibylgeminilive` etc. — §3.2 |

---

## 7. The weekly checks

Five minutes, and each one catches something that fails silently.

```bash
# 1. Backups are actually happening
curl -s -H "Authorization: Bearer $TOKEN" \
  https://<host>/api/v1/privacy/readiness | jq '.checks[] | select(.key=="database_backed_up")'

# 2. Nothing blocking on privacy
curl -s -H "Authorization: Bearer $TOKEN" \
  https://<host>/api/v1/privacy/readiness | jq '.action_required'

# 3. The worker is alive — costing should be minutes behind, not hours
docker compose exec postgres psql -U postgres -c \
  "SELECT max(costed_at), now() - max(costed_at) AS lag FROM workflow_runs WHERE costed_at IS NOT NULL;"

# 4. No uncosted calls
docker compose exec postgres psql -U postgres -c \
  "SELECT count(*) FROM workflow_runs WHERE billable_seconds > 0 AND costed_at IS NULL;"
```

And monthly: **rehearse a restore.** A backup nobody has restored is a
hypothesis.

```bash
./scripts/rehearse_restore.sh <backup-file>
```

---

## 8. Scaling up

Defaults are sized for a handful of concurrent calls. Before a campaign of any
size, set these in `.env`:

| Setting | Default | For ~80 concurrent calls |
|---|---|---|
| `DEFAULT_ORG_CONCURRENCY_LIMIT` | 10 | 100 |
| `FASTAPI_WORKERS` | 2 | 4 |
| `DB_POOL_SIZE` | 20 | 25 |
| `DB_POOL_MAX_OVERFLOW` | 20 | 25 |
| `ARQ_MAX_JOBS` | 10 | 30 |
| `POSTGRES_MAX_CONNECTIONS` | 300 | 300 |

The concurrency limit is a **hard gate** — call 11 is refused, not queued.

Pool exhaustion is the one that misleads: it does not raise, callers *queue* on
a connection while a live caller hears silence. It presents as latency and gets
blamed on the model.

After changing any of these, **rehearse the spend ceiling at the new numbers**:

```bash
docker compose exec api python -m scripts.rehearse_concurrency --calls 40
```

Raising concurrency raises how many calls can start in the same instant, which
is exactly the condition the per-organization reservation lock exists for. The
script fires that burst at the real database on a throwaway funded account and
checks that only what the balance covers was allowed. It places no calls and
deletes every row it writes. It also runs the real sweeper, so stale holds
anywhere on the deployment are released — the same thing the scheduled job
does.

---

## 9. What still needs a human

Listed at `/api/v1/privacy/readiness` with status `needs_a_human`, and no
configuration can clear them:

- **Notice and consent** for the people being called — the customer's
  obligation as Data Fiduciary, and only a signed DPA allocates it there
- **Published privacy notice** — `compliance/` holds the facts, a lawyer writes
  the document
- **Significant Data Fiduciary assessment** — re-check as volume grows
- **Cross-border transfer review** — most sub-processors are outside India

---

## Related documents

| | |
|---|---|
| `DEPLOY.md` | First deployment, in order |
| `DASHBOARD.md` | How a call is priced, and every billing invariant |
| `PRIVACY.md` | Retention, erasure, export, access logging |
| `compliance/DPA-TEMPLATE.md` | The agreement to send customers |
| `KNOWN_ISSUES.md` | Open problems, each with what fixing it involves |
| `HANDOVER.md` | For a developer taking this over |
