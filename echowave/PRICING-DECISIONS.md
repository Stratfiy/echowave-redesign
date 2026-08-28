# Pricing decisions — record of truth

**Written 28 Aug 2026**, after a founder review session. This file exists so a
future session (human or Claude) that has not seen that conversation can pick
up the current pricing model without re-deriving it. When this file and a
chat transcript disagree, **this file wins** — update it the same day a
decision changes, or it becomes another stale doc like the ones it replaces.

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

### 2.4 Per-model / per-LLM markup override — genuine gap

`MANAGED_PROVIDER_MARKUP_BPS` (currently 1.7x live, see §2.9) is **one number**,
applied identically to every managed STT, LLM and TTS line on every provider
and every model (`api/services/billing/markup.py`, `cost_engine.py`). There is
no per-provider or per-model markup field anywhere in the schema —
`provider_rates` carries a cost, not a multiplier.

**What "override individually" requires, concretely:**
1. A markup override column (or a parallel small table, effective-dated like
   the global one) keyed on `(component, provider, model)` or just `model` for
   LLMs specifically — matching how `provider_rates` is already keyed.
2. Resolution order in the cost engine: model-specific override → provider-wide
   override (optional) → the global `MANAGED_PROVIDER_MARKUP_BPS`. Same
   "most-specific-wins-with-fallback" pattern `default_rates.py` already uses
   for rates themselves.
3. An admin screen or endpoint to set it, reusing the OTP-confirmation flow
   `markup.py` already has for the global value — a per-model change is smaller
   blast radius than the global one, so it may not need the same two-factor
   ceremony, but that's a call to make explicitly rather than skip silently.
4. Test coverage mirroring `test_managed_markup.py`.

**Estimate: a focused day of backend work** (schema + resolution + endpoint),
plus admin UI. I can build this — see the plan in §4.

### 2.5 Bundle LLM + embedding + STT + TTS as one displayed "model" cost

Today the customer-facing catalogue (`GET /agent-options/catalogue`, rendered
by `ModelCatalogue.tsx` / `CostPerMinuteBar.tsx`) prices and lists STT, LLM,
TTS **separately** — a customer picks a model per slot, and the cost bar sums
three independent lines. There is no concept of a "bundle" (e.g. "Sarvam
bundle" = Sarvam STT + Sarvam TTS + a chosen LLM + embedding, shown as one
number) anywhere in the schema or the UI.

**What this needs, concretely:**
1. A product decision on what a "bundle" actually is: a fixed named
   combination (Sarvam STT + Bulbul TTS + Gemini Flash + text-embedding-3-small,
   always sold together), or a computed roll-up (whichever STT/TTS/LLM/embedding
   the account has selected, summed and displayed as one figure)? These are
   different builds — the first is a new catalogue entity, the second is
   presentation-only on top of what already exists.
2. If it's a fixed bundle: a `model_bundles` table or config list (code, label,
   which provider/model per component, list price) and a bundle picker in the
   UI, replacing or sitting alongside the per-slot picker.
3. If it's a roll-up: `CostPerMinuteBar` already computes STT+LLM+TTS
   (`DASHBOARD.md §Cost estimate`) — adding embedding to that sum and relabelling
   the total as "model cost" instead of three lines is a smaller, presentation-
   only change.
4. Either way: **embedding must get a real cost component first** (see §2.6 —
   it currently has none at all, not even an uncosted one), or "bundle it into
   the model cost" is bundling in a number that is always zero.

**This needs your call on which shape (fixed bundle vs roll-up) before I build
either — they're different amounts of work and different UI.** See §4.

### 2.6 Fold KB + post-call QA (summary, sentiment, etc.) into model cost — flagged, not implemented

This is worth pausing on because it **reverses a decision already recorded in
this repository**, not just adds a feature.

**What exists today:** `api/services/billing/addons.py` treats knowledge-base
retrieval and post-call QA as **separately billed, revenue-generating add-ons**
(`ADDON_KNOWLEDGE_BASE_MICROS_USD`, `ADDON_CALL_QA_MICROS_USD`), currently
switched off (`ADDON_BILLING_ENABLED=false`). `COMPETITIVE-PRICING-STUDY.md
§3, Tier 2` sized **not** charging for these at **+$60,000/yr** in foregone
revenue, on the reasoning that every competitor (Retell, Vapi) charges for
knowledge base, QA, PII redaction, etc. separately, and Decibyl ships all of it
free today.

