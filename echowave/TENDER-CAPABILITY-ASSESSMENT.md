# Technical Capability Assessment

**Outbound conversational voice platform — agricultural advisory campaign**
Internal engineering review. Responds to §14 of the requirement document.

---

## 0. The number that changes the design

The requirement is **not** 80,000 calls. It is **80,000 unique farmers, up to 3
attempts each, 50,000 successful connections, inside 7 days.**

That fourth figure is a hard SLA and everything else follows from it.

```
Cumulative reach needed      50,000 / 80,000  =  62.5%
Reach after 3 attempts       1 − (1−p)³       =  0.625
Required per-attempt connect p                =  27.9%
```

At that connect rate:

| | |
|---|---|
| Attempt 1 | 80,000 calls |
| Attempt 2 | 57,700 calls |
| Attempt 3 | 41,600 calls |
| **Total dial attempts** | **≈ 179,000** |
| Successful connections | 50,000 |
| Calling window | 7 days × 10 h = **70 hours** |
| **Attempts per hour** | **2,560** (0.71/sec) |

**The 50,000-connection SLA is only reachable because of the retry policy.**
A single-attempt campaign at 27.9% reaches 22,300 farmers — less than half the
requirement. Retries are not a nice-to-have here; they are 55% of the delivered
outcome. That makes §5 the highest-risk requirement in the document, and §5 is
where our largest gap is (see 2.2).

### Concurrency this implies

Assumes 30 s ring-out on an unanswered attempt, 8 s ring + 150 s conversation
on a connected one. Conversation length is the biggest single assumption —
§9 describes intro → advisory → farmer Q&A → contextual answer → confirmation
→ summary, which is not a 60-second call.

| | Average | Peak (1.5×) | Provision |
|---|---|---|---|
| SIP channels | 47 | 70 | **100** |
| Concurrent AI conversations | 30 | 45 | **60** |
| Conversation minutes | — | — | **125,000** |

Provisioning includes Erlang-B headroom for <1% blocking. Answer rates are not
flat across a 10-hour rural day — early morning and evening carry the load —
so the peak multiplier is real, not padding.

**Sensitivity.** If average conversation runs 210 s rather than 150 s,
concurrent conversations go from 30 to 42 average / 63 peak, and minutes from
125,000 to 175,000. Call length must be capped in the workflow design.

---

## 1. Architecture review — what exists today

The platform is a fork we own outright. Backend is FastAPI + SQLAlchemy async
on PostgreSQL 16 with pgvector; Redis/ARQ for queues and background work;
pipecat for the media pipeline; Next.js dashboard. Providers sit behind
factories, so telephony, STT, LLM and TTS are swappable per organisation.

Directly relevant subsystems:

| Subsystem | Location | State |
|---|---|---|
| Campaign engine | `api/services/campaign/` | orchestrator, dispatcher, rate limiter, circuit breaker, source sync |
| Retry machinery | `campaign_orchestrator.py`, `queued_runs` | attempt counting, parent linkage, `scheduled_for` |
| Media pipeline | `api/services/pipecat/` | streaming STT/LLM/TTS, VAD, barge-in |
| Conversation graph | `api/services/workflow/` | node types, extraction variables, transitions |
| Knowledge base | `knowledge_base_documents`, `document_chunks` | pgvector 1536-dim, cosine index, exposed as an LLM tool |
| Concurrency control | `api/services/call_concurrency.py` | Redis counters, org-wide + per-campaign scope |
| Reporting | `api/services/reports/`, `call_cost_items` | daily report, per-run report, CSV export |
| Privacy/security | `api/services/privacy/`, `auth/mfa.py` | retention, erasure, access log, readiness, TOTP |

**Assessment: the architecture is right for this workload.** The campaign
engine already separates *what to dial* (queued runs) from *when to dial it*
(orchestrator batches under a rate limiter and a schedule window) from *what
happens on the call* (workflow graph). That separation is what makes the
gaps below small changes rather than redesigns.

---

## 2. Feature gap analysis

Requirement by requirement, against the code as it stands.

