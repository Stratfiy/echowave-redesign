# Step 2 and Step 8 source notes — how the billing code charges and costs each activity

Read-only trace of the code on 29 Sep 2026 (branch finance/costing-audit at origin/main af0c751). Live flags on app.decibyl.ai at the time of the audit: charge_rule false, so the 'rule off' column is what production bills today.

# How Decibyl charges and costs each activity, with file:line for every number

All paths are under `/home/user/echowave-redesign/echowave/api/`. `B/` means `services/billing/`, and `C` means `constants.py`.

**Flags decide which price list is live.** All of these default to off:
- `CHARGE_RULE_2026_09_ENABLED` (C:562): the "exchange" price list.
- `PLAN_LADDER_2026_09_ENABLED` (C:535): the 21 Sept plan ladder.
- `METERING_SPLIT_2026_09_ENABLED` (C:550): LLM tokens split into input / cached / output lines, plus the `data` component.
- `ADDON_BILLING_ENABLED` (C:526).
- `BYOK_TIERED_FEE_ENABLED` (C:538).

Where a figure depends on a flag, I give both values.

---

## 1. The credit unit, markups and minimum charges

**Credit value**
- 1 credit = 50 paise = ₹0.50 (`B/credits.py:29`), decided 14 Sept 2026 (KAN-47/52).
- The same constant is copied in two other places: `B/onboarding_credits.py:69` and `B/kpi_board.py:54`.
- Every charge is rounded up to whole credits per event (`credits.py:32-42`). Balances are shown rounded down (`credits.py:45-51`).
- USD packs price a credit at $0.006 (`B/topup_packs.py:50`).

**Money and FX constants (`B/money.py`)**
- Money is stored as integer paise. Rates are stored in millipaise (mpaise).
- Fallback exchange rate: `DEFAULT_USD_INR_PAISE = 9_600`, i.e. ₹96/USD (money.py:104). `default_rates.REFERENCE_USD_INR = 96.0` (default_rates.py:147) matches it.
- Pulse: `DEFAULT_PULSE_SECONDS = 15` (money.py:114). A call is billed on seconds rounded up to a 15-second pulse (money.py:208-221).
- Platform fee: `DEFAULT_PLATFORM_RATE_MPAISE = 0` (money.py:126), so there is no per-minute platform fee. It was ₹3.00/min from 28 Aug to 14 Sept 2026 (comment at money.py:116-126).
- `DEFAULT_PLATFORM_RATE_MICROS_USD = 20_000` ($0.02) at money.py:97 is referenced nowhere.

**Markups applied to vendor cost (`B/markup.py`)**

Per-component multipliers, KAN-54 (markup.py:568-583):

| Component | Multiplier |
|---|---|
| telephony | 1.15x (11_500) |
| stt | 1.30x (13_000) |
| llm, llm_input, llm_cached, llm_output, llm_cache_write | 2.00x (20_000) |
| tts | 1.80x (18_000) |
| embedding | 1.00x (10_000) |
| data | 1.00x (10_000) |

- Premium TTS vendors {elevenlabs, cartesia, openai} use 1.40x (markup.py:587-588).
- A component not in the table is sold at cost, 1.0x (markup.py:603).
- Order of precedence: an internal account pays cost (1.0x); otherwise a per-(component, provider, model) override row; otherwise the component default (markup.py:623-643).
- Overrides must be between 1.0x and 3.0x (markup.py:58-59, 306-307).
- The old global markup `MANAGED_PROVIDER_MARKUP_BPS = 17000` (1.7x, C:448), stored in `managed_markup_history`, is still resolved in `costing.py:394`. But `costing.py:307-318` fills a per-line markup for every usage key, so the global figure never actually prices a line. markup.py:552-556 says the engine no longer reads it.

**BYOK uplift and add-ons (`B/fees.py`)**
- BYOK uplift (flag off by default): STT +$0.002/min (`BYOK_STT_UPLIFT_MICROS_USD=2000`, C:479); TTS +$0.015/min (C:480); LLM none. Applied in fees.py:299-340.
- Add-ons (flag off): knowledge base $0.005/min (C:493); call QA $0.02/min (C:496). Applied in fees.py:343-374 and `B/addons.py:285-298`.

**`BALANCE_COST_BPS = 6_200`** (`B/subscription_plans.py:56`)
- This is the assumed cost of ₹1 of granted balance: 62%, from "₹2.36 of cost inside a ₹3.81 composed minute".
- It is used only by the plan loss guard (subscription_plans.py:486-492) and the dashboard (`routes/billing_dashboard.py:1991`).

