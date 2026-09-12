# Pricing, operating cost and infrastructure

Written 12 September 2026 as part of the /office-hours session that produced
`humans-and-agents.md`. Every figure below is derived from this repository:
`scripts/infra_sizing.py` for instance prices and concurrency, `data/pricing.ts`
in `Stratfiy/decibyl` for the shipped tiers, `api/constants.py` for the
commercial constants, and `PROVIDER-PRICING.md` for the seeded price book.
Where a number rests on an unmeasured assumption, it says so.

**The short version.** Infrastructure is not your cost problem and you should stop
thinking about it. Vendor pass-through is 90%+ of cost of goods. Your margin
varies 31 points based on a choice the *customer* makes and you do not price for.
And the product you now want to sell, agents built by chat, introduces a cost
line that is currently neither metered nor billed.

---

## 1. Infrastructure: smaller than you think

From `scripts/infra_sizing.py`, using its own constants (`CONCURRENT_CALLS_PER_VCPU = 5`,
`HEADROOM = 0.40`, ap-south-1 on-demand list, ₹96/USD):

| | |
|---|---|
| One `c7i.large` (2 vCPU, $0.1071/hr) carries | **6 concurrent calls** |
| Compute cost at 100% utilisation | **₹0.029 per call-minute** |
| at 50% utilisation | ₹0.057 per call-minute |
| at 25% utilisation | ₹0.114 per call-minute |

Against an all-in charge of ₹4.91 to ₹26.37 per minute, **compute is 0.4% to 2% of
revenue.** It is a rounding error. Note that `CONCURRENT_CALLS_PER_VCPU = 5` is
flagged in the script's own docstring as a guess, not a measurement, so treat the
fleet size as provisional until the soak test in `INFRASTRUCTURE.md` runs.

**The always-on floor**, on managed AWS rather than the current single box:

| Component | Rate | Monthly |
|---|---|---|
| Control node, `m7i.large` | $0.1176/hr | ₹8,241 |
| Postgres, `db.t4g.medium` | $0.0930/hr | ₹6,517 |
| Redis, `cache.t4g.medium` | $0.0850/hr | ₹5,957 |
| **Total, single-AZ** | | **₹20,716/mo before a single call** |
| Multi-AZ (db and cache doubled) | | ₹33,190/mo |

Today everything runs on one EC2 instance, which is cheaper and has no failover.
`PRD.md` §9 already lists "single region, no failover" as a Medium risk that
"blocks enterprise." The gap between those two numbers, roughly ₹12,000 a month, is
the actual price of being enterprise-sellable. That is one Starter customer.

Storage and egress: recordings at 90-day retention are tens of dollars a month at
the documented 13,000 calls/day, and are not worth modelling yet. **TURN relay
egress is the one to watch** — WebRTC traffic that cannot go peer-to-peer is
metered bandwidth, and `FORCE_TURN_RELAY` exists as a diagnostic that would make
every call relay if left on.

**Conclusion: do not spend engineering time on infra cost. Spend it on vendor cost
and on the margin variance in section 2.**

---

## 2. Your margin swings 31 points on a choice the customer makes

From `data/pricing.ts` tiers and per-minute rates, with the 1.4x managed markup and
Starter's ₹2.50 platform fee:

| Tier | Price | Balance | Stack | ₹/min | Minutes | Provider cost | GM at 100% use | GM at 75% |
|---|---|---|---|---|---|---|---|---|
| Starter | ₹2,999 | ₹2,500 | Indic | 4.91 | 509 | ₹876 | **71%** | 78% |
| Starter | ₹2,999 | ₹2,500 | Budget | 9.95 | 251 | ₹1,337 | 55% | 67% |
| Starter | ₹2,999 | ₹2,500 | Premium | 26.37 | 95 | ₹1,616 | **46%** | 60% |
| Growth | ₹7,999 | ₹7,200 | Indic | 4.91 | 1,466 | ₹2,524 | 68% | 76% |
| Growth | ₹7,999 | ₹7,200 | Premium | 26.37 | 273 | ₹4,655 | 42% | 56% |
| Scale | ₹19,999 | ₹18,500 | Indic | 4.91 | 3,768 | ₹6,486 | 68% | 76% |
| Scale | ₹19,999 | ₹18,500 | Premium | 26.37 | 702 | ₹11,962 | **40%** | 55% |

