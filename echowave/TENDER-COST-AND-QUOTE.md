# Cost report and quote — agricultural advisory campaign

Two documents in one file, and **they must never be sent together.**

- **Part A — cost report.** Internal. Costs, margins, where the risk moved.
- **Part B — the quote.** What the department sees. No cost or margin in it.

All figures at ₹96/USD, reproducible from `scripts/tender_cost_model.py`.
Volume from the requirement document: 80,000 unique farmers, up to 3 attempts,
7 days.

---
---

# PART A — COST REPORT (INTERNAL)

## A0. The delivery basis, and why it is the best decision in this deal

**We commit to effort — 80,000 farmers, up to 3 attempts each — not to 50,000
connections.**

That single choice removes the trap in the tender. The arithmetic behind it:

| Per-attempt connect | Reach in 3 attempts | Connections from 80,000 |
|---|---|---|
| 30% | 65.7% | 52,560 |
| **27.9%** | **62.5%** | **50,016** ← what a 50,000 promise silently assumes |
| 25% | 57.8% | 46,250 |
| 22% | 52.5% | 42,036 |
| 20% | 48.8% | **39,040** |

Promising 50,000 connections requires a **27.9% per-attempt answer rate**.
Below that the promise is arithmetically unreachable — at 20%, entirely
ordinary for rural handsets, three attempts on the whole list yields 39,040
and we breach by 22% **having dialled every farmer three times and logged
every attempt correctly.** The trap is a short list, not weak execution.

Committing to effort makes a low connect rate merely cheap instead of a
breach. Keep the arithmetic above in the file: it is the reason for the
structure, and it is what we say if procurement pushes back.

### But the risk inverts, and this is the part nobody prices for

Under an outcome commitment, a **low** connect rate is what hurts.
Under an effort commitment at a fixed price, a **high** one is:

> More farmers answer → more conversations run → more variable cost against
> fixed revenue. **Success is now the downside case.**

It does not feel like risk, which is exactly why it needs a cap. See A4 and
A6.

## A1. Volume

Attempts barely move with the connect rate — a farmer who answers on attempt 1
stops, one who never answers uses all three, and the two effects nearly
cancel. Conversations move a great deal.

| Connect rate | Attempts | Connections | Conversation minutes |
|---|---|---|---|
| 20% | 195,200 | 39,040 | 65,067 |
| 25% | 185,000 | 46,250 | 77,083 |
| **27.9%** | **179,267** | **50,016** | **83,359** |
| 30% | 175,200 | 52,560 | 87,600 |
| 35% | 165,800 | 58,030 | 96,717 |

**Conversation modelled at 100 s.** Shortening from 150 s took ₹0.78L out of
the campaign and dropped concurrency by a third — **~75 SIP channels, ~40
concurrent conversations**, so the media fleet is two instances rather than
three.

100 s is a real design constraint, not just a cheaper assumption. The budget
is roughly: 15 s disclosure and purpose, 40 s advisory, 30 s for **one** farmer
question and its answer, 15 s confirmation and close. A second question does
not fit. That still satisfies §6 and §9 — the conversation is genuinely
multi-turn and contextual — but it is one exchange, not a discussion, and the
script has to be written knowing that.

Two things follow. Shorter calls probably *raise* completion rates on cold
rural calls, so this may deliver more finished advisories rather than fewer.
And **100 s is an average, not a cap** — half the calls run longer by
definition. Without a hard ceiling around 150 s a tail of long calls drags the
average up and the cost with it. Cap it in the workflow and check the real
distribution in the pilot.

## A2. Unit cost by stack, per conversation minute

Sarvam at published list: **STT ₹30/hour, TTS ₹30 per 10,000 characters.**
Telephony ₹0.27 (connected minutes only), infrastructure ₹1.14 — the per-minute
infra figure *rises* as calls shorten, because the same fixed cost spreads over
fewer minutes. The campaign total is what matters, and it falls.