**The charge rule's multipliers (`B/exchange.py`)**
- `M_COMPUTE = 3.4` (exchange.py:63).
- `M_PASS_THROUGH = 1.7` (exchange.py:66).
- `PREMIUM_MODEL_MULTIPLIER = 3.4` (exchange.py:68).
- Model cost included per credit: 50 / 3.4 = 14.7 paise (exchange.py:75).
- Model line in extra credits: `ceil(max(0, model_cost − N × 14.7p) × 3.4 / 50)` (exchange.py:326-333, 472-489).

**Minimum and rounding rules**
- Call total is lifted to a whole credit by a "credits" rounding line (`B/cost_engine.py:140-164`).
- Transcription: 1 credit minimum (exchange.py:451-458).
- Translation: at least 1 unit of 100 characters (`B/events.py:265-268`).
- Rentals: at least 1 day when prorated (`B/rentals.py:106+`).
- `paise_for` charges at least quantity 1 (events.py:202-203).
- **Not rounded to credits**: data lookups (`B/data_costs.py:222`) and embedding ingestion (`B/embedding_ingestion.py:120`). These write fractional-credit ledger rows, for example a 10-paise search.

**Premium handling**
- Premium connectors: `DEFAULT_PREMIUM_CONNECTORS` lists 21 Composio slugs (CRM, ERP/accounting, commerce/logistics, payments, helpdesk), events.py:99-128. The `PREMIUM_CONNECTORS` environment variable overrides the list (events.py:130-137).
- Under the charge rule, a premium connector call is premium only if its action name is a write (`exchange.is_write_action`, exchange.py:355-424).
- Premium voice under the charge rule: `PREMIUM_VOICE_PROVIDERS = {"elevenlabs"}`, overridable by environment (exchange.py:107-111).
- Premium models under the charge rule: any model's cost beyond the included allowance is charged (see 3a).

---

## 2. Rate cards held in code

### `B/default_rates.py` (dated `AS_OF = "2026-09-14"`, line 42; LLM rows blended at `LLM_INPUT_SHARE = 0.7`, line 47)

Seeded rows are converted to rupees once, at seed time, at the FX rate then in force (`seed_rates.py:117-124`), and stored as `rate_mpaise`. So seeded rows do not move with FX afterwards; only dollar-quoted rows entered on the admin screen do (`rates.py:305-317`).

**LLM rates (USD per 1M tokens, input/output; stored blended per 1k tokens), lines 162-487**

| Provider | Model | Price | Notes |
|---|---|---|---|
| openai | "" (fallback) | 0.15/0.60 | gpt-4o-mini |
| openai | gpt-4o-mini | 0.15/0.60 | |
| openai | gpt-4o | 2.50/10.00 | |
| openai | gpt-5 | 1.25/10.00 | |
| openai | gpt-4.1 | 2.00/8.00 | |
| openai | gpt-4.1-mini | 0.40/1.60 | |
| anthropic | "" | 1.00/5.00 | Haiku |
| anthropic | claude-haiku-4-5 | 1.00/5.00 | |
| anthropic | claude-sonnet-5 | 2.00/10.00 | |
| anthropic | claude-sonnet-4-5 | 3.00/15.00 | |
| anthropic | claude-sonnet-4-6 | 3.00/15.00 | |
| anthropic | claude-opus-5 | 5.00/25.00 | |
| anthropic | claude-opus-4-8 | 5.00/25.00 | |
| cerebras | "" | 0.25/0.69 | provisional |
| deepseek | "" | 0.28/1.10 | provisional |
| mistral | "" | 0.20/0.60 | provisional |
| fireworks | "" | 0.90/0.90 | provisional |
| google | "" | 0.30/2.50 | Flash |
| google | gemini-2.5-flash | 0.30/2.50 | |
| google | gemini-2.5-flash-lite | 0.10/0.40 | "retiring 2026-10-16" |
| google | gemini-3.5-flash-lite | 0.30/2.50 | |
| google | gemini-3.8-flash | 0.75/3.75 | through 31 Dec 2026; $1.50/$7.50 from 1 Jan 2027 |
| sarvam | sarvam-105b, sarvam-105b-conversations, "" | ₹29.28/₹73.20 | rupee-quoted |
| sarvam | gemma-4-31b | ₹36.60/₹91.50 | provisional |

All non-provisional rows above are `checked_on 2026-09-14`.

**Split LLM rows (metering split flag), lines 1149-1323**
- The same prices, un-blended.
- Cached-input share: openai 0.5 (gpt-5 0.1), anthropic 0.1, google 0.1, deepseek 0.1, others 1.0 (lines 1159-1168).
- Cache write: anthropic 1.25x input (line 1174).

