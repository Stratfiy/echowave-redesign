# Pricing review — August 2026

Two things this answers: is the price book right, and what does moving the
managed markup to 1.4× actually require.

---

## 1. Do not multiply the rate card by 1.4

The rate card holds **what the vendor charges us**. The customer-facing markup
is applied on top, at cost time, from a single setting:

```
MANAGED_PROVIDER_MARKUP_BPS   default 13000 = 1.3×
```

So moving to 1.4× is **one environment variable**:

```
MANAGED_PROVIDER_MARKUP_BPS=14000
```

Multiplying the rate rows by 1.4 instead would apply it twice — 1.4 × 1.3 =
**1.82×** — and would also destroy every margin figure on the unit-economics
screen, because `provider_cost_paise` would then be storing retail rather than
cost. `constants.py` says this explicitly at the setting; it is worth reading
before touching either.

The markup applies to **STT, LLM and TTS only**. Telephony is excluded
deliberately: its rate card holds the price we *sell* a minute at, so marking it
up again would mean the number an operator types is not the number a customer
pays. The platform fee is never marked up — that would be margin on margin.

### What 1.4× does to a representative minute

Using the current card, a one-minute call on the managed default stack
(Sarvam STT → Gemini 2.5 Flash → Sarvam Bulbul v2, Plivo telephony):

| Component | Vendor cost | At 1.3× | At 1.4× |
|---|---|---|---|
| STT (sarvam, 60s) | $0.00521 | $0.00677 | $0.00729 |
| LLM (gemini-2.5-flash, ~2.5k tokens) | $0.00120 | $0.00156 | $0.00168 |
| TTS (bulbul:v2, ~900 chars) | $0.01406 | $0.01828 | $0.01969 |
| Telephony (plivo) | $0.00625 | $0.00625 | $0.00625 |
| **Provider subtotal** | **$0.02672** | **$0.03286** | **$0.03491** |
| Platform fee | — | $0.02000 | $0.02000 |
| **Customer pays** | | **$0.05286** | **$0.05491** |

Roughly **+$0.002 per minute**, or about 4% on the bill. Speech synthesis is
the line that moves — it is over half the provider cost on this stack, and any
markup change is felt there first.

---

## 2. The price book is in better shape than expected

33 rows, `AS_OF = "2026-08"`. Every rate I could check against a vendor's
current published price agrees:

