# MVP launch checklist

What a customer must be able to do end to end, what actually works today, and
what is left. Written against `claude/pricing-correctness`.

**The short version.** The engine is built — provisioning, compliance, rental
billing, per-call costing, MCP, docs. What is missing is almost entirely
*surface*: a customer cannot see what they are spending, cannot pay
automatically, and cannot be put on the managed path at all because one flag
has no setter. Roughly **9–10 dev-days** to a sellable MVP, and the single
half-day item blocks more than anything else on the list.

---

## 1. The journey, step by step

Every step a paying customer takes, in order.

| # | Step | Status | Gap |
|---|---|---|---|
| 1 | Sign up, land in the app | ✅ Works | Local auth only — Google sign-in needs a Stack Auth migration |
| 2 | Add credit (Razorpay) | ✅ Works | One-off top-ups only. **No autopay** — see §4 |
| 3 | Build an agent in the UI | ✅ Works | |
| 4 | Build an agent from Claude Code (MCP) | ✅ Works | 20 tools; connect with an API key |
| 5 | Use Decibyl's model keys | ✅ Works | Seeded from `PLATFORM_KEY_*`; per-slot managed/BYOK |
| 6 | Test the agent over the browser | ✅ Works | No phone number needed |
| 7 | Submit telephony verification | ⚠️ Built, gated | `MANAGED_TELEPHONY_ENABLED=false` until Plivo reseller is approved |
| 8 | Carrier approves | ⚠️ Built, unproven | Never run against Plivo's live Compliance API |
| 9 | **Be put on the managed path** | ❌ **Blocked** | `is_platform_managed` has no setter. **This blocks 10–13.** 0.5d |
| 10 | Search available numbers | ✅ API + MCP | No UI. 1.5d |
| 11 | Buy a number | ✅ API | No UI. Included above |
| 12 | Number bills monthly | ✅ Works | Prorated, dunning, suspension, release |
| 13 | Attach agent to the number, take a call | ✅ Works | |
| 14 | See call logs | ✅ Works | Table only — **no graphs**. 1d |
| 15 | **See token usage** | ❌ **Missing for customers** | Superadmin has it; customers see one number. 1.5d |
| 16 | See spend by model / component | ❌ **Missing for customers** | Same |
| 17 | Download invoice | ⚠️ Partial | Issued and numbered; **no PDF** |

---

## 2. What is actually missing, ranked

Ranked by what unblocks the most, not by size.

| # | Item | Days | Why it ranks here |
|---|---|---|---|
| 1 | **Admin route for `is_platform_managed`** | **0.5** | Nothing in the managed-number path is reachable without it. Every other number item depends on this one |
| 2 | **Customer token & spend dashboard** | 1.5 | The thing you asked for. The API is *already built* — `/tokens` takes an `organization_id` — so this is an org-scoped route plus a page |
| 3 | **Call-log graphs** | 1 | Volume over time, duration distribution, outcome mix, cost per call. `recharts` is already a dependency and already used in `/reports` |
| 4 | **Provider markup (your 1.3×)** | 1 | Does not exist today — see §3. Needed before you can price on managed keys |
| 5 | **Number provisioning UI** | 1.5 | Search, buy, see the monthly price before committing |
| 6 | **Autopay for number rental** | 3–4 | Razorpay e-mandate is a separate product with its own onboarding — see §4 |
| 7 | Prove managed numbers against live Plivo | ? | Cannot estimate. File one application, buy one number, budget for it going wrong |

**Total ≈ 9–10 days** excluding #7.

---

## 3. Pricing

### Your three decisions

| What | Value | Where |
|---|---|---|
| Number rental (retail) | **₹349/month** | `NUMBER_RENTAL_PRICE_PAISE=34900` |
| Number rental (our cost) | ~₹250/month | `NUMBER_RENTAL_COST_PAISE=25000` — **estimate, not a Plivo quote** |
| Model keys | **1.3× what we pay** | ⚠️ **Not implemented** — see below |
| Telephony per minute | Your call | Rate card: Plivo ₹0.60/min is what we pay |

At ₹349 retail on ~₹250 cost you keep **₹99/number/month, ~28%**. Confirm the
cost against Plivo's live India price list before treating that as real —
every rental margin figure rests on it.

### The 1.3× needs a design decision, not just a number

Today's model is:

```
total charged = platform fee (per minute) + Σ(provider costs at cost)
```

Provider costs pass through **at cost**. There is no multiplier anywhere. So
"1.3× on API keys" cannot be configured — it has to be built. Two ways:

**A. Bake 1.3× into the rate-card rows.** No code. You already have a rate-card
editor. **Do not do this.** `provider_rates` would then hold retail, not cost,
and every margin figure on the unit-economics screen silently becomes zero —
the system would believe it earns nothing on model usage.

**B. Add a markup multiplier applied to managed provider costs.** Keeps
`provider_rates` as true cost, so margin stays visible and adjustable. Same
lesson as number rental, where cost and price are deliberately stored
separately. **~1 day.**

One thing to settle before building it: BYOK accounts are correctly charged
**nothing** for provider usage (they pay the vendor directly). So the markup
applies only to managed components — and the platform fee presumably still
applies to both. Confirm that is what you want, because it decides whether a
managed customer pays `cost×1.3 + platform fee` or `cost×1.3` alone.

### Rate card — checked