**Realtime rows (LLM component, blended per 1k tokens at a 3-minute reference call), lines 517-550**
- Providers: decibylgeminilive, decibylgeminilivevertex, decibylopenairealtime, decibylazurerealtime.
- Rates are derived from `realtime_rates.py` (below). My hand calculation from those inputs: about $0.00342/1k for Gemini and about $0.0356/1k for OpenAI and Azure.

**STT (per minute), lines 553-660**

| Provider | Model | Price |
|---|---|---|
| deepgram | "" | $0.0058 (Nova-3 multilingual streaming) |
| deepgram | flux-general-en | $0.0065 |
| deepgram | flux-general-multi | $0.0078 |
| sarvam | "", saarika:v2.5, saaras:v3 | ₹30/hour (₹0.50/min) |
| elevenlabs | "" | $0.0065 ($0.39/hour) |
| assemblyai | "" | $0.15/hour |
| azure | "" | $0.0167 |

**TTS (per 1k characters), lines 662-835**

| Provider | Model | Price |
|---|---|---|
| smallest | "", lightning_v3.1 | $0.0175 |
| smallest | lightning_v3.1_pro | $0.0195 |
| openai | "" | $0.015 |
| elevenlabs | "", eleven_flash_v2_5, eleven_v3_conversational | $0.05 |
| elevenlabs | eleven_multilingual_v2 | $0.10 |
| cartesia | "" | $0.05 (provisional) |
| sarvam | "", bulbul:v3 | ₹3.00 |
| sarvam | bulbul:v2 | ₹1.50 |
| rumik | "", mulberry | ₹0.50 |
| rumik | muga | ₹0.99 |

**Telephony (per minute), lines 840-980**

| Provider | Model | Price | Notes |
|---|---|---|---|
| twilio | "" | $0.0496 | India outbound to mobile. The comment gives landline/inbound as $0.0699, but there is no row for it. |
| plivo | "", "outbound", "inbound" | ₹0.38 | confirmed on the account 27 Aug 2026 |
| cloudonix, vobiz, telnyx, vonage | "" | ₹1.20 | PROVISIONAL stand-ins |

**Embedding (per 1k tokens), lines 986-1036**

| Provider | Model | Price |
|---|---|---|
| openai | "", text-embedding-3-small | $0.00002 |
| openai | text-embedding-3-large | $0.00013 |
| azure | "" | $0.00002 |
| decibyl | "" | $0.00002 (provisional) |

**Data (each), lines 1041-1064**
- serper "" and "search": $0.001 per request (provisional, 2026-09-21).

### Other rate modules

**`B/seed_rates.py`**
- Loader only; no rates of its own. Modes: seed, refresh-seeded, force (lines 108-167).
- FX comes from `resolve_usd_inr`.

**`B/realtime_rates.py`** (`AS_OF "2026-07"`, line 35)

| Provider | Model | Audio in / out (USD per 1M) | Cached in | Tokens per second in / out |
|---|---|---|---|---|
| google_realtime | gemini-3.1-flash-live-preview | $3 / $12 | $0.30 | 32 / 25 |
| google_vertex_realtime | gemini-live-2.5-flash-native-audio | $3 / $12 | $0.30 | 32 / 25 |
| openai_realtime | gpt-realtime-2 | $32 / $64 | $0.40 | 10 / 20 |
| azure_realtime | gpt-realtime-2 | $32 / $64 | $0.40 | 10 / 20 |

Lines 45-114. Ultravox and Grok are deliberately unpriced, so their usage is recorded as uncosted (lines 116-126).

**`B/realtime_pricing.py`**
- Pure model. Default call shape: 180 seconds, 10 seconds per turn, agent talking 0.45 of the time, caller 0.30, caching off (lines 90-102).
- Re-sent context multiple = (turns + 1) / 2 (line 142).
- My calculation from these defaults: Gemini ≈ $0.049/min, OpenAI ≈ $0.171/min.

**`B/carrier_rates.py`**
- `MANAGED_CARRIER_ALLOWLIST = {"plivo"}` (line 57). Only Plivo carriage is sold on the managed path.

**`B/data_costs.py` and `B/lookup_source.py`**
- No figures of their own; they resolve DATA-component rates.
- `lookup_source` meters kind `verified_email` (line 55), which has no rate row anywhere.

**`B/provider_catalogue.py` and `B/rate_card.py`**
- No numbers. These are admin views and the effective-dated writers.
- The rate card treats a zero-threshold volume tier as the global rate (rate_card.py:54-55).