| § | Requirement | Status | Note |
|---|---|---|---|
| 4 | Campaign scheduling, queue mgmt | **Built** | state machine, batching, rate limiter |
| 4 | Pause / resume | **Built** | `paused` state, orchestrator honours it |
| 4 | 09:00–19:00 calling window | **Built, one flaw** | see 2.1 |
| 5 | Max 3 attempts | **Built** | `max_retries` default 2 = 3 attempts |
| 5 | **Intelligent retry intervals** | **Gap — critical** | see 2.2 |
| 5 | Stop after success | **Built** | retry fires only on not-connected terminal states |
| 5 | Retry on mid-call disconnect | **Gap** | see 2.3 |
| 6 | Streaming ASR, natural conversation | **Built** | pipecat streaming pipeline |
| 6 | Barge-in / interruption | **Built** | VAD turn strategies; server-side VAD on realtime models |
| 6 | Multi-turn, context retention | **Built** | workflow graph + context composer |
| 7 | Telugu + dialects | **Built, unvalidated** | see 2.6 |
| 7 | Mid-call language switching | **Built** | `LanguageFollower`, hysteresis on 2 confirmations |
| 8 | Editable knowledge base | **Built** | upload → chunk → embed → retrieve as an LLM tool |
| 9 | Call flow incl. summary | **Built** | node graph, extraction variables, run report |
| 10 | Recordings, transcripts, duration | **Built** | |
| 10 | Connection / completion / retry / language stats | **Gap — data exists, report does not** | see 2.4 |
| 11 | Encryption in transit | **Built** | TLS throughout; credentials Fernet-encrypted at rest |
| 11 | **Encryption at rest (DB, recordings)** | **Gap — unverified** | see 2.5 |
| 11 | Audit logging | **Built** | every recording/transcript access logged |
| 11 | Secure authentication | **Built** | TOTP MFA with replay protection |
| 11 | Secure data deletion | **Built** | erasure by phone number, verifiable, irreversible |
| 12 | High concurrency, horizontal scaling | **Built, untested at scale** | see 2.7 |
| 13 | Core logic internally owned | **Fully satisfied** | see section 3 |

### 2.1 Calling window is enforced per batch, not per dial

`_is_within_schedule()` gates whether the orchestrator *schedules a batch*.
A batch scheduled at 18:58 can still place calls after 19:00.

Why it matters beyond tidiness: telemarketing hour restrictions are legally
enforced in India, and "our batch scheduler allowed it" is not a defence. The
fix is a window check at dial time in the dispatcher, not only in the
orchestrator.

**Effort: 2 days.**

### 2.2 Retry intervals are flat, and would miss the SLA

This is the finding that matters most in this document.

`retry_delay_seconds` defaults to **120**, applied identically to every
attempt. Three attempts therefore land inside roughly four minutes.

If a farmer's phone is switched off, out of coverage, or he is in a field away
from the handset — the dominant failure modes in rural Telangana — **all three
attempts fail for the same reason within four minutes.** The retry policy
consumes its budget without meaningfully re-sampling the farmer's
availability.

The consequence is quantifiable. Retries in a flat-120s schedule are close to
statistically dependent, so effective reach collapses toward the
single-attempt figure of ~22,300 — **against a contracted 50,000.** The
platform would report three dutiful attempts per farmer and still breach the
SLA.

What is needed is day-part spreading:

| Attempt | Interval | Rationale |
|---|---|---|
| 1 | — | as scheduled |
| 2 | +3–4 h, different day-part | re-sample availability, not the same minute |
| 3 | next day, different day-part again | a phone off all Tuesday morning may be on Wednesday evening |

The mechanism already exists — `queued_runs.scheduled_for` is honoured, and
`retry_count` and `parent_queued_run_id` are tracked. **Only the policy is
wrong.** This is a small change with a very large effect on delivered outcome.

It must also be window-aware: a +4 h retry computed at 18:00 lands at 22:00,
outside the permitted window and outside the law. Retries need to snap to the
next available slot.

**Effort: 4 days including the window interaction and tests.**

### 2.3 A call that drops mid-conversation is never retried

Retry is published only from `TERMINAL_NOT_CONNECTED_STATUSES`. A call that
connects and then drops before completion takes the connected branch and is
counted as handled.

§5 explicitly requires retry when "the call disconnects before completion."
Needs a completion test — advisory delivered, or a minimum turn count — to
distinguish a finished call from a dropped one.

**Effort: 3 days.**

### 2.4 The campaign report does not exist as a report

Every figure §10 asks for is in the database: `answered_at`, `ended_at`,
`language`, `call_disposition_codes`, `retry_count`, `parent_queued_run_id`,
recordings, transcripts, cost items. There is a daily report, a per-run
report, and a campaign CSV export.

What does not exist is the single artefact the department will actually ask
for: **connection rate, completion rate, retry statistics, language
distribution and daily progress, per campaign, on one screen and in one
export.**

