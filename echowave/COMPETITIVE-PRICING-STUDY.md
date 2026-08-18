# Competitive and pricing study — August 2026

**Office of the CFO.** Companion to `PRICING-REVIEW.md` (is the price book right?)
and `PROVIDER-PRICING.md` (what do vendors charge?). This one asks the two
questions those do not: **what does the market charge, and where are we leaking?**

No code or configuration was changed in producing this. Every recommendation is
a pricing decision or a one-row rate-card edit.

---

## 0. The finding in one line

`PROVIDER-PRICING.md §4` claims we charge *"60% less than the cheapest major
platform."* **Bolna's published platform fee is $0.02/min — identical to ours.**
We are not the cheapest by a distance; we are roughly at market. What is
genuinely distinctive is the number of places value leaves without an invoice.

**Identified leakage: ~$190,800/yr at 500k min/month — approximately 100% of
current gross margin.**

---

## 1. What the market actually charges

Verified against vendor pricing pages and dated 2026 analyses, August 2026.
The structural fact: **the headline fee is not the price.** Buyers now compare
landed cost.

| Platform | Platform fee | All-in/min | Floor or commitment | Model |
|---|---|---|---|---|
| **Decibyl** | **$0.020** | **$0.086** | **none** | fee + 1.4× managed providers |
| Bolna | $0.020 | $0.045–0.060 | $10 min. top-up | fee + pass-through, 30s pulse |
| Vapi | $0.050 | $0.10–0.30 | $10/line/mo over 10 | fee + at-cost providers |
| Retell AI | $0.055 | $0.07–0.31 | $8/slot/mo over 20 | fee + priced add-ons |
| Bland AI | bundled | ~$0.090 | Scale plan | all-in, no BYOK |
| Synthflow | $0.090 | $0.15–0.37 | Enterprise $30k/yr | voice engine + add-ons |
| ElevenLabs Agents | bundled | $0.08+ | $990/mo Business | plan + included minutes |

### The India field — the buyers we actually compete for

| Vendor | Rate | Includes | Threat |
|---|---|---|---|
| **Decibyl** | **₹5.17–8.21/min** | fee + marked-up providers + carriage | — |
| Bolna | ₹5.52/min | fee + pass-through, BYOK, 30s pulse | **Direct.** Same fee, India-native |
| Trikon | ₹5.00/min | bundled all-in, INR, GST invoice | **High.** Undercuts on simplicity |
| Dvaarik | ₹2.00/min | whole billed minute | price floor-setter |
| Ringg | ₹9–12/min | fee + separate telephony/models | low |
| Gnani.ai | custom | enterprise BFSI, 30M conv./day | enterprise only |
| Dograh (OSS) | ₹0 licence | self-host, BYOK, visual builder, MCP | **Structural** — see §5 |

---

## 2. Our own numbers disagree with each other

Three internal documents model the same managed Indic minute and produce three
different answers, because each assumes a different volume of synthesised speech.

| Source | Chars/min | Provider cost | Customer pays | GM $/min | GM % |
|---|---|---|---|---|---|
| `PROVIDER-PRICING.md` | 850 | $0.0259 | $0.0538 | $0.0279 | 51.8% |
| `PRICING-REVIEW.md` | 900 | $0.0267 | $0.0549 | $0.0282 | 51.3% |
| `PRD.md` (estimator) | 2,300 | $0.0486 | $0.0855 | $0.0369 | 43.2% |

**A 2.7× spread in the single largest cost line.** It moves the bill from ₹5.17
to ₹8.21/min — 59% — and margin by 8.6 points. TTS is 42–66% of provider cost,
so this is not a rounding argument.

> **Fix this first; it costs nothing.** The pipeline already records characters
> synthesised per call. One query replaces all three assumptions. The same
> applies to `LLM_INPUT_SHARE = 0.7`, where the Tokens screen already reports
> the fact. **No pricing decision below should execute before that query runs.**

