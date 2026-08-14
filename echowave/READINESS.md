# Product readiness — 14 August 2026

A point-in-time assessment, written to be argued with. Everything below is
either something I verified in this repository or something I explicitly could
not verify from here — those two are kept apart, because a readiness document
that blurs them is worse than none.

**Verdict: the money is ready, the platform is ready, one advertised feature is
not, and one thing has never been proven end to end.**

You can take real customers. You cannot yet promise them the knowledge base,
and nobody has round-tripped a real rupee.

---

## 1. Ship blockers

### 1.1 Knowledge base ingestion cannot work on this deployment

**Severity: critical — an advertised feature that fails on first use.**

The path is four stages:

```
upload → convert & chunk → embed → store → retrieve at call time
```

Only the second stage leaves the process. It is delegated to MPS
(`MPS_API_URL`, defaulting to `https://services.decibyl.ai`), and **there is no
local chunker to fall back to** — `docling` appears in this codebase only as
the name of a metadata field.

I proved the boundary rather than assuming it. `api/tests/test_knowledge_base_end_to_end.py`
stubs MPS and nothing else; every stage downstream passes against real
infrastructure — real embeddings written to a real pgvector column, a real
cosine search, real per-organization scoping. So the gap is **one service to
stand up**, not a feature unfinished in several places. That distinction is the
difference between a week and a quarter.

Two other features have the same unresolved dependency:

| Feature | MPS call | Fallback? |
|---|---|---|
| Knowledge base ingestion | `process_document` | **none** |
| Recording transcription (`/workflow/recording/transcribe`) | `transcribe_audio` | **none** |
| Service keys (`/user/service-keys`) | `get/create/archive_service_key` | **none** |
| Agent generation from a description | `call_workflow_api` | yes — builds locally |
| Voice picker | *(migrated)* | yes — local catalogue |

The last two rows are the important context: somebody has already been
migrating off MPS deliberately, and the voice catalogue's own docstring says
the external service "no longer exists". These three were left behind rather
than judged safe.

`MPS_API_URL` does not appear in `DEPLOY-ENV.md` at all, so a deployment
inherits the default hostname silently.

**Three ways out, in the order I would consider them:**

1. **Bring chunking in-process.** `docling` or `unstructured` in the worker
   image. Largest change, removes the dependency permanently, and the worker
   already does the embedding — this is the only option that makes the feature
   yours.
2. **Stand up a minimal MPS** exposing just `/api/v1/document/process`. Fastest
   to green, but it keeps a whole service alive for one endpoint and leaves
   transcription and service keys still pointing at it.
3. **Turn the feature off in the UI until one of the above.** Not a fix, but
   strictly better than a customer uploading a policy document and getting a
   raw `ConnectError` string. This is what I would do *today*, alongside 1.

### 1.2 No real payment has ever been round-tripped

**Severity: high — not a defect, an unproven loop.**

The payments code is the strongest part of this codebase and I want to be
precise that this is not a criticism of it. It is idempotent (`already_credited`
on redelivery), signature-gated with `hmac.compare_digest`, GST-correct (gross
collected, net credited), refuses a top-up outright when no webhook secret is
configured, caps an overstated payload and logs loudly, and credits the
shortfall net of tax on a partial capture. 141 tests pass across the payments
slice.

What has not happened is a live transaction. Specifically unproven:

- The webhook endpoint (`POST /api/v1/billing/razorpay/webhook`) is publicly
  reachable from Razorpay's servers through your load balancer.
- That URL is registered in the Razorpay dashboard, subscribed to
  `payment.captured` and `payment.failed`.
- `RAZORPAY_WEBHOOK_SECRET` on the box matches the one in the dashboard.
- Razorpay's live keys are activated, not just test keys.

Each of those is invisible until the first real customer tries to pay, and the
failure mode is the one the code was written to avoid: a customer charged and
nobody credited. **Do a ₹1 live top-up yourself before anyone else does.**
`/superadmin` → billing readiness will tell you what is still unset; it cannot
tell you whether the round trip works.

---

## 2. Ship, but with these written down

| | Finding | Why it can wait |
|---|---|---|
| 2.1 | A document that converts to **zero chunks** — a scanned PDF with no text layer — lands as `completed` with `total_chunks = 0`. The screen says ready; the agent answers nothing from it. | Silent, but it only bites after 1.1 is fixed. Fix them together. |
| 2.2 | When ingestion fails the customer sees a raw transport error (`nodename nor servname`). | Cosmetic until the feature works at all. |
| 2.3 | **39 pre-existing test failures**: `test_ts_bridge` (26), `test_mcp_save_workflow`, `test_pipecat_engine_tool_calls`, `test_telephony_routes`, `test_user_idle_handler`, `test_camb_tts_integration`. I confirmed by stashing that the set is identical before and after all of my work. | None sit on the money or call path. But 26 of them are the TypeScript workflow bridge, which is not nothing. |
| 2.4 | The `admin` organization role gates nothing. | Documented as reserved. Safe, but somebody will assign it expecting restriction. |
| 2.5 | Several billing tests assume an empty database and fail when one has leftover rows. | Test hygiene, not product behaviour — but it makes CI results harder to trust, which has a way of becoming a product problem. |

