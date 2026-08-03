"""A starter price book, so the cost engine has something to price with.

`provider_rates` ships empty. Nothing seeds it, so on a fresh install every
estimate is the platform fee and a warning — the model picker shows the same
number whatever you choose, and margin reporting says provider cost is zero
because no rate was on file, not because the call was free. Every comparable
product ships a vendor price book; this is ours.

**These are approximate published list prices, and they will be wrong for you.**
Three reasons, all of which matter:

* Vendor prices change, and this file does not. Treat `AS_OF` as an expiry date,
  not a footnote.
* Almost nobody pays list. Volume commitments, startup credits and negotiated
  rates all move the real number, usually downward.
* **LLM rates here are blended.** Vendors price input and output tokens
  separately; this schema carries one rate per model because a call's token
  split is not known until it happens. The blend below assumes
  `LLM_INPUT_SHARE` input — voice agents resend a growing transcript every turn,
  so they are input-heavy — and a different mix moves the number materially.

So this is a starting point that makes the machinery work on day one, not a
statement about what anything costs. Every row is written with a note saying so,
which surfaces in the rate card, and the seeding script refuses to overwrite a
rate an operator has already set. Correct them in
`/superadmin/billing/rate-card`, where the value that actually bills lives.

Prices are held in **USD**, because that is the currency vendors quote in.
Conversion to millipaise happens at seed time against the configured USD→INR
rate, so the conversion is explicit and re-runnable rather than baked into a
constant nobody can audit.
"""

from __future__ import annotations

from dataclasses import dataclass

from api.enums import CostComponent, RateUnit

#: When these prices were last checked against vendor pricing pages. A price
#: book with no date is one nobody can tell is stale.
AS_OF = "2026-05"

#: Share of LLM tokens assumed to be input, for blending a two-sided vendor
#: price into the single rate this schema carries. Voice agents resend the
#: conversation each turn, so they skew heavily to input.
LLM_INPUT_SHARE = 0.7

#: Written onto every seeded row so an operator can tell at a glance which
#: rates were chosen and which were merely defaulted.
SEED_NOTE = (
    f"Seeded default — list price as of {AS_OF}. Verify before relying on margin."
)


@dataclass(frozen=True)
class DefaultRate:
    provider: str
    #: Empty string is the provider-wide fallback for any model without a row.
    model: str
    component: CostComponent
    unit: RateUnit
    usd_per_unit: float
    #: How the number was arrived at, for anyone auditing it later.
    basis: str


#: Reference USD→INR for the few vendors who publish in rupees. Matches
#: ``money.DEFAULT_USD_INR_PAISE``; a test holds them together, because if they
#: drift the rupee prices below silently stop being the rupee prices published.
REFERENCE_USD_INR = 96.0


def _inr(rupees_per_unit: float) -> float:
    """A rupee-quoted vendor price, expressed in this file's USD.

    Indian vendors — Sarvam foremost — publish in ₹, and this schema is USD, so
    their rows are a round trip: ₹ here, back to ₹ at seed time against whatever
    USD→INR is then configured. The trip is lossless only while that rate equals
    ``REFERENCE_USD_INR``. It is worth the wart to keep one unit in the file, but
    it means a rupee vendor's row moves when the dollar moves and its real price
    did not — check these two rows first when Sarvam's cost looks off.
    """
    return rupees_per_unit / REFERENCE_USD_INR


def _blend(input_per_million: float, output_per_million: float) -> float:
    """Vendor per-million-token prices to one blended USD per 1k tokens."""
    per_million = input_per_million * LLM_INPUT_SHARE + output_per_million * (
        1 - LLM_INPUT_SHARE
    )
    return per_million / 1000


#: Language models — unit is 1k tokens, blended. Provider-wide fallbacks are the
#: cheapest common model, so an unpriced model under-reports rather than
#: over-reports; a surprise on the invoice should be pleasant.
LLM_RATES = (
    DefaultRate(
        "openai",
        "",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(0.15, 0.60),
        "gpt-4o-mini list, blended",
    ),
    DefaultRate(
        "openai",
        "gpt-4o-mini",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(0.15, 0.60),
        "$0.15/$0.60 per 1M, blended",
    ),
    DefaultRate(
        "openai",
        "gpt-4o",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(2.50, 10.00),
        "$2.50/$10.00 per 1M, blended",
    ),
    DefaultRate(
        "anthropic",
        "",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(0.80, 4.00),
        "Haiku-class list, blended",
    ),
    DefaultRate(
        "google",
        "",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(0.075, 0.30),
        "Flash-class list, blended",
    ),
    DefaultRate(
        "sarvam",
        "",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(0.10, 0.30),
        "Indic-model list, blended",
    ),
)


def _realtime_blend(provider: str) -> float:
    """Blended USD per 1k tokens for a speech-to-speech model.

    Derived from ``realtime_pricing`` rather than written down, so the seeded
    rate moves with the model that explains it instead of drifting away from it.
    """
    from api.services.billing.realtime_pricing import blended_usd_per_1k_tokens
    from api.services.billing.realtime_rates import price_for

    price = price_for(provider)
    if price is None:  # pragma: no cover - guarded by a test
        raise KeyError(f"No realtime price book entry for {provider!r}")
    return blended_usd_per_1k_tokens(price)


