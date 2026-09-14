# Pricing study — cost stack, market, and the model to ship

Date: 14 September 2026. Author: cofounder session. Status: for the founder's decision.

All vendor figures are **list prices** from the engine's own price book
(`api/services/billing/default_rates.py`, `AS_OF = 2026-08`) at Rs 96 to the
dollar, with the estimator's call shape (405 spoken characters and 3,500 LLM
tokens per minute). Market figures are from public pages read today and are
dated inline. Nothing here is a negotiated rate.

## 1. What a minute costs us

| Component | Everyday tier resolves to | Rs / min | Note |
|---|---|---:|---|
| Speech to text | Sarvam saaras v3 | 0.50 | Rs 30 / hour published |
| Text to speech | Sarvam bulbul v3 | 1.22 | Rs 30 / 10k chars (beta); v2 is 0.61 |
| Language model | gpt-4.1-mini (default) | 0.26 | lite (Sarvam 105B) 0.03; advanced (gpt-5) 1.30 |
| Carriage | Plivo, in or out | 0.38 | confirmed on the account 27 Aug |
| **Everyday, all in** | | **2.36** | 1.75 on bulbul v2; 2.13 on the lite brain |
| Natural (speech to speech) | Gemini Live + Plivo | 5.10 | 14,355 audio tokens / min |
| Premium (speech to speech) | GPT Realtime + Plivo | 16.83 | 4,815 tokens / min at a dearer price |

Other lines: a number costs Rs 250 and sells at 559 (55% gross); a WhatsApp
message costs Rs 0.12 and sells at 1.00; a knowledge lookup is priced as an
add-on at Rs 0.48 / min, call QA at 1.92 / min.

Two facts that matter more than any single rate:

- **The Everyday stack is 84% rupee-quoted.** Only the language model is in
  dollars. A move from Rs 96 to Rs 104 adds Rs 0.02 to the minute. Every
  competitor priced in dollars carries the whole call on FX.
- **The site's "regional floor" of Rs 3.28 is an ElevenLabs stack.** ElevenLabs
  Flash at Rs 1.94 / min for the voice, plus STT, brain and carriage, lands
  at 3.08. Bulbul v3 speaks the same eleven Indic languages at 1.22, so the
  regional margin problem in the site's OPEN-ITEMS is a model choice, not a
  market fact. The three-way bake-off (KAN task on Smallest / Sarvam /
  ElevenLabs) decides whether it goes away.

**The one number to measure before anything is signed:** spoken characters
per minute. The estimator assumes 405. Three internal documents assumed 850,
900 and 2,300. At 800 the voice line doubles to 2.44 and the Everyday minute
costs 3.58; at Rs 5.56 that is a 36% margin, not 58%. The pricing-inputs
screen (`/superadmin/billing/pricing-inputs`) reports the measured median
once twenty calls exist. Read it first.

## 2. What the market charges (September 2026)

| Vendor | Headline | All in, Rs / min | Notes |
|---|---|---:|---|
| Trikon (India) | Rs 5 / min flat | 5 | platform, STT, TTS, LLM, recording included; no monthly fee |
| Ringg AI (India) | Rs 6 / connected min | 6 + Rs 499 / number | own STT, TTS, 4.1-class LLM, telephony included; monthly minimum |
| Bolna (India) | 6 cents (Rs 5.52) base | 7.5 to 8.5 | STT, TTS, LLM passed through on top; 30-second pulse; 4.5 cents at volume |
| Sarvam agents | "Rs 1 / min operating cost" | custom | enterprise quote only; the Rs 1 is their marketing of their own stack cost |
| Skit.ai | Rs 18 to 28 / min | 18 to 28 | enterprise, 6 to 10 week deployment |
| Caller Digital, SquadStack | Rs 8 to 25 per outcome | n/a | per successful outcome, managed |
| Smallest Atoms | from 5 cents | 8.6 to 20 | components itemised; $10 / number |
| ElevenLabs Agents | 8 to 12 cents | 9 to 14 | LLM and telephony extra |
| Bland | 11 to 14 cents | 10.6 to 13.4 | plus $299 to $499 / month for the lower rates |
| Vapi | 5 cents platform | 9.6 to 28.8 | four vendors passed through at cost |
| Retell | 7 cents platform | 12.5 to 30 | same shape as Vapi |

