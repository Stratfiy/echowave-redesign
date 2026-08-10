# Decibyl — product requirements

The commercial companion to `HANDOVER.md`. That document says what exists and
how it works; this one says who it is for, what it should cost, where it wins,
and what to build next.

Written to be argued with. Every number is derived from the running code and the
seeded price book — the derivations are shown, so a disagreement can be about
the assumption rather than the arithmetic.

---

## 1. What Decibyl is

A voice-AI platform for building conversational phone agents, sold prepaid, with
Indian tax compliance and per-second-class billing built in rather than bolted
on.

**The one-line positioning:** *the voice-AI platform that bills the way Indian
businesses actually buy — prepaid, in rupees, GST-compliant, in 15-second
pulses instead of rounded-up minutes.*

Everything else in the market is a US product with an India problem: priced in
dollars, billed monthly on a card, no GST invoice, no rupee ledger, and rounding
every 25-second call up to a full minute.

---

## 2. Market and competition

### The field

| Product | Model | Where it is strong | Where it leaves a gap |
|---|---|---|---|
| **Vapi** | Platform fee + provider pass-through | Developer ergonomics, ecosystem | US-centric billing, no GST, per-minute rounding |
| **Retell AI** | All-in per-minute | Clean analytics, latency reporting | Opaque provider costs, no BYOK transparency |
| **Bolna** | Platform fee + pass-through | India presence, price | Thin analytics, limited compliance surface |
| **Synthflow / Air** | Seat + usage | No-code reach | Weak developer surface |
| **LiveKit Agents** | Infrastructure only | Best-in-class latency primitives | Not a product — you build the business layer |

*Competitor pricing moves constantly. Treat the shape of each model as the
durable fact and re-check the numbers before quoting them to a customer.*

### Where the gap actually is

Not in the agent runtime — that is commoditised, and everyone is running the
same handful of STT/LLM/TTS vendors over the same open-source pipeline. **The
gap is in everything around the call**: billing that a CFO accepts, tax
documents that a CA accepts, and cost visibility that survives contact with a
finance team.

That is precisely the half this branch built.

---

## 3. Who it is for

### Primary — Indian SMB / mid-market outbound teams
Insurance, lending, edtech, healthcare front-desk, real estate. 10k–500k
minutes a month. Buy on price and on whether the invoice is usable. **Prepaid is
a feature, not a limitation** — they do not want a card on file with a US
company and an unpredictable monthly bill.

### Secondary — Indian SaaS embedding voice
Want a white-label agent inside their own product. Care about API surface,
per-tenant cost attribution, and whether they can resell with their own margin.

### Tertiary — agencies and consultancies
Run agents on behalf of clients. Need per-account isolation, per-account rates,
and defensible per-client cost reporting. **The admin dashboard already does all
three.**

### Explicitly not, yet
- **US healthcare (PHI).** HIPAA needs a BAA with every sub-processor touching
  the data; several AI vendors will not sign one. Verify before selling.
- **EU-resident data**, until SCCs and a transfer impact assessment exist.

---

## 4. Unit economics — read this before changing the price

The model is **provider cost passed through at cost, plus a platform fee**. The
fee is USD-denominated (`$0.020/min`) and billed on 15-second pulses.

Derived from the seeded price book at ₹96/USD, and the estimator's default
usage assumptions (1,400 tokens and 2,300 TTS characters per minute):

| Stack | Provider cost/min | Customer pays | Our margin | Margin % |
|---|---|---|---|---|
| Indic (Sarvam STT+TTS, gpt-4o-mini, Plivo) | $0.0624 | $0.0824 (₹7.91) | $0.020 | **24%** |
| Budget (Deepgram, gpt-4o-mini, Cartesia) | $0.1297 | $0.1497 (₹14.37) | $0.020 | **13%** |
| Premium (Deepgram, gpt-4o, ElevenLabs) | $0.2510 | $0.2710 (₹26.01) | $0.020 | **7%** |

### Three conclusions, and they are uncomfortable

