# Pricing decisions — record of truth

**Written 28 Aug 2026**, after a founder review session. **Updated the same
day** with round two: the per-model markup override is now built, the bundle
scope was narrowed to calculation-only, and the KB/QA/sentiment question was
researched against Vapi and Bolna rather than left as an open flag. This file
exists so a future session (human or Claude) that has not seen that
conversation can pick up the current pricing model without re-deriving it.
When this file and a chat transcript disagree, **this file wins** — update it
the same day a decision changes, or it becomes another stale doc like the ones
it replaces.

Supersedes the numbers in `PRICING-REVIEW.md`, `PROVIDER-PRICING.md`,
`COMPETITIVE-PRICING-STUDY.md` and `PRICING-FIX-PLAN.md` wherever they
disagree with this file — those stay as historical research, this is current
state and current intent.

---

## 1. What is decided, and what it means

| # | Decision | Status |
|---|---|---|
| 1 | **₹3.00/min platform fee for pay-as-you-go accounts** (no subscription plan) | Mechanism exists — **needs to be set**, see §2.1 |
| 2 | **Tiered platform fee, plan-wise** — each subscription plan can carry its own per-minute fee | **Already built and wired**, see §2.2 |
| 3 | **Dedicated/negotiated platform fee for enterprise & agency accounts** on commit | **Already built**, see §2.3 |
| 4 | **Per-model markup override** for LLMs — override the blanket managed markup for an individual model | **Not built.** See §2.4 for the gap and what building it involves |
| 5 | **Bundle LLM + embedding cost into one displayed "model" cost** (e.g. a Sarvam bundle showing STT+TTS+LLM+embedding as one number) | **Not built.** Product/UI decision, see §2.5 |
| 6 | **Fold Knowledge Base and post-call QA (summary, sentiment, etc.) into model cost** rather than a separate line, since the cost is negligible | **Conflicts with the existing add-on design and an earlier revenue recommendation** — flagged, not silently implemented. See §2.6 |
| 7 | **First recharge minimum = ₹1,000** | **Already built**, see §2.7 |
| 8 | **Numbers included in a subscription must not be billed again** as a separate rental | **Already built and verified in code**, see §2.8 |
| 9 | **Managed provider markup is currently 1.7x** | **Live in the database, not in the code default** — see §2.9, action needed so a fresh install doesn't regress to 1.4x |

---

## 2. Detail, file references, and what (if anything) is left to do

### 2.1 ₹3/min for non-subscribed accounts

The platform fee resolves in this order (`api/services/billing/rate_card.py`,
documented in `DASHBOARD.md` under *Rate resolution order*):

```
1. Account override        (organization_rate_history — enterprise deals, §2.3)
2. The account's plan rate (subscription plan's platform_rate_mpaise, §2.2)
3. Volume tier              (platform_volume_tiers, by minutes/period)
4. Global default            (platform_volume_tiers at threshold 0)
```

An account with **no** plan and **no** override falls through to the global
default. That default is currently **$0.02/min USD** (`DEFAULT_PLATFORM_RATE_MICROS_USD`
in `api/services/billing/money.py`, or whatever is set on the threshold-0 row
at `/superadmin/billing/rate-card`).

**To make it ₹3.00/min flat for everyone with no plan:** set the global tier
to a **rupee-native** rate (`platform_rate_mpaise`) of `300000` (₹3.00 in
millipaise) at `/superadmin/billing/rate-card` → Volume tiers → threshold 0.
A rate row carries either a dollar price or a rupee one, never both — this
makes the no-plan price a fixed rupee number rather than one that drifts with
the exchange rate, which is almost certainly what "₹3 for normal users" means.

**This is a config change, not a code change.** Nobody needs to write code for
this — an operator with superadmin access sets it on the rate-card screen.

### 2.2 Tiered pricing, plan-wise — confirmed working

`SubscriptionPlanModel.platform_rate_mpaise` (`api/services/billing/subscription_plans.py`)
lets **every plan** carry its own per-minute fee, independent of its balance
and number entitlement. `None` means "the plan says nothing, the account keeps
whatever rate it already has" — not the same as zero, which would give the fee
away.

