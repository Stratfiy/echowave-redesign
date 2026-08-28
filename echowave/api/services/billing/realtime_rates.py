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
    RealtimeModelPrice(
        provider="openai_realtime",
        model="gpt-realtime-2.1",
        label="OpenAI GPT Realtime 2.1",
        # Identical token prices to the ``-2`` it supersedes, which is the
        # whole reason it is worth moving to: a newer flagship that costs the
        # same. Carried as its own row rather than left to inherit, so that the
        # day the two prices diverge one number moves and the other does not.
        audio_input_usd_per_million=32.00,
        audio_output_usd_per_million=64.00,
        cached_audio_input_usd_per_million=0.40,
        audio_input_tokens_per_second=10.0,
        audio_output_tokens_per_second=20.0,
        basis="$32/$64 per 1M audio tokens, $0.40 cached — same as gpt-realtime-2.",
    ),
    RealtimeModelPrice(
        provider="openai_realtime",
        model="gpt-realtime-2.1-mini",
        label="OpenAI GPT Realtime 2.1 mini",
        # $10/$20 against the flagship's $32/$64 — 3.2x cheaper on *both* legs,
        # which is what makes the blend scale cleanly: the ratio is identical
        # whatever the mix of input and output turns out to be.
        #
        # Priced as its own entry rather than left to the provider-wide row,
        # because that row is the flagship's. A mini call inheriting it would be
        # costed at 3.2x what the vendor charges, and provider lines carry the
        # managed markup — so the error is money taken from the customer, not
        # an internal reporting slip.
        audio_input_usd_per_million=10.00,
        audio_output_usd_per_million=20.00,
        cached_audio_input_usd_per_million=0.30,
        # Same tokenisation as the flagship: the mini is a smaller model behind
        # the same audio front end, so a second of speech becomes the same
        # number of tokens. Only the per-token price differs.
        audio_input_tokens_per_second=10.0,
        audio_output_tokens_per_second=20.0,
        basis=(
            "$10/$20 per 1M audio tokens, $0.30 cached, from OpenAI's pricing "
            "page. Tokenisation assumed identical to gpt-realtime-2 — verify, "
            "it is inherited rather than published."
        ),
    ),
    RealtimeModelPrice(
        provider="azure_realtime",
        model="gpt-realtime-2",
        label="Azure OpenAI GPT Realtime 2",
        # Azure resells the same model at the same token prices; what differs
        # is the commercial wrapper around it — support plans, committed
        # throughput — none of which lands on a per-call receipt. The pair is
        # carried separately rather than aliased so that when the two do
        # diverge, one number moves and the other does not.
        audio_input_usd_per_million=32.00,
        audio_output_usd_per_million=64.00,
        cached_audio_input_usd_per_million=0.40,
        audio_input_tokens_per_second=10.0,
        audio_output_tokens_per_second=20.0,
        basis=(
            "Mirrors OpenAI's published $32/$64 per 1M audio tokens, $0.40 "
            "cached. Azure quotes token prices identical to the direct API; "
            "verify against your enterprise agreement, which is where the two "
            "actually differ."
        ),
    ),
)

# Ultravox and Grok Realtime are deliberately absent.
#
# Both bill **per minute of audio** — around $0.05 — and neither publishes a
# tokenisation rate. The pipeline records speech-to-speech usage as LLM tokens,
# so pricing them here would mean inventing a tokens-per-second figure for a
# vendor that does not have one, and every call on them would be costed against
# that invention rather than against their invoice.
#
# Their usage is reported as uncosted instead, which is visible on the receipt
# and on the Models margin screen. Pricing them properly needs a per-minute
# usage path for realtime, which is a schema change, not a price book entry.


#: The entry used when a realtime model has no price of its own — so, the rate
#: the provider-wide rate card row is seeded from.
#:
#: **First entry per provider wins, and that is load-bearing.** A dict
#: comprehension over ``REALTIME_PRICES`` takes the *last*, which meant adding
#: ``gpt-realtime-2.1-mini`` below the flagship silently made the mini the
#: default for all of ``openai_realtime`` — and the two flagships, which have no
#: model-specific row, would have costed at a third of what they charge. The
#: order of a list is not a thing to hang a price on, so it is spelled out here
#: rather than left to whoever appends next.
#:
#: The flagship is the right default for the same reason: it is what the tiers
#: resolve to, so the common case is priced correctly, and a cheaper model that
#: needs its own row will announce itself as one that costs less rather than
#: hide as one that costs more.
def _by_provider() -> dict[str, RealtimeModelPrice]:
    out: dict[str, RealtimeModelPrice] = {}
    for price in REALTIME_PRICES:
        out.setdefault(price.provider, price)
    return out


REALTIME_PRICES_BY_PROVIDER: dict[str, RealtimeModelPrice] = _by_provider()


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