### The PRD's margin table is stale, in our favour

`PRD.md §4` concludes margin is "fixed at $0.020/min no matter what the customer
runs" and calls it "a pricing bug." That predates `MANAGED_PROVIDER_MARKUP_BPS`,
now live at 1.4×. Margin is no longer flat:

| Stack | Provider cost | Revenue | GM $/min | GM % | PRD claimed |
|---|---|---|---|---|---|
| Indic managed | $0.0486 | $0.0855 | $0.0369 | 43.2% | $0.020 |
| Western managed | $0.1297 | $0.1990 | $0.0694 | 34.9% | $0.020 |
| Premium managed | $0.1408 | $0.2147 | $0.0738 | 34.4% | $0.020 |
| **Any stack, BYOK** | $0.0062 | $0.0262 | **$0.0200** | 76.2% | $0.020 |

The markup did its job. **BYOK is the only place the old pathology survives** —
and a BYOK customer consuming identical orchestration, concurrency and support
pays us **$0.0369/min less** than a managed one.

---

## 3. The leak ledger

Sized at **500k min/month, 45s average call**. Tiered by nature — an unpriced
feature and a negative-margin line are not the same problem.

### Tier 1 — margin defects (real money, already lost)

| Leak | Annual | Detail |
|---|---|---|
| ElevenLabs multilingual v2 billed at Flash rate | **−$34,500** | Vendor $0.10/1k; we bill $0.05 × 1.4. At 2,300 chars/min that is **−$0.069/min — a negative-margin call.** One rate row fixes it. |
| Gemini 2.5 Flash-Lite retires **16 Oct 2026** | **−$13,700** | Replacement lists $0.25/$1.50 per M — **3.7× blended**. `fast`, `lite`, `zen` all point at it. Recoverable via 1.4× only if repointed before the date. |
| Uncosted components + stale FX | unquantified | No rate row → bills **zero**, reports `uncosted`, overstates margin. ₹96 fallback under-bills **−7.7%** at ₹104. |

### Tier 2 — structural under-monetisation (delivered, never invoiced)

| Leak | Annual | Detail |
|---|---|---|
| Feature add-ons bundled free | **$60,000** | Retell prices knowledge base (+$0.005/min *and* $8/KB/mo), PII removal (+$0.01/min), denoising (+$0.005/min), QA ($0.10/min), branded calls (+$0.10/call). Vapi prices HIPAA $2,000/mo, ZDR $1,000/mo. **We built all of it and charge for none.** |
| BYOK earns no provider margin | **$30,500** | At 30% share. We carry orchestration, concurrency, storage, support; collect $0.02. |
| Concurrency given away | **$22,800** | Enforced in `call_concurrency`, never billed. Vapi $10/line/mo; Retell $8/slot/mo. |
| No monthly floor | **$12,000** | Every competitor has one. We have zero. Sub-scale accounts can bill ₹0 in a month. |
| Telephony at 0% markup | **$7,500** | 30% of the cheap stack. We carry the carrier relationship, KYC filing and credit risk for nothing. Number rental already runs at **28% margin** (₹250→₹349) where Retell charges ~₹960 — the principle is accepted, just not applied to minutes. |

### Tier 3 — deliberate giveaways (strategy, but measure them)