It is applied for real: `api/services/billing/mandates.py:712-731` sets the
account's platform rate from `plan.platform_rate_mpaise` **at the moment the
plan's mandate is authorised** (not at signup, not at checkout — when the bank
confirms the standing instruction). This is genuinely wired into billing, not
just a field sitting in a table.

**To use it:** set `platform_rate_mpaise` on each plan via
`set_plan_platform_rate()` (`subscription_plans.py:280`) or the plans admin
screen. A higher plan gets a lower per-minute fee the same way Bolna's
Starter/Growth/Scale tiers do (`COMPETITIVE-BUNDLES-2026.md §"How the market
packages bundles"`) — that pattern is directly reproducible today.

**Nothing to build.** Set the rate on each plan you want tiered.

### 2.3 Enterprise / agency dedicated platform fee — confirmed working

`PUT /admin/billing/accounts/{organization_id}/platform-rate`
(`api/routes/billing_dashboard.py:1134`) → `rate_card.set_account_rate()`
(`rate_card.py:659`) sets a **per-organization** override, in either currency,
with its own pulse if negotiated. This is priority #1 in the resolution
order — it beats the plan rate and every volume tier.

Effective-dated like every other rate here (closes the old row, opens a new
one), audited in `billing_audit_log`, and it is exactly the mechanism to use
for "an agency or enterprise account commits for more, give them a dedicated
fee." A negotiated deal is a row, not a special code path.

**Nothing to build.** This is the account-override screen at
`/superadmin/billing/rate-card` → Per-account overrides, or the route above
directly.

### 2.4 Per-model / per-LLM markup override — built, backend only

**Status: backend done, on this branch, not yet in an admin UI.**

`MANAGED_PROVIDER_MARKUP_BPS` (1.7x live, see §2.9) remains the blanket
multiple. A new, narrower table sits alongside it:

- **`ManagedMarkupOverrideModel`** (`api/db/models.py`, migration
  `ae88bf29885d`) — effective-dated, keyed by `(component, provider, model)`,
  exactly the same shape and same partial-unique-index pattern as
  `provider_rates`. `model=""` is a provider-wide override; a model-specific
  row wins over it.
- **`api/services/billing/markup.py`** gained `resolve_markup_override_bps`,
  `set_markup_override`, `clear_markup_override`, `list_markup_overrides`. No
  OTP confirmation — a single-line override can't move every account's bill
  the way the global value can, so it follows the same admin-only write
  pattern as editing a provider rate.
- **`cost_engine.compute_call_cost`** takes a new `markup_overrides` map and
  resolves it per line, most-specific-first, falling back to the flat
  `markup_bps` — additive and backward compatible: an empty map behaves
  exactly as before this existed.
- **`costing.py`** (what actually bills a finished call) and **`estimator.py`**
  (what quotes one in advance) both build and pass this map now, resolved as
  of the call's own time — so a March override can't be silently replaced by
  today's when an old call is re-costed, and a forward-looking quote can't
  disagree with the invoice it predicts.
- **Admin API**, staff-only, under `/admin/billing`:
  - `GET /rate-card/markup-overrides` — list what's in force
  - `PUT /rate-card/markup-overrides` — set one:
    `{"provider": "openai", "component": "llm", "model": "gpt-4o", "markup_bps": 20000, "note": "..."}`
    (`model` omitted or `""` = every model from that provider)
  - `DELETE /rate-card/markup-overrides?provider=openai&component=llm&model=gpt-4o`
    — clear one, returning that line to the blanket 1.7x
- **Tests**: `api/tests/test_markup_overrides.py` — resolution precedence,
  effective-dating, bounds, and four `cost_engine` cases proving a line with
  an override uses it, a line without still uses the flat markup, a
  provider-wide override reaches a model with none of its own, and the
  platform fee is never touched by any of this.

**What's still open:**
- **No admin screen** — it's an API only right now. Usable today via the
  routes above (e.g. curl or the existing rate-card UI's network calls as a
  template); a proper screen next to the rate card is a follow-up.
