# Decibyl costing audit — October 2026

Prepared 29 September 2026 for the founder of NAutomation Labs. Read-only: nothing in the application, database or infrastructure was changed. Every number is labelled **measured** (our own data), **vendor price** (official page, URL and date in `cost_model.xlsx`, sheet *Unit prices*), **code constant** (a figure the billing code carries, not re-verified) or **assumption** (reasoning given).

Exchange rate used throughout: **₹95.968 per USD**, the FBIL/RBI reference rate of 28 September 2026, the latest published before the audit date. It was read from CEIC, which republishes the FBIL series, because fbil.org.in timed out; confirm it there before quoting. Foreign vendor invoices (OpenAI, Anthropic, Google, Deepgram, ElevenLabs, Smallest, Composio, Serper, Meta) attract **18% GST under reverse charge**. It is creditable as input tax credit, so it is not a cost in this model, but it is cash out on every foreign bill until the credit is used.

Framing: NAutomation Labs is the autopilot studio; Decibyl is the platform its agents run on. The prices tested here are Decibyl's proposed self-serve prices, exactly as written in the brief. The same unit costs price a studio engagement per outcome, since a purchase order, a matched invoice or an assessment is a known number of these actions.

---

## Summary

**The proposed credit pricing makes money; the proposed voice pricing does not clear the bar on the default Indian stack, and loses money outright on two of the voice stacks.** Charging every action at cost × 3 with a 0.5-credit floor gives 66.7% gross margin on every credit action by construction, and 77–99% on the cheap ones that hit the floor. The three plans sit at 60–64% gross margin if every allowance and every credit is used on the default model, 80–91% at realistic use, and the 5,000-credit pack at 62%. Voice is the problem: a minute on the default Indic stack costs ₹2.50 (Sarvam speech in and out, gpt-4.1-mini, Plivo carriage), so ₹6 pay-as-you-go earns 57% after Razorpay's 2%, the 500-minute pack 54% and the 2,000-minute pack 49%. The ElevenLabs premium voice at ₹6 earns 44%, Gemini Live 51%, and OpenAI's realtime model costs ₹8.71 a minute, so selling it at ₹6 loses ₹2.71 on every minute. Global voice at $0.18 is fine (73–77%) except on OpenAI realtime (49%). Two further findings matter more than any price: production charges nothing for a Decibyl chat turn today (tokens are recorded, no ledger row), and the live per-component markups are 1.15–2.0×, not 3×.

---

## 1. What we pay for

Twenty-two paid or potentially paid vendors were found in the code (sheet *Vendors*; full file:line inventory in `step1_inventory.md`). The ones that move the unit costs:

| Vendor | Used for | Unit | Who pays |
|---|---|---|---|
| OpenAI | Managed LLM tiers (gpt-4.1-mini default, gpt-4.1, gpt-5), embeddings, realtime voice, call QA | per token | Platform |
| Sarvam AI | Managed STT (₹30/hour), TTS bulbul v3 (₹30 per 10k chars), lite LLM, translation | per hour / per char / per token | Platform |
| Google | Gemini Live realtime voice; builder fallback | per audio token | Platform |
| Anthropic | Agent builder (claude-sonnet-5); chat catalogue up to claude-opus-5 | per token | Platform |
| Plivo | Indian carriage ₹0.38/min both ways; numbers ₹200/month | per minute / month | Platform |
| Meta | WhatsApp templates: marketing ₹0.8631, utility and auth ₹0.115; service messages ₹0.115 from 1 Oct 2026 after 1,000 free per number | per message | Platform |
| Composio | Every non-Google connector; free to 100k calls a month, then $0.0003 a call | per call | Platform |
| Serper | Web search, $1 per 1,000 queries on the starter pack | per query | Platform |
| AWS | One EC2 box, 145 GB EBS, egress; S3 optional | per hour / GB | Platform |
| Razorpay | 2% of every payment plus GST on the fee | per payment | Platform |

Self-hosted with no vendor bill: Postgres, Redis, FalkorDB, MinIO, Gotenberg, Tesseract, the code sandbox, coturn, nginx. Customer-paid (bring your own key): every other LLM, speech and carrier provider in the factory.

## 2. Unit cost per activity

Base case: cache hit 70%, TTS 450 characters a minute, FX 95.968, model prices as published. Median and p90 are the two cases on sheet *Unit costs*; the formula for each is in the cell. Credits are cost × 3 in rupees, floored at 0.5.

