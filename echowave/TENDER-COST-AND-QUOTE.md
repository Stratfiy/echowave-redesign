# Cost report and quote — agricultural advisory campaign

Two documents in one file, and **they must never be sent together.**

- **Part A — cost report.** Internal. Costs, margins, the contractual trap.
- **Part B — the quote.** What the department sees. No cost or margin in it.

All figures at ₹96/USD. Volume from the requirement document: 80,000 unique
farmers, up to 3 attempts, 50,000 successful connections, 7 days.

---
---

# PART A — COST REPORT (INTERNAL)

## A0. Read this before anything else: the tender's own numbers may be impossible

**50,000 connections from 80,000 farmers in 3 attempts requires a 27.9%
per-attempt connect rate. Below that, the target is arithmetically
unreachable — no engineering, no budget, no effort fixes it.**

| Per-attempt connect | Reach in 3 attempts | Connections from 80,000 | List size needed for 50,000 |
|---|---|---|---|
| 30% | 65.7% | 52,560 | 76,100 |
| **27.9%** | **62.5%** | **50,016** | **80,000** ← the tender's implied assumption |
| 25% | 57.8% | 46,250 | 86,500 |
| 22% | 52.5% | 42,036 | 95,200 |
| 20% | 48.8% | **39,040** | 102,500 |
| 15% | 38.6% | 30,870 | 129,600 |

The tender assumes a connect rate at the **optimistic end of the plausible
range for rural India.** At 20% — entirely realistic for farmers who are in
fields, out of coverage, or on handsets switched off through the working day —
we deliver 39,040 against a contracted 50,000 and breach by 22%.

**We would breach while performing perfectly.** Every farmer dialled three
times, every attempt logged, every retry correct — and still short, because
the list is too small for the connect rate.

Most bidders will sign this clause without doing this arithmetic. We must not.
Section A6 sets out what has to change in the contract; the pilot in Part B is
what measures the real rate before we are bound to a number.

Note the perverse financial shape: a *lower* connect rate makes the campaign
**cheaper** for us (fewer conversations to pay for) at a fixed price, so
margin rises as we fail. The exposure here is not margin. It is penalty,
non-payment, and a government client who does not renew.

## A1. Volume model

At the tender's implied 27.9%:

| | |
|---|---|
| Total dial attempts | 179,292 |
| Failed attempts | 129,292 |
| Successful connections | 50,000 |
| Conversation minutes | **125,000** |
| Carrier-billable minutes | 131,667 |
| Calling hours (7 × 10 h) | 70 |
| Average SIP channels | 47 → provision **100** |
| Average concurrent conversations | 30 → provision **60** |

Conversation modelled at 150 s. §9 of the requirement — intro, advisory,
farmer questions, contextual answers, confirmation, summary — is not a
60-second call. **Every cost below scales linearly with this number, so it is
the assumption to defend in design: cap the call at 3 minutes.**

## A2. Unit cost by stack, per conversation minute

Agent-speech share 65%; 2,300 characters/minute of Telugu speech; 8 turns.
Sarvam at published list: **STT ₹30/hour, TTS ₹30 per 10,000 characters.**

| # | Stack | Agent | Telephony | Infra | **All-in** | **Campaign** |
|---|---|---|---|---|---|---|
| **C** | Gemini Live + context cache | 2.16 | 0.26 | 1.53 | **3.95** | **₹4.94L** |
| **A** | **Hybrid — Sarvam pre-rendered + live** | **2.38** | 0.26 | 1.53 | **4.17** | **₹5.22L** |
| B | Gemini Live (native audio) | 3.61 | 0.26 | 1.53 | 5.40 | ₹6.75L |
| D | Sarvam full cascade (all TTS live) | 5.06 | 0.26 | 1.53 | 6.85 | ₹8.56L |
| E | OpenAI GPT Realtime | 13.09 | 0.26 | 1.53 | 14.88 | ₹18.60L |
| F | Deepgram + GPT-4o + ElevenLabs | 17.17 | 0.26 | 1.53 | 18.96 | ₹23.70L |

**TTS is the entire cost question.** Live Sarvam TTS at 1,495 characters per
conversation minute is ₹4.49/min on its own. The LLM is ₹0.07 — a rounding
error. Anyone who tells you the model choice drives cost in a voice agent has
not built one.

