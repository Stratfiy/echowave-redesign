# STATUS — Decibyl platform audit

Read from the code on branch `claude/product-deployment-readiness-2o8hbt`, not
from memory or docs. Every claim below names the file it came from.

**One-line summary:** the platform is more complete than it is *operable*. The
engineering is good and the wiring is not. Nothing here is vapour; several
things are configured wrong in ways that lose money silently.

> **Update — 12 Aug 2026.** The wiring gap this audit led with is now largely
> closed. Payments, recording read-back, docs hosting and inbound calling were
> all verified against production after it was written; the items below are
> marked inline where that happened, with what was actually observed. Anything
> not marked was **not** re-checked and should be read at its original date —
> the point of this document is that every claim names its evidence, and a
> blanket refresh would break that.

> **Update — 27 Aug 2026.** The billing sections were re-read against the code
> on branch `claude/payment-rates-logic-analysis-cb7ktg`, and four money
> defects were found and fixed there — a BYOK quote that did not apply the
> uplift its own invoice applies, bundle margin counting the platform fee as a
> cost, a pinned Razorpay plan ignoring an account's tax treatment, and a
> sub-processor list whose text depended on PostgreSQL's scan order. Items
> marked inline below carry the date. **One entry changed severity rather than
> status and is the thing to read first: cached-token pricing in §2(c) is no
> longer margin-safe.** See `PRICING-FIX-PLAN.md` for what is still open, in
> the order it costs money.

---

## 1. WHAT SHIPS TODAY

### Works end-to-end (verified on a live call this week)

| Capability | Evidence |
|---|---|
| Inbound phone call | Real Plivo call connected, greeted, transcribed Telugu+English code-mixed, LLM replied, agent spoke |
| Agent builder | 10 node types, versioning, draft/publish, visual canvas |
| Web voice widget | WebRTC path, `routes/webrtc_signaling.py` |
| Text chat | `services/workflow/text_chat_runner.py` |
| BYOK credential vault | Encrypted, masked, per-org, tenant-scoped |
| Managed model tier | Platform keys, per-slot managed/BYOK mixing |
| Per-turn latency capture | `call_turn_metrics`, 6-point timeline, p50/p95 in SQL |
| Recordings + transcripts | Stored fine. Read-back was fixed on 12 Aug — `BACKEND_API_ENDPOINT` and `MINIO_PUBLIC_ENDPOINT` were falling back to the root host; `constants.py` now derives the API subdomain the same way the deploy renderer does. **Broken again as of 28 Aug** — see `KNOWN_ISSUES.md` #33. Do not read this row as working |

### Half-built

- **Outbound calls.** The trigger route exists (`routes/public_agent.py:368`)
  and the dispatch path works, but it has never completed a real outbound call
  in this deployment. Untested ≠ broken, but it is untested.
- **Campaigns.** Concurrency limiter, retries, circuit breaker, CSV upload all
  present. Never run against real traffic. No DND scrubbing anywhere in the
  path (see §4) — running one today is a regulatory incident, not a feature.
- **Payments.** Full Razorpay integration: signature-verified webhook,
  idempotent by partial unique index, amount taken from our order not the
  payload. ~~Not configured.~~ **Configured and verified end-to-end (12 Aug):**
  top-up → webhook → credit applied → voucher generated → emailed via Resend,
  landing in the inbox. The 503 path in `routes/payments.py` remains as the
  correct behaviour when the secret is unset.
- **Knowledge base.** Upload path calls MPS unconditionally
  (`tasks/knowledge_base_processing.py:178`). MPS is the upstream vendor's
  hosted service and does not resolve from this deployment. **Document upload
  is broken.**

### Stubbed / absent

- **Call transfer** raises `NotImplementedError` on Plivo, Vonage and Vobiz
  (`providers/plivo/provider.py:547` and equivalents). Twilio and Telnyx only.
  The "transfer to human" node will fail on your actual carrier.
- **Managed telephony** gated off by `MANAGED_TELEPHONY_ENABLED` (default
  false). Deliberate — the reseller account does not exist yet.
