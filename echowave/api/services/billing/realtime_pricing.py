"""What a minute on a speech-to-speech model actually costs.

A realtime model replaces the STT + LLM + TTS stack with one bidirectional
connection, and bills for it in a unit nothing else in this package uses: the
audio itself, tokenised. That alone would just be another rate row. What makes
it need its own module is the part every published per-minute figure leaves
out.

**The conversation is re-sent as input on every turn, and the audio goes with
it.** Turn twelve carries the audio of turns one through eleven — the caller's
and the agent's own replies — and pays input price on all of it again. So the
cost of a realtime call grows with the *square* of its length, while the
tidy "$0.015 a minute" a vendor quotes describes only the first turn. On a
three-minute call at ten seconds a turn the input side is billed about nine
times over. A calculator that multiplies a per-minute rate by the number of
minutes is not slightly optimistic; it is wrong by most of the bill.

That is the same quadratic already visible on the tokens dashboard for
cascaded stacks, except audio tokens cost twenty to fifty times what text
tokens do, so here it dominates rather than nags.

Two things pull the other way and both are modelled:

* **Cached input.** Providers charge a fraction — often under 2% — for context
  they have already seen. It converts the quadratic back into something close
  to linear, and it is far and away the largest lever available.
* **Silence.** A phone call is not wall-to-wall speech. Only audio that exists
  gets tokenised, so the share of the call anyone is actually talking is a
  first-class input rather than an assumption buried in a constant.

Everything here is pure: no clock, no database, no network. The numbers it is
fed live in ``realtime_rates.py`` and, once seeded, in the rate card where an
operator can correct them.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

#: A minute is the unit every vendor quotes and every competitor compares in,
#: so results are reported per minute however long the modelled call is.
SECONDS_PER_MINUTE = 60


@dataclass(frozen=True)
class RealtimeModelPrice:
    """Vendor prices and tokenisation rates for one speech-to-speech model.

    Audio is priced per million tokens, and how many tokens a second of audio
    becomes is a property of the model rather than of the audio — the providers
    differ by more than 3x on this, which is why two models with the same
    headline per-million price are not the same price at all.
    """

    provider: str
    model: str
    label: str

    audio_input_usd_per_million: float
    audio_output_usd_per_million: float
    #: What re-sent context costs. ``None`` means the provider does not offer
    #: it, which on a long call is the most expensive difference between two
    #: otherwise comparable models.
    cached_audio_input_usd_per_million: float | None

    audio_input_tokens_per_second: float
    audio_output_tokens_per_second: float

    #: Where these figures came from, carried through to the UI. An estimate
    #: whose provenance is invisible invites more trust than it has earned.
    basis: str

    @property
    def supports_caching(self) -> bool:
        return self.cached_audio_input_usd_per_million is not None


@dataclass(frozen=True)
class CallShape:
    """The conversation being priced.

    Defaults describe a typical Indian outbound call: three minutes, turns of
    about ten seconds, and roughly two-thirds of the wall clock spent with
    somebody speaking. They are defaults, not findings — the point of exposing
    them is that a customer whose calls look different gets a different number
    instead of a reassuring one.
    """

    duration_seconds: int = 180
    seconds_per_turn: float = 10.0
    #: Share of wall-clock time the agent is speaking. Its audio re-enters the
    #: context as input on later turns, so it is billed twice over: once as
    #: output, then repeatedly as input.
    agent_talk_share: float = 0.45
    #: Share of wall-clock time the caller is speaking. The remainder is
    #: silence, which tokenises as nothing.
    caller_talk_share: float = 0.30
    #: Whether the provider's context cache is in play. Off is the honest
    #: default for a first estimate — caching needs the session to be driven in
    #: a way that hits it, and assuming it silently halves the number.
    caching_enabled: bool = False

    def __post_init__(self) -> None:
        if self.duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")
        if self.seconds_per_turn <= 0:
            raise ValueError("seconds_per_turn must be positive")
        if not 0 <= self.agent_talk_share <= 1:
            raise ValueError("agent_talk_share must be a share between 0 and 1")
        if not 0 <= self.caller_talk_share <= 1:
            raise ValueError("caller_talk_share must be a share between 0 and 1")
        if self.agent_talk_share + self.caller_talk_share > 1:
            raise ValueError(
                "agent_talk_share + caller_talk_share cannot exceed the call"
            )

    @property
    def turns(self) -> int:
        """Model responses in the call. At least one — a call that connects and
        gets a greeting has had a turn."""
        return max(1, round(self.duration_seconds / self.seconds_per_turn))

    @property
    def agent_audio_seconds(self) -> float:
        return self.duration_seconds * self.agent_talk_share

    @property
    def caller_audio_seconds(self) -> float:
        return self.duration_seconds * self.caller_talk_share

    @property
    def context_multiple(self) -> float:
        """How many times over the call's audio is billed as input.

        At turn *k* of *n* the request carries roughly ``k/n`` of the eventual
        conversation, so summing across turns gives ``(n+1)/2``. One turn means
        1.0 — nothing has been repeated yet — and it climbs from there without
        bound as calls lengthen. This single number is the difference between a
        vendor's quoted per-minute price and the invoice.
        """
        return (self.turns + 1) / 2


@dataclass(frozen=True)
class RealtimeEstimate:
    """What one call, and one minute of it, costs on a given model."""

    provider: str
    model: str
    label: str
    basis: str

    #: Tokens billed at the full input price: every second of audio is new
    #: exactly once.
    fresh_input_tokens: int
    #: Tokens billed again as re-sent context — at the cached price when the
    #: provider offers one, at full price when it does not. This is the number
    #: that grows with call length.
    repeated_input_tokens: int
    output_tokens: int

    fresh_input_micros_usd: int
    repeated_input_micros_usd: int
    output_micros_usd: int
    total_micros_usd: int
    micros_usd_per_minute: int

    context_multiple: float
    turns: int
    caching_applied: bool

    @property
    def repeated_share(self) -> float:
        """Fraction of the bill that is context being re-sent.

        The number to lead with when someone asks why a realtime call costs
        more than the vendor's page said.
        """
        if self.total_micros_usd <= 0:
            return 0.0
        return self.repeated_input_micros_usd / self.total_micros_usd


def _micros_usd(tokens: float, usd_per_million: float) -> int:
    """Token count times a per-million price, in micro-USD.

    Rounded half-up through ``Decimal`` rather than left as a float, for the
    same reason as everything else in this package: money that has been through
    binary floating point cannot be reconciled against money that has not.
    """
    usd = Decimal(str(tokens)) * Decimal(str(usd_per_million)) / Decimal(1_000_000)
    micros = usd * Decimal(1_000_000)
    return int(micros.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def estimate_realtime_call(
    price: RealtimeModelPrice, shape: CallShape | None = None
) -> RealtimeEstimate:
    """Price one speech-to-speech call, re-sent context included.

    The output side is billed once — generated audio is charged as it is
    produced. The input side is billed ``context_multiple`` times, because
    every turn re-sends what came before, and *both* participants' audio is in
    there: the agent pays input price on replaying its own replies.

    Context audio is tokenised at the input rate throughout, including the
    agent's own speech. Once it is in the conversation it is input, whatever
    produced it.
    """
    shape = shape or CallShape()

    conversation_audio_seconds = shape.agent_audio_seconds + shape.caller_audio_seconds
    total_input_tokens = (
        conversation_audio_seconds
        * price.audio_input_tokens_per_second
        * shape.context_multiple
    )
    # Each second of audio is new exactly once; everything above that is the
    # same audio arriving again.
    fresh_input_tokens = (
        conversation_audio_seconds * price.audio_input_tokens_per_second
    )
    repeated_input_tokens = max(total_input_tokens - fresh_input_tokens, 0.0)

    output_tokens = shape.agent_audio_seconds * price.audio_output_tokens_per_second

    caching_applied = shape.caching_enabled and price.supports_caching
    repeated_price = (
        price.cached_audio_input_usd_per_million
        if caching_applied
        else price.audio_input_usd_per_million
    )
    # Narrows the Optional for the type checker; caching_applied already
    # guarantees it is set.
    assert repeated_price is not None

    fresh_micros = _micros_usd(fresh_input_tokens, price.audio_input_usd_per_million)
    repeated_micros = _micros_usd(repeated_input_tokens, repeated_price)
    output_micros = _micros_usd(output_tokens, price.audio_output_usd_per_million)
    total_micros = fresh_micros + repeated_micros + output_micros

    minutes = Decimal(shape.duration_seconds) / Decimal(SECONDS_PER_MINUTE)
    per_minute = (Decimal(total_micros) / minutes).quantize(
        Decimal(1), rounding=ROUND_HALF_UP
    )

    return RealtimeEstimate(
        provider=price.provider,
        model=price.model,
        label=price.label,
        basis=price.basis,
        fresh_input_tokens=round(fresh_input_tokens),
        repeated_input_tokens=round(repeated_input_tokens),
        output_tokens=round(output_tokens),
        fresh_input_micros_usd=fresh_micros,
        repeated_input_micros_usd=repeated_micros,
        output_micros_usd=output_micros,
        total_micros_usd=total_micros,
        micros_usd_per_minute=int(per_minute),
        context_multiple=shape.context_multiple,
        turns=shape.turns,
        caching_applied=caching_applied,
    )


def blended_usd_per_1k_tokens(
    price: RealtimeModelPrice, shape: CallShape | None = None
) -> float:
    """One rate per 1k tokens, for the rate card's single-rate schema.

    The pipeline records a realtime call as ``prompt_tokens + completion_tokens``
    — one number — while the vendor charges four different prices depending on
    whether a token was audio or text, input or output, fresh or cached.
    ``provider_rates`` carries one rate per model, so something has to collapse
    that, and the honest way is to divide this module's modelled cost by the
    tokens that cost was incurred on. The blend is then a consequence of the
    same assumptions as everything else here rather than a number picked to
    look reasonable.

    It is only right for calls shaped like ``shape``. A book of thirty-second
    calls has far less re-sent context and is over-charged by a rate blended at
    three minutes; a book of ten-minute calls is under-charged. That error is
    bounded and visible, which beats the alternative on offer — no rate at all,
    every realtime call costed at zero, and margin reported as 100%.
    """
    shape = shape or CallShape()
    estimate = estimate_realtime_call(price, shape)

    total_tokens = (
        estimate.fresh_input_tokens
        + estimate.repeated_input_tokens
        + estimate.output_tokens
    )
    if total_tokens <= 0:
        return 0.0

    usd = Decimal(estimate.total_micros_usd) / Decimal(1_000_000)
    return float(usd / Decimal(total_tokens) * Decimal(1_000))


def compare_realtime_models(
    prices: tuple[RealtimeModelPrice, ...] | list[RealtimeModelPrice],
    shape: CallShape | None = None,
) -> tuple[RealtimeEstimate, ...]:
    """Every model priced against the same call, cheapest first.

    Ranking only means anything when the shape is held constant — the models
    differ enough in tokenisation rate that whichever is cheapest on a
    thirty-second call is not necessarily cheapest on a five-minute one.
    """
    shape = shape or CallShape()
    return tuple(
        sorted(
            (estimate_realtime_call(price, shape) for price in prices),
            key=lambda estimate: estimate.micros_usd_per_minute,
        )
    )