**The hybrid trick, and why it is worth 2.7 points of margin.** The advisory
body is identical for every farmer in a mandal/crop segment. Pre-render it
once per segment — 200 segments costs **₹2,070 for the entire campaign** — and
synthesise live only the personalisation and the Q&A answers. That takes TTS
from ₹4.49 to ₹1.79 per minute and is the difference between stack A and
stack D: **₹3.34 lakh on one campaign.**

E and F are not negotiating positions. They are architectures we cannot
afford at any price this department will pay, and both have weak Telugu.

## A3. Cost build-up — recommended stack A

| Line | Basis | Cost |
|---|---|---|
| Sarvam TTS (live 40% of agent speech) | 598 chars/min × ₹3/1k | ₹2.24L |
| Sarvam TTS (pre-rendered advisory) | 200 segments, one-off | ₹0.02L |
| Sarvam STT | ₹30/hour × 125,000 min | ₹0.63L |
| LLM (Gemini Flash class) | ~8,800 tokens/min blended | ₹0.09L |
| Telephony | 131,667 min × ₹0.25 | ₹0.33L |
| Infrastructure | ap-south-1, one campaign month | ₹1.91L |
| **Campaign delivery cost** | | **₹5.22L** |
| Campaign-specific one-offs | DLT/KYC, DIDs, Telugu validation | ₹0.50L |
| **Total attributable cost** | | **₹5.72L** |

**Not charged to this campaign:** 34 engineer-days (≈ ₹2.04L) of platform
work — retry scheduler, campaign report, `ap-south-1` migration, load
testing. All of it is reusable on every campaign afterwards, so it is capex,
not cost of sale. Charging it here would price us out of a contract that
funds it over two campaigns.

**Infrastructure is 37% of delivery cost** and the reason a one-off campaign
prices worse than a recurring one. ₹1.91L of monthly infrastructure spread
over 125,000 minutes is ₹1.53/min; the same infrastructure running a campaign
every month, on reserved instances, is ₹0.92/min. **Recurrence is worth more
to our margin than any price concession we could win.**

## A4. Price and margin

| Quote | Margin | Profit | Per connection | Per attempt | Per farmer |
|---|---|---|---|---|---|
| ₹7.00L | 18.3% | ₹1.28L | ₹14.00 | ₹3.90 | ₹8.75 |
| ₹7.50L | 23.8% | ₹1.78L | ₹15.00 | ₹4.18 | ₹9.38 |
| ₹8.00L | 28.5% | ₹2.28L | ₹16.00 | ₹4.46 | ₹10.00 |
| **₹8.50L** | **32.7%** | **₹2.78L** | **₹17.00** | **₹4.74** | **₹10.63** |
| ₹9.00L | 36.5% | ₹3.28L | ₹18.00 | ₹5.02 | ₹11.25 |

**Bid ₹8.50L. Floor ₹7.00L.** Quoting at ₹8.50L leaves one concession, which
a government negotiation will expect and which a first-round-final price
denies us.

**Repeat campaigns: ₹4.45L cost** (development done, reserved instances). At
₹7.00L that is a 36% margin, and it is the number that matters — see A5.

### The competitive floor, and why it is ours

Cost by architecture, same campaign:

```
A  hybrid Sarvam        Rs 5.22L   ← us
D  full cascade         Rs 8.56L   ← most competent competitors
F  premium Western TTS  Rs 23.70L
```

**A competitor running live premium TTS cannot bid below ₹8.6L without losing
money.** We bid ₹8.50L at a 33% margin. That is not a discount we are
choosing to give — it is an architecture they would have to rebuild to match.

This is the most defensible thing in the file, and it is arithmetic rather
than a claim. It is also perishable: the pre-rendering trick is a week of
work for anyone who thinks of it.

## A5. Sensitivity — what actually moves

| Change | Effect on cost | Comment |
|---|---|---|
| Call runs 210 s not 150 s | **+₹1.27L** | the biggest lever we control; cap the call |
| Connect rate 20% not 27.9% | −₹0.7L cost, **SLA breached** | cheaper and catastrophic — see A0 |
| Carrier ₹0.40 not ₹0.25 | +₹0.20L | assumption, unverified |
| Agent speaks 80% not 65% | +₹0.52L | tighten the script |
| Pre-rendering not built | **+₹3.36L** | wipes out the entire margin |
| Campaign repeats monthly | −₹0.77L each | infra amortises, dev is done |

