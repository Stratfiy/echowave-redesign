# Launch checklist — Decibyl, doctor launch

**Written 21 Aug 2026, after taking over from the session that merged #41.**
Everything below was verified against the code at `00c2ace` in a running
container: migrations applied to a fresh database, the price book seeded, and
the estimator called directly. Where a number is measured it says so; where it
rests on an assumption, the assumption is named.

This supersedes nothing. `PRICING-FIX-PLAN.md` is still the plan of record for
pricing and `NEXT-SESSION.md` for the environment. This is the gate list: what
must be true before a doctor pays us.

---

## 0. Where the last session left it

**The pricing work is already in production.** `Deploy` succeeded on `main` at
`00c2ace` on 21 Aug 04:39 UTC, and every deploy runs `alembic upgrade head`.
CI was green on the merge (`c4289a5`). This is not a branch waiting to ship —
it is live, with the commercial flags switched off.

What that PR actually delivered, confirmed in the code:

| | State |
|---|---|
| Telephony double-charge | Fixed — carriage we did not sell records no line |
| Speech-to-speech quoted at a text-token rate | Fixed in `realtime_pricing`; **still broken on the path customers see — see 1.1** |
| Autopay revenue reading 18% high | Fixed — `recurring_charge_periods.charged_paise` is net everywhere |
| No tax document for autopay collections | Fixed — one receipt voucher per collection, keyed on the provider payment id |
| Flash-Lite retirement (16 Oct deadline) | Closed — managed LLM tiers are off Google entirely |
| Rate card / tiers / bundles operator-editable | Built, with screens |
| ₹2,999 starter plan | Backend complete. **No purchase UI — see 2.1** |
| Migration failing on a fresh database | Fixed — verified: 0 → head clean on an empty DB |

**Verification I ran, so nobody repeats it:** 3,835 API tests pass, 10 fail.
All 10 are environmental (6 need `ts_validator` npm deps, 4 are timing tests on
Python 3.11 where the project pins ≥3.13); 9 errors need `moto[s3,server]`.
`tsc --noEmit` is clean. 68 UI vitest tests pass. None of the last three run in
CI — see 3.4.

---

## 1. P0 — the numbers were wrong. **All three are fixed.**

Fixed on this branch, with `api/tests/test_quote_matches_invoice.py` defending
them: a real call through `cost_workflow_run` compared against what
`estimate_cost_per_minute` quoted for the same stack, in every BYOK and add-on
combination, over 40 randomised rate cards, **with both commercial flags forced
on**. Each fix was reverted in turn to confirm the test fails without it.

What it looks like now, against the seeded price book at ₹96/USD with Plivo
carriage:

| | Before | After |
|---|---:|---:|
| Natural (Gemini Live), quoted | ₹2.76/min | ₹9.37/min |
| Premium (OpenAI realtime), quoted | ₹2.76/min | ₹25.79/min |
| BYOK-voice stack, flags on | ₹3.60 quoted / ₹5.04 invoiced | ₹5.04 / ₹5.04 |
| Managed + KB + QA, flags on | ₹8.43 quoted / ₹10.83 invoiced | ₹10.83 / ₹10.83 |

With the flags off — today's production state — **every number is unchanged**.
That is asserted too.

### 1.1 Speech-to-speech was quoted at ₹2.76/min and costs up to ₹18.97 — FIXED

**This is the one that would have cost us money on day one.** Measured, not
inferred:

| Simple picker card | Quoted | Real sell price | Real cost |
|---|---:|---:|---:|
| Natural (Gemini Live) | ₹2.76/min | ₹9.37/min | ₹7.24/min |
| Premium (OpenAI realtime) | ₹2.76/min | ₹25.79/min | ₹18.97/min |

A 3.4× and a 9.3× under-quote, on a customer-facing screen.