---

## 3. What is genuinely solid

Worth stating plainly, because a readiness document that only lists problems
gives no sense of proportion.

- **The money path.** Effective-dated rates everywhere, so a historical invoice
  always reproduces. GST-exclusive ledger. Audited adjustments with a required
  note. Refunds that reverse both halves and refuse to refund spent credit.
  Reservation before a call, costing after. Credit exhaustion never cuts a live
  call.
- **Tenant isolation.** Every org-scoped read and write filters by
  `organization_id`, including the knowledge base search — I tested that one
  specifically, because an unscoped vector search would leak whatever a
  customer uploaded and would look like a working feature.
- **Secrets.** Encrypted at rest, never readable back, and now rotatable
  without a window in which anything is unreadable.
- **Operational limits.** Per-account concurrency split by direction (5 in, 10
  out) and a daily spend circuit breaker.
- **Scale of the thing.** 3,266 tests, 130 migrations. This is a real system.

---

## 4. Backups and disaster recovery

You asked about this specifically, so it gets its own section rather than a row
in a table.

### What is now true

- A nightly encrypted `pg_dump`, verified by reading the object back and
  comparing its size — a truncated upload is the failure that looks most like
  success.
- 30-day retention with a prune sweep, so the backups age under the same
  obligation as the data inside them.
- **A restore is rehearsed monthly and has actually been carried out.** This is
  new, and it is the part that turns a backup from a hypothesis into a copy. It
  restores into a scratch database, checks the credit ledger came back, checks
  its running balance still reconciles against `balance_after_paise`, and drops
  the scratch copy.
- I proved it in both directions locally: 24 ledger rows and ₹16,263.52 of
  movement went out and came back intact; then I deleted three rows from the
  middle of a dump and re-ran — **every row count still read "ok", and only the
  reconciliation check caught it.** A row count alone would have passed that
  corrupted backup.
- The result is recorded and read by `/privacy/readiness`, which reports the
  newest *attempt* rather than the newest success.

### What is still exposed

These are the four I would put on a board slide.

**4.1 — Recovery point is 24 hours.** There is no WAL archiving and no
point-in-time recovery anywhere in this stack. A database failure at 17:00
loses that day's calls, costings and top-ups. The ledger is the only record of
what customers paid, so that is not merely inconvenient — it is money you
cannot reconstruct, against invoices you have already issued. **This is the
single largest gap in the platform.** Managed Postgres with PITR (RDS,
CloudSQL) closes it in an afternoon of configuration and I would do it before
almost anything else on this page.

**4.2 — Recovery time is unmeasured.** Nobody has timed a restore into a
serving deployment. The rehearsal proves the data comes back; it does not
prove how long it takes to be answering calls again. Until somebody measures
it, any RTO you quote a customer is invented.

**4.3 — The backups live beside the data they protect.** They are written to a
prefix (`backups/postgres`) inside the same object store, under the same
credentials, in the same account as the call recordings. A compromised or
deleted bucket takes the database and its backups together. Ransomware and a
mistaken `aws s3 rm` both have this shape. Copy nightly to a second account or
provider with write-once retention.

**4.4 — A rotation can strand old backups.** A dump is restorable only while
the key it was written under is still configured. The rotation runbook says so,
but the coupling is real: restore-test an old dump *before* dropping a previous
key, or accept that everything older than the rotation is gone.

### What I would do, in order

1. Managed Postgres with PITR. Turns a 24-hour RPO into minutes.
2. Cross-account backup copy with object lock.
3. Time one full restore-to-serving and write the number down.

---

## 5. The gates before you take a customer

Only you can close these — they are outside the repository.

- [ ] A ₹1 live top-up, end to end, with the credit landing in the ledger.
- [ ] Razorpay webhook registered, reachable, and secret matched.
- [ ] Knowledge base either working or switched off in the UI.
- [ ] `MPS_API_URL` documented and pointed at something real, or its three
      dependent features retired.
- [ ] `/superadmin` billing readiness and `/privacy/readiness` both clear.
- [ ] One restore rehearsal run against production backups
      (`python -m scripts.rehearse_restore`).
- [ ] A decision recorded on the 24-hour RPO: accept it, or fix it first.

---

## 6. What I could not check from here

Stated so the confidence above is not read as broader than it is.

- Whether `services.decibyl.ai` is actually running. Outbound network from this
  environment goes through a proxy that refuses arbitrary hosts, so both my DNS
  and HTTPS probes were inconclusive. The code dependency is certain; the
  host's status is yours to confirm.
- Anything about the production EC2 box: what is deployed, which environment
  variables are set, whether the ARQ worker is alive. The readiness endpoints
  answer all three from the box itself.
- Real provider behaviour — Razorpay, the carriers, the model vendors. Every
  test here stubs them at the boundary.
