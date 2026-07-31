# Telangana agriculture advisory — pricing

Two documents in one file, and **they must never be sent together**.

- **Part A — internal.** Costs, margins, downside cases. Never leaves the
  company.
- **Part B — the quote.** What the client sees. No cost or margin appears in it.

Volume assumption throughout: **80,000 attempts/week**, ~90-second advisory
calls, **40% connect rate**. Every figure below is per *billed* minute unless
stated. Rates ₹96/USD.

---
---

# PART A — INTERNAL ONLY

## A1. The volume that actually bills

The single most expensive mistake available here is treating 80,000 attempts as
80,000 billable conversations.

| | |
|---|---|
| Attempts/month | 346,400 |
| Connected at 40% | 138,560 |
| **Billable minutes** | **207,840** |
| **Carrier minutes we pay for** | **311,760** |

Carrier minutes exceed billed minutes by **50%**, because an unanswered call
still holds a channel for ~30 seconds of ringing. Rural Telangana phones are
off or out of coverage often. **Assume 40%, not 50%.**

## A2. Model comparison — true cost per billed minute

Telephony ₹0.38 (Plivo India ₹0.25/min carrier, grossed up for ringing);
infrastructure ₹0.46 (ap-south-1, ~₹0.97L/month spread over billed minutes).

| # | Stack | Agent | **All-in** | Margin @₹6 | Profit/mo @₹6 |
|---|---|---|---|---|---|
| **C** | Gemini Live + context cache | 1.70 | **2.54** | **58%** | ₹7.2L |
| **B** | Gemini Live (native audio) | 3.04 | **3.88** | **35%** | ₹4.4L |
| **A** | Hybrid — Sarvam pre-rendered + live | 3.04 | **3.88** | **35%** | ₹4.4L |
| D | Sarvam full cascade (live TTS) | 8.44 | 9.28 | **−55%** | −₹6.8L |
| E | OpenAI GPT Realtime | 11.18 | 12.02 | −100% | −₹12.5L |
| F | Deepgram + gpt-4o + ElevenLabs | 23.30 | 24.14 | −302% | −₹37.7L |

**Three of six options lose money at ₹6.** D, E and F are not negotiating
positions — they are architectures we cannot afford at this price. If the
client insists on premium TTS, the price is ₹13+ or we walk.

**TTS is the whole story.** Sarvam TTS at ₹3/1k chars × 2,300 chars/min is
₹6.90/min on its own — more than the entire hybrid stack. The LLM is a rounding
error at ₹0.04.

## A3. Why A and B tie, and which to choose

Identical cost, different risk:

| | A — Hybrid Sarvam | B — Gemini Live |
|---|---|---|
| Telangana Telugu quality | **Better** — Indic-trained | Coastal-Andhra default |
| Audio reviewable before send | **Yes** | No |
| Cost predictability | **Fixed** | Grows with call length |
| Personalisation | Live TTS on the variable 20% | Native |
| Code-switching (Te/Hi/En) | Adequate | **Better** |

**Recommend A, with B as fallback.** On a government advisory, sounding wrong
to a Telangana farmer is a reputational problem the price difference does not
cover. And a department that can listen to tomorrow's advisory before it goes
out is a department that renews.

**Do not price against C.** Context caching would take margin to 58%, but it is
unproven on our sessions. Treat it as upside, not as the plan.

## A4. Pricing — per attempt, not per minute

This is the most important commercial decision in the deal.

| Per attempt | = per billed min | Revenue/mo | Margin | Profit/mo |
|---|---|---|---|---|
| ₹2.50 | 4.17 | ₹8.7L | 7% | ₹0.6L |
| **₹3.00** | **5.00** | **₹10.4L** | **22%** | **₹2.3L** |
| ₹3.50 | 5.83 | ₹12.1L | 33% | ₹4.1L |
| ₹4.00 | 6.67 | ₹13.9L | 42% | ₹5.8L |

**Why per attempt.** We cannot control whether a farmer's phone is on, so we
must not price as though we can:

| If connect comes in at 30% | Revenue | Profit |
|---|---|---|
| ₹6.00 per billed minute | ₹9.4L | ₹3.0L |
| **₹3.00 per attempt** | ₹10.4L | **₹4.0L** |

Per-attempt pricing is **₹1L/month better in the downside case** and identical
in the base case. It converts our largest uncontrolled variable into their
planning assumption.

**Bid ₹3.00–3.50 per attempt.** ₹3.50 if there is any room; ₹3.00 to win.
**₹2.50 is the walk-away floor** — below it there is no cover for a carrier
quote coming in high or connect rates disappointing.

## A5. Risks, in order of what actually kills us

**1. Working capital — the real one.** Cost ₹8.1L/month, paid monthly.
Government pays on government terms. **At 90 days we must fund ₹24.2L before
the first rupee arrives**, to earn ₹28L a year. Insist on advance or milestone
payment, or the deal is a loan we are making at 0%.

**2. Volume shortfall.** Infrastructure is fixed. At half the promised volume
our infra cost per minute doubles and margin halves. **Get a minimum committed
volume in writing.** Departments routinely promise 80K/week and deliver 30K.

**3. Sarvam rate error — found and fixed.** The seeded price book carried
Sarvam TTS at ₹1.92 per 1k characters against a published ₹3.00 (₹30 per 10k),
a **1.56× understatement in the single largest line of an Indic voice call**.
Every margin figure the platform produced was optimistic. Corrected in the
price book, and the figures in this document use ₹3.00. STT was fine — ₹30/hour
published, seeded a shade above it.