| Activity | Median INR | Median USD | p90 INR | p90 USD | Credits at 3× (median) | Note |
|---|---|---|---|---|---|---|
| Chat message, default model (gpt-4.1-mini) | 0.268 | 0.0028 | 0.451 | 0.0047 | 0.80 | 8,197-token fixed prefix (measured) + history (assumed) |
| Chat message, premium (gpt-5) | 0.802 | 0.0084 | 1.600 | 0.0167 | 2.41 | |
| Chat message, dearest catalogue model (claude-opus-5) | 2.725 | 0.0284 | 5.052 | 0.0526 | 8.17 | code rate book $5/$25; page now lists Opus 5.5 at $4/$20 |
| Chat message, cheapest (gpt-5-nano) | 0.049 | 0.0005 | 0.081 | 0.0008 | 0.50 (floor) | below minimum |
| Agent-building message (claude-sonnet-5) | 1.791 | 0.0187 | 3.582 | 0.0373 | 5.37 | tokens assumed; builder is off in production |
| Task run (3 turns + 2 tool calls, gpt-4.1-mini) | 0.808 | 0.0084 | 2.716 | 0.0283 | 2.42 | steps assumed |
| Routine run (1 turn + 1 tool call, gpt-5-nano) | 0.060 | 0.0006 | 0.462 | 0.0048 | 0.50 (floor) | shape measured on 20 Inbox Brief runs |
| Trigger run (1 turn, gpt-4.1-mini) | 0.250 | 0.0026 | 0.434 | 0.0045 | 0.75 | |
| Tool call, ordinary or premium connector | 0.029 | 0.0003 | 0.029 | 0.0003 | 0.50 (floor) | Composio list; zero below 100k a month |
| Knowledge ingest, 10-page typed PDF | 0.014 | 0.0002 | 0.072 | 0.0008 | 0.50 (floor) | parse and OCR are local CPU |
| Knowledge answer (embed + retrieve + reply) | 0.305 | 0.0032 | 0.543 | 0.0057 | 0.92 | |
| Voice minute, Indic stack, India | 2.504 | 0.0261 | 3.602 | 0.0375 | 7.51 | STT 0.50 + LLM 0.27 + TTS 1.35 + Plivo 0.38; matches the code's own ₹2.51 (exchange.py:120) |
| Voice minute, natural voice (Smallest Pro TTS), India | 1.997 | 0.0208 | 3.095 | 0.0323 | 5.99 | |
| Voice minute, premium voice (ElevenLabs), India | 3.314 | 0.0345 | 4.412 | 0.0460 | 9.94 | ElevenLabs rate is plan-dependent (assumption $0.05/1k) |
| Voice minute, Gemini Live, India | 2.876 | 0.0300 | 4.124 | 0.0430 | 8.63 | context re-sent each turn, the code's own model |
| Voice minute, OpenAI realtime, India | 8.705 | 0.0907 | 12.868 | 0.1341 | 26.12 | |
| Voice minute, overseas (Deepgram + gpt-4.1-mini + ElevenLabs + Twilio US) | 4.526 | 0.0472 | 5.624 | 0.0586 | 13.58 | |
| Voice minute, overseas, Gemini Live + Twilio US | 3.839 | 0.0400 | 3.839 | 0.0400 | 11.52 | |
| WhatsApp utility template | 0.115 | 0.0012 | 0.115 | 0.0012 | 0.50 (floor) | |
| WhatsApp service reply (window), incl. the reply's LLM | 0.262 | 0.0027 | 0.445 | 0.0046 | 0.79 | Meta line is ₹0.115 from 1 Oct 2026 |
| WhatsApp marketing template | 0.863 | 0.0090 | 0.863 | 0.0090 | 2.59 | |
| Web search (1 Serper query) | 0.096 | 0.0010 | 0.288 | 0.0030 | 0.50 (floor) | |
| Script run (sandbox, compute only) | 0.006 | 0.0001 | 0.036 | 0.0004 | 0.50 (floor) | |
| Translation, 100 characters (Sarvam) | 0.200 | 0.0021 | 0.200 | 0.0021 | 0.60 | |
| Transcription of an upload, per minute (Deepgram batch) | 0.499 | 0.0052 | 0.499 | 0.0052 | 1.50 | |
| Storage per workspace-month (2 GB at S3 price) | 4.80 | 0.050 | 23.99 | 0.250 | 14.4 | MinIO on the box today; S3 price used as the shadow cost |
| Phone number per month, India (Plivo) | 200 | 2.08 | 200 | 2.08 | 600 | official ₹200; code carries ₹250 |
| Phone number per month, US (Twilio) | 110 | 1.15 | 110 | 1.15 | 331 | |