The Indian self-serve price is **Rs 5 to 6 a minute, all in, no platform
fee line.** The global players are two to five times that in rupees and
itemise. Sarvam is the only one that could undercut on cost and does not
publish a price.

## 3. What Decibyl says today, in three places that disagree

| | Public site (`decibyl/data/pricing.ts`) | Billing engine (echowave) | KAN-47 decision, 14 Sept |
|---|---|---|---|
| Entry | Starter Rs 2,999: Rs 2,500 credit, 1 number, platform fee 2.5 / min | Starter 2,999 / 2,500 / 1 number; platform rate default Rs 3.00 / min | Everyday Rs 999 text-only (2,000 credits); Business 2,999 (5,000 credits, 1 number) |
| Middle | Growth 7,999: 7,200 credit, 2 numbers, 2.0 / min | not seeded | Growth 7,999: 15,000 credits, 2 numbers |
| Top | Scale 19,999: 18,500 credit, 4 numbers, 1.5 / min | not seeded | Scale 19,999: 40,000 credits, 4 numbers |
| Voice rate | overage 5.30 / 4.50 / 4.00; PAYG 5.30 sliding to 4.20 | Everyday bundle flat Rs 5.56 / min | 12 / 11 / 10 credits = Rs 6 / 5.5 / 5 |
| Unit | rupees of credit | rupees (paise ledger) | credits at Rs 0.50 |
| Markup | platform fee per minute by tier | flat managed markup + platform fee | per component: carriage 1.15x, STT 1.3x, LLM 2x, TTS 1.8x, plus platform fee |

Two of these cannot both be true: Scale at 40,000 credits is Rs 20,000 of
composed cost sold for Rs 19,999, a loss before the number rental; the site
grants that tier Rs 18,500.

## 4. Margin at each candidate price

Everyday stack at list, 2.36 / min.

| Price, Rs / min | Gross at list cost | Gross if chars / min is 800 |
|---:|---:|---:|
| 6.00 (KAN-47 Business) | 61% | 40% |
| 5.56 (engine bundle) | 58% | 36% |
| 5.50 (KAN-47 Growth) | 57% | 35% |
| 5.30 (site Starter overage) | 55% | 32% |
| 5.00 (KAN-47 Scale, Trikon) | 53% | 28% |
| 4.50 (site Growth overage) | 48% | 20% |
| 4.00 (site Scale overage) | 41% | 11% |
| 3.50 (enterprise floor) | 33% | negative |

**The Sarvam arrangement is not in any margin figure** (founder's call, 14
Sept): the Rs 25,000 a month of free usage funds acquisition, not price.
At Rs 500 of composed cost per free signup (1,000 credits) it covers about
fifty signups a month; the later 30% discount, when it starts, is upside
that goes to margin, never to the card.

Speech to speech: Natural needs Rs 10 / min for 49% at list; Premium needs
Rs 25 / min for 33%. Premium at Rs 25 sits with Skit's enterprise price and
should not be on the self-serve card.

## 5. The three architectures, and the objection each invites

**A. Per minute, all in, no plan.** Rs 5.56 a minute, number Rs 559, WhatsApp
Rs 1. Trikon and Ringg's shape. Cleanest to explain, matches the market
exactly, zero commitment. Objection: nothing to sell on a call except a
rate, no floor revenue, and the bill is unpredictable, which is the one thing
an owner asks about.

**B. Plan plus credits, minutes as the visible unit.** A monthly plan that
includes numbers and a credit grant, credits drawn per minute at the tier's
rate, overage at the same rate, top-ups that never expire. Bland and
ElevenLabs's shape, and what KAN-47 already decided. Objection: expiring
or rolling grants create a liability and a churn moment every month; and
"credits" is a second currency an owner has to convert in their head.

**C. Per outcome.** Rs 8 to 25 per booked appointment, confirmed order,
collected payment. Caller Digital and SquadStack's shape. The strongest
story for a sales bot and the engine already files outcomes. Objection:
disputes over what counts, and it cannot price a receptionist that answers
questions all day. A vertical pack option later, not the base.

## 6. Recommendation