**Same price, 40% to 71% gross margin, and the customer picks.** This is
`PRD.md` §4's "margin per minute is fixed no matter what the customer runs...
that is a pricing bug, not a pricing strategy", expressed in the shipped price list.

Three fixes, in order of how quickly they can ship:

1. **Price the tier by stack.** `SIMPLE-MODEL-CHOICE.md` already proposes fixed
   per-minute tier prices. A Premium bundle at ₹2,999 should grant fewer rupees, or
   Premium should only be available from Growth up. One field.
2. **Make the markup progressive rather than flat 1.4x.** Higher-cost components
   carry more working capital and more vendor risk and should carry more markup.
   The rate card already stores rates per account, so this can be piloted on new
   accounts without touching existing ones.
3. **Measure breakage and size the grant to it.** Every tier gains 5-8 margin points
   at 75% consumption versus 100%. `COMPETITIVE-BUNDLES-2026.md` already says to
   size the grant so a typical account uses 70-80%, and
   `scripts/pricing/measure.sql` §6 returns minutes per organisation per month.
   That query has not been run. Run it before changing any price.

### The number that invalidates this whole table if it is wrong

**TTS characters per minute.** `REMAINING-WORK.md` E1 calls it "the largest single
unknown" and `LAUNCH-CHECKLIST.md` §3.1 says "one unmeasured constant moves the
price 58%." Four documents in this repository quote four different per-minute
figures for the same Indic minute: ₹7.91 (`PRD.md`), ₹5.17 and ₹8.21
(`COMPETITIVE-BUNDLES-2026.md`, at 850 and 2,300 chars/min), and ₹4.91
(`data/pricing.ts`, after the Sarvam TTS rate was halved).

**Do not quote any customer a price until section 1 of `scripts/pricing/measure.sql`
has been run.** It is one query against data you already have.

---

## 3. The new cost line nobody is metering

The product being proposed, where clients build agents by chat, introduces a cost
that does not exist in the current model.

`api/services/agent_builder/limits.py` caps builder usage per organisation per IST
day **by message count**, and its own docstring concedes the problem:

> **Counted in messages, not tokens.** Tokens are the real cost.

And `grep -rn "agent_builder" api/services/billing/ api/tasks/` returns **nothing**.
Builder usage is rate-limited, never costed, never billed.

What a build session costs, assuming ~150k input and 25k output tokens across a
conversation with growing context:

| Model class | Cost per build session |
|---|---|
| GPT-4o-mini class | **₹3.60** |
| Gemini 2.5 Flash (your seeded rate) | **₹5.20** |
| GPT-4o / Sonnet class | **₹60.00** |

Individually trivial. The exposure is that this is your **front door**, so it is
paid before any revenue, by everyone including tyre-kickers. A prospect who builds
ten agents on a frontier model and never pays costs ₹1,050 of pure loss, and you
have no line item that would show it.

**Three rules:**
1. **Run the builder on a cheap model, deliberately.** The gap between the top and
   bottom row is 17x for a task that is structured generation, not reasoning.
2. **Keep the free tier capped by session, not just by message**, and make the cap a
   business decision that someone owns.
3. **Meter it even when you do not charge for it.** You cannot manage a CAC line you
   cannot see, and this is the same gap the 12 September audit found on embed text
   chat (finding F5), which runs LLM turns with no credit check at all.

---

## 4. The pricing architecture: three meters, not one

You have one meter today, a prepaid rupee balance, and it prices variable vendor
cost well. The product you are describing has three different cost shapes and needs
three meters.

| Product | Cost shape | Right meter | Status |
|---|---|---|---|
| Voice, WhatsApp, telephony, runtime LLM | Variable, per-second, vendor pass-through | **Prepaid balance** | Built, works, keep it |
| Agent building by chat | Bursty, front-loaded, pre-revenue | **Included allowance, capped** | Rate-limited only, not metered |
| Workspace, memory, inbox, the humans in it | Near-fixed per active human | **Per seat, per month** | Does not exist |
| Enterprise setup "with a guy" | Human hours | **One-time fee plus retainer** | Does not exist |

**The seat is the missing line, and it is the fix for the margin problem.**

- A seat's marginal cost is storage, retrieval and support. Call it 95% gross margin.
- Your competitor for that seat is a ₹15,000-35,000/month ops person. A seat at
  **₹699/month is 2-4% of that human's cost**, which is an easy yes.