**Root cause — two names for one vendor.** `managed_tiers` names realtime
upstreams `openai_realtime` / `google_realtime` (what `service_factory` needs).
The rate card names them `decibylopenairealtime` / `decibylgeminilive` (what
`provider_from_processor` emits from the running pipeline). Nothing bridges the
two on the estimate path, so `resolve_provider_rate` finds nothing, the model
line is dropped into `unpriced`, and the estimate returns telephony + platform
fee alone — ₹2.76 — which reads as a cheap stack rather than as an error.

The invoice prices it correctly, because the pipeline records the `decibyl*`
name. So the quote and the invoice disagree by 9×.

Two things make it silent:

* `agent_options.price_per_minute` and `realtime_price_per_minute` return
  `estimate.total_paise_per_minute` and **discard `estimate.unpriced`**. The
  estimator raises the flag; the caller drops it.
* `bundle_economics` computes margin as price − cost from the same broken
  estimate, so the operator screen shows ₹2.76 sell / ₹2.52 cost / ₹0.24 margin
  on a stack losing ₹15/min.

**The fix.** `estimator._REALTIME_RATE_CARD_NAMES` already held exactly the
mapping needed — it was used for the token assumption and never for the rate
lookup. `rate_card_provider` now normalises the provider before the rate is
resolved, which corrects both halves at once: the token assumption was also
falling through to the 1,400-token text default for every realtime model.

And the failure is now loud. `price_per_minute` refuses to return a price when
`unpriced` is non-empty, because a bundle card with no price is a bug report
and a bundle card with a wrong price is an invoice dispute.

- [x] Map realtime tier providers to rate-card names before the rate lookup — `estimator.rate_card_provider`, which also fixes the token assumption (these models were falling through to the 1,400-token text default)
- [x] Propagate `unpriced` out of `price_per_minute` / `realtime_price_per_minute` — both now return `None` and log which rate is missing; the Simple picker renders "Price unavailable" and `bundle_economics` reports no margin rather than a subtraction
- [x] Fix `_per_minute_line`, found while doing the above: the STT line never passed its model to the rate lookup, so a model-specific transcription rate would be billed and never quoted
- [x] The `test_bundle_economics` fixture seeded realtime rates under the *tier* name, which is why it passed while production was broken. It now seeds through `rate_card_provider`, like the real price book

### 1.2 The estimator did not apply the BYOK uplift it documents — FIXED

`estimate_cost_per_minute`'s own docstring says of a customer-keyed component:
*"the platform fee is uplifted instead."* It is not. The word `uplift` appears
in that file exactly once — in the docstring.

`costing.cost_workflow_run` **does** apply it: `$0.002/min` when the customer
brings transcription, `$0.015/min` when they bring the voice.

So the moment `BYOK_TIERED_FEE_ENABLED=true`, a BYOK-TTS customer would have
been quoted a $0.020 platform fee and invoiced $0.035 — the platform line is 75% higher than
quoted, roughly 20–25% on a full stack. This is the same defect class as the
"wizard quoting 40% under the invoice" bug that PR fixed, in the one path it
did not check.

- [x] Add the uplift to the estimate — both paths now call `billing/fees.py`, a new module that is the only place the uplift and the add-on rates are decided, and is flag-gated there rather than at each call site
- [x] Test that the estimate and the receipt agree for all three tiers (managed / stt / tts)
- [x] `usage.byok_tier_from_components` shares the tier cut with `byok_platform_tier`, so a completed call and a forward estimate cannot land in different tiers

### 1.3 Add-on charges appeared in no estimate and had no label — FIXED

`ADDON_KNOWLEDGE_BASE_MICROS_USD` ($0.005/min) and `ADDON_CALL_QA_MICROS_USD`
($0.020/min) are charged by `costing` and are absent from `estimator` entirely.
On a call using both, that is $0.025/min unquoted — **more than the platform fee
itself**.

