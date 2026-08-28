# How pricing actually works

**Written 28 Aug 2026, from the code.** Every claim names the file it came from.

This exists because the question "what does a minute cost, and who decided
that" had four plausible answers depending on who you asked, and three of them
were wrong. It is the reference; `PRICING-FIX-PLAN.md` is the to-do list and
`DASHBOARD.md` explains what each dashboard number means.

## What production is actually set to

Read off the superadmin rate card on **28 Aug 2026**. The rest of this document
explains the machinery; these are the numbers currently in it.

| Setting | Value | Note |
|---|---|---|
| Platform price | **₹3.00/min**, quoted in **Rupees** | In force since 27 Aug. Rupee-native — read §3, it has consequences |
| Pulse | **15 seconds** | A 62-second call bills 75 |
| Managed model markup | **1.7×** | A vendor line costing ₹1.00 is charged at ₹1.70 |
| Volume tiers | none configured | So every account without an override pays ₹3.00 |

A worked check against a real quote, which is how these were confirmed: managed
Indic showed Transcription ₹0.85, Brain ₹0.02, Voice ₹1.04, Platform ₹3.00.
Divide the vendor lines by 1.7 and transcription comes back to ₹0.50/min —
exactly Sarvam's ₹30/hour. The markup and the rate card agree.

---

## 1. The whole thing in one formula

For one completed call:

```
what the customer pays  =  platform fee
                        +  Σ (vendor cost × managed markup)     ← per component
                        +  Σ (add-on fees)                      ← if any ran
```

Three separate decisions, three separate places they are set, and **they do not
interact**. Most of the confusion comes from assuming one of them moves another.

| Part | Whose money | Marked up? | Where it is set |
|---|---|---|---|
| **Platform fee** | Ours | **Never** | Rate card → Platform price, or a tier, or an account override |
| **Vendor usage** | The vendor's, resold | **Yes** | Rate card → per provider and model |
| **Add-ons** | Ours | **Never** | `billing/addons.py`, gated by `ADDON_BILLING_ENABLED` |

`api/services/billing/cost_engine.py::compute_call_cost` is the whole
computation, and it is pure — no database, no clock. Everything below is about
what gets fed into it.

---

## 2. The platform fee, and the thing everyone gets wrong

### It is resolved as a three-step ladder

`api/services/billing/rates.py::resolve_platform_rate`, in this order, **first
match wins**:

1. **Account override** — an explicit effective-dated rate for that one account.
   Enterprise deals live here. Set at
   `PUT /admin/billing/accounts/{id}/platform-rate`.
2. **Volume tier** — matched on the account's **billable minutes so far this
   calendar month**. The highest threshold it has reached wins. Set at
   `PUT /admin/billing/rate-card/tiers`.
3. **Global default** — what everybody else pays. Set at
   `PUT /admin/billing/rate-card/platform`. Falls back to **$0.02/min** if no
   row exists at all.

### ⚠️ Tiers are by MINUTES, not by plan

This is the misconception worth stating loudest, because the vocabulary invites
it. `platform_volume_tiers.min_period_minutes` is *minutes used this month*
(`costing.py::_period_minutes` sums `billable_seconds` from the 1st of the
month). It has nothing to do with which plan an account is on.

**No plan changes the platform fee.** Grep confirms it: neither
`billing/plans.py` nor `billing/rentals.py` touches `organization_rate_history`
or any rate. A plan does exactly two things — see §6.

So "the platform fee is tiered by plan" is **not** how the system works today.
It is tiered by consumption. If a plan should carry a different fee, that is a
feature nobody has built, and the honest way to fake it now is an **account
override** per customer.

### The pulse

The fee is charged on time rounded **up to a whole pulse**, default **15
seconds** — not whole minutes.

```
billed_seconds = ceil(connected_seconds / pulse) × pulse
```

A 62-second call bills **75 seconds**, not 120. At `pulse=60` this reproduces
whole-minute billing exactly, which is how it stays comparable to competitors
who bill that way. `billing/money.py::billed_seconds`.

Time counts from **answer to hangup**. Ringing, busy and unanswered cost
nothing, because `billable_seconds` comes from connected duration
(`usage.py:billable_seconds_from_usage_info`).

---

## 3. Dollars vs rupees — the one trap that bites silently

The platform rate can be stored **two ways**, and the superadmin form offers
both. They are not two spellings of one number; they are different products.

| Stored as | Field | Behaviour |
|---|---|---|
| **Dollars** (normal) | `platform_rate_micros_usd` | Converted to rupees at the FX rate **in force when the call happened**. Moves with the rupee. |
| **Rupees** | `platform_rate_mpaise` | Fixed. Does not move with FX. For a contract written in rupees. |

The list price is fixed in **dollars** on purpose: voice-AI platforms compete on
a dollar figure, and a rupee-fixed price would drift against Vapi and Bolna
every time the rupee moved (`money.py` header).