- 100 seats at ₹699 contributes roughly ₹66,000/month, which covers the entire
  managed multi-AZ infrastructure floor twice over.

That gives a clean rule to run the company by:

> **Seat revenue covers fixed infrastructure. Usage revenue covers vendors. The plan
> fee covers everything else.**

Today you have no revenue line that is uncorrelated with minutes, which is precisely
why the margin moves when the customer changes stack. The seat fixes that
structurally, not cosmetically.

---

## 5. Proposed tiers

Keeps ₹2,999 as the anchor, because `COMPETITIVE-BUNDLES-2026.md` establishes it is
the Indian market's entry subscription price and Agni charges exactly that for 300
minutes and 5 concurrency.

| | Free | Starter | Growth | Scale | Enterprise |
|---|---|---|---|---|---|
| Price | ₹0 | **₹2,999/mo** | **₹7,999/mo** | **₹19,999/mo** | Setup + retainer |
| Seats included | 1 | 1 | 3 | 10 | Negotiated |
| Extra seat | — | ₹699/mo | ₹699/mo | ₹599/mo | — |
| Call balance | ₹0 | ₹2,500 | ₹7,200 | ₹18,500 | Negotiated |
| Build sessions/mo | 5 | 25 | 100 | Unlimited | Unlimited |
| Numbers included | 0 | 1 | 2 | 5 | Negotiated |
| Concurrency | 0 | 5 | 15 | 30 | Negotiated |
| Live phone number | No | Yes | Yes | Yes | Yes |

- **Free tier costs you at most ₹60** (5 build sessions on a cheap model, no number,
  no balance, no calls). That is an affordable front door and it is capped by
  construction rather than by trust.
- **Concurrency rises with the tier and is never sold separately.** It costs almost
  nothing and reads as generous. `COMPETITIVE-BUNDLES-2026.md` reaches the same
  conclusion and notes the Indian SMB buyer reacts badly to a second meter.
- **Do not offer Premium stacks on Starter.** At 46% gross margin it is your worst
  customer at your cheapest price.

### Enterprise, the "with a guy" tier

This is a services business wearing a software price. Treat it accordingly.

- **One-time setup: ₹75,000 to ₹2,00,000**, invoiced separately, never folded into
  the subscription. Folding it in hides a 40-60% gross margin inside an 70% line and
  you will not notice until it is structural.
- **Monthly retainer from ₹25,000**, which buys named support and a response time,
  not minutes.
- **Usage at Scale rates on top**, drawn from the same prepaid balance so the ledger,
  the GST invoice and the reconciliation all keep working unchanged.
- Track delivery hours against it from day one. The failure mode is a customer whose
  setup consumed 60 hours against a ₹75,000 fee.

### The n8n agencies, which is your only real demand

Two mechanisms already exist in the code and you should use the simpler one first.

1. **Referral commission** — `api/routes/partners.py`, `partner_admin.py`, with
   applications, referrals, commission and statements already built. The agency
   refers, the client pays you directly, the agency earns a percentage. Zero build,
   zero credit risk, and the client relationship is yours.
2. **Wholesale balance** — the agency buys credit at a discount and resells at its
   own margin. More revenue per agency, but you take credit risk and lose the end
   customer relationship.

Start with commission. Move an agency to wholesale only when it has brought three
paying clients.

---

## 6. What to do before changing any price

In order. None of these is a build.

1. **Run `scripts/pricing/measure.sql` section 1** to settle TTS characters per
   minute. Every number in this document moves with it, by up to 58%.
2. **Run section 6** to get minutes per organisation per month, and size each tier's
   balance so a typical account consumes 70-80% of it.
3. **Reconcile the four different per-minute figures** across `PRD.md`,
   `COMPETITIVE-BUNDLES-2026.md` and `data/pricing.ts`. Today a salesperson can quote
   ₹4.91 or ₹8.21 for the same minute depending on which file they opened.
4. **Meter agent-builder token spend**, even at ₹0 charge, so the front door has a
   visible cost.
5. **Fix audit finding F19 first.** Export status is self-declared, so any org admin
   can set `country_code` to a non-IN value and buy credit with no GST. That is an
   18% self-service discount with the liability landing on you, and it changes every
   margin figure above.

---

## 7. What this does not answer

- **Seat pricing is a guess anchored to a salary, not a measurement.** ₹699 is
  defensible arithmetic against a ₹15,000-35,000 ops person; it is not validated. The
  first three customers settle it.