| Component | Current | Note |
|---|---|---|
| Platform fee | $0.02/min (≈₹1.78) | `DEFAULT_PLATFORM_RATE_MICROS_USD = 20_000` |
| Telephony — Plivo | ₹0.60/min | Published India local. SIP is ₹0.34 |
| Telephony — Twilio | ₹1.20/min | India mobile |
| STT — Sarvam | per-minute | |
| TTS — Sarvam / Rumik | per-1k chars | Rumik Mulberry ₹0.50 is the cheap option |
| LLM | per-1k tokens | Cached tokens are **captured but billed at full rate** |

Two things worth knowing:

- **The tender model assumed ₹0.25/min telephony.** Even Plivo's SIP rate is
  above that. Telephony is the largest line in a cheap stack and the easiest to
  under-budget.
- **Cached tokens are billed at full price.** Providers charge ~10% for a cache
  read. On a voice agent — which resends the whole conversation every turn —
  this is not a rounding error. Not on the critical path, but it is money.

---

## 4. Autopay — the honest answer

**It does not exist, and it is bigger than it looks.**

Today: Razorpay one-off top-ups credit a prepaid balance. Rental is collected
from that balance monthly, and if the balance is short the dunning schedule
runs (past due → day 7 suspend → day 45 eligible for release). That is a
working model — it just is not autopay.

Real autopay in India means **Razorpay Subscriptions / UPI e-mandate**, which
is:

- a separate Razorpay product with its own activation and underwriting
- a mandate the customer authorises once (UPI Autopay, cards, or eNACH)
- its own webhook events — `subscription.charged`, `subscription.halted`,
  `mandate.revoked` — none of which are handled. `payments.py:296` explicitly
  acknowledges subscription events and acts on none of them
- new failure modes: a revoked mandate, an expired card, a bank-side failure

**3–4 days**, and it depends on Razorpay approving the product for your account
— which is not in your control and can take days.

**Recommendation for launch:** ship with prepaid + the dunning schedule, and add
a low-balance email (currently missing — nobody is emailed) plus auto-top-up as
a fast follow. A customer whose number is suspended at day 7 with no email is
the worst version of this. **The email is worth more than the mandate for MVP.**

---

## 5. Metrics — what exists, and where

This is the part where the answer is better than expected.

### Already built (superadmin, `/superadmin/billing/*`)

All with charts, all live:

| Screen | Shows |
|---|---|
| `tokens` | Token series over time, **by model**, and context growth per turn |
| `latency` | Per-call latency |
| `unit-economics` | Cost vs revenue per minute, pulse give-away |
| `calls` | Per-call cost breakdown |
| `campaigns`, `realtime`, `accounts`, `rate-card` | |

The `/tokens` endpoint **already accepts `organization_id`**. The hard part —
aggregation, by-model grouping, context-growth analysis — is done.

### What a customer sees

| Page | Content |
|---|---|
| `/usage` | "Agent Runs" and a runs table. **No charts. No tokens. No model breakdown.** One number: total Decibyl Tokens |
| `/billing` | Add credit, billing details, tax documents, payment history. **No spend breakdown** |
| `/reports` | Campaign charts (duration, disposition) — the only customer-facing graphs today |

**So the gap is precise: customers cannot see what they are spending or on
what.** Superadmins can see everything.

### What to build (items 2 and 3 above)

Customer **Usage** page:
- spend over time, stacked by component (STT / TTS / LLM / telephony / platform)
- token totals: prompt, completion, cached
- **by model** — reuse `token_usage_by_model`
- top agents by spend
- current balance and burn rate → projected days remaining

Customer **Call logs** page — the table stays, add above it:
- call volume over time
- duration distribution
- outcome mix (answered / no-answer / voicemail / failed)
- cost per call, and the same distribution
- latency percentiles, since it is already captured

`recharts` is installed and the superadmin components in
`superadmin/billing/_components/primitives.tsx` are reusable.

---

## 6. Before you open the doors

- [ ] `is_platform_managed` admin route exists *(blocks everything below)*
- [ ] One number bought end to end against **live** Plivo
- [ ] One compliance application filed and accepted
- [ ] Rental charged, visible on the customer's billing page
- [ ] Number suspended and restored by a top-up (test the dunning path)
- [ ] `NUMBER_RENTAL_COST_PAISE` confirmed against Plivo's real price list
- [ ] Markup decision made and built (§3)
- [ ] Customer token/spend dashboard live
- [ ] Call-log graphs live
- [ ] Low-balance email
- [ ] One real end-to-end call — no test in this repo dials a carrier
- [ ] `ENABLE_SIGNUP=false` once your accounts exist
- [ ] Razorpay webhook repointed to `api.decibyl.ai` *(payments capture and
      never credit if this is missed)*
- [ ] Backup restore rehearsed — `scripts/rehearse_restore.sh`
- [ ] `MANAGED_TELEPHONY_ENABLED=true` only after the reseller account is live

---

## 7. Known gaps carried into launch

Not blockers, but say them out loud rather than discover them:

- **No invoice PDF.** Documents are issued, numbered and readable via API.
- **No credit notes.** Refunds are a Razorpay-dashboard action plus a manual
  ledger adjustment.
- **Cached tokens billed at full rate.**
- **No e-invoicing (IRN).** Mandatory above ₹5 crore turnover.
- **Google sign-in needs a Stack Auth migration** — the box runs local auth.
- **No subaccount per organization.** Compliance applications are filed under
  the parent Plivo account, so cost attribution across managed customers is at
  the parent level only.