### What a rupee-native rate silently switches off

When the rate is rupee-native, `ResolvedPlatformRate.usd_inr_paise` is `None` —
and two features are gated on it being present:

- **Add-on billing stops.** `costing.py::_addon_rates_mpaise` returns `{}` when
  `usd_inr_paise is None`, whatever `ADDON_BILLING_ENABLED` says. Knowledge
  base and Call QA bill nothing.
- **The BYOK fee uplift stops.** `costing.py` line ~175 applies it only
  `if platform.usd_inr_paise is not None`.
- The dollar figure disappears from every quote (`CostEstimate.
  total_micros_usd_per_minute` is `None`).

**Neither failure raises anything.** They are correct behaviour — there is no
honest FX rate to convert a dollar-quoted add-on at — but they look like nothing
at all from a screen.

> **How to tell which you have:** if a quote shows a `$` figure beside the `₹`,
> the rate is dollar-denominated. No `$`, and it is rupee-native.
>
> **This is the live configuration, confirmed 28 Aug.** The platform price is
> set to ₹3.00 with "Quoted in" on **Rupees**, so `platform_rate_mpaise =
> 3 × 100 × 1000 = 300000` and `usd_inr_paise` is `None`.
>
> **It is doing no harm today**, because both features it gates —
> `ADDON_BILLING_ENABLED` and `BYOK_TIERED_FEE_ENABLED` — are already off. The
> cost is *latent*: the day somebody switches add-on billing on, Knowledge Base
> and Call QA will bill nothing at all, the flag will read as enabled, and
> nothing will say why.
>
> To keep ₹3.00 and remove the trap, set it in **dollars** at **$0.03125**
> (at ₹96/USD). Same price to the customer; the rate then also tracks the rupee,
> which is what a dollar-quoted list price is for.

---

## 4. Vendor costs and the markup

A provider line is **measured usage × the rate on file × the managed markup**.

- **Measured usage** is raw: seconds for STT and telephony, characters for TTS,
  tokens for LLM. Callers never pre-divide, so there is exactly one rounding
  step per line (`money.py::cost_paise`).
- **The rate** is looked up by `(component, provider, model)`, effective-dated.
  A model-specific row beats the provider-wide one (`rates.py::
  resolve_provider_rate`).
- **The markup** is `MANAGED_PROVIDER_MARKUP_BPS`, effective-dated in
  `billing/markup.py` and resolved **as at the call's own time**, so re-costing
  a March call uses March's multiple. Changing it needs a code emailed to the
  company inbox.

Marked up: **STT, LLM, TTS and telephony** — `cost_engine.MARKED_UP_COMPONENTS`.
One rule for all four: *the number in the rate card is what the vendor charges
us; the markup is what we add.*

**Both figures are stored per line.** `provider_cost_paise` is what the vendor
charged us; `cost_paise` is what the customer pays. Anything calling itself a
*cost* must use the first (see `DASHBOARD.md`).

### Entering a rate by hand

The rate card UI takes **rupees per rate unit** and converts internally
(`mpaise = rupees × 100,000`).

```
₹ per unit  =  USD per unit  ×  ₹ per USD
```

For a language model the rate is a **blend**, because vendors quote two prices
and the schema has one field. `LLM_INPUT_SHARE = 0.7` — a voice agent resends a
growing transcript every turn, so input dominates:

```
USD per 1k tokens  =  (0.7 × input_per_1M + 0.3 × output_per_1M) ÷ 1000
```

> **Provider rates are stored INR-native**, so the exchange rate is baked in the
> moment you save. Unlike the platform fee, they do **not** float. Set the FX
> rate before entering a batch, or every row carries whatever rate was in force
> when it was typed.

### Usage with no rate is *uncosted*, not free

If no rate is on file, the call still bills the platform fee, the missing vendor
cost is reported on the unit-economics screen, and margin is overstated until a
rate is entered. Silently pricing at zero would make a misconfiguration look
like a profitable month.

**But a model with no rate of its own is different and worse:** it falls through
to the provider-wide row, which for OpenAI is the cheapest common model. A
ticked `gpt-4.1` bills at roughly **a thirteenth** of its cost, with no warning
beyond a superadmin line reading "N models are priced against their provider's
default". See `KNOWN_ISSUES.md` #35.

---

## 5. Add-ons

Priced features a call used — knowledge-base retrieval, post-call QA. Charged on
the **same pulse-rounded time** as the platform fee, so a 40-second call is
charged 45 seconds of every fee and not a full minute of any of them.

Ours, so never marked up. Gated by `ADDON_BILLING_ENABLED` (**off**), and
additionally require a dollar-denominated platform rate (§3).

---

## 6. What a plan actually does

The starter plan is **₹2,500 of call balance + ₹499 for the number = ₹2,999
net**, collected monthly by one autopay mandate.

It does exactly two things (`billing/plans.py::grant_plan_cycle`):