| Row | Card | Published | Source |
|---|---|---|---|
| sarvam STT | $0.005208/min | ₹30/hour = ₹0.50/min | [Sarvam](https://www.sarvam.ai/api-pricing) |
| sarvam bulbul:v3 | $0.031250/1k chars | ₹30/10k chars | [Sarvam](https://www.sarvam.ai/api-pricing) |
| deepgram nova-3 | $0.0077/min | $0.0077/min streaming | [Deepgram](https://deepgram.com/learn/best-speech-to-text-apis-2026) |
| elevenlabs TTS | $0.050/1k chars | $0.05/1k Flash/Turbo | [Murf comparison](https://murf.ai/blog/cartesia-vs-elevenlabs) |
| openai TTS | $0.015/1k chars | $15/M chars tts-1 | [CallMissed](https://www.callmissed.com/en/blog/tts-showdown-2026-elevenlabs-vs-cartesia-vs-openai-vs-sesame-the-ultimate-compar) |
| gemini-2.5-flash-lite | $0.000190/1k tok | $0.10/M in, $0.40/M out | [pricepertoken](https://pricepertoken.com/pricing-page/model/google-gemini-2.5-flash-lite) |
| gpt-4o | $0.004750/1k tok | $2.50/M in, $10/M out | — |

Two mechanisms make this work and are worth knowing before editing the file:

- **`_inr(rupees)`** — Indian vendors publish in ₹; the file is USD. The round
  trip is lossless only while the configured FX equals `REFERENCE_USD_INR`
  (96.0). A rupee vendor's row moves when the dollar moves and its real price
  did not.
- **`_blend(input_per_million, output_per_million)`** — the schema carries one
  rate per model, vendors price input and output separately. `LLM_INPUT_SHARE
  = 0.7` is the assumed split, because a voice agent resends the conversation
  every turn and skews to input. **Check this against your real traffic** — the
  Tokens screen reports prompt vs completion per turn, so it is measurable
  rather than a guess. If the real share is 0.85, every LLM rate is currently
  overstated.

> An earlier note of mine said "27 provider/component pairs have no rate row".
> That counted every provider in the config registry, including local and
> exotic ones nobody will select. The providers customers actually use are
> priced. Corrected below.

---

## 3. Rows worth adding

These are providers a customer can select today that fall through to no rate.
Usage on them is reported as `uncosted` rather than silently zeroed — so
nothing is mispriced, but a receipt omits the component and margin reads high.

Prices researched August 2026. **Verify against your own vendor invoices before
seeding** — published pricing and negotiated pricing differ, and several of
these vary by plan.

### Speech-to-text (`RateUnit.MINUTE`, USD per minute)

| Provider | Model | USD/min | Basis |
|---|---|---|---|
| `assemblyai` | *(any)* | 0.0075 | Universal-3.5 Pro Realtime $0.45/hr |
| `speechmatics` | *(any)* | 0.0117 | Enterprise real-time |
| `cartesia` | `ink-whisper` | *verify* | Not published per-minute; billed in credits |
| `google` | *(any)* | 0.0160 | Chirp streaming, ~$0.96/hr |
| `openai` | *(any)* | 0.0060 | gpt-4o-transcribe |
| `gladia` | *(any)* | *verify* | Usage-based, not published per-minute |
| `smallest` | *(any)* | *verify* | Published per-minute for TTS, not STT |

### Text-to-speech (`RateUnit.THOUSAND_CHARS`, USD per 1k characters)

| Provider | Model | USD/1k chars | Basis |
|---|---|---|---|
| `elevenlabs` | `eleven_multilingual_v2` | 0.1000 | $0.10/1k — **2× the existing provider-wide row** |
| `rime` | *(any)* | 0.0390 | ~$39/M chars effective, PAYG |
| `deepgram` | *(any)* | 0.0150 | Aura-2 |
| `google` | *(any)* | 0.0160 | Neural2 / Studio |
| `inworld` | *(any)* | *verify* | |
| `minimax` | *(any)* | *verify* | |
| `camb` | *(any)* | *verify* | |
| `xai` | *(any)* | *verify* | |
| `azure_speech` | *(any)* | 0.0160 | Neural standard |

The ElevenLabs multilingual row matters most: the existing provider-wide row is
Flash/Turbo at $0.05, and a customer selecting multilingual v2 costs twice that
while being billed at half. That is the loss-making case the Models margin
screen was built to surface — and it is exactly the row that showed a **−29%
margin** in the seeded demo.

### Language models (`RateUnit.THOUSAND_TOKENS`, use `_blend()`)

| Provider | Model | Input $/M | Output $/M | Basis |
|---|---|---|---|---|
| `groq` | *(any)* | *verify* | *verify* | Varies sharply by model |
| `openrouter` | *(any)* | — | — | **Do not price provider-wide.** It is a router; the real cost depends on the downstream model. Price per model or leave uncosted. |
| `google_vertex` | *(any)* | same as `google` | same | Vertex mirrors Gemini list pricing |
| `azure` | *(any)* | same as `openai` | same | Azure OpenAI mirrors OpenAI list pricing |
| `aws_bedrock` | *(any)* | *verify* | *verify* | Per-model, region-dependent |
| `minimax` | *(any)* | *verify* | *verify* | |

### How to add them

Append `DefaultRate(...)` rows to `api/services/billing/default_rates.py`,
model-specific where the vendor's price varies by model:

```python
DefaultRate(
    "assemblyai", "", CostComponent.STT, RateUnit.MINUTE,
    0.0075, "Universal-3.5 Pro Realtime $0.45/hour.",
),
DefaultRate(
    "elevenlabs", "eleven_multilingual_v2", CostComponent.TTS,
    RateUnit.THOUSAND_CHARS, 0.1000,
    "Multilingual v2 $0.10/1k chars — twice Flash/Turbo.",
),
```

Rates resolve **model-first with a provider-wide `""` fallback**, so a
model-specific row overrides the general one without removing it. Bump `AS_OF`
when you do — a price book with no date is one nobody can tell is stale.

---

## 4. The README contradicts this

`README.md` still says, under **Pricing model**:

> A platform fee plus provider costs passed through **at cost, with no markup**

That was true before `MANAGED_PROVIDER_MARKUP_BPS` existed, and it is still
true for **bring-your-own-key** — a customer on their own key pays the vendor
directly and we add nothing. It is **not** true for managed inference, which
has carried 1.3× since the setting was introduced and is proposed to go to
1.4×.

The distinction is real and defensible — we carry the vendor account, the
credit risk and the key rotation, and a customer choosing managed is choosing
not to hold an API key. But the sentence as written does not draw it, and it is
a pricing claim sitting in the repository.

**This is a positioning decision, not a code change**, so it is flagged rather
than edited: either narrow the sentence to BYOK, or state the managed multiple
openly. Whichever, the customer-facing analytics no longer expose the per-model
unit price that would let someone derive it themselves.

---

## 5. Recommendations

1. **Set `MANAGED_PROVIDER_MARKUP_BPS=14000`.** One variable, no code, no
   migration. Existing receipts are unaffected — `provider_cost_paise` is
   stored per line precisely so re-costing an old call against a new multiplier
   cannot rewrite what a customer was charged.
2. **Add the ElevenLabs multilingual row first.** It is the only known case in
   the card where we bill less than the vendor charges.
3. **Measure `LLM_INPUT_SHARE` against real traffic** before trusting any LLM
   margin figure. The Tokens screen already reports prompt vs completion.
4. **Leave `openrouter` uncosted** rather than guessing a provider-wide rate.
   A router's cost is whatever it routed to.
5. **Re-check this file quarterly.** Six of the seven rates I verified moved
   within the last year.