- **Auto-recharge / UPI mandate.** ~~No code at all.~~ Built since:
  `services/billing/mandates.py`, with `REQUIRE_MANDATE_FOR_NUMBERS` defaulting
  to true in `constants.py:197`. **Blocked on Razorpay**, not on us — the
  Subscriptions product is a separate approval and the account does not have it
  yet. Manual top-up until then.

### Auth, tenancy, dashboard

- **Auth:** dual-mode. `AUTH_PROVIDER=local` (JWT) or Stack Auth. API-key auth
  takes precedence in `get_user` (`services/auth/depends.py:36`). Sound.
- **Tenancy:** genuinely good. `organization_id` filtering is a documented,
  enforced convention (`api/AGENTS.md`), and the credential vault, rate card
  and runs are all org-scoped. This is the strongest part of the codebase.
- **Dashboard:** billing overview, per-call receipts, latency p50/p95, token
  counts, unit economics, campaign progress. All real, all reading real tables.
- **Admin:** superuser-gated, and `scripts/grant_superuser.py` is the only way
  in on a Docker install.

---

## 2. BILLING

### The path, traced

```
call ends
  → event_handlers.py:437  enqueue PROCESS_WORKFLOW_COMPLETION (ARQ)
  → tasks/workflow_completion.py:29  run integrations (QA can add token usage)
  → tasks/workflow_completion.py:39  cost_completed_workflow_run
  → billing/costing.py  resolve rates in force at call time
  → billing/usage.py    usage_info → UsageItems
  → billing/cost_engine.py  compute_call_cost (pure)
  → release reservation, write CallCostItems, debit CreditLedger
```

**Metering accuracy: good.** Better than most of what I've seen.

- Money is integer paise throughout, `round_half_up_div` with no float step
  (`money.py:77`) — banker's rounding is explicitly rejected as wrong for money.
- Quantities are raw units (seconds, characters, tokens); callers never
  pre-divide, so there is exactly one rounding step per line.
- `total_charged_paise` is *defined* as the sum of rounded line items, so an
  invoice always reconciles against itself.
- Platform fee bills on `pulse_seconds` (default 60 = whole-minute), so
  per-second billing is one parameter, not a second code path.
- Costing is idempotent on `costed_at`; recosting deletes the prior debit
  rather than stacking.

### Who pays for failed/abandoned calls

`billable_seconds` = `usage_info.call_duration_seconds` — **connected** time
only (`usage.py:171`). A call that never connects has no duration, so no
platform fee. Correct.

But: STT/LLM/TTS usage is recorded independently of connection. A call that
connects for 2 seconds and drops still bills whatever the pipeline consumed.
Small, and arguably right, but it means **your cost floor per attempt is not
zero**.

### Rate card — this is where the money leaks

Three distinct problems, in severity order:

**(a) `DEFAULT_RATES` was never loaded, and the loader could not be run.**
`scripts/seed_provider_rates.py` exists and documents
`docker compose exec api python -m scripts.seed_provider_rates`. That command
failed with `No module named scripts.seed_provider_rates` because the Dockerfile
copies only an explicit list of scripts and this one was never added. Fixed this
session (`6beacd2`); **the seed has now been run** and the dashboard shows real
costs. Before that, every call on every deployment reported ₹0 provider cost and
100% margin.

**(b) `stt:openai` still has no rate.** ~~Visible in the UI banner right now.~~
**Closed, 27 Aug** — not by adding a rate but by making the gap a decision
somebody had to type. `tests/test_every_service_is_priced.py` enumerates every
service class the pipeline factory can build and fails on any that is neither
priced nor listed in `_UNPRICED_BY_DESIGN` with a reason. OpenAI transcription
is listed there ("Whisper; we price OpenAI language models and synthesis only").
A silent gap became an audited one, and a new provider cannot be added without
answering the question.

**(c) Cached LLM tokens are billed at full rate.** `cached_tokens` is captured
in `pipeline_metrics_aggregator.py` and serialised into `usage_info` as
`cache_read_input_tokens`; `usage.py` computes `prompt_tokens +
completion_tokens` and never reads it back. Cached input is typically 10% of
the list price.