**`B/rates.py`**
- Order of precedence: internal account → account override → volume tier → `DEFAULT_PLATFORM_RATE_MPAISE` = 0 (rates.py:170-249).

**`B/embedding_ingestion.py`**
- No figures; uses embedding rates at the 1.0x markup.

**`B/knowledge_pages.py`**
- Typed pages past the cap: 10 per credit (line 38).
- Scanned (OCR) page past the cap: 2 credits (line 40), with Sarvam Document AI at ₹0.50/page cited (line 6).
- Pages are counted at 3,000 characters per page when a document has no page count (line 45).

**`B/messaging_charges.py`**
- Flag off: every message costs `WHATSAPP_MESSAGE_PRICE_PAISE = 100` (C:520), against a vendor cost of `WHATSAPP_MESSAGE_COST_PAISE = 12` (C:521).
- Flag on: priced by template category, see 3g.

**`B/rentals.py`**
- Extra phone number price: `NUMBER_RENTAL_PRICE_PAISE = 55_900` (₹559/month net, C:358).
- Carrier cost: `NUMBER_RENTAL_COST_PAISE = 25_000` (₹250, C:349).
- The plan's own figure overrides the price (rentals.py:158-180). First month is prorated by days.

**Plan ladder, 14 Sept (`B/subscription_plans.py:588-680`)** — prices net of GST; GST 18% (C:1112)

| Plan | Monthly | Credits | Annual | Numbers | Voice | Other |
|---|---|---|---|---|---|---|
| Free | ₹0 | — | — | 0 | no | |
| Everyday | ₹999 | 2,000 | ₹9,990 | 0 | no | $10 |
| Business | ₹2,999 | 6,000 | ₹29,990 | 1 | yes | |
| Growth | ₹9,999 | 25,000 | — | 2 | yes | |
| Scale | ₹19,999 | 60,000 | — | 4 | yes | |
| Campus | ₹0 | 3,600 | — | — | — | disabled |
| Starter | ₹2,999 (C:401) | ₹2,500 balance (C:386) | — | 1 | — | |

**Plan ladder, 21 Sept (lines 697-816)**
- India: Go ₹499 / 300 credits; Personal ₹999 / 700; Business_v2 ₹2,999 / 1,800; Pro ₹9,999 / 12,000; Scale_v2 ₹19,999 / 30,000.
- Global variants (all disabled): Personal $15 / 1,200 credits; Business $69 / 4,500; Pro $229 / 20,000; Scale $399 / 40,000.

**Voice bundle (`services/configuration/bundles.py:81-82`)**
- Everyday bundle: list 650 paise/min; per-plan `{"business": 650, "growth": 600, "scale": 550}`, i.e. 13 / 12 / 11 credits.
- Migration `alembic/versions/d8e9f0a1b2c3_rates_and_packs.py:37-38` sets the same values.
- The Natural and Premium (realtime) bundles have no list price, so they are costed line by line.

**`B/plan_limits.py`**
- Caps only (lines 128-326), no prices.
- `knowledge_pages`: Free 50, Everyday 500, Business 2,000, Growth 10,000, Scale 50,000, Go 500, Personal 2,000, Business_v2 5,000, Pro 20,000, Scale_v2 50,000.
- `builder_messages`: 30 / 30 / 100 / 300 / unlimited.
- `chat_context_tokens`: 8k to 128k.

**`B/topup_packs.py:104-115`**
- Rupee packs: p500 ₹500 → 1,000 credits (restricted); p999 ₹999 → 2,000; p4999 ₹4,999 → 10,500; p19999 ₹19,999 → 44,000.
- Dollar packs: $12 → 2,000; $60 → 10,500; $240 → 44,000.
- Minimum top-up ₹1,000 (C:815).

**`B/addons.py`**
- knowledge_base $0.005/min; call_qa $0.02/min (constants above). Default agent add-on: {call_qa} (line 249).

**FX (`B/fx_source.py` and `B/rates.py`)**
- `fx_source` fetches `https://api.frankfurter.dev/v1/latest?from=USD&to=INR` (line 41).
- It accepts values between ₹50 and ₹200 (lines 49-50) and writes a new row only when the rate moves by ₹0.25 or more (line 54).
- Rates are stored in `usd_inr_rate_history`. Resolution is effective-dated at call time (rates.py:111-140), falling back to ₹96.
- A migration (`c73e1b5a94d2`) seeds ₹96 (`readiness.py:795`).
- Note: `B/exchange.py` is not about FX. It is the credit price list.

---

## 3. Per activity: what is charged and how it is computed