This is assembly, not new capability, but it is the deliverable the client
judges the platform by day to day. It should not be left to the end.

**Effort: 5 days including the dashboard view.**

### 2.5 Encryption at rest is claimed but not evidenced

Provider credentials are Fernet-encrypted and the recordings bucket is
private. Beyond that, **there is no check anywhere that the database volume or
the object store is encrypted**, and the privacy readiness endpoint — which
exists precisely to answer "are we compliant" — has no at-rest check among its
fourteen.

This is the one §11 clause we currently cannot evidence to an auditor. Needs
verified encryption (RDS/EBS encryption, S3 SSE-KMS) plus a readiness check
that reports it, so the answer comes from the system rather than from memory.

**Effort: 2 days.**

### 2.6 Telugu is supported; Telangana Telugu is unproven

Indic STT/TTS is wired and mid-call language following works. Nobody has
validated **Telangana dialect** output with a native listener on real calls.

A generic Telugu voice that reads as coastal Andhra to a Telangana farmer is
not a defect any test will catch, and it is exactly the thing a government
advisory campaign gets judged on. This is calendar time and a native
reviewer, not engineering.

**Effort: not code. 20 real calls, one local listener, before commitment.**

### 2.7 It has never been run at this scale

Concurrency control, the from-number pool and the circuit breaker are all
built and correct in design. The platform has **not been load tested**, and
has no production history at 60 concurrent conversations.

Everything else in this document is a known quantity. This is the honest
unknown, and no amount of code review substitutes for a load test.

**Effort: 5 days to build the harness and run it.**

---

## 3. Platform ownership (§13)

This requirement is fully satisfied, and it is the strongest part of our
position.

**Internally owned:** campaign engine and orchestration, retry logic, queue
management, conversation graph and execution, knowledge base ingestion and
retrieval, dashboard, reporting, cost and billing engine, privacy and
retention controls, concurrency management.

**Third-party, and pluggable:** telephony carriage (Twilio, Plivo, Telnyx,
Vonage, Cloudonix, ARI all implemented behind one provider interface), STT,
LLM, TTS, cloud hosting.

No business logic sits in a vendor's product. Any single provider can be
replaced by configuration without touching orchestration — which is what §13
is actually testing for, and it is why a provider price change or outage is a
switch rather than a rebuild.

---

## 4. Missing modules — consolidated

| # | Module | Priority | Effort |
|---|---|---|---|
| 1 | Day-part retry scheduler, window-aware | **Critical — SLA** | 4 d |
| 2 | Campaign report (§10 metrics, one view + export) | **High — client-facing** | 5 d |
| 3 | Load test harness + first full run | **High — the unknown** | 5 d |
| 4 | Mid-call disconnect detection and retry | High | 3 d |
| 5 | Dial-time calling window enforcement | High — legal | 2 d |
| 6 | Encryption-at-rest verification + readiness check | High — §11 evidence | 2 d |
| 7 | Migration to `ap-south-1` (Mumbai) | **Critical — data residency** | 5 d |
| 8 | Number pool sizing and warm-up | Medium | 2 d |
| 9 | Scheduled scaling — media fleet off outside the calling window | **Critical — the bid depends on it** | 2 d |
| | **Subtotal** | | **30 d** |
| | Contingency (20%) | | 6 d |
| | **Total** | | **36 engineer-days** |

≈ **7 weeks with one engineer, 4 weeks with two.**

**Item 9 exists because of the price.** Running the media fleet for a calendar
month to serve 70 hours of calling is ₹0.91L of pure waste on a campaign
quoted at ₹5.75L — it is the difference between a viable bid and a marginal
one. It must be built before the campaign, not during it.

**Calendar dependencies that run in parallel and may dominate:** Plivo India
KYC, DLT/entity registration for outbound voice, and carrier provisioning of
~100 channels. These are weeks of external process, not engineering, and they
start now or they become the critical path.

---

## 5. Implementation roadmap

**Phase 1 — SLA and legality (weeks 1–2).** Day-part retry scheduler.
Dial-time window enforcement. Mid-call disconnect retry. These three decide
whether 50,000 connections is achievable and whether the campaign is lawful.
Nothing else matters if retries stay flat.

**Phase 2 — Infrastructure and evidence (weeks 2–4, parallel).**
`ap-south-1` migration. Scheduled scaling of the media fleet. Encryption at
rest, verified and reported. Number pool sizing. Carrier KYC in flight
throughout.