1. Writes a `credit_ledger` entry of kind `plan` for the balance.
2. Marks the number's rental period collected, so the monthly cron does not
   debit it from the balance as well.

**It does not change the platform fee, the markup, or any rate.** A plan
customer and a top-up customer pay the same per-minute price unless somebody
also set an account override for them.

The price is **derived**, not typed: `STARTER_PLAN_PRICE_PAISE =
STARTER_PLAN_BALANCE_PAISE + NUMBER_RENTAL_PRICE_PAISE`. Raise the rental and
the plan price follows, by construction.

---

## 7. GST — kept entirely out of the way

**The credit ledger is GST-exclusive, everywhere, without exception.**

- The customer is charged **gross** at Razorpay.
- The ledger is credited **net**.
- Tax never enters the rate card, the cost engine, or any balance.

A customer paying ₹1,180 is credited ₹1,000. Consuming ₹500 of that produces a
tax invoice for ₹500 + ₹90 — and that ₹90 was already collected inside the ₹180.
Nothing is taxed twice because only one of those two numbers is ever money in
the ledger. `billing/tax.py`.

---

## 8. Where every number is set

| Number | Where | Endpoint |
|---|---|---|
| Global platform fee + pulse | Rate card → Platform price | `PUT /admin/billing/rate-card/platform` |
| Volume tier (by minutes/month) | Rate card → Add tier | `PUT /admin/billing/rate-card/tiers` |
| One account's rate | Account detail | `PUT /admin/billing/accounts/{id}/platform-rate` |
| Vendor rates, per model | Provider keys → the vendor's card | `PUT /admin/billing/providers/rates` |
| Managed markup | Rate card → markup | `POST /admin/billing/rate-card/markup/request` (emailed code) |
| USD→INR | Rate card → exchange rate | `PUT /admin/billing/rate-card/exchange-rate` |
| Which model a tier serves | Superadmin → managed tiers | `PUT /admin/billing/managed-tiers` |
| Bundles | Superadmin → bundles | `PUT /admin/billing/bundles` |

The starter plan price, rental price, add-on prices and the BYOK uplifts are
**environment variables**, not rate-card rows — see `DEPLOY-ENV.md`.

---

## 9. A worked minute

Managed Indic stack, one connected minute, at the **live** settings — ₹3.00
platform fee, 1.7× markup:

```
                         vendor cost   × 1.7   customer pays
Transcription  sarvam       ₹0.50               ₹0.85
Language       openai       ₹0.10               ₹0.17
Voice          sarvam       ₹0.61               ₹1.04
                            -----               -----
Vendor lines                ₹1.21               ₹2.06
Platform fee                ₹0.00               ₹3.00   ← ours, never marked up
                            -----               -----
                            ₹1.21               ₹5.06
```

**Margin is ₹3.85 of a ₹5.06 minute — 76%.** It is the markup on the vendor
lines (₹0.85) *plus the entire platform fee* (₹3.00), because the fee costs us
nothing.

It is **not** ₹5.06 − ₹2.06. That subtraction treats the marked-up price as a
cost and is the mistake that made the bundle screen report 21.7% where the truth
was 45.7%.

Telephony is absent here because on a customer's own carrier we record no
carriage at all — those minutes are on their carrier's invoice, not ours
(`services/telephony/carriage.py`).

---

## 10. The traps, collected

Each of these has cost somebody real time:

1. **Volume tiers are minutes, not plans.** §2.
2. **A rupee-native platform rate silently disables add-on billing and the BYOK
   uplift.** §3. No error, no log line.
3. **Provider rates do not float with FX; the platform fee does.** §4.
4. **A model with no rate of its own bills at the provider fallback**, which is
   the cheapest model — not an error, a 13× under-charge. §4.
5. **"Cost" means `provider_cost_paise`**, never `cost_paise`. Using the second
   makes margin read as the platform fee alone. `DASHBOARD.md`.
6. **A quote must be computed by the same functions as the invoice.** Every
   pricing defect on this branch was a second calculation that drifted —
   including one where speech-to-speech was looked up under a different vendor
   name than it bills under. `HANDOVER.md` money rule 10.
7. **Cached LLM tokens are billed at the full rate**, and since provider lines
   carry the markup that is an overcharge, not a reporting slip.
   `KNOWN_ISSUES.md` #30.

---

## 11. What is switched off right now

Everything that would change a bill is off, so today the product charges the
platform fee plus marked-up vendor usage and nothing else:

| Flag | Default | What it would add |
|---|---|---|
| `ADDON_BILLING_ENABLED` | off | Knowledge base, Call QA |
| `BYOK_TIERED_FEE_ENABLED` | off | The fee uplift (moot — model BYOK is not sold, `KNOWN_ISSUES.md` #36) |
| `MANAGED_TELEPHONY_ENABLED` | off | Buying numbers from us |
| `BALANCE_ENFORCEMENT_ENABLED` | **on** | Refusing calls an account cannot pay for |