**The generic event debit** is `events.charge` (events.py:354-445). It writes one row in `credit_ledger`: kind `usage`, `ref_type` = the event name, deduplicated on (event, ref_id). Internal accounts are skipped.
- Amount = `credits_for(event) × 50 × quantity`.
- Under the charge rule, events in `TOKEN_EVENTS` = {text_reply, knowledge_answer, trigger_run, task_run, routine_run} (exchange.py:312-314) add the model line on the same row.

**Event credits**

| Event | Rule off: `EVENT_CREDITS` (events.py:61-73) | Rule on: `EXCHANGE_TABLE` (exchange.py:171-301) |
|---|---|---|
| text_reply | 1 | 1 |
| knowledge_answer | 2 | 1 |
| routine_run | 2 | 2 |
| trigger_run | 1 | 1 |
| task_run | 1 | 1 |
| script_run | 4 | 4 |
| tool_call | 1 | 1 |
| tool_call_premium | 3 | 2 (writes only) |
| builder_message | 5 (0 when the ladder flag is on, events.py:183-184) | 5 (0 with ladder) |
| number_verification | 2 | 2 |
| translation (per 100 characters) | 1 | 1 |
| transcription_minute | — | 2 |
| whatsapp utility / auth / marketing / service | — | 1 / 1 / 3 / 0 |
| voice minute | — | 12, premium 18 |

**a) Decibyl chat message (default or premium model)**
- **No per-turn charge.** The turn runs through `services/workflow/decibyl.py:1594`.
- Usage is recorded only in `model_usage` with feature `"decibyl"` (`B/model_usage.py:77-98`). There is no ledger row.
- The routine runner confirms this: "No workflow run and no ROUTINE_RUN charge: a Decibyl turn is metered as every Decibyl turn is (model_usage, feature "decibyl")" (`services/workflow/routine_runner.py:299-302`).
- Model: the chat preset or named model, else the builder default. Builder defaults: anthropic claude-sonnet-5, openai gpt-4.1, google gemini-2.5-flash (C:717-723; `services/agent_builder/settings.py:108-150`).
- The preset picker shows `reply_credits` from `exchange.estimated_reply_credits` (`services/configuration/chat_presets.py:224-231`), but nothing charges that for Decibyl's own turns.
- **Tool calls made from Decibyl are charged**: `connected_tools.execute` → tool_call or tool_call_premium (`services/workflow/connected_tools.py:553-563`).
- Memory recall charges knowledge_answer "found or not" (`services/knowledge_graph/recall.py:123-128`). Also charged from `connections.py:230` and `sunday_review.py:255`.
- Decibyl board task: task_run 1 credit, passed with no `usage`, so no model line even under the charge rule (`services/workflow/decibyl_tasks.py:293-298`).

**b) Agent-builder message**
- Within the plan's `builder_messages` allowance: free.
- Past it: 5 credits (`routes/agent_builder.py:151-176`, `services/agent_builder/limits.py:50`).
- With the ladder flag on, `credits_for` returns 0, but `events.charge` still writes a **0-paise ledger row**, and the route returns `PAST_ALLOWANCE_CREDITS` = 5 as "charged_credits".
- Model tokens are recorded to `model_usage` with feature "builder" (`services/agent_builder/session.py:210`). No charge for them.

**c) Channel, share-link, trigger, routine and task runs (bot text runs)**
- Channel or chat reply: `event_for_turn` gives text_reply, or knowledge_answer if retrieval ran and found something (events.py:222-262) (`services/workflow/channel_reply.py:252-263`). `usage` = all turns of the session.
- Share link: `routes/public_embed.py:903-912`, last turn only.
- Trigger: 1 credit (`trigger_runner.py:134-141`).
- Routine: 2 credits, first routine run free (`routine_runner.py:178-188`).
- Board task: 1 credit (`tasks_board.py:969-976`).
- There is no per-step charge anywhere.
- The run's vendor cost is still written to `call_cost_items`, charged at 0: `costing.py:259` treats the text run as flat rate 0 with the fee waived, so `total_charged_paise = 0` and `_debit_ledger` skips it (costing.py:503-504). `B/tasks.py:14-35` re-costs after every turn.

**d) Tool calls**
- On a call: `pipecat_engine_custom_tools.py:1254-1275`. Decibyl: as above. Bot documents: `workflow/documents.py:191`. Documents tools: `documents/tools.py:750`.
- Rule off: ordinary connector 1 credit; Composio slug in `PREMIUM_CONNECTORS` 3 credits.
- Rule on: premium connector 2 credits only if the action is a write; otherwise 1.
- Composio's own charge to us ($0.0003/call past 100,000 free a month, `B/readiness.py:1229-1234`) is **never recorded per call**.