And `addon` is not in any `COMPONENT_LABELS` map in the UI. Every one falls
through to `?? line.component`, so the customer's call detail shows a charge
labelled `addon`. `CostPerMinuteBar` groups lines into agent / telephony /
platform, and an add-on line belongs to none of the three — so the bar will not
sum to the total.

- [x] Include add-ons in the estimate — `POST /cost-estimate/per-minute` takes `addons`, returns `addon_paise_per_minute`
- [x] Label `addon` in `CostPerMinuteBar` (now a fourth bar group, so the segments sum to the total), `unit-economics`, and the call detail, which now lists add-on rows
- [x] Two further errors found on the call detail screen while doing it: add-on lines were counted into **provider cost**, inflating what vendors appear to charge us by our own revenue; and the platform fee's units are seconds and were labelled minutes, so a 75-second call read as "75 min"

### 1.4 The pinned Razorpay plan silently discards the gross-up

`mandates._ensure_plan` opens with `if pinned: return pinned`. The caller
carefully grosses ₹2,999 up to ₹3,538.82 against the account's billing profile,
and when `RAZORPAY_STARTER_PLAN_ID` is set — which `constants.py` strongly
recommends — that computed figure is thrown away and the bank collects whatever
amount the pinned plan was created with.

Two consequences, neither of which any code can detect:

* A plan created at ₹2,999 collects **no GST at all**, monthly, by standing
  instruction. The receipt voucher would then split ₹2,999 as if tax-inclusive
  and we would absorb ₹457.47 a month per customer.
* An export customer with an LUT is zero-rated but pays the domestic pinned
  price.

- [x] **Asserted in code**: the pinned plan is read back at mandate creation and compared to the derived gross. A mismatch refuses to subscribe anyone and names the variable to fix; a provider we cannot reach is allowed through, because an outage must not stop signups
- [ ] Still confirm in the Razorpay dashboard that the starter plan reads **₹3,538.82** — the guard stops the damage, it does not create the plan for you (`OPERATOR-RUNBOOK.md` §2)
- [ ] The export case is documented and still unhandled: a pinned plan charges everyone the same, so an LUT account on autopay is over-charged by the GST. Keep them on prepaid, or give them a plan row with its own net-priced provider plan

### 1.5 The exchange rate is a floor, not a rate

`DEFAULT_USD_INR_PAISE = 9600`. Every rate in the price book is quoted in USD
and settled in rupees, so if `usd_inr_rate_history` is empty, **every charge on
the platform settles 7–8% light** against a real ~₹104.

The daily refresh cron is wired (`refresh_exchange_rate`, 02:30 UTC) and the
source is keyless. The risk is the network policy: a fetch that never succeeds
writes nothing, logs a warning, and leaves ₹96 in force forever with no visible
symptom.

- [ ] `SELECT * FROM usd_inr_rate_history ORDER BY effective_from DESC LIMIT 5` on the box — confirm a row from the last 48h
- [ ] If empty, set one manually via the rate card before the first invoice
- [ ] The KPI screen shows the age of the newest rate — put it on someone's morning check

### 1.6 Four carriers are priced at US rates or at nothing real

`default_rates.TELEPHONY_RATES`: Plivo and Twilio are India figures. Telnyx
(`$0.0070`) and Vonage (`$0.0139`) are labelled "US outbound". Cloudonix and
Vobiz carry the comment `PLACEHOLDER at the Twilio India mobile rate. Not the
published price.`

Telephony is now marked up like everything else, so an under-priced base rate
compounds into the sell price rather than just eating margin.

- [ ] Either put real India rates against all four, or remove them from what a customer can select

---

## 2. P1 — built, working, and unreachable

I diffed all 291 API routes against the hand-written UI (excluding the generated
client). Coverage is genuinely good — most of what follows is deliberate
server-to-server. These are the ones that are not.

### 2.1 The ₹2,999 starter plan cannot be bought — FIXED

