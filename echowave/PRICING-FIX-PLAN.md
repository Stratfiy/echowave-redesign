# Pricing fixes — plan of record

**Written 19 Aug 2026.** Supersedes the "ninety days, in order" section of
`COMPETITIVE-PRICING-STUDY.md`, which was written before three things were
known: that nobody is being billed yet, that the bundles are being built, and
that the rate card lives in the database rather than in the repository.

## The one fact that reshapes everything

**No customer is being billed today.** That removes the entire migration
problem the study assumed: no notice periods, no grandfathering, no
effective-dated rate changes rolled forward across live accounts, no
"customers on the old plan" forever.

It also sets a deadline of a different kind. Every price below is free to
change *today* and expensive to change the day after the first invoice goes
out. The work here is not a migration, it is **getting the number right before
anybody is on it**.

## What is actually built, and where

| | State | Gate |
|---|---|---|
| Add-on billing (KB $0.005/min, QA $0.02/min) | Merged | `ADDON_BILLING_ENABLED=false` |
| BYOK tiered platform fee | **PR #41, unmerged** | `BYOK_TIERED_FEE_ENABLED=false` |
| Number rental ₹499 | Merged, no flag | `MANAGED_TELEPHONY_ENABLED=false` |
| Agent builder | Merged | `AGENT_BUILDER_ENABLED=false` |
| Bundles (₹2,999 / ₹6,999) | **Not built** — no plan or bundle code exists | — |
| Concurrency tiering | **Not built** | — |

Everything that exists is switched off. Today the product charges exactly what
it charged before the study.

---

## Phase 0 — Measure. Blocks everything below.

`scripts/pricing/measure.sql`. Read-only, no PII — counts, rates and
durations. Validated against PostgreSQL 16 with synthetic data, where sections
2 and 5b correctly identified a planted below-cost rate row.

```
psql "$DATABASE_URL" -f scripts/pricing/measure.sql > pricing-measurements.txt
```

Six things come out of it, and four of them are decisions nobody can make
without the numbers:

1. **TTS characters per minute** — median, mean and p90, per model and per
   language. Three internal documents say 850, 900 and 2,300. At $0.015 per 1k
   characters that 2.7× spread is the difference between a healthy margin and a
   negative one. *Every price in this plan is provisional until this returns.*
2. **Rate rows that do not exist** — the query that would have caught the
   ElevenLabs multilingual leak. A missing row bills zero, marks the run
   uncosted, and inflates margin by exactly what it failed to charge.
3. **Uncosted runs** — revenue forgone, already.
4. **Whether the ₹96 FX fallback is live** — if `usd_inr_rate_history` is
   empty, every USD-denominated charge has been settling 7.7% light against a
   real ₹104.
5. **Realised margin per component**, and anything being resold below cost.
6. **Minutes per organisation per month, and peak concurrency** — which is what
   the bundle balances and the concurrency tiers have to be sized against.
   Choosing ₹2,500 of balance without this is guessing.

---

## Phase 1 — The margin defects. One has a hard deadline.

### 1a. Repoint the Flash-Lite tiers — before 16 October

`gemini-2.5-flash-lite` retires **16 Oct 2026, roughly eight weeks out**, and
`api/services/configuration/managed_tiers.py` points **three of the five
managed LLM tiers at it**: `fast`, `lite` and `zen`. On that date they stop
resolving, and every managed customer on those tiers stops talking.

The tier map reads an environment override, so this needs no release:

```
MANAGED_LLM_FAST=google:gemini-3.1-flash-lite
MANAGED_LLM_LITE=google:gemini-3.1-flash-lite
MANAGED_LLM_ZEN=google:gemini-3.1-flash-lite
```

**But the rate rows for the replacement must exist first.** Repointing to a
model with no row in `provider_rates` swaps a broken call for a free one, which
is harder to notice. Do 1b before this, not after.

The replacement lists at roughly 3.7× the blended rate of what it replaces.
That is recoverable through the existing 1.4× managed markup, but only if the
rate row is right.

### 1b. Make the rate card a seeded artefact, not hand-typed rows

`api/services/billing/default_rates.py` holds ~100 carefully-annotated list
prices and is **read by nothing that charges anybody** — it feeds a
`list_prices_as_of` field on the superadmin dashboard. The live rates are rows
in `provider_rates`, entered one at a time through `PUT /rate-card/providers`.

So the ElevenLabs multilingual fix exists as a comment in a Python file and may
or may not exist as a row in production. Nobody can tell without querying.

Two pieces of work:

- **An idempotent seeder** that applies `default_rates.py` to `provider_rates`,
  effective-dated, reporting what it would change before it changes it. The
  card becomes version-controlled and reviewable instead of retyped.
- **A real readiness check.** `_price_book_evidence` in
  `api/services/billing/readiness.py` currently asserts `count > 0` — it passes
  with one row on file and a hundred missing. Replace it with the diff from
  section 2 of the measurement pack: *which* provider/model/component
  combinations have been used on a call and have no live rate.