**1. Margin per minute is fixed at $0.020 no matter what the customer runs.**
A customer on the premium stack consumes 4× the support, 4× the vendor risk and
4× the working capital of one on the Indic stack, and pays us exactly the same.
That is a pricing bug, not a pricing strategy.

**2. TTS dominates provider cost — by a wide margin.** At 2,300 characters a
minute, ElevenLabs list is $0.23/min against $0.0066 for gpt-4o. **The language
model is a rounding error; the voice is the entire bill.** Every optimisation
conversation should start there, and the seeded ElevenLabs rate is the one line
in the price book most worth verifying against your actual plan.

**3. At $0.020/min you need volume to matter.** 100k minutes a month is ~$2,000
(₹1.92 lakh) of gross margin. One million minutes is ~$20,000 (₹19.2 lakh).
This is a volume business at this price, or it is a different price.

### Options

| Option | Effect on Indic stack | Trade-off |
|---|---|---|
| Keep $0.020 flat | 24% margin | Simple, cheapest in market, volume-dependent |
| **Percentage fee (15–25% of provider cost)** | Scales with what the customer runs | Aligns revenue with cost-to-serve. **Recommended.** |
| Raise flat fee to $0.03 | 32% | Still competitive; easiest to implement — one field |
| Tiered by volume | Rewards commitment | `platform_volume_tiers` already exists and is unused |

**Recommendation:** move to a **percentage-of-provider-cost fee with a floor**.
It fixes conclusion 1 directly, and the rate card already stores rates
per-account, so it can be piloted on new accounts without touching existing
ones. This is a pricing decision, not an engineering one — the engine supports
either.

### The pulse, as a sales weapon

| Call length | We bill | Per-minute competitor bills | Customer saves |
|---|---|---|---|
| 25s | 30s | 60s | **50%** |
| 40s | 45s | 60s | **25%** |
| 95s | 105s | 120s | **12%** |

Outbound campaigns are full of short calls — not-interested, wrong number,
voicemail. On a book of 30–45 second calls this is a genuine 25–50% reduction
against per-minute billing, and it is **provable from the invoice**. Lead with
it.

---

## 5. Differentiators

Ranked by how hard they are to copy.

### Tier 1 — structural, months to replicate

**GST done properly.** Place of supply, CGST/SGST vs IGST, zero-rated export
under LUT, receipt vouchers on advance receipt, monthly tax invoices, gap-free
numbering per financial year. A US competitor will not build this, and an Indian
customer's CA will reject an invoice without it.

**Prepaid with a real credit ledger.** Balance gate, per-call reservations, no
surprise bills. Matches how Indian SMBs buy everything else.

**15-second pulse billing.** Not a discount — a different billing unit. Copying
it means rewriting a competitor's metering.

**Privacy metrics no one reports.** Recordings past retention (must be zero),
share-link access counts, erasure turnaround against the statutory deadline. The
first thing an enterprise security review asks for, and no competitor can
answer it.

### Tier 2 — real, weeks to replicate

**Context growth per turn.** Nobody shows that voice LLM spend grows with the
*square* of call length. It is the largest available saving and it is invisible
on every invoice in the market.

**Cost transparency at cost.** The estimator prices a stack before the first
call, from the same rate card the invoice uses. No competitor shows the customer
the vendor's actual price.

**DPDP compliance built, not promised.** Retention, erasure, export, access log,
spoken recording disclosure, derived sub-processor list. Penalties commence
13 Nov 2026; full compliance 13 May 2027. Being early is a sales asset for
exactly two years.

### Tier 3 — table stakes we have
Latency percentiles, per-call receipts, campaign management, BYOK.

---

## 6. What exists today

Complete inventory in `HANDOVER.md` §5. Summary:

**Built and tested (1,777 tests, green):** cost engine, prepaid billing via
Razorpay, GST and tax documents, telephony KYC, 8-screen admin dashboard,
retention/erasure/export/access-log, recording disclosure, sub-processor
derivation, breach scoping, latency percentiles (TTFT/TTFB/perceived), token
efficiency, context growth, tool timings.