| Leak | Annual | Detail |
|---|---|---|
| 15s pulse vs whole-minute | **$40,000** | **25% of platform-fee revenue** forgone on a 45s call; 50% on a 25s call. Likely worth keeping — it is provable on the invoice and a competitor cannot answer it without rewriting their metering. But `foregone_paise` already exists; put it in the board pack as a marketing line. |
| $5 signup bonus, unqualified | **$18,000** | Amount is right (matches Bolna, under Retell's $10). Problem is it is granted on signup with no qualification, 300/month. **Gate it, don't cut it.** |

**Total: ~$190,800/yr** against gross margin of ~$191,000/yr. ~$48k is Tier 1/3
measurement work; **~$133k is revenue we built the product to deliver and never
asked for.**

---

## 4. Options modelled

| Option | Customer pays | GM $/min | GM % | ₹/min | Verdict |
|---|---|---|---|---|---|
| **Today** — $0.02 flat + 1.4× | $0.0855 | $0.0369 | 43.2% | ₹8.21 | baseline |
| Fee → $0.03 + 1.4× | $0.0955 | $0.0469 | 49.1% | ₹9.17 | +27% GM, still under Vapi |
| 25% of provider, $0.02 floor | $0.0855 | $0.0369 | 43.2% | ₹8.21 | **no effect on Indic** |
| 1.4× + telephony 1.2× + fee $0.025 | $0.0918 | $0.0432 | 47.1% | ₹8.81 | broadest base, smallest headline move |

> **The percentage fee does not do what `PRD.md §4` expects.** A 25% fee with a
> $0.02 floor changes the Indic minute by **exactly zero** — the floor binds,
> because 25% of $0.0486 is $0.0121. It adds $0.0124/min on Western and $0.0152
> on premium. It is *a premium-stack tax, not a general price rise*, and will
> not move the India book — which is the book we have.

### The positioning problem underneath the arithmetic

We target India, where credible bundled competitors sit at **₹5.00–5.52**. Our
Indic minute lands at **₹8.21** on the estimator's own assumptions. **We may
already be above market in our home segment while believing we are 60% below
it.** Meanwhile we serve the entire dollar-paying world — where Vapi charges
2.5× our fee — at the India price, because the fee is global and flat.

**Clearest single move: regionalise the fee.** Hold $0.02 for INR-settled
accounts, where it is a real weapon against Bolna and Trikon. Introduce **$0.04
for USD-settled accounts** — still 20% under Vapi, 27% under Retell, less than
half Synthflow. Rates are already stored per account, so it is configuration,
not migration.

---

## 5. Moat check

The PRD's strategic argument — compete on billing, compliance and cost
transparency, not the agent loop — survives contact with the market. Two
qualifications.

**The compliance moat is genuine and time-boxed.** DPDP penalties reach ₹250
crore per breach, enforcement window closing May 2027, and every Indian B2B
buyer needs a DPA with sub-processor provisions, breach notification, retention
and deletion. We have built all of it. **No US competitor will build Indian tax
and DPDP plumbing.** But Trikon already advertises GST invoicing on a ₹5/min
bundle — the moat is against *US* entrants, not Indian ones.

**The licence is the structural risk nobody has priced.** We ship BSD-2-Clause,
and `.gitmodules` points `echowave/pipecat` at **`dograh-hq/pipecat`** — Dograh
is not merely a competitor, it is our upstream. It ships the same feature list
(visual workflow builder, MCP-native, BYOK, telephony) and publicly positions
*"no per-minute platform fee on top of your AI vendor bills."* Any customer with
an engineer can self-host and pay us nothing. **That is the ceiling on what the
orchestration fee can ever be**, and the strongest argument for moving revenue
toward what a fork cannot replicate: managed carrier accounts, GST invoicing as
a service, compliance attestations, and the provider markup.

### Positioning exposure — resolve before the next customer signs

`README.md §Pricing model` states provider costs are passed through **"at cost,
with no markup."** Untrue for managed inference since the markup shipped; now
1.4×. Still true for BYOK. **This is a pricing claim in a public repository that
contradicts what we bill.** The defence is sound — we carry the vendor account,
credit risk and key rotation — but it must be *stated*. Narrow the sentence to
BYOK, or publish the multiple.

---

## 6. Ninety days, in order

| When | Action | Value |
|---|---|---|
| Week 1 | **Measure chars/min and token split** on production traffic | unblocks everything |
| Week 1 | **Add the ElevenLabs multilingual rate row** | +$34,500/yr |
| Week 2 | **Correct both pricing claims** (README "no markup"; "60% cheaper") | removes exposure |
| Week 3–4 | **Price the add-ons** — KB, PII redaction, QA, branded calls, attestations | +$60,000/yr |
| Week 4 | **Bill concurrency** above 20 slots at ₹600/slot/mo | +$22,800/yr |
| Week 5 | **Regionalise the fee** — $0.04 USD-settled, hold ₹ at $0.02 | 2× on dollar minutes |
| Week 6 | **Close the BYOK gap** — $0.03/min orchestration fee | +$30,500/yr |
| **Before 16 Oct** | **Repoint the Flash-Lite tiers** — hard deadline | avoids −$13,700/yr |
| Quarter | **Markup telephony 1.2×; rental ₹349 → ₹599** | +$7,500/yr and up |
| Quarter | **₹5,000/mo floor**, credited against usage | +$12,000/yr |
| Quarter | **Gate the signup bonus** — verified domain, grant on first top-up | recovers most of $18,000 |
| Standing | **Own the rate card quarterly.** `AS_OF` is an expiry date. | protects all of it |

**None of the Tier 2 items require a headline price rise.** They monetise
capability that already ships, where every competitor already charges — roughly
**$133,000/yr without giving up the ₹5-a-minute story or the pulse.** The lever
we should *not* pull is the India platform fee.

---

## 7. Caveats

**Assumptions.** 500k min/mo, 45s average call, 666,667 calls/mo, 30% BYOK,
200 peak lines, 40 accounts, 300 signups/mo. **Scenario inputs, not observed
values — substitute actuals before this reaches a board pack.** Rates from the
seeded book at `AS_OF = 2026-08`, ₹96/USD, 1.4× markup, 2,300 chars and 2,500
tokens/min, 70/30 LLM split. Add-on sizing applies Retell's published rates to
half our base — the most speculative line here, worth testing on three customers
first. **Nobody pays list**; negotiated rates move in both directions.

**Where I would push back on myself.**

- **$190,800 is not $190,800 of recoverable cash.** Tier 1 is money already
  lost; Tier 2 carries a churn cost this analysis does not model. At 40
  accounts, losing two to a price rise costs more than the add-on revenue earns.
- **The pulse should probably survive.** It costs $40,000/yr and is the one
  claim a competitor cannot answer without rewriting their metering. Cutting it
  to save 3% of revenue would be a poor trade.
- **The India price may be the real problem, not the leaks.** If measured
  chars/min comes back near 2,300, we are a ₹8.21 product in a ₹5.00–5.52
  market and no amount of add-on monetisation fixes that. If it comes back near
  900, we are at ₹5.27 and competitive, and this becomes a monetisation exercise
  rather than a repricing one. **That single query decides which conversation
  we are having.**

---

## Sources

Competitor pricing verified August 2026 against
[Bolna](https://www.bolna.ai/pricing), [Vapi](https://vapi.ai/pricing),
[Retell AI](https://www.retellai.com/pricing),
[ElevenLabs](https://www.cekura.ai/blogs/elevenlabs-pricing),
[Synthflow](https://quiq.com/blog/synthflow-pricing/),
[Bland](https://www.retellai.com/blog/vapi-vs-bland).
India field from the
[Dvaarik India AI Voice Agent Pricing Index](https://www.dvaarik.com/research/india-ai-voice-agent-pricing-index),
[Trikon](https://www.trikon.tech/voice-pricing-comparison) and
[Caller Digital](https://caller.digital/blog/top-10-voice-ai-agents-india-2026).
Open-source threat from [Dograh](https://github.com/dograh-hq/dograh).
Regulatory position from [ConsentOS DPDP for SaaS](https://consentos.in/learn/dpdp-for-saas/)
and [Caller Digital's 2026 regulatory map](https://www.caller.digital/blog/voice-ai-india-regulatory-map-2026).

Internal figures derived from `api/services/billing/`, `api/constants.py`,
`PRICING-REVIEW.md`, `PROVIDER-PRICING.md` and `PRD.md`.