### 1c. Put a real exchange rate on file

If section 4 comes back empty, set one, and wire the refresh
(`POST /rate-card/exchange-rate/refresh` already exists) to run on a schedule.
The fallback is a floor for a broken day, not a billing rate.

---

## Phase 2 — Turn on what is already built

With nobody being billed, this is a configuration change and an announcement
nobody needs to receive.

1. **Merge PR #41.** Both checks green. It prices BYOK by *which* key the
   customer brought rather than how many — $0.015 uplift on their own TTS,
   $0.002 on their own STT, nothing on the language model, applied as an uplift
   on the resolved rate so a negotiated account rate survives the call.
2. `BYOK_TIERED_FEE_ENABLED=true`
3. `ADDON_BILLING_ENABLED=true` — Knowledge Base and Call QA stop being free.
4. Re-run the estimator against the Phase 0 numbers and confirm the blended
   rate is what the study claims before anything is quoted to anyone.

Order matters: 1 → 2, and Phase 1b before either, or the flags switch on over a
rate card with holes in it.

---

## Phase 3 — Build the bundles

₹2,999 for one number and ₹2,500 of balance; ₹6,999 for two numbers and ₹6,300;
extra numbers ₹559. Nothing of this exists — there is no plan, bundle or
subscription-tier code in the repository.

**Size the balances against section 6 first.** At our own all-in ₹5.17–8.21/min,
₹2,500 buys 304–483 minutes against Agni's 300 at the same ₹2,999 — parity at
the pessimistic end, not the 2–3x advantage an earlier draft assumed.

**Grant a balance, not minutes.** A competitor's "5,000 minutes" is minutes of
*their platform fee* — Bolna's plan price is their fee times the included
minutes to the cent — with providers still drawn from a credit balance on top.
Minutes cannot be sold honestly while a minute costs ₹5.17 on one stack and
₹8.21 on another; that needs the fixed tier prices in
`SIMPLE-MODEL-CHOICE.md` first. See `COMPETITIVE-BUNDLES-2026.md`.

Roughly a week of work, in this order:


1. **Schema** — a `plans` table (code, price, numbers included, balance
   granted, concurrency allowance) and a `subscriptions` link from
   organisation to plan with a period. Reuse `recurring_charges` and
   `recurring_charge_periods`, which already carry the rental and already
   handle proration and autopay failure.
2. **Balance grant on renewal** — a `credit_ledger` entry of kind `grant`
   written when a period opens. The decision to make explicitly: **does granted
   balance expire at period end, or roll over?** Expiry is standard and is what
   makes a bundle better than a top-up; rollover is friendlier and much harder
   to reason about on an invoice. Recommend expiry, stated plainly at purchase.
3. **Number entitlement** — a plan includes N numbers; number N+1 bills ₹559 as
   its own recurring charge. Rentals already work this way, so this is a count
   check rather than new billing.
4. **Razorpay plan IDs** — `RAZORPAY_RENTAL_PLAN_ID` already exists as a
   pattern; each bundle needs its own, and autopay revocation has to fall back
   to prepaid the way the rental does.
5. **Purchase and management UI**, and the pricing page.

---

## Phase 4 — Concurrency, and the floor

Both are in the leak ledger and neither is built. Concurrency is enforced in
`call_concurrency` and never billed; competitors charge $8–10 per line per
month. A monthly floor is something every competitor has and we do not.

Price them off section 6b — peak concurrency actually reached — rather than off
what the limiter permits.

---

## Phase 5 — Say it out loud

`account/billing` and `getting-started/index` describe a flat $0.02/min, which
is accurate *only while the Phase 2 flags are off*. The moment they flip, both
pages are wrong, and so is every quote anyone has pasted from them.

What they need to say instead — the fee is tiered on **which** component the
customer brought, not how many, and the tiers do not stack:

| | Platform fee / min |
|---|---|
| Everything on our keys | $0.020 |
| Your transcription, our voice | $0.022 |
| Your voice | $0.035 |

The language model is never tiered. Bringing your own costs nothing extra,
because at a twentieth of a cent a fee for it would exceed the margin it costs
us and your bill would go *up* for bringing a key — which is not an invoice
anybody can defend.

Worth stating plainly on the page rather than burying: **every one of those
rows is still under Vapi ($0.050), Retell ($0.055) and Bolna ($0.060).**

Nothing here reaches a customer until this is done.

## Still unmeasured, and worth saying so

Every rupee in the study rests on assumed characters per minute, an assumed
call mix, and provider list prices that were read rather than invoiced. Phase 0
replaces the first. The second needs a month of real traffic. The third needs
somebody to reconcile a provider invoice against `provider_cost_paise`, which
nothing in this plan does and which is the only way to know the cost side is
true.