**Deployed:** EC2, HTTPS, live against a real database.

---

## 7. Roadmap

### P0 — before a paying customer (2–3 weeks)

| Item | Why |
|---|---|
| **PDF tax invoices** | A customer cannot download an invoice. This blocks the first real sale. |
| **Low-balance email** | Prepaid without a warning email means silent service loss and churn |
| **Recost script** | Calls placed before the price book existed report 100% margin. Your own numbers are currently wrong. |
| **Verify the price book** | Especially ElevenLabs — it is the largest cost line and the most plan-dependent |
| **Pricing decision** | §4. Do not sign a customer onto a rate you intend to change. |

### P1 — first ten customers (4–6 weeks)

| Item | Why |
|---|---|
| **Managed numbers, admin route** | Provisioning, compliance and rental billing are built; nothing sets `is_platform_managed`, so the path is reachable only by hand |
| **Managed numbers, live proving** | Never run against Plivo's live Compliance API — no application filed, no number bought |
| **Credit notes** | Refunds are currently manual ledger adjustments |
| **Interruption rate** | Best remaining quality signal; needs pipeline capture |
| **Cost per outcome** | Cost per *booking*, not per call — the number that closes deals |
| **Role model** | `is_superuser` is one boolean. Agencies will need more. |

### P2 — scale (quarter)

E-invoicing (IRN via IRP — mandatory above ₹5 crore turnover), SOC 2 readiness,
multi-region, prompt-cache optimisation surfaced as a recommendation, white-label
for the SaaS segment.

---

## 8. Success metrics

**Business**

| Metric | Definition | Why |
|---|---|---|
| Gross margin per minute | Revenue − provider cost | The whole model in one number. Currently fixed at $0.020. |
| Minutes per account per month | — | Volume business; this is the growth axis |
| Prepaid top-up frequency | — | Leading indicator of both retention and expansion |
| Cost per completed outcome | Spend ÷ successful dispositions | The number a customer actually cares about |

**Product**

| Metric | Target | Where |
|---|---|---|
| Perceived latency p95 | < 800ms | `/superadmin/billing/latency` |
| Tokens per minute | Flat month over month | `/superadmin/billing/tokens` |
| Context growth multiple | < 3× by turn 10 | Tokens tab |
| Prompt cache hit rate | > 50% | Tokens tab |
| Uncosted call share | 0% | Unit economics |

**Compliance**

| Metric | Target |
|---|---|
| Recordings past retention | **0, always** |
| Erasure turnaround | < 7 days against a 30-day deadline |
| Recording disclosure coverage | 100% of voice calls |

---

## 9. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| **Margin too thin to fund support** | High | §4. Decide the pricing model before scale, not after. |
| **Vendor price changes silently erode margin** | High | Price book has an `AS_OF` date; unit economics screen shows the gap. Needs a quarterly review owner. |
| **TTS cost dominates and is plan-dependent** | High | Verify actual plan rates; negotiate volume commits early |
| **Single region, no failover** | Medium | Documented. Fine for now; blocks enterprise. |
| **ARQ worker dies silently** | Medium | Calls stop being costed while the API keeps answering. **Needs monitoring — currently none.** |
| **DPDP enforcement from Nov 2026** | Medium | Technical controls built; policy documents still need a lawyer |
| **Commoditisation of the runtime** | Structural | Compete on billing, compliance and cost transparency — not on the agent loop |

---

## 10. The strategic argument

Every competitor is building a better agent runtime. That is the wrong race:
the runtime is open source, the models are rented from the same four vendors,
and the latency floor is set by physics and by whoever is closest to the carrier.

**The durable advantage is being the platform whose numbers a finance team
trusts.** GST invoices a CA accepts. A prepaid ledger that reconciles to the
paise. Billing in the unit the customer actually consumes. Cost visibility good
enough that the customer can defend the spend internally — and privacy metrics
good enough that their security review passes.

None of that is glamorous. All of it is built. It is also the part a
well-funded US competitor is least likely to build for the Indian market, which
is exactly why it is worth defending.