Ship **B**, as KAN-47 decided, with six corrections. Everything below is
priced at list cost; the Sarvam free usage is spent on signup credits (see
section 4) and no discount is assumed.

1. **Voice at 12 / 11 / 10 credits a minute (Rs 6 / 5.5 / 5) stands.** It is
   the market rate, 53 to 61% gross at list, and the site's 4.00 and 4.50
   overages come off the page. The engine's Everyday bundle list price moves
   from 556 to 600 paise, with volume tiers at 550 from 2,000 minutes and 500
   from 8,000 minutes a month, so the same rate applies whether a minute is
   inside the grant or over it.
2. **The Scale grant stands at 40,000 credits.** An earlier draft of this
   section read "composed cost" as provider cost and called the grant a
   loss; it is not. A credit is Rs 0.50 of *marked-up* cost (provider cost
   times the component multipliers), so 40,000 credits is Rs 20,000 of
   sell value for Rs 19,999, with the margin inside every credit spent, and
   the model assumes 60 to 80% of plan credits are consumed.
3. **Credits at Rs 0.50 stay internal for the first hundred customers.** Show
   minutes and rupees on every screen ("about 410 minutes left"), with the
   credit figure on the statement only. An owner in Hosur does not want a
   third unit. Revisit when a customer asks for it.
4. **Rollover capped at one month's grant.** Unlimited rollover on an active
   subscription is a growing liability booked at cost; one month keeps the
   goodwill and bounds it.
5. **Bill in 30-second pulses with a 30-second minimum, and unanswered calls
   are free.** Bolna does 30 seconds; Bland charges a minimum on failed calls
   and gets hated for it. Carriage on an unanswered ring is under Rs 0.20 and
   not worth the review.
6. **Everyday at Rs 999 text-only ships**, but as "Text" not "Everyday": the
   engine already calls the voice bundle Everyday and two things with one
   name will cost a support ticket a week.

Do not ship: model BYOK on self-serve (KAN task 48's instinct is right, the
uplift machinery stays behind a flag for enterprise); Premium speech to
speech on the card; a separate platform-fee line on the invoice.

## 7. Break-even, parameterised

Gross margin per Everyday minute at Rs 6 and list cost: Rs 3.64. Fixed costs
are not in this study (hosting, salaries, Sarvam minimum), so the table is
minutes and customers needed to cover a monthly burn, at 800 billed minutes
per paying customer.

| Monthly burn | Minutes to cover | Customers at 800 min |
|---:|---:|---:|
| Rs 2,00,000 | 55,000 | 69 |
| Rs 5,00,000 | 1,37,400 | 172 |
| Rs 10,00,000 | 2,74,700 | 344 |

Plan fees add to this: a Business customer on Rs 2,999 who uses 400 minutes
contributes Rs 2,999 minus Rs 944 of cost minus Rs 250 for the number, about
Rs 1,800, before any overage.

## 8. Inputs still needed from the founder

- Measured characters per minute from the pricing-inputs screen (or twenty
  real calls to produce it).
- Sarvam agreement: start date and whether the 30% covers bulbul v3.
- Monthly fixed cost, so section 7 becomes a date.
- Which languages the first fifty customers speak; if Tamil and Telugu
  dominate, the bake-off result decides the regional voice before launch.

## 9. Engine changes this implies

- Bundle list price 600 paise with volume tiers seeded (migration).
- Plan ladder seeded per section 6: Text 999, Business 2,999, Growth
  7,999, Scale 19,999, with grants 2,000 (text) / 5,000 / 14,000 / 34,000
  credits and 0 / 1 / 2 / 4 numbers (KAN-53, amended).
- Rollover cap of one grant in `plans.py` expiry.
- 30-second pulse and free unanswered calls in `costing.py`.
- Site `data/pricing.ts` re-read from the plan rows; overage lines replaced
  by the tier rates; regional-floor copy removed once the bake-off lands.
- KAN-52 (credits display) deferred behind a flag; statement shows both.

## Sources

- Engine price book: `api/services/billing/default_rates.py`; estimator
  constants: `api/services/billing/estimator.py`; site ladder:
  `decibyl/data/pricing.ts` and `OPEN-ITEMS.md`; decision: KAN-47.