| # | Stack | Agent | **All-in** | **Campaign @ 27.9%** |
|---|---|---|---|---|
| **A** | **Hybrid — Sarvam, mostly pre-rendered** | **1.75** | **3.16** | **₹2.64L** |
| C | Gemini Live + context cache | 2.02 | 3.43 | ₹2.86L |
| B | Gemini Live (native audio) | 3.06 | 4.47 | ₹3.72L |
| D | Sarvam full cascade (all TTS live) | 5.07 | 6.48 | ₹5.40L |
| E | OpenAI GPT Realtime | 11.24 | 12.65 | ₹10.54L |
| F | Deepgram + GPT-4o + ElevenLabs | 17.47 | 18.88 | ₹15.74L |

(Campaign totals exclude the ₹0.50L of one-offs, which are stack-independent.)

**Three changes took the campaign from ₹5.72L to ₹3.14L — a 45% cut — and they
are what makes a ₹5L bid possible at all.**

**1. Pre-render almost everything (₹1.79 → ₹1.12/min).** Previously only the
fixed advisory body was pre-rendered. But the *slot values* are finite too —
there are a few dozen mandals, a handful of crops, four seasons — and so are
the common questions, which is what the knowledge base is for. Pre-render the
advisory, the slot fragments and the top FAQ answers, assemble at call time,
and synthesise live **only a genuinely novel question.** Live TTS share drops
from 40% to 25%.

This is not an IVR and §6 is not violated: the model still drives the
conversation, chooses what to say and when. It is choosing from pre-rendered
audio rather than generating every waveform fresh. The dependency is FAQ
coverage — **if the knowledge base is thin, live share rises and so does
cost.**

**2. Stop paying for a month of infrastructure to run a 7-day campaign
(₹1.91L → ₹0.95L).** The old number provisioned Multi-AZ everything for 730
hours. The database and cache genuinely need ~4 weeks across setup, pilot,
campaign and reporting; **the media fleet needs about 120**, because it only
runs inside the calling window — and at 100 s calls it is two instances, not
three.

This costs about 2 engineer-days of scheduled scaling and trades away burst
headroom. Worth it at this price point, and it must be built before the
campaign, not during it.

**3. Shorten the call to 100 s (−₹0.78L).** Straight proportional saving on
every per-minute line. See A1 for what it costs in conversation design — it is
a real constraint, not free money.

**TTS is still the whole cost question.** All-live Sarvam TTS is ₹4.49/min on
its own. The LLM is ₹0.08.

## A3. Cost build-up — stack A at the tender's own 27.9%

50,016 connections · 83,359 conversation minutes · 179,267 attempts.

| Line | Basis | Cost |
|---|---|---|
| Sarvam TTS — live | 374 chars/min × ₹0.003 × 83,359 min | **₹93,467** |
| Sarvam TTS — pre-rendered library | 400 fragments × 3,450 chars × ₹0.003 | ₹4,140 |
| Sarvam STT | ₹0.50/min × 83,359 min | **₹41,680** |
| LLM (Gemini Flash class) | 9,900 tokens/call × 50,016 calls | ₹6,774 |
| Telephony | 90,029 connected min × ₹0.25 | **₹22,507** |
| Infrastructure | campaign-scoped, 4 weeks | **₹95,000** |
| Campaign one-offs | DLT/KYC, DIDs, Telugu validation | **₹50,000** |
| **Total attributable cost** | | **₹3,13,568** |

### Fixed vs variable — the number to hold on to

| | Amount | Behaviour |
|---|---|---|
| Variable (live TTS, STT, LLM, telephony) | ₹1,64,428 | scales with connections |
| **Fixed** (pre-render, infra, one-offs) | **₹1,49,140** | the same whatever happens |

**Each extra connected conversation costs ₹3.29.** Fixed cost is now **48% of
the total**, which changes the shape of the deal: extra volume is nearly free.
If the department has 120,000 farmers rather than 80,000, the extra 40,000
costs about ₹0.82L — **50% more reach for 26% more cost.** That is the upsell
to put in front of them, and it is worth more than the price concession they
will ask for.

