# Bundles, concurrency, and a correction

**Researched 19 Aug 2026** against live vendor pricing pages. Read this before
`COMPETITIVE-PRICING-STUDY.md`, which it corrects on a central point.

## The correction

The study's headline says:

> `PROVIDER-PRICING.md §4` claims we charge *"60% less than the cheapest major
> platform."* **Bolna's published platform fee is $0.02/min — identical to
> ours.** We are not the cheapest by a distance; we are roughly at market.

**That is wrong, and the study's own India table disproves it.** The table
quotes Bolna at ₹5.52/min. Bolna's pricing page prints that figure as
`6.00¢ (₹5.52)` — ₹5.52 *is* six cents. The study read Bolna's platform-fee
band ($0.045 at volume to $0.060 at list) as an all-in price, and then recorded
$0.020 as the fee. Both halves of that row are wrong.

The real position. **Ours is a range, not a number** — the platform fee is
tiered on which keys the customer brought (`BYOK_TIERED_FEE_ENABLED`), because
the components are not worth the same to us: the markup margin on a typical
Indic minute is about $0.014 on speech synthesis, $0.002 on transcription and
$0.0005 on the language model.

| | Platform fee / min |
|---|---|
| **Decibyl — everything managed** | **$0.020** |
| **Decibyl — their transcription, our voice** | **$0.022** |
| **Decibyl — their voice** | **$0.035** |
| Vapi | $0.050 |
| Retell | $0.055 |
| Bolna | $0.060 list, $0.045 at ~$3k/mo |

The tiers are cut on *which* component, not how many, and they do not stack —
a customer who brings their own voice pays $0.035 whether or not they also
brought transcription. The language model is deliberately never tiered: at a
twentieth of a cent, any fee for it would exceed the margin it costs us, and
the account's bill would *rise* when they brought their own key.

**Compare on the right row.** Every competitor charges one platform fee whether
or not you bring keys, so their headline is the whole of what they earn on a
BYOK minute. Ours is $0.035 in that case — **30–42% under them**, not a third
of them. The $0.020 row is not comparable to their headline at all: on a
managed minute we also earn the 1.4x provider markup, so what we make per
minute is well above the fee.

So `PROVIDER-PRICING.md`'s "60% less than the cheapest major platform" holds
for a managed minute's headline fee and overstates it for a BYOK one.

That still inverts the study's strategic conclusion — we are cheapest on every
row against every competitor, not "roughly at market". But the headroom is
30–42% on the BYOK book, not 3x, and the BYOK book is where the platform fee
is all we get.

The leak ledger in the study stands. Its framing does not.

## How the market packages bundles

Nobody sells minutes alone. Every plan bundles **minutes + concurrency**, which
is the shape our own bundles should take.

| Plan | Price/mo | Minutes | Concurrency | Rate |
|---|---|---|---|---|
| Bolna Starter | $350 | 5,000 | 20 | 7¢ |
| Bolna Growth | $1,200 | 20,000 | 50 | 6¢ |
| Bolna Scale | $2,500 | 50,000 | 75 | 5¢ |
| Bolna Pilot *(one-time)* | $500 | 10,000 +20% bonus | 50 | 5¢ |
| Vapi Build | usage | — | 10 incl., then $10/line | 5¢ |
| Aixclerate Starter | $99 | 500 | 2 | — |
| Aixclerate Business | $299 | 2,000 | 4 | — |

Three patterns worth stealing:

1. **The rate falls as the plan rises.** 7¢ → 6¢ → 5¢. The discount is the
   reason to commit, and it is legible on one line.
2. **Concurrency rises with the plan** rather than being sold separately. It
   costs the vendor almost nothing and reads as generous.
3. **A one-time pilot exists** and is priced *below* the equivalent monthly
   commitment, with a bonus on top. It converts a buyer who will not sign a
   subscription yet.

### The Indian field, which is who we actually sell to

| | Price | Included | Concurrency | Numbers |
|---|---|---|---|---|
| **Agni (Ravan.ai) Starter** | **₹2,999/mo** | **300 min** | **5** | India DID ₹350/mo + ₹0.80/min |
| Dvaarik | ₹2.00/min | whole billed minute | — | — |
| Trikon | ₹5.00/min | bundled all-in | — | — |
| Ringg | ₹9–12/min | fee + pass-through | — | — |

Market benchmarks for India 2026: **₹2–12/min headline, ₹3–6 the common
mid-market band, and a platform subscription "typically from ₹2,999/month".**

## What this says about the proposed bundles

**₹2,999 is exactly the right number.** It is the Indian market's entry
subscription anchor — Agni charges it, and the survey literature names it as
the typical floor. A buyer comparing us to Agni sees the same price and does
not have to think about it.

**The included balance is not generous. It is roughly at parity.** An earlier
draft of this document said ₹2,500 buys 600–1,000 minutes, on an assumed
₹2.5–4/min. That was wrong: our own study puts the all-in charge at
**₹5.17–8.21/min**, so the grant buys

| Charged per minute | What ₹2,500 buys |
|---|---|
| ₹5.17 *(at 850 TTS chars/min)* | **483 minutes** |
| ₹8.21 *(at 2,300 TTS chars/min)* | **304 minutes** |

against **Agni's 300 minutes at the same ₹2,999**. At the pessimistic end we
are level with them. At the optimistic end we are 60% better — not three times
better.

**So cutting the grant to ₹1,500 would have been a mistake**, and this document
recommended it. ₹1,500 buys 183–290 minutes: *strictly worse than the
competitor at the same headline price*, which is the one comparison every buyer
actually makes. **Keep ₹2,500.**