- Trikon comparison page (August 2026): https://www.trikon.tech/voice-pricing-comparison
- Ringg pricing: https://www.ringg.ai/pricing
- Bolna pricing: https://www.bolna.ai/pricing
- Smallest agents pricing: https://smallest.ai/pricing/agents
- Caller Digital India roundup (15 Aug 2026): https://caller.digital/blog/top-10-voice-ai-agents-india-2026
- Vapi: https://www.cloudtalk.io/blog/vapi-ai-pricing/ and https://vapi.health/learn/vapi-pricing-per-minute
- Retell: https://www.cekura.ai/blogs/retell-ai-pricing-per-minute
- Bland: https://www.cloudtalk.io/blog/bland-ai-pricing/
- ElevenLabs Agents: https://elevenlabs.io/pricing/agents and https://www.cekura.ai/blogs/elevenlabs-pricing
- Sarvam API pricing: https://docs.sarvam.ai/api-reference-docs/pricing

## 10. Total operating cost: what it takes to run Decibyl for a month

Two kinds of cost, kept apart because they behave differently: **cost of
revenue** moves with every minute and message and is what the markups in
section 11 recover; **fixed cost** is the same whether one customer or a
hundred is on the box, and is what the plan fees recover.

Figures are ap-south-1 on-demand list (the same table `scripts/infra_sizing.py`
uses) at Rs 96 to the dollar. The production footprint is one compose host
plus managed Postgres after the 13 Sept cutover; the exact instance classes
are KAN-36's console review, so the two rows marked "confirm" are the
assumptions to replace.

### Fixed cost, per month

| Line | Rs / month | Basis |
|---|---:|---|
| Compute: one c7i.xlarge (4 vCPU, 8 GB) running api, worker, ui, nginx, redis, embeddings, coturn | 15,000 | $0.2142 / h; confirm class |
| RDS Postgres db.t4g.medium, single-AZ, 50 GB gp3 | 7,100 | $0.093 / h; Multi-AZ doubles it to 14,000; confirm class |
| EBS 100 GB gp3 on the host | 800 | |
| S3 recordings and transcripts, 100 GB, with lifecycle | 300 | grows with retention policy |
| Data transfer out, 200 GB (audio to carriers and browsers) | 1,750 | $0.09 / GB |
| Snapshots and backups | 500 | |
| Cloudflare tunnel, DNS | 0 | free tier |
| Domain, TLS | 125 | |
| **Infrastructure** | **25,600** | 32,500 with Multi-AZ |
| GitHub Team, Atlassian, Google Workspace (2 seats) | 5,000 | |
| Vercel Pro for the public site | 1,900 | $20 |
| Sentry Team, PostHog | 2,500 | free tiers until volume |
| Composio platform plan | 2,800 | $29 tier; confirm |
| AI coding and design tools (this session's own bill) | 19,000 | $200 class subscription |
| CA, GST filing, TDS, ROC | 4,000 | retainer |
| **Tooling and compliance** | **35,200** | |
| **Fixed cost, no salaries** | **60,800** | ~ Rs 61,000 |
| First hire: support and onboarding rep | 50,000 | |
| Founder draw, two, at Rs 1,00,000 | 2,00,000 | parameter |
| **Fixed cost, fully loaded** | **3,10,800** | |

Not in the table, and one-off: DPDP notice and policy drafting, DLT
registration for SMS, Plivo reseller KYC, a laptop, ISO 27001 later.

Sarvam has no minimum today; the KAN-47 arrangement (Rs 25,000 a month free
for six months, then 30% off) is a credit against cost of revenue, not a fee.

### Cost of revenue, per unit

| Unit | Rs | Basis |
|---|---:|---|
| Everyday voice minute | 2.36 | section 1; no vendor discount assumed |
| Natural voice minute | 5.10 | Gemini Live + carriage |
| Text reply (1,400 tokens, gpt-4.1-mini) | 0.10 | |
| Knowledge answer (retrieval + reply) | 0.13 | embeddings are self-hosted |
| Routine run (about 3,000 tokens) | 0.25 | |
| Composio tool call | 0.10 to 0.50 | depends on plan tier; confirm |
| WhatsApp, service window or utility template | 0.12 | Meta India utility rate |
| WhatsApp, marketing template | 0.80 | Meta India marketing rate |
| Phone number, per month | 250 | Plivo, confirmed |
| Document page, scanned (Sarvam Document AI) | 0.50 | |
| Builder message (gpt-5, about 10,000 tokens) | 2.00 | |
| Payment collection | 2.36% of the amount | Razorpay 2% plus GST on the fee |

## 11. Markup per component

Two layers, and the engine already supports both. The **managed bundle** sells
at one flat rate a minute (`list_paise_per_minute` with volume tiers), which
is what a self-serve customer sees. **Per-component markups** are the floor
behind that rate: what an itemised or enterprise quote charges, and the
check that the flat rate never falls below what the parts would fetch.

### Voice, per minute, at list cost

| Component | Cost | Markup | Price | Note |
|---|---:|---:|---:|---|
| Carriage (Plivo) | 0.38 | 1.15x | 0.44 | a pass-through with a handling margin; nobody wins on carriage |
| Speech to text (Sarvam) | 0.50 | 1.3x | 0.65 | commodity; Ringg sells the API at the same Rs 30 / hour |
| Language model (gpt-4.1-mini) | 0.26 | 2.0x | 0.52 | the prompt, memory and tools are ours; the model is not |
| Text to speech (bulbul v3) | 1.22 | 1.8x | 2.20 | the largest line and the one negotiated; premium voices 1.4x |
| **Components** | **2.36** | | **3.81** | |
| **Minute, sold** | | | **6.00 / 5.50 / 5.00** | 12 / 11 / 10 credits by decision; no platform fee on calls (founder, 14 Sept) |

There is no platform fee on a voice minute. The sold rate is a decision,
12 / 11 / 10 credits by tier; the multipliers are the floor beneath it.
At the estimator's 405 characters a minute the floor is Rs 3.81 (8
credits) and the sold rate carries 4 credits of headroom; at the spec's
850 characters the floor is Rs 6.05 and 12 credits is exactly the
computation. Which of those is true is the characters-per-minute
measurement again. If a vendor discount arrives later the floor falls and
the sold rate holds: a discount goes to margin, never into the card.