Break-even at ₹5.25L revenue is ~125,000 conversations, against a realistic
ceiling of 58,000. There is a great deal of room before this contract turns.

**Not charged here:** ~36 engineer-days of platform work — retry scheduler,
campaign report, `ap-south-1` migration, scheduled scaling, load testing.
Reusable on every campaign after this one, so it is capex. Charging it to this
contract prices us out of the deal that funds it.

## A4. Price — and why it holds across the whole range

Margin at a fixed price, by connect rate. **Read down the columns, not
across:** the worst case is the bottom row, not the top.

| Connect rate | Cost | @₹4.50L | @₹5.00L | **@₹5.25L** | @₹5.75L |
|---|---|---|---|---|---|
| 20% | ₹2.77L | 38.4% | 44.5% | **47.2%** | 51.7% |
| 25% | ₹3.01L | 33.1% | 39.8% | **42.6%** | 47.6% |
| 27.9% | ₹3.14L | 30.3% | 37.3% | **40.3%** | 45.5% |
| 30% | ₹3.22L | 28.5% | 35.6% | **38.7%** | 44.0% |
| 35% | ₹3.40L | 24.5% | 32.0% | **35.3%** | 40.9% |

**Bid ₹5.25L. Concede once to ₹4.75L. Floor ₹4.25L.**

At ₹5.25L we hold **35–47% margin across every realistic connect rate.** The
100-second call bought a full ₹0.50L off the previous recommendation while
*improving* margin — that is the bid to win with.

₹5.75L would earn more per contract and is defensible on 41–52% margin, but on
a first government tender against unknown competition, **winning at ₹5.25L
with a 40% margin beats losing at ₹5.75L with 45%.** The repeat campaign is
where the money is.

### The competitive floor is still ours

```
A  hybrid, mostly pre-rendered   Rs 3.14L   <- us (incl. one-offs)
D  full cascade, live TTS        Rs 5.90L   <- most competent competitors
F  premium Western TTS           Rs 16.24L
```

**A competitor running live TTS cannot bid below ₹5.9L without losing money.
We bid ₹5.25L at a 40% margin.** That is an 11% undercut while earning a
margin they cannot reach at any price — not a discount we are choosing to
give, but an architecture they would have to rebuild to match.

The gap narrowed with the shorter call, because shortening helps them too. Our
durable advantage is the pre-rendering, not the call length.

It is also perishable. The pre-rendering technique is a fortnight of work for
anyone who thinks of it.

## A5. Sensitivity

| Change | Cost delta | Comment |
|---|---|---|
| **Pre-rendering not built** | **+₹2.80L** | the entire bid depends on it |
| **Infra not scaled to the window** | **+₹0.96L** | 2 engineer-days, build it first |
| Call averages 150 s not 100 s | +₹0.78L | the cap must be real, not aspirational |
| Agent speaks 80% not 65% | +₹0.22L | tighten the script |
| Carrier ₹0.40 not ₹0.25 | +₹0.14L | still unverified |

**Two of these wipe out the bid on their own**, and both are ours to control.
Neither depends on the client, the carrier or any vendor. If we cannot commit
to shipping pre-rendering and scheduled scaling, we should not bid at ₹5.75L.

## A6. Contract non-negotiables

**1. Cap the conversations included.** ₹5.25L covers up to **55,000 connected
conversations**; beyond that, ₹8 per conversation. This is the protection
against the inverted risk in A0 — without it, an unusually good connect rate
costs us money, and there is no clause anywhere else in the deal that catches
it.

₹8 is 2.4× the ₹3.29 marginal cost of a conversation. At 35% connect the
overage bills ~₹0.24L against ~₹0.26L of extra cost — near-exact cover, and a
rate low enough that the department is unlikely to argue with it. Do not let
it be negotiated to zero; a cap with no price is not a cap.