#: Speech-to-speech models, recorded by the pipeline as ordinary LLM usage.
#:
#: The provider names are the ones ``provider_from_processor`` derives from the
#: service class — ``decibylgeminilive``, not ``google_realtime`` — because that
#: is what lands in ``call_cost_items``. Getting these strings wrong is not a
#: visible failure: the rate simply never matches, every realtime call is
#: reported uncosted, and margin reads 100%.
#:
#: The rate is a blend across audio in, re-sent context and audio out at a
#: three-minute reference call. Realtime billing has four prices and this schema
#: has one field, so a blend is unavoidable; see ``blended_usd_per_1k_tokens``.
REALTIME_RATES = (
    DefaultRate(
        "decibylgeminilive",
        "",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _realtime_blend("google_realtime"),
        "Gemini Live audio tokens, blended at a 3-minute call",
    ),
    DefaultRate(
        "decibylgeminilivevertex",
        "",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _realtime_blend("google_vertex_realtime"),
        "Gemini Live on Vertex, blended at a 3-minute call",
    ),
    DefaultRate(
        "decibylopenairealtime",
        "",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _realtime_blend("openai_realtime"),
        "GPT Realtime audio tokens, blended at a 3-minute call",
    ),
)

#: Speech to text — unit is a minute of audio.
STT_RATES = (
    DefaultRate(
        "deepgram",
        "",
        CostComponent.STT,
        RateUnit.MINUTE,
        0.0043,
        "Nova streaming, pay-as-you-go",
    ),
    DefaultRate(
        "sarvam",
        "",
        CostComponent.STT,
        RateUnit.MINUTE,
        _inr(30.0 / 60),
        "Saarika — ₹30/hour published. Diarization is ₹45/hour and not this row.",
    ),
    DefaultRate("elevenlabs", "", CostComponent.STT, RateUnit.MINUTE, 0.0060, "Scribe"),
    DefaultRate(
        "azure", "", CostComponent.STT, RateUnit.MINUTE, 0.0167, "$1.00/hour standard"
    ),
)

#: Speech synthesis — unit is 1k characters, which is what voice vendors bill.
TTS_RATES = (
    DefaultRate(
        "openai",
        "",
        CostComponent.TTS,
        RateUnit.THOUSAND_CHARS,
        0.0150,
        "$15 per 1M characters",
    ),
    DefaultRate(
        "elevenlabs",
        "",
        CostComponent.TTS,
        RateUnit.THOUSAND_CHARS,
        0.1000,
        "Mid-tier plan effective rate — varies most of any line here",
    ),
    DefaultRate(
        "cartesia",
        "",
        CostComponent.TTS,
        RateUnit.THOUSAND_CHARS,
        0.0500,
        "Sonic pay-as-you-go",
    ),
    DefaultRate(
        "sarvam",
        "",
        CostComponent.TTS,
        RateUnit.THOUSAND_CHARS,
        _inr(3.00),
        "Bulbul v3 — ₹30 per 10k characters published",
    ),
    DefaultRate(
        "smallest", "", CostComponent.TTS, RateUnit.THOUSAND_CHARS, 0.0200, "Lightning"
    ),
)

#: Carriage — unit is a call minute. Outbound domestic; international and
#: inbound differ, sometimes by an order of magnitude, and no single row can
#: express that.
TELEPHONY_RATES = (
    DefaultRate(
        "twilio", "", CostComponent.TELEPHONY, RateUnit.MINUTE, 0.0140, "US outbound"
    ),
    DefaultRate(
        "plivo", "", CostComponent.TELEPHONY, RateUnit.MINUTE, 0.0030, "India outbound"
    ),
    DefaultRate(
        "telnyx", "", CostComponent.TELEPHONY, RateUnit.MINUTE, 0.0070, "US outbound"
    ),
    DefaultRate(
        "vonage", "", CostComponent.TELEPHONY, RateUnit.MINUTE, 0.0139, "US outbound"
    ),
)

DEFAULT_RATES: tuple[DefaultRate, ...] = (
    *LLM_RATES,
    *REALTIME_RATES,
    *STT_RATES,
    *TTS_RATES,
    *TELEPHONY_RATES,
)


def usd_to_mpaise(usd: float, *, usd_inr: float) -> int:
    """One USD price to millipaise, rounded half-up.

    ₹1 is 100 paise is 100,000 millipaise. Rounding away from zero rather than
    banker's rounding, to match how every other money conversion in this
    codebase behaves — consistency matters more than the last thousandth of a
    paise on a rate that is an estimate anyway.
    """
    from decimal import ROUND_HALF_UP, Decimal

    mpaise = Decimal(str(usd)) * Decimal(str(usd_inr)) * Decimal(100_000)
    return int(mpaise.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