Speech to speech: one line, markup 1.9x, so Natural at 5.10 sells at Rs 10
(20 credits) and Premium at 16.83 would need Rs 32; Premium is quote-only.

### Everything that is not a voice minute

| Unit | Cost | Price | Credits | Effective markup | Verdict on KAN-47 |
|---|---:|---:|---:|---:|---|
| Text reply | 0.10 | 0.50 | 1 | 5x | keep |
| Knowledge answer | 0.13 | 1.00 | 2 | 7.7x | keep |
| Routine run | 0.25 | 1.00 | 2 | 4x | keep |
| Composio call, standard | 0.10 to 0.50 | 0.50 | 1 | 1x to 5x | keep, confirm Composio tier |
| Composio call, premium | 0.50 | 1.50 | 3 | 3x | keep |
| WhatsApp utility or service | 0.12 | 1.00 | 2 | 8.3x | keep |
| WhatsApp marketing template | 0.80 | 1.00 | 2 | 1.25x | **raise to 4 credits (Rs 2)** or it is a 25% margin on the message most likely to be sent in bulk |
| Phone number | 250 | 559 | monthly | 2.2x | keep; Ringg is 499 |
| Scanned page | 0.50 | 1.00 | 2 | 2x | keep; KAN-57's 1 credit loses |
| Builder message past allowance | 2.00 | 2.50 | 5 | 1.25x | keep; the allowance is the product |
| Payment collection | 2.36% | absorbed | | | absorb below Rs 20,000 a month per account; above that it is a line on enterprise invoices |

### What a customer contributes

At Rs 6 a minute and list cost the gross margin per voice minute is Rs 3.64.
A Business customer (Rs 2,999, 5,000 credits, one number) who uses 400
minutes contributes Rs 2,999 minus Rs 944 of minutes minus Rs 250 for the
number, about **Rs 1,800 a month** before overage or Razorpay.

### Break-even, on the fixed costs above