**Phase 3 — Reporting (weeks 3–5).** Campaign report and dashboard view.
Delivered before the pilot, so the pilot is reported through the same artefact
the campaign will be.

**Phase 4 — Proving it (weeks 5–6).** Load test to 60 concurrent
conversations sustained. Telugu dialect validation on real numbers. Pilot of
5,000 farmers in one mandal, measuring the actual per-attempt connect rate.

**The pilot is the deliverable that de-risks everything above**, because it
replaces the one assumption we cannot compute — the true connect rate — with a
measurement. Every capacity and outcome figure in this document scales off it.

---

## 6. Scalability assessment

**Verdict: the architecture scales to this campaign. The deployment does not
yet, and neither is proven.**

Sound today:
- Redis-backed concurrency, org-wide and per-campaign, so one campaign cannot
  starve another
- From-number pool with acquisition and release, needed to spread 179,000
  attempts across numbers without spam-flagging
- Circuit breaker pausing a campaign on a failure-rate spike
- Explicit DB pool (20 + 20 overflow), sized deliberately after finding the
  framework default of 15 would have silently queued calls under load
- Stateless API and workers — horizontal scaling is a matter of instance count

Needs attention:
- **Connection budget.** 12 processes × 40 connections = 480 to Postgres. Fine
  on a 32 GB instance (~3,600 limit), tight on 8 GB (~900). Either size the
  instance for it or put pgbouncer in front. This is the failure that looks
  like a slow model rather than an exhausted pool.
- **Number pool depth.** 100 channels at ~2,560 attempts/hour needs enough
  distinct numbers that no single CLI carries an implausible call rate.
- **No load test.** Stated once more because it is the only genuinely unknown
  item on this list.

---

## 7. Infrastructure recommendation

**Region: `ap-south-1` (Mumbai). Non-negotiable.** Farmer personal data for a
government department must not sit in Virginia — it is a DPDP cross-border
question we should never have to argue.

| Component | Specification | Purpose |
|---|---|---|
| API + media | 3 × c6i.2xlarge behind ALB | 24 vCPU; ~60 concurrent at ~50% utilisation |
| Background workers | 2 × c6i.large | ARQ: retries, reports, post-call work |
| Database | RDS PostgreSQL 16 + pgvector, db.r6g.xlarge, Multi-AZ, encrypted | calls, campaigns, KB vectors |
| Cache / queue | ElastiCache Redis, cache.m6g.large, Multi-AZ | concurrency counters, number pool, ARQ |
| Object storage | S3 with SSE-KMS, lifecycle to retention window | recordings, transcripts |
| Backup | Nightly encrypted dump, restore rehearsed | already built |

Media pipelines are I/O-bound — STT, LLM and TTS are all remote — but carry
real cost in resampling and VAD. Sized at 5–8 concurrent conversations per
vCPU, deliberately conservative.

Multi-AZ on both database and cache: a 7-day campaign against a hard
connection SLA has no room to lose an afternoon.

---

## 8. What this changes about our commercial model

Flagged because it materially changes the deal, though procurement is out of
scope for this document.

Earlier modelling assumed **80,000 attempts per week, recurring**. The
requirement is **80,000 unique farmers, once, inside 7 days** — roughly
179,000 attempts and 125,000 conversation minutes **in total**, not per month.

Two consequences:

1. **Deal size is materially smaller** than previously modelled, and
   per-minute infrastructure cost is roughly double, because fixed
   infrastructure amortises over one campaign rather than a month of them.
2. **The single most important commercial question is whether this repeats.**
   80,000 farmers once is one figure. 80,000 farmers advised through a growing
   season is a different business. The requirement document does not say, and
   the answer changes both the price and whether dedicated infrastructure is
   justified. **Ask before quoting.**

---

## 9. Summary

**Can the platform do this? Yes — the architecture is right, and §13 platform
ownership, the requirement most vendors fail, is our strongest answer.**

Three things stand between here and delivery:

1. **The retry policy would miss the 50,000-connection SLA.** Flat 120-second
   intervals make three attempts behave like one. Small fix, largest
   consequence in this document.
2. **Data residency.** Running Indian farmer data from Virginia is not
   defensible for a government contract.
3. **Nothing has been load tested.** Every other item here is known; this one
   is genuinely open, and only a pilot closes it.

None is architectural. All are inside a 4–7 week window, and the external
carrier processes should start immediately because they, not the code, are
likely to set the date.