`GET /billing/plan` and `POST /billing/plan` are complete: mandate creation,
idempotent balance grant keyed on the provider payment id, rent settled in the
same cycle so the monthly job cannot debit it twice, receipt voucher issued.
`plans.py` is careful, well-tested code.

**No screen calls either endpoint.** The generated client has
`getPlanApiV1BillingPlanGet` and `subscribeToPlanApiV1BillingPlanPost`; nothing
in `ui/src` outside `client/` references them.

This is the launch SKU. Everything behind it is done and the customer cannot
reach it.

- [x] Built: **Billing → Plan** lists every plan with its price, the grossed-up figure the bank will actually take, what it grants, and the authorisation hand-off
- [x] **The entitlement is now real.** A plan includes N numbers; number N+1 opens its own ₹499 monthly rental against the balance. Before this, a plan mandate authorised *every* number an account provisioned — the monthly job skipped them all because the bank was collecting, and the collection settled only the lowest-numbered charge, so numbers two and beyond were free for ever with nothing reading as an error. The same hole existed for a plain rental mandate
- [x] **Plans are rows, not constants.** `subscription_plans` table + `/superadmin/billing/plans`, so a second plan needs no release. Refuses a plan granting more balance than it collects — balance is spendable at our cost the moment it lands

### 2.2 Autopay can be started but not cancelled — FIXED

`POST /billing/mandate/cancel` exists and is called from nowhere. The repo's own
test records this: `adminControls.test.ts` lists that route with
`calledFrom: null`.

A customer who wants out has to go to their bank or to Razorpay. For a
subscription sold to clinics, "no way to cancel in the product" is a support
load and a trust problem before it is a compliance one.

- [x] Added to **Billing → Plan**, behind a confirmation that states what happens to the number: it is not released, its rent falls back to the credit balance, and an unpaid rental suspends it after seven days
- [x] Found while wiring it: `cancel_mandate` looked up only the *rental* purpose, so an account on the plan could not cancel at all — the lookup found nothing and the route answered 404 to a customer whose bank was being debited monthly. It now withdraws whichever instruction the account holds, plan first

### 2.3 The go-live readiness report has no screen — FIXED

`GET /admin/billing/readiness` is the best piece of operational code in this
repo. It checks supplier identity, live-vs-test Razorpay keys, webhook
reachability, that every captured payment has a receipt voucher, that the price
book covers what calls actually used, and that the worker is alive. Its module
docstring names the exact failure mode it exists to catch: *the dangerous
failure is the one that produces a plausible number rather than an exception.*

Nothing renders it. `PRODUCTION-CHECKLIST.md` Step 8 says "verify readiness
reports zero blockers" and there is no way to look.

- [x] **Superadmin → Billing → Readiness.** Blockers first, then anything not yet proven, then the standing obligations, then the passes. Each check shows its remedy verbatim — a check that says what is wrong and not what to do about it is one somebody has to go and research
- [x] A fresh install reads *not proven yet*, never *ready*. Rounding an absence of evidence up to a green tick is the exact dishonesty the readiness vocabulary exists to avoid
- [x] The obligations no code can discharge are counted apart from the blockers, because they never clear and folding them in would make the blocker count permanently non-zero and therefore ignored
- [x] "Test the webhook" is a button rather than automatic: it is the only check that proves Razorpay can reach us, and it makes an outbound request on a page that is otherwise cheap to poll

### 2.3b Money moved and nobody was told — FIXED

Two silences, both of which read as nothing going wrong. See
`OPERATOR-RUNBOOK.md` §4.

- [x] **The autopay receipt was issued and never sent.** The webhook route
      enqueued the email off `receipt_voucher_id`, which only the prepaid
      top-up path sets; the collection path nests it under `voucher` and
      reported only a human-readable number, not an id. So every monthly bank
      debit produced a tax document that reached nobody