- **I could not run this against a real database from this session** — no
  Postgres/Redis available here. Verified: every touched file compiles
  cleanly, and the test file mirrors `test_managed_markup.py` and
  `test_cost_engine.py`'s existing, passing patterns exactly. **Run
  `pytest api/tests/test_markup_overrides.py` for real before trusting it in
  production** — see §4.

### 2.5 Bundle LLM + embedding + STT + TTS as one displayed "model" cost — scope confirmed: calculation only

**Founder clarified: "Bundle means only for calculation."** That resolves the
open question in the previous version of this section — it's the **roll-up**,
not a new named catalogue entity. No new picker UI, no `model_bundles` table.
What it needs:

1. **Embedding needs a real cost component first.** Today it has none — see
   §2.6's finding that ingestion embeds on our own key and bills nothing, not
   even as `uncosted`. A number that is always zero cannot usefully be summed
   into anything. This is the actual prerequisite, and it's a small, contained
   change: a `CostComponent.EMBEDDING` (or reuse `LLM` with a distinguishing
   model tag — worth a two-minute decision, not a design problem) plus rate
   rows in `default_rates.py` for `text-embedding-3-small` and whatever else
   `DecibylEmbeddingService`/`OpenAIEmbeddingService` can resolve to.
2. **The roll-up itself is presentation-only** on top of what already exists.
   `CostPerMinuteBar` / `POST /cost-estimate/per-minute` already computes
   STT+LLM+TTS as three lines and a total (`DASHBOARD.md §Cost estimate`).
   Adding embedding to that sum and showing "Model cost: ₹X/min" instead of
   four separate lines is a small change to that one function plus the
   component that renders it — no schema change beyond step 1.
3. **The receipt keeps the itemised lines regardless.** Rolling up the
   *display* doesn't mean collapsing `call_cost_items` — the underlying
   per-component rows stay, because that's what the unit-economics screen and
   every margin figure downstream depend on. This is a UI-layer sum, not a
   change to what's stored.

**Not yet built — next up once §2.6 lands, since embedding's rate rows serve
both.** See §4.

### 2.6 Fold KB + post-call QA (summary, sentiment, etc.) into model cost — researched against Vapi and Bolna

**What "fold in" means concretely:** no separate line on the receipt or the
pricing page for knowledge-base retrieval, post-call QA, call summary, or
sentiment analysis — their cost (real, but assumed small) is absorbed into the
model's per-minute price instead of itemised and charged for.

**What exists today, unchanged from before:** `api/services/billing/addons.py`
treats knowledge-base retrieval and post-call QA as separately billed
add-ons (`ADDON_KNOWLEDGE_BASE_MICROS_USD` = **$0.005/min**,
`ADDON_CALL_QA_MICROS_USD` = **$0.02/min**), switched off today
(`ADDON_BILLING_ENABLED=false`). Sentiment analysis has no catalogue entry at
all — nothing bills or even measures it yet.

**Competitor research — done, not assumed, since the earlier version of this
doc had it wrong:**

| | Knowledge base | Post-call QA / analysis / sentiment |
|---|---|---|
| **Vapi** | **Bundled free.** No separate charge found on their pricing page or docs — it's part of the $0.05/min platform fee. | **Bundled free.** Vapi's own docs (`docs.vapi.ai/assistants/call-analysis`) describe automatic post-call summary, sentiment, outcome scoring and structured-data extraction as a standard feature, not a metered add-on. |
| **Bolna** | Available via their knowledge-base API; **no separate per-minute charge found** in their pricing pages — appears bundled the way Vapi's is. | Call transcripts and analytics are advertised as a feature; no dedicated sentiment/QA line-item pricing found. |
| **Retell** | **Charged separately**: $0.005/min when used, **+$8/month per document** beyond the first 10. | **Charged separately**: $0.10/min for AI-driven QA, after the first 100 minutes free. Also charges $0.005/min for denoising, $0.005/min for safety guardrails, $0.01/min for PII removal — add-ons stack. |

**This corrects the earlier framing in this repo.** `COMPETITIVE-PRICING-STUDY.md`
attributed the "charge separately" pattern to "every competitor (Retell,
Vapi)" — that's only true of Retell. **Vapi, the competitor you pointed at,
does exactly what you're now asking for**: sentiment, summary and outcome
scoring ship free inside the base fee, and knowledge base isn't a metered
line either. Bolna looks the same way, though its pricing pages are thinner
on the specifics than Vapi's.