Cross-check from our own workspace's last 30 days (301 calls, 120.7 billable minutes, ₹458 charged): backing the code's markups out of what we were charged gives STT ₹0.61, TTS ₹0.83 and LLM at most ₹0.32 per billable minute. The TTS figure implies about **275 characters a minute**, below the 450 assumed, so the Indic minute is probably nearer ₹2.00 than ₹2.50 on real traffic. The model keeps 450 as the conservative base and runs 350 and 600 in the sensitivity.

## 3. Top five cost drivers

1. **The fixed prompt prefix on every chat turn.** System prompt 2,523 tokens plus 24 tool schemas 5,674 tokens (measured) is 8,197 tokens before the user says a word: 73% of a median message's input and the whole reason a gpt-4.1-mini reply costs ₹0.27 rather than ₹0.07. Caching is what keeps it there; at 50% hit rate the message is ₹0.33, at 90% ₹0.20.
2. **Text-to-speech.** At Sarvam's ₹3 per 1,000 characters, TTS is ₹1.35 of the ₹2.50 Indic minute (54%) at 450 characters a minute, ₹0.83 at the measured 275. ElevenLabs at $0.05 per 1,000 is ₹2.16 a minute on its own.
3. **Streaming speech-to-text.** ₹0.50 a minute on Sarvam, billed for the whole call including silence (20% of the minute).
4. **Carriage and numbers.** Plivo ₹0.38 a minute (15%) and ₹200 a number a month; Twilio $0.014 a minute overseas.
5. **The model chosen for chat.** The same message costs ₹0.05 on gpt-5-nano, ₹0.27 on gpt-4.1-mini, ₹0.80 on gpt-5 and ₹2.73 on claude-opus-5. Nothing in production bills the difference today (section 6).

