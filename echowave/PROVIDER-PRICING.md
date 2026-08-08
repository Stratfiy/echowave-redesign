# Provider pricing and where we sit against the market

Checked against vendor pricing pages in **August 2026**. Sources at the end.

This is the research behind the seeded price book in
`api/services/billing/default_rates.py`. The rates below are now what the cost
engine uses; correct any of them at `/superadmin/billing/rate-card`, which is
where the value that actually bills lives — the seeder never overwrites a rate
an operator has set.

---

## 1. What changed, and by how much

Seven rows were wrong. Two of them mattered a lot.

| Line | Was | Now | Direction |
|---|---|---|---|
| **Deepgram STT** | $0.0043/min | **$0.0077/min** | **+79%** — we were quoting the *batch* price; a voice agent streams |
| **Sarvam TTS (default)** | ₹3.00/1k chars | **₹1.50/1k chars** | **−50%** — v3 beta price quoted for a tier that runs v2 |
| **Plivo India** | ₹0.29/min | **₹0.60/min** | **+107%** — published India outbound local |
| **Twilio** | $0.014/min (US) | **₹1.20/min (India)** | reframed — the traffic is Indian |
| ElevenLabs TTS | $0.10/1k | **$0.05/1k** | −50% — Flash v2.5 list |
| Cartesia TTS | $0.05/1k | **$0.035/1k** | −30% — Sonic 3 |
| Google LLM | $0.075/$0.30 per 1M | **$0.15/$1.25** | +2× — Gemini 2.5 Flash current list |
| Sarvam LLM | $0.10/$0.30 per 1M | **₹4/₹16 per 1M** | −60% — published rupee price |

The two that matter:

**Deepgram was priced at the batch rate.** Streaming costs nearly twice as much
($0.0077 vs $0.0043) and streaming is what a live conversation uses. Every
Deepgram call was under-costed by 44%.

**Sarvam TTS quoted the wrong Bulbul generation.** v2 is ₹15/10k characters and
v3 beta is ₹30/10k. The provider-wide row quoted v3 while the default managed
tier resolves to `bulbul:v2`, so every managed Indic call was priced at twice
the synthesis it actually bought. Both generations now have explicit rows, so
switching moves the cost.

---

## 2. The full price book, as seeded

**Speech to text** — per minute of audio

| Provider | Rate | Basis |
|---|---|---|
| Sarvam (Saarika v2) | **₹0.50** | ₹30/hour published. Diarization is ₹45/hour. |
| Deepgram (Nova-3) | **$0.0077** | streaming; batch is $0.0043 |
| ElevenLabs (Scribe) | $0.0060 | |
| Azure | $0.0167 | $1.00/hour standard |

**Text to speech** — per 1,000 characters

| Provider | Rate | Basis |
|---|---|---|
| Sarvam Bulbul **v2** | **₹1.50** | ₹15/10k — the managed default |
| Sarvam Bulbul v3 beta | ₹3.00 | ₹30/10k |
| Cartesia Sonic 3 | $0.035 | ~$35/1M chars |
| ElevenLabs Flash v2.5 | $0.05 | varies most of any line here, by plan |
| OpenAI | $0.015 | $15/1M chars |
| Smallest Lightning | $0.020 | |

**Language models** — per 1,000 tokens, blended 70% input / 30% output

| Model | Input / output per 1M | Blended |
|---|---|---|
| Gemini 2.5 Flash-Lite | $0.10 / $0.40 | $0.00017 |
| Gemini 2.5 Flash | $0.15 / $1.25 | $0.00048 |
| Sarvam-105B | ₹4 / ₹16 | ₹0.0075 |
| gpt-4o-mini | $0.15 / $0.60 | $0.00029 |
| gpt-4.1-mini | $0.40 / $1.60 | $0.00076 |
| gpt-4o | $2.50 / $10.00 | $0.00475 |

> **Gemini 2.5 Flash-Lite retires 16 October 2026.** Its replacement, Gemini 3.1
> Flash-Lite, lists at $0.25/$1.50 — **2.5× dearer**. The `fast`, `lite` and
> `zen` managed tiers all point at Flash-Lite today, so their cost jumps on that
> date unless they are repointed. This is the single dated risk in the book.

**Telephony** — per minute

| Provider | Rate | Basis |
|---|---|---|
| Plivo India | **₹0.60** | outbound local published; **SIP / Browser SDK is ₹0.34** |
| Twilio India | **₹1.20** | to mobile; landline ~₹0.65 |
| Telnyx | $0.007 | US outbound |
| Vonage | $0.0139 | US outbound |

Plivo is seeded at the **higher** of its two published rates deliberately.
Under-reporting carriage overstates margin, and which rate applies depends on
how calls are actually placed. Drop it to ₹0.34 once the account is confirmed to
be dialling over SIP.

---

## 3. What a real call costs now

A 100-second Telugu call, 65% agent speech, ~850 characters/minute, at the
seeded rates:

| Stack | STT | TTS | LLM | Telephony | Provider cost | Invoice |
|---|---|---|---|---|---|---|
| **Managed Indic** (Sarvam v2 + Gemini Flash + Plivo) | ₹0.83 | ₹1.38 | ₹0.09 | ₹1.00 | **₹3.31** | ₹6.67 (₹4.00/min) |
| Western BYOK (Deepgram + gpt-4o-mini + ElevenLabs) | ₹1.23 | ₹4.42 | ₹0.06 | ₹1.00 | **₹6.71** | ₹10.07 (₹6.04/min) |
| Premium (+ gpt-4o + Twilio) | ₹1.23 | ₹4.42 | ₹0.06 | ₹2.00 | **₹7.71** | ₹11.07 (₹6.64/min) |