| Fixed cost | Rs / month | Business customers at Rs 1,800 | Or voice minutes at Rs 3.64 |
|---|---:|---:|---:|
| No salaries | 61,000 | 34 | 16,800 |
| Plus one rep | 1,11,000 | 62 | 30,500 |
| Fully loaded, two founders paid | 3,11,000 | 173 | 85,400 |

The second and third rows are the numbers to plan hiring against. The first
is the number at which the company stops costing money to keep alive.

### Engine changes for section 11

- Per-component markup table in `services/billing/markup.py` seeded with
  1.15 / 1.3 / 2.0 / 1.8 (TTS premium 1.4), applied when no bundle flat
  rate resolves; the managed bundle keeps its flat rate.
- No platform fee on voice: `platform_rate_mpaise` goes to zero on every
  plan row and the site's 2.5 / 2.0 / 1.5 lines come off.
- WhatsApp marketing templates priced separately from utility
  (`WHATSAPP_MESSAGE_PRICE_PAISE` becomes two constants).
- Margin watch (`margin_watch.py`) alerts on any component whose realised
  markup falls under 1.1x, which is how a vendor price change surfaces
  before an invoice does.

## 12. Notes from seeding the ladder (KAN-53, 14 Sept)

Decisions taken with Nithish on the day, after the spec:

1. **Plan credits expire at the end of the cycle they were granted for.**
   Top-ups are a separate pool and never expire, spent after plan credits.
   The spec's rollover and this study's one-month cap are both withdrawn.
   Annual is ten months for twelve: a year's credits granted at once, living
   a year.
2. **The credits-per-rupee ratio rises with the plan**, the way Claude's
   usage multiplier rises faster than its price: Everyday 2,000 credits at
   Rs 999 (2.0), Business 6,000 at Rs 2,999 (2.0), Growth 25,000 at
   Rs 9,999 (2.5), Scale 60,000 at Rs 19,999 (3.0). Scale is the last rung;
   Enterprise is a quote, not a plan. At the 62% provider share and full
   consumption Scale clears by Rs 399; at the model's 60 to 80% consumption
   its margin is 20 to 40%. Expiry is what makes the top of the ladder safe.
3. **The loss guard on a plan reads balance at cost**: a credit is fifty
   paise of marked-up cost, so a plan can lose at most 62% of the balance's
   face value plus the carrier rent on its numbers.
   `subscription_plans.BALANCE_COST_BPS`.
4. **Model BYOK is allowed on every plan, Free included.** API keys only;
   consumer subscriptions cannot be driven by third-party apps. Credits still
   apply to every event; a BYOK per-event rate with the model component
   removed (voice 12 → 11 credits) is proposed for KAN-56.
5. **Free's 1,000 credits is still the signup bonus** ($5, 960 credits at
   Rs 96). Exactly 1,000 is a one-line change to $5.21 or a rupee bonus.
   Open.
6. **Campus Builder is seeded off sale** with Business's features and 300
   voice minutes a month (3,600 credits). KAN-69 ships the eligibility check.
7. **Proposed caps are seeded as proposed** and marked so in the registry
   and the super-admin screen; nothing enforces them until each feature reads
   its cap through `plan_limits.resolve`.
8. **Knowledge storage ceilings in bytes** behind the page caps are an
   engineering figure, not a published one.
9. **Voice plans are India-only.** A foreign account is offered Everyday in
   dollars; anything else is refused rather than sold in rupees at the net.
10. **Starter is withdrawn from sale, not deleted.** Business is its
    successor; accounts on Starter keep collecting and granting what they
    bought.
11. **The referral programme is being withdrawn** (scope to be confirmed
    against KAN-76, which builds on its statements).

## 13. Notes from the markup step (KAN-54, 14 Sept)

1. **One multiplier per component replaces the flat 1.7x.** Carriage 1.15x,
   speech-to-text 1.3x, the model 2.0x, the voice 1.8x, premium voices
   (ElevenLabs, Cartesia, OpenAI) 1.4x, embeddings at cost.
   `services/billing/markup.py`. A per-model override still wins for its
   line; the global figure in `managed_markup_history` is no longer read by
   the engine and stays for the audit trail.
2. **No platform fee on a call.** The default rate is zero. Account rate rows
   written by plan authorisation and open volume tiers are closed by the
   migration; a rate a person negotiated is left alone, which is how an
   enterprise contract can still carry a fee.