**e) Knowledge**

*Ingest*
- The embedding vendor cost is debited directly, at 1.0x and not rounded to credits. Kind `embedding_ingest`, `ref_type` `kb_document` (`B/embedding_ingestion.py:124-222`). A cost row goes to `embedding_ingestion_costs`.
- No rate on file: ingested unbilled (line 178-187).
- There is a balance pre-check (`tasks/knowledge_base_processing.py:128-152`).
- Separately, pages past the plan cap are charged: `ceil(typed / 10) + 2 × scanned` credits (`B/knowledge_pages.py:80-84, 205-270`), called from `knowledge_base_processing.py:386, 527`.
- Parsing and OCR (tesseract) have no vendor cost line.

*Answer*
- Knowledge answer: 2 credits (1 under the charge rule).
- On a voice call: a query-time embedding line (1.0x), plus the knowledge_base add-on at $0.005/min on itemised calls when the add-on flag is on.

**f) Voice minute (`B/costing.py:220-485`, `B/cost_engine.py:201-442`)**
- Time billed = seconds rounded up to a 15-second pulse.

*Everyday (Indic/Sarvam cascade) bundle, rule off*
- 650 / 600 / 550 paise per minute on Business / Growth / Scale; Starter pays Business's rate (costing.py:99-110).
- Plus 50 paise (1 credit) per minute overage once plan credits are exhausted, except on the last rung (costing.py:44, 119-139). The run is marked `overage_applied`.
- Vendor lines are kept at a charge of 0; only the flat line is charged. Add-ons are ignored on the flat path (cost_engine.py:325-370).

*Everyday bundle, rule on*
- 12 credits/min (600 paise), or 18 credits (900 paise) if the TTS is elevenlabs on the platform key (costing.py:95-98, 147-166).
- No overage premium and no per-plan rates.

*Natural (Gemini Live) and Premium (gpt-realtime-2) bundles*
- These have no list price, so they are costed line by line: tokens × blended realtime rate × 2.0, plus Plivo telephony × 1.15, plus add-ons if the flag is on. The same applies under the charge rule, which only applies to bundles with a list price.
- Tier mapping: `services/configuration/managed_tiers.py:317-331`.

*Inbound vs outbound*
- Same bundle rate. Itemised telephony uses the Plivo "inbound" or "outbound" row, both ₹0.38.

*India vs overseas*
- Managed carriage only on Plivo, India.
- Global plans have `voice_allowed=False`: bring your own carrier, so no telephony line (`usage.py:371` treats a missing key source as BYOK).

*Other voice rules*
- A call that delivered nothing has its fee waived (`B/delivery.py`). On a flat bundle that means nothing is charged at all.
- Reservation hold: 5 minutes (C:887) × the account's measured per-minute rate. With no history: 2 × the Everyday list rate, 650 (fallback 600) (`B/reservations.py:63-181, 221-234`).

*Vendor-cost references (from the code's comments; I did not compute these)*
- Everyday stack vendor cost ≈ ₹2.36/min, sold itemised at ≈ ₹3.81 (subscription_plans.py:47-48).
- ₹2.51 (exchange.py:120).
- ₹2.78 (`kpi_board.py:51`).

**g) WhatsApp (`B/messaging_charges.py`)**
- Rule off: 100 paise per message, whatever the category.
- Rule on: utility 1 credit, authentication 1, marketing 3, service 0; unknown categories count as utility (exchange.py:428-440).
- Meta's costs quoted in exchange.py:115-117: marketing 86.31 paise, utility 11.5 paise. The ledger note always says "vendor cost 12".
- No internal-account skip.
- Callers: `tasks/run_integrations.py:670` passes the category; `messaging/whatsapp_inbound.py:402` sends SERVICE; `workflow/documents.py:447` sends none, so utility.
- A message on the customer's own Twilio sender is not charged.

**h) Web search and data lookups**
- Search: tool_call (1 credit) plus serper pass-through $0.001 → 10 paise at ₹96, 1.0x, not rounded to credits (`services/workflow/web_tools.py:241-255`). The pass-through row goes to `data_lookup_costs` and the ledger with `ref_type` `data_lookup` (`B/data_costs.py:169-254`).
- Page fetch: tool_call only (web_tools.py:720-726).
- `usage_info["data"]` on call receipts: "today nothing writes it" (data_costs.py:130-131).