Two things fall out of this:

- **TTS is the cost question**, at 42% of the cheap stack and 66% of the
  Western one. Pre-rendering common fragments is worth more than any other
  optimisation.
- **Telephony is 30% of the cheap stack** — the second largest line, and the one
  most often under-budgeted, because it does not feel like an AI cost.
- **The LLM is a rounding error** at 3%. Paying more for a better model is
  almost free; paying more for a better voice is not.

---

## 4. How competitors charge

| Platform | Platform fee | What it includes |
|---|---|---|
| **Decibyl (us)** | **$0.02/min** (₹1.92) | orchestration only; provider cost passed through at rate-card |
| Vapi | $0.05/min | orchestration only, providers excluded; from $50/mo |
| Retell | $0.07/min | + LLM cost |
| Bland | $0.09/min | flat, all-in — no BYOK, no provider choice |
| Synthflow | $0.13–0.20/min | managed plans, LLM included; from $99/mo |
| ElevenLabs Agents | $0.08–0.24/min | whole pipeline included |

**We charge 60% less than the cheapest major platform**, and 78% less than
Bland's all-in rate. Two readings of that, and they point in opposite
directions:

- It is correct for India. A ₹1.92/min fee against a ₹3.31 provider cost is a
  believable number to a government buyer; Vapi's $0.05 (₹4.80) alone would
  exceed the entire provider stack.
- It may be leaving money on the table with anyone paying in dollars. There is
  no monthly platform minimum either, where every competitor has one ($50–$99).
  A dollar-denominated tier at $0.04–0.05/min would still undercut Vapi.

The published-rate comparison also flatters everyone but Bland: a $0.05
orchestration fee is $0.14–0.18 fully loaded once providers are added, which is
the number a customer actually pays. Our ₹4.00/min all-in managed Indic stack is
**$0.042/min** — genuinely cheaper than any of them, because the Indic providers
are cheaper, not because the fee is.

---

## 5. What this does to the Telangana tender

`TENDER-COST-AND-QUOTE.md` costs the campaign at **₹3,13,568** against a
₹5.25L bid. Two of its assumptions are now verifiable, and they move in
opposite directions:

| Assumption | Tender used | Verified | Effect on campaign cost |
|---|---|---|---|
| Sarvam TTS | ₹0.003/char (= v3) | **v2 is ₹0.0015** | **−₹46,700** if v2 is used |
| Telephony | ₹0.25/min (flagged unverified) | **₹0.34 SIP / ₹0.60 local** | **+₹8,100** to **+₹31,500** |

Best case (Bulbul v2 + SIP): campaign cost falls about **₹38,600**.
Worst case (v3 + local PSTN): it rises about **₹31,500**.

Neither breaks the bid — the margin absorbs both — but the decision is worth
making explicitly rather than discovering on the first invoice:

1. **Confirm which Bulbul generation the campaign runs.** It is the single
   largest cost lever, worth ₹46,700 on this campaign alone.
2. **Get the written Plivo quote** the tender doc already lists as risk #4. The
   ₹0.25/min assumption is below both published rates.

---

## 6. Standing caveats

- **Nobody pays list.** Volume commitments and negotiated rates move these
  down, usually materially. These are the numbers before you have asked.
- **LLM rates are blended** 70/30 input/output because the schema carries one
  rate per model and a call's token split is not known until it happens. Voice
  agents are input-heavy — they resend the transcript every turn — but a
  different mix moves the number.
- **`AS_OF` is an expiry date, not a footnote.** It reads `2026-08`. Vendor
  prices change and this file does not.
- Sarvam's cached-input rate (₹2.5/1M) cannot be expressed in a single-rate
  schema, so the blend ignores it. Real cost will be slightly below the
  quoted figure on conversations with a stable system prompt.

---

## Sources

- [Sarvam AI pricing (docs)](https://docs.sarvam.ai/api/getting-started/pricing.md) — STT ₹30/hr, Bulbul v2 ₹15/10k, v3 ₹30/10k, LLM ₹4/₹2.5/₹16 per 1M
- [Plivo India voice pricing](https://www.plivo.com/voice/pricing/in/) — ₹0.60/min local, ₹0.34/min SIP
- [Deepgram Nova-3 pricing 2026](https://convertaudiototext.com/blog/deepgram-nova-3-explained) — $0.0043 batch / $0.0077 streaming
- [Gemini API pricing (Aug 2026)](https://benchlm.ai/google/api-pricing) — 2.5 Flash $0.15/$1.25, Flash-Lite $0.10/$0.40
- [OpenAI API pricing](https://developers.openai.com/api/docs/pricing)
- [ElevenLabs pricing 2026](https://www.cekura.ai/blogs/elevenlabs-pricing) · [ElevenLabs vs Cartesia](https://futureagi.com/blog/elevenlabs-vs-cartesia-tts-2026/)
- [Voice AI pricing per minute 2026](https://caller.digital/voice-ai-pricing-comparison) · [True per-minute cost](https://devaland.com/blog/voice-ai-pricing-comparison-2025) — platform fee comparison
- [Telephony partners for voice AI in India](https://caller.digital/blog/telephony-partner-voice-ai-india-plivo-exotel-ozonetel-knowlarity-twilio-2026) — ₹0.80–1.80/min aggregator benchmarks