**So there are two real, currently-competing playbooks, not one obviously
right answer:**
- **Retell's** — itemise and charge (KB, QA, denoising, PII, guardrails all
  separately metered). Maximises revenue capture, adds line items a buyer has
  to understand.
- **Vapi's** — bundle it all into the base rate, market it as "included."
  Simpler pricing page, no per-feature nickel-and-diming, but the cost is
  absorbed rather than recovered — and Vapi can afford to at $0.05/min
  orchestration; **whether Decibyl can absorb it at $0.02–0.035/min is a real
  question, not a given**, since the margin per minute to absorb it into is
  roughly a third to a half of Vapi's.

**What it would actually cost to absorb, per call:** post-call QA
(`api/services/workflow/qa/analysis.py`) runs a real LLM call over the
transcript, on whichever model the account has configured — the resolved
model isn't fixed in code, so I can't quote an exact token count from here.
Order of magnitude: a transcript-length prompt (likely 1,000–3,000 input
tokens for a short call) plus a few hundred output tokens for structured
JSON. At Gemini Flash-tier pricing that's a fraction of a cent per call; at a
larger model it's more. **This is exactly the measurement I flagged last
round and still haven't been able to run** — a query against
`usage_info["llm"]` filtered to the `QAAnalysis` processor, which the code
already records per call.

**My recommendation, now with the competitor evidence in hand:** Vapi is real
precedent for folding this in, which removes my earlier hesitation about
reversing the $60k/yr case with nothing to point at. I'd still run the one
measurement above before committing — "negligible" should be a number, not an
assumption, especially since Retell prices QA at $0.10/min specifically
*because* it isn't negligible on a larger model. If the real cost comes back
under roughly $0.001–0.002/min, folding it in is a clean, defensible,
Vapi-shaped decision. If it's materially more, a hybrid is worth considering:
fold in KB and sentiment (cheap, thin LLM calls) but keep QA priced (the one
Retell itself treats as expensive enough to meter).