**i) Sandbox script and translation**
- Script run: 4 credits (`services/sandbox/code_mode.py:305-317`). Composio calls made inside the script are not charged. External spend cap: 50 credits (C:686).
- Translation: 1 credit per 100 characters, rounded up (`routes/translate.py:69-75`; `knowledge_base/translate_document.py:149-155`). No Sarvam vendor cost is recorded.
- Transcription (rule on only): 2 credits/min, 1 credit minimum (`routes/workflow_recording.py:374`, `dialer_import/importer.py:161`). Audio seconds are recorded in `model_usage.audio_seconds` with no price (Deepgram batch $0.0052 is quoted at exchange.py:125, but there is no rate row for it).
- Number verification: 2 credits after the first 2 numbers (`telephony/verified_numbers.py:173-190`).

**j) Storage and number rental**
- **No storage charge exists.** There is no storage pricing anywhere; storage is capped only by `knowledge_base_bytes` and `plan_limits`.
- Number rental: ₹559/month (plan override possible), cost ₹250, prorated by day. Ledger kind `rental`, `ref_type` `recurring_charge_period` (`B/rentals.py:373-530`). Skipped when the plan mandate collects it (rentals.py:394-409).

**Recorded but not charged**
- `model_usage` rows for decibyl, builder, trigger_compile, edit_proposal, acceptable_use, document_fields, graph reviews, recording_transcription, dialer_import.
- Text-run `call_cost_items`, charged 0.
- Uncosted lines: charged 0 and emailed to superadmins once a day per model (`B/uncosted_alert.py`; costing.py:402-423).
- Channel context fold: not metered at all (`B/token_report.py:52-54`).
- `margin_watch` (`B/margin_watch.py`) reads `daily_organization_rollup`, which sums only `workflow_runs.total_charged_paise` and provider cost (`B/rollup.py`). Event revenue in the ledger is excluded, while text-run vendor cost is included, so text-heavy accounts will read as thin-margin. The alarm triggers below 40% margin (C:451) on at least ₹200 of spend (C:453).

---

## 4. Where metering data lives (`db/models.py`)

**`credit_ledger` (`CreditLedgerModel`, line 3987)**
- Columns: `organization_id`, `delta_paise`, `kind`, `ref_type`, `ref_id`, `balance_after_paise`, `note`, `created_at`, `workflow_id`.
- `kind` values: topup, usage, adjustment, trial, reservation, rental, plan, plan_expiry, embedding_ingest, message.
- `ref_type` values:
  - event names, for event charges
  - `workflow_run`, for calls and reservations
  - `data_lookup`
  - `kb_document`
  - `knowledge_pages`
  - `whatsapp_message`
  - `recurring_charge_period`

**`workflow_runs` (line 1054)**
- Columns: `mode`, `call_type`, `usage_info` (JSON), `created_at`, `ended_at`, `billable_seconds`, `billed_seconds`, `platform_rate_mpaise_applied`, `platform_rate_micros_usd_applied`, `usd_inr_paise_applied`, `pulse_seconds_applied`, `total_provider_cost_paise`, `total_charged_paise`, `uncosted_usage`, `overage_applied`, `costed_at`.
- There is **no `organization_id`**; join through `workflows.organization_id`.
- `usage_info` shape: `llm` keyed `"processor|||model"` with `prompt_tokens`, `completion_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`; also `tts` (characters), `stt` (seconds), `embedding`, `telephony`, `data`, `key_sources`, `addons`, `call_duration_seconds`.

**`call_cost_items` (line 2687)**
- Columns: `workflow_run_id`, `component` (stt, llm, llm_input, llm_cached, llm_output, llm_cache_write, tts, telephony, embedding, data, platform, addon), `provider`, `model`, `units` (tokens / seconds / characters / each), `unit_rate_mpaise`, `cost_paise` (charged), `provider_cost_paise` (vendor), `created_at`.