- [x] **The dunning ladder said nothing at all** — and the schedule itself did
      not flag the day calls stop. `should_warn` was true on days 15 and 25
      only, so a number went silent on day 7 and the customer first heard on
      day 15. Day 7 now warns, and the notice names the number, the amount and
      the way back
- [x] **A pinned Razorpay plan collecting the wrong amount is now refused** —
      see 1.4, which this closes. The mandate path reads the plan back and
      compares it to the derived gross; a provider we cannot reach is allowed
      through rather than blocking signups

### 2.4 Others, in rough order of how much they matter

**Update — 21 Aug, later the same day, on `claude/decibyl-launch-checklist-nxqump`:**
the four money/compliance-relevant rows below are now shipped and merged to
`main` (PRs #49–#52). Each has its screen, and each was fixed with a real
gap found while wiring it up — a carrier-owned number could be hard-deleted
without releasing the rental (leak, now a 409), and `/privacy/metrics` was
scanning every organization's recordings for whichever customer called it
(cross-tenant leak, now scoped). Struck through below; the other three are
unchanged.

| Endpoint | Consequence |
|---|---|
| `POST /admin/billing/rate-card/exchange-rate/refresh` | No button to force an FX refresh — see 1.5 |
| ~~`/admin/telephony/*` (4 routes)~~ | ~~Managed telephony cannot be administered at all~~ — **Superadmin → Billing → account page** now has a Telephony panel (mark/unmark managed) and **Superadmin → Shared outbound numbers**. This is still gated by `MANAGED_TELEPHONY_ENABLED` |
| ~~`POST /managed-numbers/{id}/release`~~ | ~~A number can be bought and not given back~~ — **Telephony configuration page** now shows a Release action for carrier-owned numbers, and hard-delete on one is refused |
| ~~`/admin/partners/…/commission` (GET/PUT), `statements/{id}/issue`~~ | ~~Partner commissions cannot be set and statements cannot be issued~~ — **Superadmin → Billing → account page** (commission panel) and **Superadmin → Partners** (Issue action) |
| ~~`/auth/mfa/enroll｜verify｜disable`~~ | ~~MFA is implemented and cannot be turned on by anyone~~ — **Settings → Security** |
| ~~`/privacy/readiness`, `/privacy/metrics`, `/privacy/breach-report`~~ | ~~DPDP reporting has no screen~~ — **Privacy** page (metrics, breach report) and **Superadmin → Privacy readiness** |
| `/knowledge-base/search` | No way to test what the agent will actually retrieve |
| `model-configurations/v2/migration-preview` + `/migrate` | The migration path exists and cannot be run |

---

## 2b. The managed model catalogue — BUILT

Decibyl manages the providers. A customer's own key is the escape hatch for a
model we do **not** offer, never a second way to buy one we do.

* `platform_models` is the one answer to "what do we sell". Before it, the
  offering was the intersection of three things that could disagree — what the
  registry could build, what a tier pointed at, and what had a rate row — and
  the customer's picker showed every vendor this codebase had ever integrated,
  including ones we hold no key for and had never priced.
* **Superadmin → Provider Keys** now reads each vendor's own model list with the
  key we hold, and an operator ticks what we sell. Falls back to the models this
  codebase knows about when a vendor cannot be reached, and says which it did.
* A model is **sellable** only when it is on sale, keyed, and priced. Anything
  short of that is listed for us with the reason and omitted from the customer's
  picker: an offered-but-unpriced model does not fail, it bills the platform fee
  alone and reports margin we did not earn.
* The customer's price per model comes from `estimator.price_components`, built
  from the same line functions a full estimate uses — a model priced in the
  picker and the same model inside a stack estimate cannot disagree.
* The per-slot "Who provides this model" toggle is **off**
  (`BYOK_SLOT_CHOICE_ENABLED = false`). The mechanism underneath is unchanged
  and stored configurations still resolve, so enterprise can have it back.

Still open: the customer Models screen renders the tier picker rather than the
new catalogue list, so per-model choice reaches a customer only once that screen
is rewired.

## 3. P2 — numbers that are guesses, and what they swing

### 3.1 One unmeasured constant moves the price 58%

Everything rests on TTS characters per connected minute. Three internal
documents said 850, 900 and 2,300; the code ships 2,300. Measured on the Lite
managed stack (Sarvam speech, Plivo carriage, list price, ₹96):

| chars/min | Sell | Cost | What ₹2,500 of plan balance buys |
|---:|---:|---:|---:|
| 850 | ₹5.26 | ₹4.31 | 475 min |
| 900 | ₹5.36 | ₹4.38 | 466 min |
| **2,300 (shipped)** | **₹8.30** | **₹6.48** | **301 min** |

TTS is between 34% and 58% of the sell price and it is the one line that is a
guess. `/superadmin/billing/pricing-inputs` was built to answer exactly this and
has no data until real calls flow.

Two things follow. **`PRICING-FIX-PLAN.md` assumes ₹5.17–8.21/min all-in; the
estimator returns ₹8.30–9.00 today** — at or above the pessimistic end, not
inside the range. And the starter plan's ₹2,500 buys ~300 minutes, which is
parity with Agni's 300 at the same ₹2,999, not an advantage.

- [ ] Run `scripts/pricing/measure.sql` after the first week of real traffic, before quoting anyone a volume price
- [ ] Re-size the plan balance against the answer

### 3.2 Full managed economics as they stand today

List price, ₹96/USD, Plivo carriage, 1.4× markup, 2,300 chars/min:

| Tier | Sell | Cost | Margin | % |
|---|---:|---:|---:|---:|
| Lite (Sarvam) | ₹8.30 | ₹6.48 | ₹1.82 | 22% |
| Normal (GPT-4.1-mini) | ₹8.43 | ₹6.57 | ₹1.86 | 22% |
| Smart (GPT-4.1) | ₹9.00 | ₹6.98 | ₹2.02 | 22% |
| Natural (Gemini Live) | ₹9.37 | ₹7.24 | ₹2.13 | 23% |
| Premium (OpenAI realtime) | ₹25.79 | ₹18.97 | ₹6.82 | 26% |

Cross-check that gives me confidence in the rate card: stripping carriage and
the platform fee gives ₹4.72/min for Gemini Live and ₹16.45/min for OpenAI
realtime — the exact figures `managed_tiers.py` documents from an independent
calculation.

Worth saying out loud: **Premium at ₹25.79/min means a ₹2,500 balance is 97
minutes.** Selling that inside a "starter plan" will generate support tickets.
Consider hiding Premium from the plan, or warning on the card.

### 3.3 The wizard's price excludes carriage

`GET /agent-options` calls `price_per_minute` with no `telephony_provider`, so
the number in the create wizard is ₹7.46/min where the call will cost ₹8.30 —
about 10% light. Defensible (no carrier is chosen yet) but not stated.

- [ ] Either pass the account's default carrier, or label the figure "excludes telephony"

### 3.4 CI does not build or test the UI

`.github/workflows/` at the repo root runs API tests and a format/lint/OpenAPI
drift check. It never runs `tsc --noEmit`, `next build`, or the 68 vitest tests
that exist. A type error or a broken build reaches `main` and is caught on the
deploy box, where `ci_deploy.sh` rolls back — which is a rollback that could
have been a red check.

Both are clean today; I ran them. Nothing keeps them clean.

- [ ] Add `npx tsc --noEmit`, `npm run build` and `npx vitest run` to the drift check

### 3.5 Smaller things, all real

- `cost_engine.py` still carries an inline comment saying *"Telephony is
  excluded: its rate card already holds the sell price"* directly above the line
  that marks telephony up. Same stale comment in `estimator._with_markup`. The
  behaviour is right and consistent; the comments now say the opposite, and this
  file's comments are how everyone reasons about it.
- `constants.py` says `13000 = 1.30x` above a default of `14000`.
- **Calling now stops below ₹20 rather than below zero**, and top-ups are sold
  in steps of ₹100. Zero was the wrong floor: a call's cost is not known until
  it ends, so an account allowed to start on its last rupee finishes overdrawn.
  The floor makes the last call one it could always afford. It strands up to
  ₹20, which is the deliberate trade and is stated on the billing screen, in
  the low-balance email and in the refusal message.
- The plan balance grant **never expires** — `CreditLedgerKind.PLAN` is an
  ordinary credit row. `PRICING-FIX-PLAN.md` flagged expiry-vs-rollover as a
  decision to make explicitly; it has been made by default, in favour of
  rollover forever. Decide it deliberately and say so at purchase.
- The India-processed badge is computed from managed tiers only and trusts a
  one-vendor allow-list (`sarvam`). For a clinic that is a data-residency claim.
  Have the Sarvam DPA on file before it is shown to a doctor.
- `echowave/.github/workflows/` still holds five workflows that GitHub never
  reads (wrong directory level). Release automation is therefore dead.
- The repository root still contains the Emergent scaffold — `backend/server.py`
  (a MongoDB hello-world), `frontend/` (a CRA stub), `memory/`, `tests/`,
  `test_result.md`. None of it is Decibyl. Delete it before anyone new reads
  this repo.

---

## 4. Go-live sequence

Order matters. Each step assumes the one above it.

**Before any customer sees a price**

1. ~~Fix 1.1 (realtime quoting)~~ — done on this branch
2. ~~Fix 1.2 and 1.3 (BYOK uplift, add-ons in the estimate)~~ — done on this branch
3. Verify 1.4 (pinned plan amount) in the Razorpay dashboard
4. Verify 1.5 (a real FX row exists)
5. Seed and check the price book: `docker compose exec api python -m scripts.seed_provider_rates --confirm`, then fix 1.6

**Before the first rupee**

6. Ship 2.3 (readiness screen), then work it to zero blockers
7. Supplier identity set — without `SUPPLIER_LEGAL_NAME` and `SUPPLIER_GSTIN` money is taken and **no tax document is ever issued**. `PRODUCTION-CHECKLIST.md` §2.1
8. Razorpay live keys, webhook configured and reachable
9. One real payment end to end, and confirm the receipt voucher exists

**Before the plan is sold**

10. ~~Ship 2.1 (plan purchase UI) and 2.2 (cancel)~~ — both done
11. Decide balance expiry (3.5) and state it on the purchase screen
12. ~~The docs said "$0.02 per minute, billed per second" when we bill in
    15-second pulses~~ — corrected, and the plan, the ₹20 floor and the ₹100
    top-up steps are documented. Phase 5 of the pricing plan (the tiered BYOK
    fee) is still to land on the same page

**Then, and only then**

13. `BYOK_TIERED_FEE_ENABLED=true` — the quote now follows it
14. `ADDON_BILLING_ENABLED=true` — likewise
15. `MANAGED_TELEPHONY_ENABLED=true` — 2.4's telephony admin screens are now shipped; this is blocked only on the Plivo KYC verdict

**Still blocked on third parties** (unchanged): Razorpay Subscriptions approval,
Plivo KYC, DLT registration for SMS verification.

---

## 5. What I could not check from here

* Production configuration. The proxy resolves `api.decibyl.ai` to `127.0.0.1`;
  every flag value above is the code default, not what is on the box. Steps 3,
  4 and 7 all need someone at a terminal.
* Whether the price book in production matches `default_rates.py`. The seeder
  now exists and the readiness check now looks for real gaps rather than
  `count > 0`, but nobody has run either against production.
* Provider invoices against `provider_cost_paise`. Every cost figure in section
  3.2 is a published list price that was read, not an invoice that was
  reconciled. Until one month is reconciled, the cost side is an assumption and
  so is every margin number on every screen.