3. **The bundle rate is by plan.** The Everyday voice bundle moves from 556
   to 600 paise a minute at list (12 credits), 550 on Growth (11), 500 on
   Scale (10). Starter pays Business's rate. Volume tiers still apply
   beneath the plan rate. The premium bundle's 20/18/16 is *proposed* and not
   seeded.
4. **A call with every key brought costs nothing per minute.** With no fee
   and BYOK on every plan, an account on its own STT, model, voice and number
   pays for the platform through its plan, not per minute. The BYOK per-event
   rate (voice 12 → 11 credits when only the model is theirs) lands with
   KAN-56.

## 14. Notes from the rate-book refresh (KAN-58, 14 Sept)

Every row in `default_rates.py` now names the page it was read from and the
day (`source_url`, `source_checked_on`, also columns on `provider_rates`
and shown on the providers screen). Fifty-one of sixty-seven rows were re-read
on 14 Sept; the rest keep their August month-date rather than a day they never
had. Where the vendor page and the KAN-58 survey disagreed, the page won:

| Row | Survey said | Page says (14 Sept) | Book now |
|---|---|---|---|
| Sarvam TTS, provider-wide | Rs 3.00 / 1k chars | Bulbul v3 Rs 30 / 10k; v2 no longer listed | Rs 3.00 (was 1.50, lagging the tier) |
| Sarvam 105B | Rs 29.28 / 73.20 per 1M | same, Rs 10.98 cached | Rs 0.0425 / 1k blended (was 0.0076) |
| Gemma-4 31B (Sarvam) | — | Rs 36.60 / 91.50 per 1M, beta | priced, provisional |
| Deepgram Nova-3 streaming | $0.0077 / min | $0.0048 mono, $0.0058 multilingual | $0.0058 |
| AssemblyAI streaming | $0.15 / hr | same | $0.0025 / min, new row |
| Smallest Lightning v3.1 / Pro | $0.025 / 1k | $0.175 / $0.195 per 10k | unchanged |
| Cartesia Sonic | $0.035 / 1k | plans only, no per-character price | $0.05 kept, provisional |
| ElevenLabs Flash | $0.05 / 1k | same | unchanged |
| Twilio India | $0.0075 / min | $0.0496 mobile, $0.0699 landline and inbound | $0.0496 (was Rs 1.20); the survey figure is Twilio's US rate |
| Plivo India | — | Rs 0.38 in and out, number Rs 200 / month | unchanged |
| Gemini 2.5 Flash-Lite | retires 16 Oct 2026 | deprecations page lists no date | row kept as history; nothing resolves to it (test) |
| Gemini 3.8 Flash | — | $0.75 / $3.75 to 31 Dec 2026, then $1.50 / $7.50 | new row; the rise is a dated row to open in January |
| Claude Sonnet 5 | builder default | $2 / $10, now the standard price | builder default moved from Sonnet 4.5 ($3 / $15) |

Reconciliation, as the ticket asks: Sarvam STT 0.50 + Bulbul v3 at 850
spoken characters 2.55 + gpt-4.1-mini at 3,500 tokens 0.26 + Plivo 0.38 =
Rs 3.69 a minute at list against the model's 3.71; with Sarvam's 30% on its
two lines, 2.78 exactly. A test holds both to within five paise.

**What it does to the tiers.** The Lite brain (Sarvam 105B) was costed at
Rs 0.03 a minute and is Rs 0.15; the Everyday minute on it is now 3.58, not
2.13. Still the cheapest stack, but the "Sarvam is nearly free" reading of
section 1 was an old price.

**Not in this step.** Sarvam translate (Rs 20 / 10k chars) and Document
Digitization (Rs 0.50 / page) have no cost component or unit in the engine
yet; they land with the metering step (KAN-56, translation under KAN-104) and
the knowledge caps (KAN-57), which is where a page and a character get a line
on a receipt. Refreshing the production card is a button on the rate-card
screen (or `scripts/seed_provider_rates --confirm --refresh-seeded`): rows
still on the seeded default take these figures, rows somebody typed
(Smallest and Cartesia contracted, the FX) do not.