- **Build-session token counts are modelled, not measured.** 150k in / 25k out is a
  reasonable shape for a 30-turn conversation with growing context. Instrument it and
  replace the estimate.
- **Support cost per account is entirely unmodelled**, and for the enterprise tier it
  is the line most likely to eat the margin.
- **Infra numbers are AWS ap-south-1 list prices typed by hand**, per the script's own
  warning, and `CONCURRENT_CALLS_PER_VCPU` is a guess that sets the whole fleet size.

---

## Addendum — per agent, not per seat, and not per execution

Added the same day, after the founder proposed monthly-per-agent, per-execution, or token
limits per org. **This reverses the ₹699 per-seat recommendation in §4.**

### Per execution is wrong, and this product already proved it

| One "execution" | Duration | Vendor cost |
|---|---|---|
| Wrong number, no answer | 15s | ₹0.43 |
| COD confirmation, clean | 45s | ₹1.29 |
| NDR, buyer argues | 300s | ₹8.61 |

Twenty times the cost for one billable unit. 15-second pulse billing exists because a
per-call unit is dishonest; reintroducing it one layer up repeats the mistake the billing
engine was built to avoid.

### Per seat was wrong, and the reason matters

§4 proposed ₹699 per seat as the revenue line uncorrelated with minutes. That is true and it
is still the wrong meter, because **seats tax the one behaviour the product depends on.**

The memory only compounds if humans confirm facts. Charge per person and the customer adds
fewer people, fewer facts get confirmed, agents stop improving, and the differentiator
degrades. Metering seats means metering the core mechanic.

> **Unlimited seats. Price per agent.**

Seats gate what should be maximised. Agents gate what actually costs money. And "hire an
agent" matches the catalogue's mental model, so the invoice reads like the product.

### The bands

Per agent, sized to the work it does, at the shipped ₹4.91/min Indic rate and the 1.4x
managed markup:

| Band | Brand size | Price/mo | Included | Vendor cost | GM | Customer net | ROI |
|---|---|---|---|---|---|---|---|
| Starter | ~1,000 orders/mo | ₹4,999 | ₹2,500 | ₹839 | **83%** | ₹17,442 | 3.5x |
| Growth | ~3,000 orders/mo | ₹9,999 | ₹7,200 | ₹2,518 | **75%** | ₹52,326 | 5.2x |
| Scale | ~10,000 orders/mo | ₹24,999 | ₹18,500 | ₹8,392 | **66%** | ₹174,421 | 7.0x |

Overage draws from the existing prepaid balance at existing rates. Nothing new to build.

Gaming is self-correcting: cramming several jobs into one agent raises its volume, which
moves it up a band. The price follows the work either way.

### The second-agent discount

> Agent 1 full price · Agent 2 −30% · Agent 3+ −40%

Not a volume discount dressed up. The marginal cost genuinely is lower — shared memory,
shared connectors, shared onboarding — because the second agent starts on facts the first
one earned and a human already confirmed.

**The discount states the compounding story on the invoice**, which is the one document a
customer reads carefully. No competitor can price this way without shared verified memory.

### Token limits: internal only

- **Never on the invoice.** Nobody buying an NDR agent thinks in tokens, and it makes the
  bill unpredictable, which is exactly what prepaid solved for Indian SMBs.
- **Internally, urgently.** A build session costs ₹3.60-₹60, is paid before any revenue, and
  `grep -rn agent_builder api/services/billing/` returns nothing. `limits.py` caps by message
  count and its own docstring concedes *"tokens are the real cost"*.

### Final shape

| Line | Meter | Customer-visible |
|---|---|---|
| Agent subscription | Per agent per month, banded by volume | **Yes — this is the price** |
| Overage | Prepaid balance | Yes, built |
| Seats | None. Unlimited | No, deliberately free |
| Builder tokens | Internal cap and cost line | No |
| Enterprise setup | One-time fee, hours tracked separately | Yes |

### Outcome pricing, later

Charging per recovered order rather than per agent would make the stack choice a Decibyl
cost decision rather than a 31-point margin hole the customer punches. It requires
attribution defensible in a dispute, which is what the action graph and outcome record
provide. **Offer it to customer three or four, once the trail can be shown on a screen.**
A billing dispute with a first customer is expensive in ways unrelated to money.