> **Severity changed, 27 Aug. This is no longer margin-safe.** The sentence
> above was written when provider cost was passed through at cost, which made
> an overstated vendor figure our own reporting error. Provider lines now carry
> the managed markup (`cost_engine.MARKED_UP_COMPONENTS`), so the customer pays
> `vendor_cost × markup` — and every cached token inflates the vendor cost the
> markup is applied to. **We are overcharging on cached input at 1.3x**, not
> merely mis-reporting it. The conditional in the original text ("if you ever
> pass provider cost through at cost you will overcharge customers") has come
> true by a different route than the one it anticipated.
>
> The fix is not a one-liner: the LLM rate card holds a single input/output
> *blend* per model, so pricing cache reads separately needs a second rate on
> the card. That is a pricing decision, not a code change.

### Zero balance

Genuinely well designed, and the reasoning in `reservations.py` is correct:
a balance check alone is useless because a call's cost is unknown until it ends,
so ₹10 could start fifty concurrent calls.

- A call **reserves** an estimate before starting, as a real negative ledger row
  under a per-org row lock.
- Released in full at costing and replaced by actual usage.
- Stale reservations swept (`RESERVATION_MAX_AGE_MINUTES`, default 180).
- `BALANCE_ENFORCEMENT_ENABLED` defaults **true**.
- Fails **open** on DB error — deliberate, documented.

**The gap: there is no mid-call enforcement.** Nothing re-checks balance while a
call is running. A call that starts with a valid 5-minute reservation and runs
longer is not interrupted. The only backstop is
`DEFAULT_MAX_CALL_DURATION_SECONDS = 300`
(`schemas/workflow_configurations.py:5`), which is per-workflow and
customer-editable. **A customer who raises their own max duration can outrun
their balance.** Bounded, but real.

---

## 3. COST PER MINUTE

### Every paid external call per conversation

| Component | Provider (default managed) | Model | Billing unit | Rate on file |
|---|---|---|---|---|
| STT | Sarvam | `saarika:v2.5` | per minute | ₹30/hr = ₹0.50/min |
| LLM | Google | `gemini-2.5-flash` | per 1k tokens | blended ₹4/₹16 per 1M |
| TTS | Sarvam | `bulbul:v2` | per 1k chars | ₹1.50/1k |
| TTS (alt) | Rumik | `mulberry` | per 1k chars | ₹0.50/1k |
| Realtime | OpenAI | `gpt-realtime-2` | per minute | in `realtime_rates.py` |
| Embeddings | OpenAI | `text-embedding-3-small` | per 1k tokens | priced |
| Telephony | Plivo | — | per minute | ₹0.60/min (see note) |

**Worked example, 3-minute call, ~1200 characters spoken, ~2500 tokens:**

```
STT   sarvam                ₹1.50
TTS   bulbul:v2             ₹1.80    ← 53% of AI cost
LLM   gemini flash          ₹0.12
                            -----
AI subtotal                 ₹3.42
Telephony (plivo)           ₹1.80
                            -----
Provider cost               ₹5.22
```

**TTS is your single largest AI line — bigger than STT, ~15× the LLM.** If you
are on `bulbul:v3` (₹3.60) rather than `v2`, you are paying double on your
biggest line for a beta model. Rumik Mulberry at ₹0.50/1k takes AI cost to
₹2.22 — a 35% cut — but is **Hindi and English only**, so it cannot serve a
Telugu agent.

Telephony note: `default_rates.py:373` deliberately quotes Plivo's higher
₹0.60/min rather than the ₹0.34 SIP rate, and flags that the tender model
assumed ₹0.25/min. **That assumption is wrong by 2.4×.** Telephony is the
largest single line in a cheap stack and the easiest to under-budget.

### Per-call cost logging — YES

Fully implemented. `CallCostItemModel` holds itemised lines per run
(component, provider, model, units, unit rate, cost). Totals snapshot onto the
run. Uncosted usage is reported *as uncosted*, never silently priced at zero,
and logged loudly (`costing.py:135`). This is better than most commercial
platforms.

### TTS caching for static phrases — NO

There is **no TTS output cache**. Grepped `cache` across `service_factory.py`:
nothing. Every greeting, every "please hold", every confirmation is
re-synthesised and re-billed on every single call.

There *is* a pre-recorded audio option per node
(`greeting_type: "audio"` + `greeting_recording_id`,
`pipecat_engine.py:696`), but it is manual — the operator has to record and
upload a file. Nothing caches synthesised output automatically.

**This is the cheapest available saving you are not taking.** Your greeting plus
disclosure is roughly 150 characters. At ₹1.50/1k that is ~₹0.22 per call, ~6%
of AI cost, on text that is byte-identical every time. At 1,000 calls/day that
is ₹220/day for synthesising the same sentence.

### Prompt caching — NOT ENABLED

No `cache_control` anywhere (grepped across `api/`). `cached_tokens` is *read*
from provider responses but never *requested*. So:

- **Full history is resent every turn.** Standard for this architecture, but it
  means token cost grows quadratically with turn count.
- Gemini Flash does implicit caching, so you may be getting some benefit
  unmeasured — but nothing in the code asks for it, and nothing prices it.

### STT during silence and agent speech

- **During agent speech: muted.** `CallbackUserMuteStrategy` with
  `engine.should_mute_user` (`run_pipeline.py:866`). Good — this prevents both
  self-transcription and paying to transcribe your own bot.
- **During silence: streamed.** Sarvam STT is a persistent WebSocket; audio
  flows continuously. Since Sarvam bills **per minute of connection**, not per
  byte, silence costs the same as speech. **You pay for the whole call
  duration in STT regardless of how much anyone says.**

### VAD / endpointing

`SileroVADAnalyzer(params=VADParams(stop_secs=0.2))` at
`run_pipeline.py:222` and `:868`. Deepgram Flux path uses
`eot_threshold: 0.7`, `eager_eot_threshold: 0.5`
(`service_factory.py:214`). Smart-turn stop seconds is configurable per run.

0.2s is aggressive — good for latency, will cause interruptions on slow or
hesitant speakers, which is exactly the SMB customer demographic. Worth tuning
against real calls.

### Max call duration

`DEFAULT_MAX_CALL_DURATION_SECONDS = 300` (5 min), enforced in
`pipeline_engine_callbacks_processor.py:27`, overridable per workflow. A cap
exists, which is the important part.

---

## 4. MVP GAP

### Will break under real traffic or lose money

Ranked. Numbers are dev-days for one person.

**P0 — must ship before a paying customer**

| # | Item | Why | Days | Status |
|---|---|---|---|---|
| 1 | Configure Razorpay | Code is done; 503 on every top-up. Nobody can pay you | 0.5 | **Done** — top-up → credit → voucher → email verified 12 Aug |
| 2 | Fix MinIO signed URLs | `SignatureDoesNotMatch` — recordings and transcripts unreachable. The product's whole value is hearing what the agent said | 0.5 | **REGRESSED — reopened 28 Aug.** Run Preview returns "Failed to generate signed URL" for recording *and* transcript on live traffic. The 12 Aug fix was the host fallback in `constants.py`; this is that again or a second cause behind the same message. See `KNOWN_ISSUES.md` #33 |
| 3 | DND list + calling hours | No DND scrubbing anywhere. TRAI/TCCCPR exposure the moment you dial someone who isn't you. 9am–9pm window also absent | 3 | Open — **the one P0 left** |
| 4 | Rate for `stt:openai` | Undercosted calls today | 0.25 | **Closed** — now a declared gap with a reason, enforced by `test_every_service_is_priced.py` (27 Aug) |
| 5 | Mid-call balance enforcement | Customer raises their own max duration and outruns their balance | 2 | Open — **bounded, not fixed.** `MAX_CALL_DURATION_SECONDS = 1200` now caps what a workflow can set, so the overrun is at most 20 minutes rather than unbounded |

**P1 — before ten customers**

| # | Item | Why | Days |
|---|---|---|---|
| 6 | TTS cache for static phrases | ~6% of AI cost, pure waste, byte-identical text | 2 |
| 7 | Cached-token pricing | **Customers are overcharged at 1.3x on cached input** — see §2(c). Was filed here as a reporting error; the markup on provider lines made it a real one | 1 |
| 8 | Fix knowledge base (MPS) | Document upload broken; feature is advertised | 2 |
| 9 | Call transfer on Plivo | `NotImplementedError` on your actual carrier | 3 |
| 10 | Post-call summary + outcome | Owners will not read transcripts | 2 |
| 11 | Agent readiness check | Every failure this week was config with a log the customer will never see | 2 |

**P2 — can wait**

| # | Item | Days | Status |
|---|---|---|---|
| 12 | Prompt caching / history truncation | 3 | Open |
| 13 | ap-south-1 migration (~250ms RTT saving) | 1 | Not re-checked |
| 14 | Auto-recharge / UPI mandate | 5 | Built; blocked on Razorpay Subscriptions approval |
| 15 | Outbound at scale (untested) | 3 | Single outbound and inbound calls now verified; **at scale still untested** |
| 16 | `docs.decibyl.ai` (Mintlify unpointed) | 0.25 | **Done** — live on Astro Starlight, 136 pages, link check clean |

### Honest totals

Originally: P0 ~6.5 days, P0+P1 ~19, everything ~31.

As of 12 Aug, items 1, 2 and 16 are done and 14 is blocked externally rather
than unbuilt. **DND scrubbing and calling hours (item 3) is the only P0 left**,
and it is the one that carries regulatory exposure rather than lost revenue —
which makes it the gate on dialling anyone who has not asked to be called.

Add 40% for the unknowns a first cohort finds. **Call it 9 days to first paying
customer, 27 to ten of them**, single developer.

### Things that will bite that aren't on the list

- **Costing runs in the ARQ worker.** If that worker dies, calls complete
  normally and are never costed — free calls, silently, indefinitely. A liveness
  check exists; make sure it pages someone.
- **Telephony seconds arrive on the carrier's status callback, which races
  costing.** `usage_info` merges, so ordering usually works out, but a callback
  arriving after costing leaves carriage off that receipt permanently unless
  recosted. Worth a scheduled recost sweep.

  *Re-checked 27 Aug, and it is worse than "worth a sweep": there is no sweep
  that would find it.* `scripts/recost_uncosted_calls.py` deliberately scopes
  itself to runs whose `uncosted_usage` is a non-empty list — a known, flagged
  gap. Carriage that arrived **after** costing leaves no such flag: the usage
  item was simply not there when the receipt was written, so nothing marks the
  run. Nothing in `tasks/arq.py` sweeps for it either. The detector is a costed
  run whose `usage_info` carries managed telephony seconds with no `telephony`
  row in `call_cost_items`, and it does not exist yet. Carriage is routinely a
  third of what a call costs, so each miss is a third of a receipt.
- **The class of bug that dominated this week** is a value present in one place
  and absent from its pair: tiers pointing at retired models, Decibyl's language
  vocabulary not translated for Sarvam, a provider registered but not
  validatable, a script written but not shipped. Four instances in one week.
  Three now have completeness tests; the pattern is the risk, not any one
  instance.
- **`decibyl-hq/decibyl` GitHub links in `docs.json`** point at an org that may
  not exist publicly. Cosmetic, but a tender reviewer will click it.

### What is genuinely good

Worth saying, because the list above is unrelenting. The money code is careful —
integer paise, one rounding step, invoices that reconcile against their own line
items, idempotent costing, reservations under a row lock with a documented
justification. Tenant isolation is enforced as convention and honoured. The
per-turn latency instrumentation is better than most commercial platforms
expose. None of that is what is broken.

What is broken is the last mile: config that was never set, a script that was
never shipped, a service that was renamed to a domain nobody registered.