**2. Cap the call length in the approved script.** 150 s is a cost assumption,
not a hope. Sign off script and cap together.

**3. Payment 50% at award.** ₹4.0L of cost lands inside three weeks.
Government pays on government terms.

**4. Written carrier quote before signing.** ₹0.25/min was unverified, and is
now known to be **below both of Plivo's published India rates** — ₹0.34/min over
SIP, ₹0.60/min outbound local (checked 2026-08, see `PROVIDER-PRICING.md`).
Market benchmarks for outbound-to-mobile via aggregators run ₹0.80–₹1.80/min.
At 90,029 connected minutes the gap is **₹8,100 at the SIP rate and ₹31,500 at
the local rate**. Get the written quote before signing; the ₹0.25 assumption
should not survive into a contract.

**4b. Which Bulbul generation, decided in writing.** This model costs TTS at
₹0.003/char, which is Bulbul **v3**. v2 is half that (₹15 per 10k characters),
and it is what the default managed tier actually resolves to. If the campaign
runs v2 the live-TTS line falls from ₹93,467 to about ₹46,700 — a larger swing
than the telephony risk above, in the opposite direction. Confirm it, because
these two together are the difference between the campaign costing ₹38,600 less
than modelled and ₹31,500 more.

**5. State the delivery basis in writing.** "Up to 3 attempts to all 80,000
farmers within the campaign window" — connections reported, not warranted.
If procurement inserts a connection guarantee, **escalate; do not concede it
in the room.** A0 is why.

**6. Recurrence discussed now.** A second campaign costs ~₹2.35L — no one-offs
to repeat, and reserved instances once the pattern is proven. Offer ₹4.50L for
repeats; it is worth more than any concession on this one.

**7. Offer the bigger list.** Fixed cost is 48% of the total, so an extra
40,000 farmers is ₹0.82L of cost. If they have more names, we want them —
better reach for them, better margin for us, and it reframes the negotiation
from price to scope.

## A7. Pilot

5,000 farmers, one mandal. ~12,200 attempts, ~2,400 connections, ~4,000
conversation minutes, on reduced infrastructure.

| | |
|---|---|
| Cost | ~₹0.53L — of which ₹0.45L is infrastructure and setup, not calls |
| Price | **₹50,000, credited in full against the campaign fee** |

**The pilot runs at about break-even and should.** The calls themselves are
₹0.08L; the rest is standing up infrastructure we need anyway. Pricing it to
make money would turn it into a purchase decision rather than a joint
measurement, and the numbers it buys — connect rate, Telugu verdict, **and the
real call-length distribution** — are worth far more than the margin.

The pilot buys the connect-rate number and the Telugu-quality verdict before
either is contractually load-bearing. It also proves FAQ coverage — **the
single assumption the ₹5.75L price rests on** — because the share of questions
needing live synthesis is measurable there and nowhere else.

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

Each farmer receives **up to 3 call attempts**, intelligently spaced across
different times of day and different days to maximise the chance of reaching
them. Retries stop as soon as a farmer has been successfully advised.

Every call is personalised to the farmer's mandal, crop and landholding.
Farmers may ask follow-up questions and receive contextual answers drawn from
an advisory knowledge base the department controls and can edit at any time.

### 2. Commercial summary

| | |
|---|---|
| **Pilot — 5,000 farmers, one mandal** | **₹50,000** |
| **Full campaign — 80,000 farmers** | **₹5,25,000** |
| Taxes | IGST at 18% extra |
| Pilot fee | **Credited in full against the campaign fee** |

**Fixed price. All-inclusive.** Speech recognition, language models, speech
synthesis, telephony, hosting, the platform, dashboards and reporting. No
per-minute charge, no per-call charge, no platform fee, no infrastructure
pass-through. Your cost is known on the day you commission the campaign.

#### What is not charged