**Still verify against our own Sarvam plan before signing.** List is not what
we will pay, in either direction, and a 20% error at this volume is ₹10L/year.

**4. Carrier rate unverified.** ₹0.25/min is an assumption. A ₹0.10 swing is
₹35,000/month. Get written quotes for 80 channels and 350K minutes.

**5. We have never run this.** Zero customers, zero production scale. The
mitigation is a paid pilot, not a claim.

## A6. Non-negotiables before signing

- [ ] Minimum committed volume
- [ ] Advance or milestone payment
- [ ] Attempts-based billing, or an agreed minimum billable duration
- [ ] Written carrier quote (Plivo India — KYC is calendar time, start now)
- [ ] Migration to `ap-south-1` — Indian farmer data must not sit in Virginia
- [ ] Sarvam Telangana voice validated on 20 real calls by a local listener
- [ ] Call length capped at 2 minutes in the design

---
---

# PART B — THE QUOTE

*(Everything below may be sent to the client. Nothing above may be.)*

---

## Automated Agricultural Advisory — Proposal

**Prepared for:** [District] Agriculture Department, Telangana
**Prepared by:** NAUTOMATION LABS PRIVATE LIMITED
**Date:** [date] · **Valid:** 30 days

### 1. What we are proposing

A multilingual automated voice advisory reaching **80,000 farmers per week** in
**Telugu, Hindi and English**, with each call personalised to the farmer's
mandal, crop and landholding.

The system speaks first in the farmer's own language, understands their reply
in whichever of the three they use — including mixed within a sentence — and
records the outcome for departmental reporting.

### 2. Pricing

| | |
|---|---|
| **Per call attempt** | **₹3.50** |
| Billing basis | Per attempt, all-inclusive |
| Taxes | GST at 18% extra |

**All-inclusive means all-inclusive.** Speech recognition, language model,
speech synthesis, telephony, hosting, and the platform. There is no separate
line for any of these and no monthly platform fee.

At 80,000 attempts per week: **₹12,12,400 per month** plus GST.

#### What is not charged

- No setup fee
- No minimum monthly commitment
- No per-agent, per-seat or per-number charges
- No charge for campaign configuration or advisory content changes
- **No charge for unanswered calls beyond the quoted attempt price**

### 3. Why per attempt

A farmer's phone may be switched off, out of coverage, or unanswered — nothing
either of us can control. Pricing per attempt means **your budget is known in
advance and does not move with rural network conditions.** You commission
80,000 attempts; you pay for 80,000 attempts.

Per-minute pricing would leave your monthly cost varying by 30–40% month to
month for reasons unrelated to the campaign.

### 4. Language and voice

Voices are built specifically for Indian languages, with **Telangana Telugu**
rather than a generic Telugu that sounds out of place to a local listener.

The advisory content is synthesised and **available for departmental review
before any call is placed** — you hear exactly what farmers will hear.

Each call adapts to the language the farmer speaks, including switching
mid-conversation between Telugu, Hindi and English.

### 5. Reporting

Included at no additional cost:

- Per-call outcome, duration and language
- Delivery and connect rates by mandal
- Full call recording and transcript for every connected call
- Monthly consolidated report in the department's preferred format
- Live dashboard access for departmental staff

### 6. Compliance and data protection

| | |
|---|---|
| Data residency | **All data hosted in India (Mumbai region)** |
| Recording disclosure | Spoken at the start of every call, in the farmer's language |
| Retention | Recordings 90 days, transcripts 365 days — configurable to your policy |
| Erasure | Any farmer's data removable on request, verifiably |
| Access logging | Every access to a recording is logged and auditable |
| Grievance officer | Named and published, as required under DPDP s13 |
| Telecom compliance | 1600-series service numbers; DND scrubbing at the carrier |
| Invoicing | GST-compliant tax invoices, gap-free numbering |

A Data Processing Agreement is provided with this proposal.

### 7. Proposed rollout

| Phase | Duration | Scope |
|---|---|---|
| **1. Pilot** | 2 weeks | 5,000 calls, one mandal, at the quoted rate |
| **2. Review** | 1 week | Connect rates, farmer feedback, advisory refinement |
| **3. Scale** | Ongoing | Full 80,000/week across the district |

**The pilot is chargeable at the same rate and carries no commitment beyond
it.** We would rather prove the connect rates and voice quality on real
Telangana numbers than ask you to take them on trust.

### 8. Commercial terms

| | |
|---|---|
| Payment | Monthly in advance against proforma invoice |
| Contract term | 12 months, terminable at 60 days' notice |
| Volume | No minimum; charged on attempts commissioned |
| Price protection | Held for 12 months from commencement |

### 9. Contact

NAUTOMATION LABS PRIVATE LIMITED
No.86/18, Papanna Thottam, Brindhavan Nagar, TNHB PH-7, Hosur – 635109,
Krishnagiri District, Tamil Nadu
GSTIN 33AALCN7211L1ZB

---

## Notes before sending

Fill in: district name, date, and the contact person. Then delete Part A and
everything above the Part B rule.

**Quoted at ₹3.50, not ₹3.00.** It leaves room to concede once, which a
government negotiation will expect. Do not go below **₹2.50**.

**"Monthly in advance"** in §8 is the working-capital protection. It is the
single most valuable line in the quote and the first one they will try to
change. Trading price for payment terms is usually the better deal — a rupee
of margin is worth less than not funding ₹24L of float.