**What you're now asking for:** fold KB and QA (plus sentiment analysis, which
doesn't exist as a separate addon yet — see below) into the model's cost line,
*if the actual cost is negligible*, rather than a separate charged feature.

Both can be true at once, but they are **different pricing strategies**:
- Charging separately (current design) recovers cost **and** captures margin
  competitors capture — it's a revenue line, not just a cost-recovery one.
  Post-call QA in particular runs a real LLM call per finished conversation on
  your key (`api/services/billing/addons.py:47` — "a direct marginal cost to
  us"), so "negligible" needs to be checked against real numbers before folding
  it in for free.
- Folding it into "model cost" (silently included, no separate line) is a
  product-simplicity choice — fewer line items on the receipt, easier to
  explain to a first-time buyer — at the cost of the $60k/yr the study
  identified and of losing the ability to sell it as a premium tier later
  (like Retell's $0.10/min QA).

**Sentiment analysis doesn't exist as a catalogue entry at all yet** — if you
want it billed (folded in or separate), it needs the same
`record_addon_used()` runtime hook the KB and QA entries have, so usage is
measured rather than assumed.

**My recommendation, stated plainly rather than silently picked:** measure the
actual per-call cost of QA/summary/sentiment first (it is not zero — it's an
LLM call), then decide fold-in vs separate line with that number in hand. If
it really is negligible per call, folding it in costs you little; if it's not,
you'd be giving away real money believing it's free. This is a five-minute
query against `usage_info["llm"]` filtered to the `QAAnalysis` processor,
which the code already records.

**I have not implemented this either way — it needs your decision on the
strategy question above, informed by that one measurement.** See §4.

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

## 3. The signed-URL bug (screenshot, Run #64)

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

## 4. The plan — what I can do vs. what you need to do

### I can do now (code, no external access needed)
- [ ] Build the **per-model markup override** (§2.4) — schema, resolution order,
      endpoint, tests. ~1 day.
- [ ] Build whichever **bundle display** shape you pick in §2.5 (fixed bundle
      vs computed roll-up) — different scope, see that section.
- [ ] Implement the **KB/QA fold-in** once you decide the strategy in §2.6.
- [ ] Bump `MANAGED_PROVIDER_MARKUP_BPS` default to `17000` (§2.9) — one line,
      ready whenever you confirm.
- [ ] Add a **sentiment-analysis addon entry** with its own `record_addon_used`
      hook, if you want it tracked/billed at all (currently doesn't exist).

### Needs your decision first (no code until you pick)
- [ ] **§2.5** — is a "bundle" a fixed named combo or a computed roll-up of
      whatever's selected?
- [ ] **§2.6** — fold KB/QA/sentiment into model cost, or keep them as separate
      billable add-ons per the existing $60k/yr revenue case? (Run the one
      measurement first — real per-call QA cost — then decide.)
- [ ] **§2.9** — confirm 1.7x is the number you want as the durable default,
      not just today's live setting.

### Needs you specifically (access or actions only you have)
- [ ] **Set the ₹3.00/min global default** at `/superadmin/billing/rate-card`
      (§2.1) — a superadmin screen action, not code.
- [ ] **Set `platform_rate_mpaise` on each subscription plan** you want
      tiered (§2.2) — same screen.
- [ ] **Check production logs** for the Run #64 signed-URL failure — with
      today's fix, the *next* failure will show a real reason in the API
      response itself, so re-trigger it from the UI and read the new error
      text (or check server logs around `Error generating MinIO/S3 signed
      URL` if you have SSH/console access to the EC2 box). I cannot reach
      production from this session.
- [ ] **Verify §2.7 and §2.8** live — sign up a fresh test account and confirm
      ₹1,000 first-recharge and no-double-billed-numbers behave as described,
      since I can read the code but not click through your production UI.

### One open question I'd ask before building anything in §2.4/§2.5
Do you want the per-model markup override and the bundle display shipped
**before** you turn on `BYOK_TIERED_FEE_ENABLED` / `ADDON_BILLING_ENABLED`
(still both `false` as of `GO-LIVE-RUNBOOK.md`), or independently of that
switch? They don't depend on each other in code, but they do change what a
customer sees on the same screen at close to the same time, and shipping them
separately means the pricing page changes twice instead of once.