### The unit: a balance, and an earlier draft was wrong about it

This document previously recommended granting **minutes** rather than rupees,
on the grounds that every competitor advertises minutes. That was wrong, and
the arithmetic shows why.

**Nobody sells all-in minutes. They sell fee-minutes.** Bolna's plan prices are
exactly their platform fee multiplied by the included minutes:

| Plan | Price | Minutes | Price ÷ minutes | Their quoted fee |
|---|---|---|---|---|
| Starter | $350 | 5,000 | **$0.070** | 7¢ |
| Growth | $1,200 | 20,000 | **$0.060** | 6¢ |
| Scale | $2,500 | 50,000 | **$0.050** | 5¢ |

To the cent, on all three. So "5,000 minutes included" means five thousand
minutes *of Bolna's fee*. Speech, the language model, the voice and telephony
are still pass-through on top, drawn from a separate balance — which is exactly
what their own docs describe as Parts A, B and C.

**So a balance is the shape the market actually uses**, and ours is right. The
minutes in a competitor's plan are a headline number sitting on top of a credit
balance, not a replacement for one.

That also makes the comparison with Agni's 300 minutes less direct than the
table above implies: theirs quotes a number for the AI stack with the number
and PSTN priced separately, ours is a rupee balance that everything draws from.
Same price point, different envelope. Worth confirming what Agni's 300 actually
covers before either figure is used in a sales conversation.

**Minutes only become sayable if a minute gets a fixed price.** They cannot be
sold honestly while a minute costs ₹5.17 on one stack and ₹8.21 on another —
the entitlement would mean something different for every customer. That is the
dependency: `SIMPLE-MODEL-CHOICE.md` proposes fixed per-minute tier prices, and
minute-denominated bundles are a thing that becomes possible *after* those
land, not before. Until then, a balance is both what the market does and the
only honest option.

<!-- The chars/min measurement still decides the grant's real worth. It is the
     same unmeasured number, and here it decides whether the flagship bundle
     beats the competitor or ties with it. -->

**What ₹2,500 is worth is still a measurement, not a decision.** At 850
chars/min it is ~480 minutes and the bundle wins comfortably; at 2,300 it is
~300 and it ties. Section 1 of `scripts/pricing/measure.sql` settles which —
and this is no longer only a margin question, it is whether the headline
product has a story.

Whichever is chosen, **the balance must be measured, not assumed.** Section 6 of
`scripts/pricing/measure.sql` returns minutes per organisation per month; the
grant should be sized so a typical account uses 70–80% of it. Below that the
bundle feels like a waste and people churn to pay-as-you-go; above it, overage
surprises them.

### Number entitlement

Agni charges ₹350/month for an India DID **on top of** the ₹2,999. We include
one at ₹499 of value inside the bundle. Keep that — an included number is the
single clearest way the bundle reads as better, and it removes the step where a
new customer has to buy something a second time.

## Concurrency

| | Included | Then |
|---|---|---|
| Retell | 20 | $8 / concurrency / mo |
| Vapi | 10 | $10 / line / mo |
| Bolna | 20 / 50 / 75, by plan | not sold separately |
| Agni | 5 (at ₹2,999) | — |
| **Decibyl** | **enforced, never billed** | **—** |

Two viable models and they are not compatible:

- **Sell it per line** (Vapi, Retell). More revenue, and it prices a real
  constraint. But it is a second meter on the invoice, and the Indian SMB buyer
  we are aiming at reacts badly to a second meter.
- **Bundle it into the tier** (Bolna). Simpler, and the increment costs us
  almost nothing until it is genuinely large.

**Recommend bundling, with a per-line price above the top tier.** Concretely:

| Plan | Concurrency included |
|---|---|
| Pay-as-you-go | 5 |
| ₹2,999 | 10 |
| ₹6,999 | 25 |
| Above that | ₹400 / line / month |

Ten at ₹2,999 is double Agni's five at the same price, and the increment costs
us nothing until those lines are actually all busy. ₹400/line sits below
Retell's $8 (~₹700) and Vapi's $10 (~₹880), which keeps the "cheapest
infrastructure" claim true at every layer rather than only at the headline.

**Size the top of this against reality, not the limiter.** Section 6b of the
measurement pack returns peak concurrency actually reached. If nobody has ever
exceeded four, the tiers above are theatre and should be flattened.

## Prebuilt agents — the other thing Bolna does well

Bolna ships **17 production-ready agent templates across 8 industries**
(e-commerce cart recovery and delivery confirmation, BFSI payment reminders,
hospitality and salon booking, recruitment screening, ed-tech lead
qualification, health-tech onboarding, real-estate qualification). Each has an
**"Import this agent →" link — one click to a working agent**, pre-configured
with prompts, workflow and settings, most in English + Hindi.

We already have six of these built and tested in
`api/services/agent_templates/catalogue.py` — clinic appointments, real-estate
qualification, lending payment reminders, ed-tech admissions, COD confirmation,
restaurant reservations — with per-vertical legal guardrails Bolna does not
appear to have. **They are reachable through the MCP surface and the agent
builder, and there is no gallery in the product.** A customer who does not ask
an assistant never sees them.

That is a one-click-import gallery's worth of work against a catalogue that
already exists, and it is the cheapest conversion improvement on this list.

## Sources

Live vendor pages and dated 2026 analyses, fetched 19 Aug 2026:
Bolna pricing and docs, Vapi pricing, Retell pricing, Dvaarik's India AI Voice
Agent Pricing Index, Caller Digital's India pricing survey.