- No setup or onboarding fee
- No per-agent, per-seat or per-number charges
- No charge for advisory content changes during the campaign
- **No charge for unanswered calls or for retry attempts** — all three
  attempts per farmer are included in the fixed price
- No charge for reporting, dashboards or data export

### 3. What is delivered

| | |
|---|---|
| Farmers contacted | **80,000** |
| Attempts per farmer | **Up to 3**, intelligently spaced across times and days |
| Campaign window | 7 calendar days |
| Calling hours | 09:00 – 19:00 only, enforced by the platform |
| Connected conversations included | Up to 55,000 |

**We commit to the outreach, and report the outcome.** Whether a particular
farmer's phone is switched on is not something either of us controls, so we do
not think it honest to sell you a guaranteed number of conversations. What we
guarantee is that **every one of the 80,000 farmers is called, up to three
times, at sensibly varied times** — and that you see exactly what happened on
each one.

On comparable rural campaigns, three well-spaced attempts typically reach
50–60% of a list. **The pilot measures the real figure for your district
before the full campaign runs**, and we will report it to you whatever it says.

Should connected conversations exceed 55,000 — a better result than planned —
further conversations are charged at ₹8 each. There is no charge for falling
below it.

### 4. Why we price this way

A fixed price for a defined outreach means **your budget is known in advance
and does not move with rural network conditions.** You commission a campaign
covering 80,000 farmers; you pay for that campaign.

Per-minute or per-call pricing would leave your monthly cost varying by 30–40%
for reasons unrelated to the campaign's quality or your department's planning.

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
- Daily campaign progress
- Language distribution across calls
- Per-call outcome, duration and language
- Full recording and transcript for every connected call
- Mandal-level breakdown
- Live dashboard for departmental staff; final consolidated report in your
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
| **2. Pilot** | 1 week | 5,000 farmers, one mandal |
| **3. Review** | 3 days | Joint review of pilot results; refine advisory |
| **4. Full campaign** | 7 days | 80,000 farmers across the district |
| **5. Reporting** | 3 days | Final consolidated report and data handover |

Setup runs in parallel with DLT registration and carrier provisioning, which
are external processes and should begin at award.

### 10. Commercial terms

| | |
|---|---|
| Payment | 50% at award, 50% on campaign completion |
| Pilot | Invoiced separately; credited in full against the campaign fee |
| Repeat campaigns | **₹4,50,000 per campaign** at the same scope |
| Price protection | Held for 12 months |
| Cancellation | No charge if cancelled before campaign commencement |

### 11. Contact

NAUTOMATION LABS PRIVATE LIMITED
No.86/16, Papanna Thottam, Brindhavan Nagar, TNHB PH-7, Hosur – 635109,
Krishnagiri District, Tamil Nadu
GSTIN 33AALCN7211L1ZB

---

## Notes before sending

Fill in district, date and contact person. **Delete Part A and everything
above the Part B rule.**

**§3 is the most carefully written section and must not be softened.** It sells
the outreach honestly, gives an indicative 50–60% reach without warranting it,
ties the real number to the pilot, and caps our exposure at 55,000
conversations. If procurement asks for a guaranteed 50,000 connections,
**escalate — do not concede it in the room.** A0 explains why: below a 27.9%
answer rate that promise cannot be kept by anyone, at any price.

**The 55,000 cap is not padding.** Under a fixed price, a *better* connect rate
costs us money. That clause is the only thing in the document that catches it.

**₹5.25L leaves one concession to ₹4.75L.** Do not go below ₹4.25L.

**The ₹4.50L repeat price is deliberately visible.** It costs us ~₹2.35L once
the platform work is done, and planting it now is how one tender becomes a
season.

**If they push hard on price, sell scope instead.** An extra 40,000 farmers
costs us ₹0.82L. Offering 120,000 farmers for ₹6.25L is a better deal for both
sides than 80,000 for ₹4.50L, and it moves the conversation off unit price.
