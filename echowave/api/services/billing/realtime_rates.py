"""Published prices for the speech-to-speech models, so the calculator has
something to calculate with.

Same standing as ``default_rates.py`` and the same warning applies with more
force: **these are approximate list prices and they will be wrong for you.**
Realtime pricing has moved faster than anything else in this file's neighbours,
the models are renamed every few months, and almost nobody pays list.

Two figures per model, and the second is the one people forget:

* **Price per million audio tokens**, quoted by every vendor.
* **How many tokens a second of audio becomes**, quoted by almost none of them
  prominently, and different by more than 3x between providers. Gemini Live
  tokenises input audio at 32 tokens a second against OpenAI's ~10, so a
  per-million price comparison between the two is meaningless on its own —
  Gemini's cheaper-looking per-million rate is spread over three times as many
  tokens. Any calculator that compares the headline numbers and stops has
  produced a ranking with no relationship to the invoice.

The tokenisation rates below are the load-bearing assumption in this file. The
Gemini pair cross-checks against Google's own published per-minute figures to
within a few percent, which is why they carry more confidence than the OpenAI
ones. The OpenAI rates are derived from reported per-minute conversions rather
than a primary tokenisation table, and are the first thing to verify.

``AS_OF`` is an expiry date, not a footnote.
"""

from __future__ import annotations

from api.services.billing.realtime_pricing import RealtimeModelPrice

#: When these were last checked. Realtime pricing changes faster than the rest
#: of the price book, so treat anything more than a quarter old as unverified.
AS_OF = "2026-07"

#: Shown beside every figure these produce, so a number sourced from a list
#: page is never mistaken for one sourced from an invoice.
SEED_NOTE = (
    f"Published list price as of {AS_OF}. Verify against your own plan before "
    "quoting a customer or reporting margin."
)


REALTIME_PRICES: tuple[RealtimeModelPrice, ...] = (
    RealtimeModelPrice(
        provider="google_realtime",
        model="gemini-3.1-flash-live-preview",
        label="Gemini 3.1 Flash Live",
        audio_input_usd_per_million=3.00,
        audio_output_usd_per_million=12.00,
        # Google publishes implicit context caching for Live sessions. Priced
        # here at the widely-reported ~10% of input; the least certain number
        # in this file and the one that most changes a long call.
        cached_audio_input_usd_per_million=0.30,
        audio_input_tokens_per_second=32.0,
        audio_output_tokens_per_second=25.0,
        basis=(
            "$3.00/$12.00 per 1M audio tokens; 32 tok/s in, 25 tok/s out. "
            "Cross-checks against Google's published ~$0.005/min in and "
            "~$0.018/min out."
        ),
    ),
    RealtimeModelPrice(
        provider="google_vertex_realtime",
        model="google/gemini-live-2.5-flash-native-audio",
        label="Gemini 2.5 Flash Native Audio (Vertex)",
        audio_input_usd_per_million=3.00,
        audio_output_usd_per_million=12.00,
        cached_audio_input_usd_per_million=0.30,
        audio_input_tokens_per_second=32.0,
        audio_output_tokens_per_second=25.0,
        basis="$3.00 audio-video in / $12.00 audio out per 1M, Vertex list.",
    ),
    RealtimeModelPrice(
        provider="openai_realtime",
        model="gpt-realtime-2",
        label="OpenAI GPT Realtime 2",
        audio_input_usd_per_million=32.00,
        audio_output_usd_per_million=64.00,
        # The steepest cached discount of anything here — roughly 1% of fresh
        # input. On a long call it is the difference between this model being
        # unaffordable and being competitive.
        cached_audio_input_usd_per_million=0.40,
        audio_input_tokens_per_second=10.0,
        audio_output_tokens_per_second=20.0,
        basis=(
            "$32/$64 per 1M audio tokens, $0.40 cached. Tokenisation derived "
            "from reported ~600 tok/min in and ~1,200 tok/min out — verify, "
            "it is not from a primary table."
        ),
    ),
)


#: Keyed for lookup by the provider names the configuration registry uses, so a
#: configured agent can be priced without a translation table in between.
REALTIME_PRICES_BY_PROVIDER: dict[str, RealtimeModelPrice] = {
    price.provider: price for price in REALTIME_PRICES
}


def price_for(provider: str, model: str = "") -> RealtimeModelPrice | None:
    """The price book entry for a configured realtime provider.

    Matches the exact model first and falls back to the provider, mirroring how
    ``provider_rates`` resolves: a model we hold no specific price for is
    better estimated at its provider's rate than not estimated at all.
    """
    provider = (provider or "").strip().lower()
    model = (model or "").strip().lower()

    if model:
        for price in REALTIME_PRICES:
            if price.provider == provider and price.model.lower() == model:
                return price
    return REALTIME_PRICES_BY_PROVIDER.get(provider)