**Two of these are existential and both are ours to control:** shipping
pre-rendering, and capping call length. Neither depends on the client, the
carrier or the vendors.

## A6. What must be in the contract

Ranked by what actually costs us money.

**1. The 50,000 figure must be conditional.** Either (a) the department
supplies a list large enough at the connect rate measured in the pilot, or
(b) we are permitted a 4th attempt, or (c) the target resets to the pilot's
measured rate. Without one of these we have signed up to arithmetic we cannot
perform. **This is the single most important line in the negotiation.**

**2. Payment in advance or by milestone.** ₹5.7L of cost lands inside 7 days.
Government pays on government terms. At 90 days we are funding the entire
campaign to earn ₹2.78L — a loan at 0% with delivery risk attached.

**3. Call length capped in the approved script.** 150 s is a cost assumption,
not a wish. Sign off the script and the cap together.

**4. Written carrier quote before signing.** ₹0.25/min is unverified. A swing
to ₹0.40 is ₹20,000 — survivable, but only because telephony is small here.

**5. Recurrence discussed now, not after delivery.** A second campaign costs
₹4.45L against ₹5.22L. Offer a season rate; it is worth more than price.

## A7. Pilot

5,000 farmers, one mandal. ~11,200 attempts, ~3,130 connections, ~7,800
conversation minutes. Runs on reduced infrastructure.

| | |
|---|---|
| Cost | ~₹0.65L |
| Price | **₹0.75L, credited in full against the campaign fee** |

The pilot is not a sales gesture. **It is how we buy the connect-rate number
before we are contractually bound to it**, and it converts A0 from an
unquantified risk into a measurement. Do not skip it, and do not run the main
campaign until its number is in hand.

---
---

# PART B — THE QUOTE

*(Everything below may be sent. Nothing above may be.)*

---

## Automated Agricultural Advisory — Commercial Proposal

**Prepared for:** [District] Agriculture Department, Telangana
**Prepared by:** NAUTOMATION LABS PRIVATE LIMITED
**Date:** [date] · **Valid:** 30 days

### 1. Scope

An AI-powered outbound voice advisory campaign reaching **80,000 registered
farmers** in **Telugu, Hindi and English**, delivering seasonal agricultural
guidance through natural two-way conversation — not a recorded announcement.

Each farmer receives up to **3 call attempts**, intelligently spaced across
different times of day and different days to maximise the chance of reaching
them. Retries stop as soon as a farmer has been successfully advised.

Every call is personalised to the farmer's mandal, crop and landholding, and
farmers may ask follow-up questions and receive contextual answers drawn from
an advisory knowledge base the department controls and can edit at any time.

### 2. Commercial summary

| | |
|---|---|
| **Pilot — 5,000 farmers, one mandal** | **₹75,000** |
| **Full campaign — 80,000 farmers** | **₹8,50,000** |
| Taxes | IGST at 18% extra |
| **Pilot fee** | **Credited in full against the campaign fee** |

**Fixed price. All-inclusive.** Speech recognition, language models, speech
synthesis, telephony, hosting, the platform, dashboards and reporting. There
is no per-minute charge, no per-call charge, no platform fee and no
infrastructure pass-through. Your cost is known on the day you commission the
campaign and does not move.

#### What is not charged

- No setup or onboarding fee
- No per-agent, per-seat or per-number charges
- No charge for advisory content changes during the campaign
- No charge for unanswered calls or for retry attempts
- No charge for reporting, dashboards or data export

### 3. Why fixed price

Rural connectivity is outside anyone's control. Per-minute or per-call
pricing would leave the department's cost varying with network conditions and
farmer availability — factors unrelated to the quality of the campaign.

A fixed price places that variability with us, where it belongs. **You
commission a campaign; you pay for a campaign.**

### 4. Delivery targets

| | |
|---|---|
| Farmers contacted | 80,000 |
| Attempts per farmer | Up to 3, intelligently spaced |
| Campaign window | 7 calendar days |
| Calling hours | 09:00 – 19:00 only |
| Target successful connections | **50,000** |