**`model_usage` (line 2733)**
- Columns: `organization_id`, `feature`, `provider`, `model`, `prompt_tokens`, `completion_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `audio_seconds`, `created_at`. No cost column.

**`workflow_run_text_sessions.session_data`**
- `turns[].usage` and `turns[].events`, used for knowledge-answer detection.

**Other tables**
- `call_turn_metrics` (line 3012): `workflow_run_id`, `turn_index`, `latency_ms`, the `t_*_ms` timings, `prompt_tokens`, `completion_tokens`, `cached_tokens`.
- `data_lookup_costs` (line 4262): `provider`, `kind`, `requests`, `vendor_cost_paise`, `charged_paise`, `ref_id`, `workflow_id`, `created_at`.
- `embedding_ingestion_costs` (line 2948): `document_id`, `provider`, `model`, `tokens`, `vendor_cost_paise`, `charged_paise`.
- `recurring_charges` (3204) and `recurring_charge_periods` (3305): `charged_paise`, `cost_paise`, `prorated`, period start and end.
- Rate history: `provider_rates` (2620), `usd_inr_rate_history` (2578), `organization_rate_history` (2185), `platform_volume_tiers` (2547), `managed_markup_history` (4824), `managed_markup_overrides` (4865), `managed_bundles` (3516: `list_paise_per_minute`, `plan_rates`, `volume_tiers`).
- `daily_organization_rollup`: runs only.
- `agent_events` (5965): timeline; the payload may carry `credits`.

---

## 5. Dead or unreachable pricing code, and conflicting rates

### Dead or unreachable
1. **The whole of `B/lookup_source.py`** (contact lookups) is imported nowhere outside tests. Its `verified_email` kind also has no rate row.
2. `exchange.STANDARD_MODELS` and `is_standard_model` (exchange.py:91-103, 347) are never used when charging. The model allowance applies to every model.
3. `money.format_micros_usd` (money.py:254), `money.DEFAULT_PLATFORM_RATE_MICROS_USD` (money.py:97) and `rentals.monthly_margin_paise` (rentals.py:1026) have no references.
4. `carrier_rates.seeded_provisional_carriers` and `is_provisionally_priced` are used only by tests or inside the module itself.
5. The global markup (`MANAGED_PROVIDER_MARKUP_BPS` 1.7x and `managed_markup_history`) never prices a line; per-line markups always win (costing.py:307-318).
6. Add-on rates are silently dropped on flat-bundle calls (cost_engine.py:325-370), although the docstring says they are "folded in".
7. The `data` component on call receipts is unreachable ("nothing writes it", data_costs.py:130).
8. With the ladder flag on, the builder message writes a 0-paise ledger row and the route reports 5 credits charged (`routes/agent_builder.py:165-176`).
9. **Bug:** `B/token_report.py:335` filters on `WorkflowRunModel.organization_id`, which does not exist (`models.py:1190`). Any per-account token report called from `routes/billing_dashboard.py:1079` will raise.

### Conflicting rates
1. **Knowledge answer:** 2 credits (events.py:63, knowledge_pages.py:11) vs 1 (exchange.py:184).
2. **Premium tool call:** 3 credits on any call (events.py:69, connected_tools.py comment) vs 2 on writes only (exchange.py:199).
3. **Voice plan rates:** code and migration charge 13 / 12 / 11 credits (bundles.py:81-82, d8e9f0a1b2c3). Comments say 12 / 11 / 10 at money.py:119, costing.py:99, bundles.py:290 and markup.py:569. `reservations.py:170` says the list rate is "12 credits" while the seed is 650 = 13. The charge rule says 12 / 18 flat. markup.py:585-586 cites a "20/18/16-credit premium rate" that exists nowhere in code.
4. **Premium voice vendors:** markup.py:587 has {elevenlabs, cartesia, openai}; exchange.py:111 has {elevenlabs}.
5. **Platform fee:** the rates.py docstring says "$0.02/min" (line 10) and the comment says "₹3.00/min flat" (line 238). The actual value is 0 (money.py:126).
6. **Model allowance:** C:558 says "a text event includes 1,500 standard-model tokens". exchange.py:24-38, 72-77 uses a money allowance of 14.7 paise per credit on any model.
7. **WhatsApp vendor cost:** 12 paise (C:521) vs 11.5 utility and 86.31 marketing (exchange.py:115-117). Rule off, a service reply costs 100 paise; rule on, 0.
8. **Ingestion embeddings:** default_rates.py EMBEDDING_RATES comment (~line 983) says "separate, unmetered event", but `embedding_ingestion.py` debits it.
9. **Managed Indic minute vendor cost:** ₹2.51 (exchange.py:120) vs ₹2.78 (kpi_board.py:51) vs ₹2.36 (subscription_plans.py:47).
10. **Twilio:** one $0.0496 row, so inbound/landline ($0.0699 per the comment) is under-costed.
11. **Rounding to credits:** data lookups and embedding ingest debit fractional paise, against the rule in credits.py:8-12 that every charge is whole credits.
12. **Free grant:** the Free plan blurb says 1,000 credits (subscription_plans.py:597; onboarding_credits.py:72), but with the ladder flag the grant is 100 (onboarding_credits.py:~306).
13. **Internal accounts:** `messaging_charges.debit_message` and `embedding_ingestion.debit_ingestion_cost` do not skip them, unlike events, knowledge pages and calls.
14. `recall.py:123` charges a knowledge answer even when nothing was found, unlike `events.turn_found_knowledge` (1 credit on no match).