**UI work needed either way, which doesn't yet exist:**
- If folded in: nothing new to *charge*, but the pricing page and the
  model-cost display (§2.5's roll-up) need to say plainly that KB/QA/sentiment
  are included, the way Vapi's page does — otherwise a customer never learns
  the feature exists.
- If kept separate or hybrid: a toggle/indicator on the agent-builder screen
  showing which priced features are active on a given agent, and the add-on
  lines need to actually appear on `/usage` and the receipt once
  `ADDON_BILLING_ENABLED=true` — right now that UI surfacing doesn't exist
  either; the addon system bills correctly but nothing shows the customer why.

**Still not implemented — needs your call on absorb vs. price vs. hybrid,
informed by the one measurement above.** I can build any of the three once
you pick. See §4.

### 2.7 First recharge minimum ₹1,000 — confirmed working

`FIRST_TOPUP_MIN_PAISE = 100000` (₹1,000) in `api/constants.py:500`.
`api/services/billing/payments.py:136-145` — the minimum top-up for an account
that has never paid before is `max(FIRST_TOPUP_MIN_PAISE, MIN_TOPUP_PAISE)`,
which is ₹1,000 today (₹1,000 > ₹100). Every top-up after the first drops to
the regular `MIN_TOPUP_PAISE` (₹100).

**Nothing to build. Already correct.** Worth one manual check: sign up a fresh
test account and confirm the checkout screen actually offers/enforces ₹1,000
as the floor on the first top-up, not just that the constant exists — see the
verification step in §4.

### 2.8 Numbers included in a plan must not be billed again — confirmed working

This was already built (`NEXT-SESSION-CONFIG.md §B4`, 21 Aug) and is worth
restating because it is exactly what you asked to double check:

- `numbers_a_mandate_covers()` (`api/services/billing/rentals.py:127`) returns
  `plan.included_numbers` for a plan mandate — how many numbers that mandate's
  monthly collection already pays for.
- `_mandate_for_this_number()` (`rentals.py:209`) attaches the plan's mandate
  to a number **only while the count of numbers already on that mandate is
  below what it covers** (`numbers_on_mandate()` vs `numbers_a_mandate_covers()`).
  Once the entitlement is used up, the next number gets **no mandate**, so it
  is billed as an ordinary rental (₹559, per `NUMBER_RENTAL_PRICE_PAISE` /
  the extra-number price on the plan) rather than silently riding free or
  silently double-billing.
- The monthly rental job (`tasks/rental_billing.py`) skips any charge that
  carries a mandate — "the bank is collecting for it instead" — so a number
  inside the entitlement is genuinely never billed twice: once by the
  subscription, once by the rental cron.

**Nothing to build. Already correct.** Worth one manual check (see §4):
subscribe a test account to a 2-number plan, provision a 3rd number, and
confirm only the 3rd shows a separate rental charge.

### 2.9 Markup is 1.7x — live, but not the code default

`MANAGED_PROVIDER_MARKUP_BPS` in `api/constants.py:295` is a **fallback**, read
only when no row exists in `ManagedMarkupHistoryModel`. The live value is
almost certainly a row in that table, set through the OTP-confirmed change flow
in `api/services/billing/markup.py` (`start_change` → email code →
`confirm_change`) — which is why the code still shows `14000` (1.4x) as the
seed default while the platform actually charges 1.7x today. This is **not a
bug** — it's the effective-dated history design working as intended, the same
way an old platform rate survives a later price change.

**One thing worth doing so a disaster-recovery install doesn't regress:**
update the env-var fallback in `api/constants.py:295` from `"14000"` to
`"17000"`, so a fresh database with no markup history row yet (first boot, or
a restore that lost the table) starts at the value you actually intend rather
than silently reverting to 1.4x. This is a one-line, low-risk change — I can
make it now if you confirm 1.7x is final.

---

## 3. The numbers, audited — what's actually tiered, and phone number pricing

**Caveat up front, same as last round: I can read the seed code and the
current constants, but I have no access to your production database.** If an
operator has since set values through the admin screens that differ from what
the code seeds, this section describes the code's intent, not necessarily
today's live figures — cross-check against `/superadmin/billing/rate-card`
and `/superadmin/billing/plans` directly.

### 3.1 What is actually tiered right now: nothing

The mechanism from §2.2 is real, but **no plan has a `platform_rate_mpaise`
set.** `subscription_plans.ensure_seeded()` (`api/services/billing/subscription_plans.py:436`)
creates the Starter plan without passing `platform_rate_mpaise` — it defaults
to `None`, which per that field's own contract means *"the plan says nothing
about the fee, the account keeps whatever rate it already has."* Since the
account's default is the global tier, **every account today — Starter-plan or
pay-as-you-go — pays the same platform rate.** "Tiered, plan-wise" is built
and wired (§2.2) but has never actually been turned on for the one plan that
exists. There is nothing to fix in code here — this is a config action for
you at `/superadmin/billing/plans`, setting a `platform_rate_mpaise` on
Starter (and on any plan above it, lower as the tier rises, the way Bolna's
7¢→6¢→5¢ ladder works).

### 3.2 The global default platform rate

`DEFAULT_PLATFORM_RATE_MICROS_USD = 20_000` in `api/services/billing/money.py:43`
— **$0.02/min**, ≈₹1.92 at the ₹96 reference rate. This is what a pay-as-you-go
account with no plan and no override actually pays today, **not the ₹3.00
you've decided on** — see §2.1 for how to set it.

### 3.3 Phone number pricing — checked, and it's internally consistent

| Figure | Value | Source |
|---|---|---|
| Extra number / rental price | **₹559.00** | `NUMBER_RENTAL_PRICE_PAISE = 55900` (`api/constants.py:229`) |
| Starter plan price | **₹2,999.00** | `STARTER_PLAN_PRICE_PAISE = 299900` (`api/constants.py:272`) |
| Starter plan balance granted | **₹2,500.00** | `STARTER_PLAN_BALANCE_PAISE = 250000` (`api/constants.py:257`) |
| Numbers included in Starter | **1** | hardcoded in `ensure_seeded()` |

The seeded Starter plan's `extra_number_price_paise` is **not** hardcoded
separately — it falls back to `NUMBER_RENTAL_PRICE_PAISE` (₹559) whenever a
plan doesn't set its own, per `Plan._view()`'s fallback rule. So the included
number is implicitly valued at ₹559, and the plan's own arithmetic
(`Plan.parts_paise`, `Plan.discount_paise` in `subscription_plans.py:90-97`)
comes out to:

```
parts  = balance (₹2,500) + numbers_value (1 × ₹559)  = ₹3,059
price  = ₹2,999
discount = ₹60
```

**This is a real, small, positive discount — the plan is not sold at a loss,
and it is not overpriced against its own parts.** `subscription_plans.save()`
would refuse the plan outright if balance alone exceeded the price, so the
one hard invariant is enforced by code, not just by this arithmetic.

**One thing worth flagging, because it's exactly the kind of "numbers don't
agree" confusion you started this whole review over:** `COMPETITIVE-BUNDLES-2026.md`
(19 Aug) and last round's version of this doc both say *"we include one at
₹499 of value inside the bundle."* That figure is **stale** — the number
rental price was moved to ₹559 by the `Price an extra number at Rs559, and
stop deriving Starter through it` commit, after that doc was written, and the
Starter plan's included-number value now derives from that constant rather
than being pinned separately. ₹499 doesn't appear anywhere in the current
code. Nothing is broken by this — the plan math above is correct with ₹559 —
but any deck, doc, or sales conversation still quoting ₹499 as the number's
value is quoting a retired number.

### 3.4 Add-on and uplift rates on file today (all currently switched off except the markup)

| Rate | Value | Constant |
|---|---|---|
| BYOK STT uplift | $0.002/min | `BYOK_STT_UPLIFT_MICROS_USD = 2000` |
| BYOK TTS uplift | $0.015/min | `BYOK_TTS_UPLIFT_MICROS_USD = 15000` |
| Knowledge-base add-on | $0.005/min | `ADDON_KNOWLEDGE_BASE_MICROS_USD = 5000` |
| Post-call QA add-on | $0.02/min | `ADDON_CALL_QA_MICROS_USD = 20000` |
| Managed provider markup | **1.7x live** (2.0x DB ceiling, 1.0x floor) | `ManagedMarkupHistoryModel`, see §2.9 |
| First recharge floor | ₹1,000 | `FIRST_TOPUP_MIN_PAISE = 100000` |
| Regular top-up floor | ₹100 | `MIN_TOPUP_PAISE = 10000` |

`BYOK_TIERED_FEE_ENABLED` and `ADDON_BILLING_ENABLED` are both `false` as of
the last checkpoint (`GO-LIVE-RUNBOOK.md`) — so the BYOK uplifts and the two
add-on rates above are **priced in the code but not actually charged to
anyone today.** Worth confirming they're still off, since flipping either is
a real pricing change the moment it happens.

---

## 4. The signed-URL bug (screenshot, Run #64)

**Root cause, from the code:** `MinioFileSystem.aget_signed_url` and
`S3FileSystem.aget_signed_url` used to catch **every** exception during
signing and silently return `None`, logging only server-side
(`api/services/filesystem/minio.py`, `s3.py`). The route
(`api/routes/s3_signed_url.py`) then turned that `None` into the generic
`"Failed to generate signed URL"` for both the recording and the transcript —
which is exactly what the screenshot shows, and it is genuinely
undiagnosable from the browser because the real reason never left the server.

Signing itself is a **local, offline computation** for both MinIO and S3 (no
network round-trip to check the object exists) — so a failure here is almost
always a **storage configuration problem**: a wrong endpoint, rotated or
missing credentials, a bucket that doesn't exist under the current config —
not a missing recording. This is consistent with the repo being mid-migration
(`MIGRATE-TO-MUMBAI.md`, the recent `ec2-migration-virginia-mumbai` branches).

**Fixed today:** both filesystem implementations now log **and re-raise**
instead of swallowing to `None`. The route catches broadly, logs the full
exception server-side, and returns the exception type and which storage
backend it came from in the API response — e.g. `"Failed to generate signed
URL: NoCredentialsError from S3FileSystem"` — instead of a dead-end message.
Four other call sites of the same method were checked; three already handled
exceptions defensively, one (`services/campaign/sources/csv.py`) was given the
same safety wrap so its existing `ValueError` contract still holds. Verified
all four touched files compile cleanly.

**What I could not do:** I don't have access to the production server, its
logs, or its environment variables from here, so I cannot see the actual
underlying exception for Run #64 or confirm the fix live. **This is the one
item that needs you** — see §4.

---

## 5. The plan — what I can do vs. what you need to do

### Done this round
- [x] **Per-model markup override** (§2.4) — schema, resolution order in
      `costing.py` and `estimator.py`, admin API, tests. **Backend only — no
      admin screen yet, and not run against a real database from this
      session.**
- [x] **Bundle scope narrowed** (§2.5) to calculation-only per your
      clarification — not yet built, but the ambiguity that was blocking it
      is resolved.
- [x] **KB/QA/sentiment researched against Vapi and Bolna** (§2.6) — found
      Vapi bundles all three free (contradicting this repo's own earlier
      claim that "every competitor" charges for them), Bolna looks similar,
      Retell is the one that itemises. Not yet implemented — still needs your
      absorb/price/hybrid call.
- [x] **Numbers audited** (§3) — nothing is actually tiered yet, the global
      rate is still $0.02 not ₹3, phone number pricing checks out internally
      (₹559 rental, ₹60 real discount on Starter), and the ₹499 figure in
      `COMPETITIVE-BUNDLES-2026.md` is stale.

### I can do now (code, no external access needed)
- [ ] **Give embedding a real cost component and rate rows** — the actual
      prerequisite for both §2.5's roll-up and any version of §2.6's fold-in
      that includes KB (KB retrieval leans on embeddings at query time too).
- [ ] Build the **model-cost roll-up** (§2.5) once embedding is priced —
      small, presentation-layer change.
- [ ] Implement **whichever of absorb / price / hybrid** you pick for §2.6.
- [ ] Add a **sentiment-analysis addon entry**, priced or at $0 with just the
      `record_addon_used` hook so usage is at least measured either way.
- [ ] Bump `MANAGED_PROVIDER_MARKUP_BPS` default to `17000` (§2.9) — one line,
      ready whenever you confirm 1.7x is durable, not just today's setting.
- [ ] Build the **admin screen** for the per-model markup override (§2.4) —
      the API is done, the UI isn't.

### Needs your decision first (no code until you pick)
- [ ] **§2.6** — absorb (Vapi-shaped), price (Retell-shaped), or hybrid (fold
      in KB/sentiment, keep QA priced)? Ideally after the one QA-cost
      measurement below.
- [ ] **§2.9** — confirm 1.7x is the number you want as the durable default.

### Needs you specifically (access or actions only you have)
- [ ] **Set the ₹3.00/min global default** at `/superadmin/billing/rate-card`
      (§2.1) — still unset; the live default is $0.02, not ₹3 (§3.2).
- [ ] **Set `platform_rate_mpaise` on Starter** (and any plan above it) at
      `/superadmin/billing/plans` — confirmed unset on the one plan that
      exists (§3.1). Nothing is tiered until this is done.
- [ ] **Run the QA-cost measurement** — a query against `usage_info["llm"]`
      filtered to the `QAAnalysis` processor. I described the query in §2.6
      but can't run it without database access.
- [ ] **Run `pytest api/tests/test_markup_overrides.py`** (and the full
      suite) for real — I could only compile-check it here.
- [ ] **Check production logs** for the Run #64 signed-URL failure — with
      today's fix, the *next* failure will show a real reason in the API
      response itself, so re-trigger it from the UI and read the new error
      text (or check server logs around `Error generating MinIO/S3 signed
      URL` if you have SSH/console access to the EC2 box).
- [ ] **Verify §2.7 and §2.8** live — sign up a fresh test account and confirm
      ₹1,000 first-recharge and no-double-billed-numbers behave as described.
- [ ] **Correct the ₹499 figure** wherever it's quoted outside this repo
      (decks, sales conversations) — it should be ₹559 (§3.3).