The connection target is set against a per-attempt answer rate of
approximately 28%. **The pilot exists to measure the actual rate on real
numbers in your district before the full campaign is committed**, and the
target will be confirmed jointly against that measurement. We would rather
agree a number we can both stand behind than quote one that sounds better in
a document.

Where the measured rate indicates a larger contact list is needed to reach
50,000 connections, we will say so at pilot stage and work with the
department on the list.

### 5. Language and voice

Voices are built specifically for Indian languages, with **Telangana Telugu**
rather than a generic Telugu that sounds out of place to a local listener.

Each call adapts to the language the farmer actually speaks, including
switching mid-conversation between Telugu, Hindi and English.

**All advisory content is available for departmental review and approval
before any call is placed.** You hear exactly what farmers will hear.

### 6. Reporting

Included at no additional cost, live throughout the campaign:

- Connection rate, completion rate and retry statistics
- Daily campaign progress against target
- Language distribution across calls
- Per-call outcome, duration and language
- Full recording and transcript for every connected call
- Mandal-level breakdown
- Live dashboard for departmental staff; monthly consolidated report in your
  preferred format

### 7. Compliance and data protection

| | |
|---|---|
| Data residency | **All data hosted in India (Mumbai region)** |
| Encryption | In transit and at rest |
| Recording disclosure | Spoken at the start of every call, in the farmer's language |
| Retention | Recordings 90 days, transcripts 365 days — configurable to your policy |
| Erasure | Any farmer's data removable on request, verifiably |
| Access logging | Every access to a recording is logged and auditable |
| Grievance officer | Named and published, as required under DPDP s13 |
| Calling hours | Enforced by the platform, not by procedure |
| Telecom compliance | DLT-registered sender; DND scrubbing at the carrier |
| Invoicing | GST-compliant tax invoices |

A Data Processing Agreement is provided with this proposal.

### 8. Platform ownership

The campaign engine, conversation logic, advisory knowledge base, dashboard,
reporting and all business logic are **built and owned by us**. Telephony,
language models and cloud hosting are supporting infrastructure only, and any
of them can be substituted without change to the platform.

The department is not dependent on any third-party product, and no vendor
holds the campaign logic or the data.

### 9. Timeline

| Phase | Duration | Scope |
|---|---|---|
| **1. Setup and approval** | 2 weeks | Advisory content, voice approval, list import, DLT registration |
| **2. Pilot** | 1 week | 5,000 farmers, one mandal; measure connect rates and farmer response |
| **3. Review** | 3 days | Joint review of pilot results; confirm targets and refine advisory |
| **4. Full campaign** | 7 days | 80,000 farmers across the district |
| **5. Reporting** | 3 days | Final consolidated report and data handover |

Setup runs in parallel with DLT registration and carrier provisioning, which
are external processes and should begin at award.

### 10. Commercial terms

| | |
|---|---|
| Payment | 50% at award, 50% on campaign completion |
| Pilot | Invoiced separately; credited in full against the campaign fee |
| Price protection | Held for 12 months for repeat campaigns |
| Repeat campaigns | **₹7,00,000 per campaign** at the same scope |
| Cancellation | No charge if cancelled before campaign commencement |

### 11. Contact

NAUTOMATION LABS PRIVATE LIMITED
No.86/18, Papanna Thottam, Brindhavan Nagar, TNHB PH-7, Hosur – 635109,
Krishnagiri District, Tamil Nadu
GSTIN 33AALCN7211L1ZB

---

## Notes before sending

Fill in district, date and contact person. **Delete Part A and everything
above the Part B rule.**

**§4 is the most carefully written section in the quote and must not be
softened.** It states the 28% assumption in the open, ties the target to the
pilot measurement, and pre-agrees that a larger list may be needed. That
paragraph is the difference between a commercial conversation at pilot stage
and a breach-of-contract conversation at delivery. If procurement asks us to
remove it and simply guarantee 50,000, **escalate — do not concede it in the
room.**

**§10 payment terms** are the working-capital protection. 50% at award covers
the cost base. If they push to net-90 on the whole amount, trading price for
terms is the better deal.

**The repeat-campaign price of ₹7.00L is deliberately visible.** It costs us
₹4.45L, and planting it now is how a single tender becomes a season.