Fixed costs are not a driver: about **₹16,400 a month** in total (sheet *Fixed costs*; the EC2 instance type is an assumption, t3.xlarge at the official ap-south-1 price, since the box's type is not in the repo). That is ₹329 per paying workspace at 50 workspaces, ₹82 at 200 and ₹16 at 1,000. The marginal cost of one more workspace is about ₹30 a month.

## 4. Plan and voice margins

Gross margin = 1 − variable cost ÷ (price net of Razorpay 2%). Plan cost = chat allowance × chat cost + builder allowance × builder cost + credits spent × ₹0.333 (a credit sold at cost × 3 carries a third of a rupee of cost; actions that hit the 0.5 floor carry less, so this is the ceiling).

| Plan (India price) | 25% use | 50% use | 100% use | Worst case: all used, p90 on default model | After fixed cost at 50 workspaces (100% use) |
|---|---|---|---|---|---|
| Starter ₹3,999 / 3,000 credits / 30 chat a day / 100 builder | 90.9% | 81.9% | 63.8% | 55.0% | 55.4% |
| Growth ₹14,999 / 12,000 / 100 / 500 | 90.3% | 80.6% | 61.2% | 51.4% | 59.0% |
| Scale ₹39,999 / 32,000 / 300 / 1,500 | 89.9% | 79.9% | 59.8% | 48.7% | 59.0% |
| Global at $59 / $199 / $499 (same costs, USD revenue) | 91.6–93.6% | 83.2–87.2% | 66.4–74.4% | 57.2–68.2% | |
| Credit pack 5,000 for ₹4,500 / $55 | | | 62.2% / 67.8% (all spent) | | |

If premium-model chat ever ran inside the chat allowance instead of on credits (the rule says it must not), a workspace using its allowance on claude-opus-5 turns every plan negative: −51% Starter, −43% Growth, −57% Scale. That rule has to be enforced in code, not in copy.

| Voice stack | India ₹6 PAYG | India pack 500 (₹5.50/min) | India pack 2,000 (₹5/min) | Global $0.18 PAYG | Global pack 500 ($0.16) | Global pack 2,000 ($0.15) |
|---|---|---|---|---|---|---|
| Indic default (cost ₹2.50; p90 ₹3.60) | **57.4%** | **53.5%** | **48.9%** | | | |
| Natural voice, Smallest (₹2.00) | 66.0% | 63.0% | **59.3%** | | | |
| Premium voice, ElevenLabs (₹3.31) | **43.6%** | **38.5%** | **32.4%** | | | |
| Gemini Live (₹2.88) | **51.1%** | **46.6%** | **41.3%** | 77.3% | 74.5% | 72.8% |
| OpenAI realtime (₹8.71) | **−48.0%** | **−61.5%** | **−77.7%** | **48.6%** | **42.1%** | **38.3%** |
| Overseas cascade, Deepgram + ElevenLabs + Twilio (₹4.53) | | | | 73.3% | 69.9% | 67.9% |

Bold is below the 60% bar. Phone numbers: ₹500 against ₹200 cost is 60%; $5 against $1.15 is 77%.

Flags the brief asked for:

- **Cost × 3 below the 0.5-credit floor** (the floor, not the multiple, sets the price): gpt-5-nano chat, routine runs on gpt-5-nano, every tool call, knowledge ingest, WhatsApp utility and authentication templates, web search, script runs. All fine for us; the floor lifts their margin to 77–99%. The only customer-facing oddity is that a tool call and a WhatsApp utility message cost the same half credit as a chat message on the cheapest model.
- **3× leaves less than 60%**: never, for a credit-priced action, since 3× is 66.7% by construction. Every item under 60% is voice, which is priced flat and not at 3×.

## 5. Sensitivity

From sheet *Sensitivity*; plan margins at 100% use, voice on the Indic stack at ₹6 and on the 2,000-minute pack, global on the overseas cascade at $0.18.

| Scenario | Chat msg ₹ | Indic min ₹ | Starter | Growth | Scale | Voice India PAYG | Voice pack 2,000 | Global voice |
|---|---|---|---|---|---|---|---|---|
| Base | 0.268 | 2.50 | 63.8% | 61.2% | 59.8% | 57.4% | 48.9% | 73.3% |
| Model prices +50% | 0.393 | 2.64 | 58.6% | 55.6% | 53.5% | 55.1% | 46.1% | 70.2% |
| INR at 90 | 0.251 | 2.49 | 64.4% | 62.0% | 60.6% | 57.7% | 49.2% | 73.3% |
| INR at 100 | 0.279 | 2.52 | 63.3% | 60.8% | 59.2% | 57.2% | 48.7% | 73.3% |
| Cache hit 50% | 0.332 | 2.58 | 61.5% | 58.9% | 57.1% | 56.2% | 47.4% | 72.8% |
| Cache hit 90% | 0.203 | 2.43 | 66.0% | 63.6% | 62.5% | 58.7% | 50.4% | 73.7% |
| TTS 350 chars/min | 0.268 | 2.20 | 63.8% | 61.2% | 59.8% | 62.5% | 55.0% | 76.1% |
| TTS 600 chars/min | 0.268 | 2.95 | 63.8% | 61.2% | 59.8% | 49.8% | 39.7% | 69.0% |
| Telephony +50% | 0.268 | 2.69 | 63.8% | 61.2% | 59.8% | 54.2% | 45.0% | 69.3% |
| All adverse together | 0.510 | 3.41 | 54.4% | 51.2% | 48.5% | 41.9% | 30.3% | 61.4% |

FX barely matters because most cost is in dollars and the plans are priced in rupees at roughly the same ratio; a 4% move in the rupee moves margin under one point. TTS characters per minute and the cache hit rate are the two inputs worth measuring before launch.

## 6. Where the billing code differs from cost × 3 today

All references from `step2_billing_map.md`; nothing was changed. Production has the charge-rule, plan-ladder and metering-split flags **off**, so the first column is what bills today.

**Costs we incur and never charge**

- A Decibyl chat turn writes model tokens to `model_usage` (feature "decibyl") and no ledger row: `api/services/workflow/decibyl.py:1594`, `api/services/billing/model_usage.py:77-98`, confirmed at `routine_runner.py:299-302`. Cost ₹0.27 a message on the default model, ₹2.73 on opus. Only the tool calls inside the turn are charged.
- Builder messages record tokens (`agent_builder/session.py:210`) and charge nothing for them; past the allowance 5 credits = ₹2.50 against ₹1.79 median, ₹3.58 p90 (2.8× median, below cost at p90). With the ladder flag on, the route still reports 5 credits while writing a 0-paise row (`routes/agent_builder.py:165-176`).
- Text runs (task, trigger, routine, channel replies) write their vendor cost to `call_cost_items` at a charge of 0 (`costing.py:259`, `503-504`) and bill a flat 1–2 credits: a task run at 1 credit (₹0.50) is below its ₹0.81 median cost.
- Knowledge-graph extraction on every call, message and document (gpt-4.1-mini on the platform key), call-QA grading on gpt-4.1, voice samples and translation (Sarvam) are metered nowhere.
- Composio's per-call charge is never recorded (`readiness.py:1229-1234` knows the price; nothing writes it).
- Embedding ingest is passed through at 1.0× (`embedding_ingestion.py:120`) and Serper at 1.0× (`data_costs.py:222`), both in fractional credits, against the rule in `credits.py:8-12`.

**Charges below cost × 3**

- Per-component markups in force are telephony 1.15×, STT 1.3×, LLM 2.0×, TTS 1.8× (`markup.py:568-583`). The 1.7× global markup (`constants.py:448`) never prices a line (`costing.py:307-318`).
- Voice, rule off: Business 650 paise a minute = 2.6× the ₹2.50 cost, Scale 550 = 2.2× (`services/configuration/bundles.py:81-82`); rule on: 12 credits = ₹6.00 = 2.4×. Realtime stacks are costed line by line at 2.0× and 1.15×, and the OpenAI realtime minute costs ₹8.71 before markup.
- WhatsApp, rule off: 100 paise for every message (`constants.py:520`) including free service replies and ₹0.863 marketing templates (1.16×); the vendor-cost constant is 12 paise for everything (`constants.py:521`). Rule on: marketing 3 credits = ₹1.50 = 1.74×.
- Translation 1 credit per 100 characters = 2.5× Sarvam's ₹0.20, with no vendor cost recorded. Transcription 2 credits a minute = 2.0× Deepgram batch, with no rate row for batch.
- Number rental ₹559 against ₹250 in code (2.2×) and ₹200 on Plivo's page (2.8×).

**Charges well above 3×** (not a problem, but not the rule either): tool calls at 1 credit = 17× Composio's price; knowledge answers at 2 credits = 3.3×.

**Dead or unreachable pricing code**: `lookup_source.py` (never imported outside tests; its `verified_email` kind has no rate); `exchange.STANDARD_MODELS` (never consulted when charging); `money.DEFAULT_PLATFORM_RATE_MICROS_USD` and `format_micros_usd`; `rentals.monthly_margin_paise`; the `data` component on call receipts ("nothing writes it", `data_costs.py:130`); add-on rates silently dropped on flat bundles (`cost_engine.py:325-370`); `token_report.py:335` filters on a column `workflow_runs` does not have, so the per-account token report raises. Conflicting constants: knowledge answer 2 vs 1 credit, premium tool call 3 vs 2, voice plan rates 13/12/11 credits in code against 12/11/10 in comments, and the Indic minute's own cost quoted as ₹2.36, ₹2.51 and ₹2.78 in three files.

## 7. Recommended changes, with the numbers

1. **Keep cost × 3 and the 0.5-credit floor for credit actions.** 66.7% on every action; the floor makes the cheap ones 77–99%. Sell credits at no less than ₹0.85 each (⅓ ÷ 0.4 ÷ 0.98): the plans sell them at ₹1.25–1.33 and the pack at ₹0.90, so all clear it.
2. **Raise India voice or narrow what it covers.** On the default stack the 60% line after MDR is ₹6.39 a minute (p90: ₹9.18). Options: ₹7 pay-as-you-go (63.5%), 500 minutes for ₹3,250 (₹6.50, 60.7%) and 2,000 for ₹12,500 (₹6.25, 59.1%); or keep ₹6 and first measure characters per minute on real calls, because at the measured 275 the minute costs ₹1.98 and ₹6 earns 66%. The 17% pack discount cannot be funded from a 57% margin.
3. **Do not sell premium voices inside the flat price.** ElevenLabs needs ₹8.50 a minute for 60%, Gemini Live ₹7.35, OpenAI realtime ₹22.20. Either price those stacks separately or bill them as credits at 3×. Overseas, OpenAI realtime needs $0.30, not $0.18.
4. **Enforce "premium chat is credits" in code before launch.** Today no Decibyl turn is charged at all; a user on opus inside the allowance makes every plan negative. The allowance should be defined as the default model; every other model goes to credits at 3× (gpt-5 2.4 credits, opus 8.2).
5. **Pin the builder to Sonnet.** At ₹1.79 a message the Scale allowance of 1,500 is ₹2,686 (7% of price), ₹5,372 at p90. On Opus it would be ₹4,100 median and the plan would fall under 60%.
6. **Fix the three metering gaps that hide cost**: record Composio, Sarvam translation and knowledge-graph extraction as cost lines; replace the 12-paise WhatsApp constant with the category prices; add the Deepgram batch rate row.
7. **Retire the 1.15–2.0× per-component markups** once the credit table is live, or the two systems will price the same minute two ways.
8. **Before launch, measure two inputs for a week on real traffic**: TTS characters per minute and the prompt-cache hit rate. They move the voice margin by ±8 points and the chat cost by ±25%; everything else in the sensitivity is within three points.

## 8. Open questions

1. **No metering export was readable.** The staff routes (`/api/v1/admin/billing/*`, `call_turn_metrics`, `model_usage`, `call_cost_items`) need a superuser and returned 403 to the workspace key. Every token count above is therefore measured prompt size plus assumed history, not a receipt. A one-off read-only export of `call_turn_metrics` and `model_usage` for 60 days would replace most assumptions.
2. **No benchmarks were run.** The brief allows a controlled benchmark with test keys; none were available in this session and production keys may not be used, so scenarios A–G were not executed. Scenario G's shape (a 3-minute call) is what the realtime model prices.
3. **The EC2 instance type**, egress and whether recordings sit on S3 or the box. One AWS invoice answers all three.
4. **Vendor plans we are actually on**: ElevenLabs tier (rate ranges $0.022–$0.08 per 1k), Composio (Hobby or Pro), Langfuse (self-hosted or cloud), Sentry, and any negotiated OpenAI or Anthropic rate.
5. **Plivo's real number rental and per-minute invoice** against the ₹200 published and ₹250 in code.
6. **Whether managed telephony is live** (`MANAGED_TELEPHONY_ENABLED` defaults off) and how many numbers are rented.
7. **The FBIL rate** could not be read from fbil.org.in; 95.968 comes from a republisher.
8. **Who runs the legacy MPS proxy** (`services.decibyl.ai`) and whether anything still bills through it.

## 9. Every assumption made

Listed on sheets *Measured usage* and *Unit prices* with the label "assumption" or "code constant"; the material ones:

- Conversation history and memory per chat turn: 3,000 tokens median, 8,000 p90; reply 300 / 900 tokens; 0.6 tool calls per chat message.
- Prompt-cache hit rate 70% (the 8,197-token prefix is 73% of a median input and is cached automatically by OpenAI above 1,024 tokens).
- Builder message 9,000 in / 1,200 out on claude-sonnet-5; task run 3 turns and 2 tool calls; trigger 1 turn.
- Voice: 6 LLM turns a minute (the code's own 10-second turn), 1,800-token voice prompt, 220 transcript tokens added a minute, 45 output tokens a turn, 450 TTS characters a minute (measured back-out says 275), STT billed for the whole minute, 3-minute reference call, agent speaks 45% and caller 30% of the time (the code's realtime shape).
- gpt-4.1-mini, gpt-4.1, claude-opus-5, Smallest, Rumik, Deepgram Flux and the realtime token-per-second rates are the code's rate book of 14 September 2026, not re-read on 29 September; Sonnet 5, gpt-5 family, Sarvam, Plivo, Twilio, Meta, Serper, Composio, Razorpay, AWS and Gemini Live audio rates were read from the official pages on 29 September.
- ElevenLabs $0.05 per 1,000 characters, between the promotional pay-as-you-go rate and list.
- Fixed costs: t3.xlarge, 200 GB egress, 50 GB mirror, free tiers on PostHog, Sentry, Composio and Langfuse self-hosted; one box carries about 500 light workspaces.
- Storage shadow-priced at S3's ₹2.40 per GB-month even though MinIO on the box is the default.
- WhatsApp service messages priced at Meta's 1 October 2026 rate, since the pricing under test is not live before then.
- Composio priced at its list rate even though usage below 100k calls a month is free.

## Files

- `cost_model.xlsx` — eight sheets as briefed; formulas throughout, inputs on *Unit prices* and *Measured usage*. Rebuilt by `build_cost_model.py`; values in this report were computed from the workbook's formulas with pycel (LibreOffice Calc is not installed on this box).
- `step1_inventory.md`, `step2_billing_map.md`, `step4_vendor_prices.md` — the source notes behind sections 1, 6 and the price sheet.
- `raw/` — our workspace's usage exports (60-day spend, 30-day calls, 200 runs, review, daily), read through the workspace API with the workspace key.
