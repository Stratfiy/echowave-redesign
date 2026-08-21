"""Forward-looking cost estimate for a chosen agent stack.

Everything else in this package prices a call after it happens. This module
answers the question an operator asks *before*: "if I build an agent on this
STT, LLM, TTS and telephony provider, what will a minute cost me?"

The rates are the same effective-dated rows the receipts use, and the managed
markup applied to them is the same effective-dated multiple, so an estimate and
the invoice it predicts can never drift apart. Both halves matter: quoting the
vendor's rate alone understated a managed minute by the whole margin, which is
exactly the "discovered on the first invoice" surprise this module exists to
prevent. What has to be assumed is
consumption — tokens and characters per minute — and rather than ship a
constant, that assumption is measured from our own completed calls on that
exact model. A vendor's generic figure would be a guess about someone else's
traffic; the median of our own is a statement about ours.

Falls back to a documented default only where we have not yet run enough calls
on a model to measure it, and always reports which of the two it used, because
an estimate whose provenance is invisible invites more trust than it has
earned.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import Float, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import CallCostItemModel, WorkflowRunModel
from api.enums import CostComponent
from api.services.billing.cost_engine import MARKED_UP_COMPONENTS
from api.services.billing.fees import (
    addon_rates_mpaise,
    byok_uplift_micros,
    uplifted_platform_rate_mpaise,
)
from api.services.billing.markup import resolve_markup_bps
from api.services.billing.money import (
    DEFAULT_PULSE_SECONDS,
    cost_paise,
    mpaise_to_micros_usd,
    platform_fee_paise,
    round_half_up_div,
)
from api.services.billing.rates import resolve_platform_rate, resolve_provider_rate
from api.services.billing.usage import byok_tier_from_components

# Consumption per connected minute, used only when we have no measured history
# for a model. Derived from a typical Indian voice-agent turn: roughly six
# exchanges a minute, ~150 output tokens and ~380 spoken characters each, with
# the prompt re-sent per turn dominating input tokens.
DEFAULT_TOKENS_PER_MINUTE = 1_400
DEFAULT_CHARACTERS_PER_MINUTE = 2_300


#: Speech-to-speech consumes tokens on a completely different scale, and the
#: text figure above under-quotes it badly.
#:
#: A realtime model is billed on *audio* tokens, and the conversation so far is
#: re-sent every turn — so a three-minute OpenAI call bills roughly 1,350 fresh
#: input tokens, 11,475 repeated ones and 1,620 output tokens. Fourteen
#: thousand where the text assumption expects four.
#:
#: **Per model, not one number.** Gemini's audio tokenisation runs about three
#: times denser than OpenAI's — 14,355 tokens a minute against 4,815 — so a
#: single realtime constant would be wrong for one vendor whichever value it
#: took. That is the mistake this table exists to prevent.
#:
#: Derived from ``realtime_pricing``'s own default call shape rather than
#: typed, because that module models context growth turn by turn and is the
#: thing that knows what a realtime minute consumes. Keyed by the name the
#: *rate lookup* sees — derived from the pipecat service class, which is why
#: these read as one run-on word. See ``usage.provider_from_processor``.
#:
#: These describe an **uncached** session, which is what we run. Prompt caching
#: cuts the repeated share by roughly two thirds; switch it on and these have
#: to come down with it.
_REALTIME_RATE_CARD_NAMES = {
    "openai_realtime": "decibylopenairealtime",
    "azure_realtime": "decibylazurerealtime",
    "google_realtime": "decibylgeminilive",
    "google_vertex_realtime": "decibylgeminilivevertex",
}


def rate_card_provider(provider: str) -> str:
    """The name the rate card knows ``provider`` by.

    Speech-to-speech has two names for one vendor and this is the seam between
    them. ``managed_tiers`` says ``openai_realtime`` — the name
    ``service_factory`` needs to build the session. The rate card says
    ``decibylopenairealtime`` — the name ``usage.provider_from_processor``
    derives from the running pipecat processor, and therefore the only name a
    completed call is ever costed under.

    Nothing bridged the two on the estimate path, so a managed realtime tier
    resolved to no rate at all: the model line was dropped into ``unpriced``,
    the estimate came back as telephony plus the platform fee, and a card that
    should have read Rs25.79 a minute read Rs2.76. The invoice was right and the
    quote was 9x under it.

    A name already in rate-card form passes through unchanged, so this is safe
    to apply to whatever a caller supplies.
    """
    return _REALTIME_RATE_CARD_NAMES.get(provider.strip().lower(), provider)


def _realtime_tokens_per_minute() -> dict[str, int]:
    from api.services.billing.realtime_pricing import CallShape, estimate_realtime_call
    from api.services.billing.realtime_rates import REALTIME_PRICES

    shape = CallShape()
    minutes = shape.duration_seconds / 60
    out: dict[str, int] = {}
    for price in REALTIME_PRICES:
        name = _REALTIME_RATE_CARD_NAMES.get(price.provider)
        if not name:
            continue
        sample = estimate_realtime_call(price, shape)
        total = (
            sample.fresh_input_tokens
            + sample.repeated_input_tokens
            + sample.output_tokens
        )
        out[name] = round(total / minutes)
    return out


REALTIME_TOKENS_PER_MINUTE = _realtime_tokens_per_minute()

# Below this many costed calls the median is noise, so the default is used.
MIN_CALLS_FOR_MEASURED_ASSUMPTION = 20

# How far back to measure. Long enough to be stable, short enough that a prompt
# rewrite six months ago does not distort today's estimate.
ASSUMPTION_WINDOW_DAYS = 30


@dataclass(frozen=True)
class EstimateLine:
    """One component of the per-minute estimate."""

    component: str
    provider: str | None
    model: str | None
    # Raw units consumed per minute, in the unit the rate is quoted against.
    units_per_minute: int
    unit_rate_mpaise: int
    paise_per_minute: int
    # "measured" when units_per_minute came from our own calls on this model,
    # "default" when we fell back, "exact" when the quantity is not an
    # assumption at all (telephony and the platform fee are per-minute rates).
    basis: str
    # True when the rate itself came from the provider-wide fallback row rather
    # than one quoted for this specific model.
    #
    # Worth reading as a warning, not a footnote. It means the price shown is
    # the vendor's default for *some* model, not this one, so switching between
    # two unpriced models on one provider shows an unchanged number — which
    # reads as a broken calculator rather than a missing rate.
    rate_is_provider_fallback: bool = False
    # True when the customer runs this slot on their own key. They pay the
    # vendor directly, so we charge nothing for it and the line is zero.
    customer_keyed: bool = False


@dataclass(frozen=True)
class CostEstimate:
    lines: tuple[EstimateLine, ...]
    total_paise_per_minute: int
    agent_paise_per_minute: int
    telephony_paise_per_minute: int
    platform_paise_per_minute: int
    # Components we were asked about but hold no rate for. Reported rather than
    # priced at zero, exactly as the cost engine does for a real call.
    unpriced: tuple[str, ...]
    #: Priced features, summed. Already inside ``total_paise_per_minute`` and
    #: reported separately for the same reason the cost engine reports it: the
    #: breakdown bar splits into agent, telephony and platform, and an add-on
    #: belongs to none of the three, so a bar built from those alone would not
    #: sum to the total beside it.
    addon_paise_per_minute: int = 0
    # Components priced against a provider-wide fallback rather than a rate for
    # the model actually chosen. Hoisted out of the lines because it is the
    # difference between "this is what it costs" and "this is what something on
    # that provider costs", and a caller should not have to go looking.
    approximated: tuple[str, ...] = ()
    # Components the customer runs on their own key, charged at zero.
    customer_keyed: tuple[str, ...] = ()
    # The same total in dollars, because that is the unit every competitor
    # quotes and the unit our own list price is fixed in. Derived from the
    # rupee figure rather than computed separately, so the two can never
    # disagree — billing still happens on the rupee side.
    total_micros_usd_per_minute: int | None = None
    usd_inr_paise: int | None = None
    # The pulse this account bills at. A per-minute estimate does not depend on
    # it, but the number beside it is the reason a minute is not what gets
    # charged, so the UI can say so.
    pulse_seconds: int = DEFAULT_PULSE_SECONDS
    # Which BYOK tier the platform fee was quoted at, and what that added.
    # Reported rather than folded silently into the fee because a customer who
    # brings their own voice pays a higher platform rate and is entitled to see
    # that it is the uplift rather than a price rise. Zero and "managed" when
    # nothing was brought, or while BYOK_TIERED_FEE_ENABLED is off.
    byok_tier: str = "managed"
    byok_uplift_micros_usd: int = 0


async def _measured_units_per_minute(
    session: AsyncSession,
    *,
    component: CostComponent,
    provider: str,
    model: str,
) -> int | None:
    """Median units per connected minute on this model, from our own calls.

    None when we have not run enough of them to say. Uses the median rather
    than the mean because one runaway call — a caller who never hung up, a
    prompt-injection loop — would drag an average badly.
    """
    since = datetime.now(UTC) - timedelta(days=ASSUMPTION_WINDOW_DAYS)

    conditions = [
        CallCostItemModel.component == component.value,
        CallCostItemModel.provider == provider,
        WorkflowRunModel.billable_seconds > 0,
        WorkflowRunModel.costed_at.isnot(None),
        WorkflowRunModel.created_at >= since,
    ]
    # An empty model means "whatever we ran", so it must not filter.
    if model:
        conditions.append(CallCostItemModel.model == model)

    per_minute = (
        cast(CallCostItemModel.units, Float)
        * 60.0
        / cast(WorkflowRunModel.billable_seconds, Float)
    )

    row = (
        await session.execute(
            select(
                func.percentile_cont(0.5).within_group(per_minute).label("median"),
                func.count().label("calls"),
            )
            .select_from(CallCostItemModel)
            .join(
                WorkflowRunModel,
                CallCostItemModel.workflow_run_id == WorkflowRunModel.id,
            )
            .where(*conditions)
        )
    ).one()

    if not row.calls or row.calls < MIN_CALLS_FOR_MEASURED_ASSUMPTION:
        return None
    if row.median is None:
        return None
    return max(int(round(row.median)), 0)


def _with_markup(paise: int, *, component: CostComponent, markup_bps: int) -> int:
    """Apply the managed markup exactly as ``cost_engine`` does, or not at all.

    Same component set, same rounding, once per line. Telephony is excluded
    there because its rate card already holds the sell price, and it has to be
    excluded here for the same reason — otherwise the estimate and the receipt
    would disagree about a line whose rate an operator typed by hand.
    """
    if component.value not in MARKED_UP_COMPONENTS:
        return paise
    return round_half_up_div(paise * markup_bps, 10_000)


async def _inference_line(
    session: AsyncSession,
    *,
    component: CostComponent,
    provider: str,
    model: str,
    at: datetime,
    default_units: int,
    markup_bps: int,
    rate_provider: str | None = None,
) -> EstimateLine | None:
    """A per-minute line for a token- or character-metered component.

    ``rate_provider`` is the name to price under when it differs from the name
    the caller asked about — see :func:`rate_card_provider`. Both the rate
    lookup and the measured-consumption query use it, because both read tables
    written by the running pipeline. The line still reports the name the caller
    supplied: they asked what ``openai_realtime`` costs, and answering with an
    internal processor name would be a different question's answer.
    """
    priced_as = rate_provider or provider
    rate = await resolve_provider_rate(
        session, provider=priced_as, component=component, at=at, model=model
    )
    if rate is None:
        return None

    measured = await _measured_units_per_minute(
        session, component=component, provider=priced_as, model=model
    )
    units = measured if measured is not None else default_units

    return EstimateLine(
        component=component.value,
        provider=provider,
        model=model or None,
        units_per_minute=units,
        unit_rate_mpaise=rate.rate_mpaise,
        paise_per_minute=_with_markup(
            cost_paise(quantity=units, rate_mpaise=rate.rate_mpaise, unit=rate.unit),
            component=component,
            markup_bps=markup_bps,
        ),
        basis="measured" if measured is not None else "default",
        rate_is_provider_fallback=bool(model) and rate.model == "",
    )


async def _per_minute_line(
    session: AsyncSession,
    *,
    component: CostComponent,
    provider: str,
    at: datetime,
    markup_bps: int,
    model: str = "",
) -> EstimateLine | None:
    """A line for a component already quoted per minute — STT and telephony.

    No consumption assumption is involved: a minute of call is a minute of
    audio, so this is exact rather than estimated.

    ``model`` is passed through to the rate lookup for the same reason the
    token-metered components pass it: the receipt resolves an STT rate against
    the model the pipeline recorded (``usage_items_from_usage_info`` carries
    it), so an estimate that looked up only the provider-wide row would quote a
    different rate from the one billed the moment anybody priced a specific
    transcription model — or quote nothing at all where only the specific row
    exists. Empty for telephony, which has no model dimension: a phone call is
    a phone call.
    """
    rate = await resolve_provider_rate(
        session, provider=provider, component=component, at=at, model=model
    )
    if rate is None:
        return None

    return EstimateLine(
        component=component.value,
        provider=provider,
        model=model or None,
        units_per_minute=60,
        unit_rate_mpaise=rate.rate_mpaise,
        paise_per_minute=_with_markup(
            cost_paise(quantity=60, rate_mpaise=rate.rate_mpaise, unit=rate.unit),
            component=component,
            markup_bps=markup_bps,
        ),
        basis="exact",
        rate_is_provider_fallback=bool(model) and rate.model == "",
    )


async def estimate_cost_per_minute(
    session: AsyncSession,
    *,
    organization_id: int | None,
    stt_provider: str | None = None,
    stt_model: str = "",
    llm_provider: str | None = None,
    llm_model: str = "",
    tts_provider: str | None = None,
    tts_model: str = "",
    telephony_provider: str | None = None,
    at: datetime | None = None,
    marked_up: bool = True,
    customer_keyed: frozenset[str] | set[str] | None = None,
    addons: frozenset[str] | set[str] | tuple[str, ...] | None = None,
) -> CostEstimate:
    """What one connected minute costs on this stack, itemised.

    Priced with the account's own platform rate, so two accounts on different
    negotiated rates see their own number rather than a list price. Pass
    ``organization_id=None`` for the list price itself — what an account with
    no negotiated override pays, which is the number an operator screen wants.

    ``marked_up`` is what the customer pays: the vendor rate plus the managed
    markup on the components we buy on our keys. That is the default because
    every caller of this is a product surface answering "what will this cost
    me" for a customer. Pass ``False`` for the other question — what the stack
    costs *us* — which is an operator's view and the one place the raw vendor
    rate is the right number.

    ``customer_keyed`` names the components — ``"stt"``, ``"llm"``, ``"tts"`` —
    the account runs on its own vendor key. Those cost the customer nothing
    *from us*: they have already paid the vendor directly, the invoice carries
    no provider line for them (see ``usage.usage_items_from_usage_info``), and
    the platform fee is uplifted instead. They are reported as zero-cost lines
    rather than dropped, so the screen can show the component with a price of
    nil and say why, instead of a row silently disappearing when a key is
    added.

    ``addons`` names the priced features the agent will use — ``"knowledge_
    base"``, ``"call_qa"``. Both are charged per billable minute by
    ``costing``, and quoting a stack without them understated a call using both
    by $0.025 a minute, which is more than the platform fee. Passed in rather
    than derived because whether a feature *fires* is a fact about a call and
    this runs before there is one: the caller names what the agent is
    configured to do, and the quote says what that would cost.

    Both the uplift and the add-on rates come from ``billing/fees.py``, which
    is the same module ``costing`` charges from and is flag-gated there, so an
    estimate quotes exactly nothing for a charge that is switched off.
    """
    at = at or datetime.now(UTC)
    lines: list[EstimateLine] = []
    unpriced: list[str] = []
    keyed = {c.strip().lower() for c in (customer_keyed or set())}

    def _free(component: CostComponent, provider: str, model: str = ""):
        """A slot on the customer's own key: shown, and priced at nothing."""
        return EstimateLine(
            component=component.value,
            provider=provider,
            model=model or None,
            units_per_minute=0,
            unit_rate_mpaise=0,
            paise_per_minute=0,
            basis="customer_keyed",
            customer_keyed=True,
        )

    # Read as at the estimate's own moment, the same way costing reads it, so
    # the quote and the receipt cannot be computed against different multiples.
    markup_bps = await resolve_markup_bps(session, at=at) if marked_up else 10_000

    if stt_provider:
        if "stt" in keyed:
            lines.append(_free(CostComponent.STT, stt_provider, stt_model))
        else:
            line = await _per_minute_line(
                session,
                component=CostComponent.STT,
                provider=stt_provider,
                model=stt_model,
                at=at,
                markup_bps=markup_bps,
            )
            lines.append(line) if line else unpriced.append(f"stt:{stt_provider}")

    if llm_provider:
        if "llm" in keyed:
            lines.append(_free(CostComponent.LLM, llm_provider, llm_model))
        else:
            # A realtime session is metered as language-model usage, and the
            # rate card knows it under the processor's name rather than the
            # tier's. Resolving that once here fixes both halves at the same
            # time: the rate lookup below, and the token assumption, which was
            # also missing every realtime model and falling through to the
            # text-conversation default of 1,400 tokens a minute.
            llm_rate_provider = rate_card_provider(llm_provider)
            line = await _inference_line(
                session,
                component=CostComponent.LLM,
                provider=llm_provider,
                rate_provider=llm_rate_provider,
                model=llm_model,
                at=at,
                default_units=REALTIME_TOKENS_PER_MINUTE.get(
                    llm_rate_provider, DEFAULT_TOKENS_PER_MINUTE
                ),
                markup_bps=markup_bps,
            )
            lines.append(line) if line else unpriced.append(f"llm:{llm_provider}")

    if tts_provider:
        if "tts" in keyed:
            lines.append(_free(CostComponent.TTS, tts_provider, tts_model))
        else:
            line = await _inference_line(
                session,
                component=CostComponent.TTS,
                provider=tts_provider,
                model=tts_model,
                at=at,
                default_units=DEFAULT_CHARACTERS_PER_MINUTE,
                markup_bps=markup_bps,
            )
            lines.append(line) if line else unpriced.append(f"tts:{tts_provider}")

    if telephony_provider:
        line = await _per_minute_line(
            session,
            component=CostComponent.TELEPHONY,
            provider=telephony_provider,
            at=at,
            markup_bps=markup_bps,
        )
        lines.append(line) if line else unpriced.append(
            f"telephony:{telephony_provider}"
        )

    platform = await resolve_platform_rate(
        session, organization_id=organization_id, at=at
    )

    # A customer on their own keys pays an uplifted platform rate rather than a
    # second fee line, exactly as ``costing`` applies it: added to the resolved
    # rate *before* the fee is computed, so the fee rounds once and the quote
    # can be checked against the receipt to the paise. This was documented in
    # this function's own docstring and never implemented, which made every
    # BYOK quote understate the fee by up to 75% the moment the flag moved.
    byok_tier = byok_tier_from_components(keyed)
    platform_rate_mpaise = uplifted_platform_rate_mpaise(
        rate_mpaise=platform.rate_mpaise,
        tier=byok_tier,
        usd_inr_paise=platform.usd_inr_paise,
    )

    platform_line = EstimateLine(
        component=CostComponent.PLATFORM.value,
        provider=None,
        model=None,
        units_per_minute=1,
        unit_rate_mpaise=platform_rate_mpaise,
        # The platform rate is already quoted per minute, so one minute costs
        # the rate itself. Priced through platform_fee_paise rather than by
        # dividing here, so the estimate rounds exactly the way the invoice will.
        paise_per_minute=platform_fee_paise(
            billable_minutes=1, rate_mpaise=platform_rate_mpaise
        ),
        basis="exact",
    )
    lines.append(platform_line)

    # One line per priced feature the agent will use, charged per billable
    # minute on the same basis as the platform fee. ``cost_engine`` prices
    # these with ``cost_paise(quantity=billed_seconds, unit=MINUTE)``; over one
    # minute that is arithmetically identical to ``platform_fee_paise`` at the
    # same rate, so a per-minute estimate and a per-call receipt agree by
    # construction rather than by two roundings that happen to match.
    addon_lines: list[EstimateLine] = []
    for key, rate in sorted(
        addon_rates_mpaise(
            keys=tuple(addons or ()), usd_inr_paise=platform.usd_inr_paise
        ).items()
    ):
        addon_lines.append(
            EstimateLine(
                component=CostComponent.ADDON.value,
                # The catalogue key rides in ``provider`` exactly as it does on
                # a receipt line, so one label map serves both screens.
                provider=key,
                model=None,
                units_per_minute=1,
                unit_rate_mpaise=rate,
                paise_per_minute=platform_fee_paise(
                    billable_minutes=1, rate_mpaise=rate
                ),
                basis="exact",
            )
        )
    lines.extend(addon_lines)

    agent = sum(
        line.paise_per_minute
        for line in lines
        if line.component in {"stt", "llm", "tts"}
    )
    telephony = sum(
        line.paise_per_minute for line in lines if line.component == "telephony"
    )

    # Defined as the sum of its own lines, the same rule the receipt uses, so
    # an estimate can always be reconciled against its breakdown.
    total_paise = sum(line.paise_per_minute for line in lines)

    return CostEstimate(
        lines=tuple(lines),
        total_paise_per_minute=total_paise,
        agent_paise_per_minute=agent,
        telephony_paise_per_minute=telephony,
        platform_paise_per_minute=platform_line.paise_per_minute,
        addon_paise_per_minute=sum(line.paise_per_minute for line in addon_lines),
        unpriced=tuple(unpriced),
        # Hoisted so a caller reads one field instead of scanning lines. This
        # is the signal that answers "why did the price not change when I
        # picked a different model" — it did not change because we hold no
        # rate for either, and both fell through to the same provider row.
        approximated=tuple(
            line.component for line in lines if line.rate_is_provider_fallback
        ),
        customer_keyed=tuple(line.component for line in lines if line.customer_keyed),
        total_micros_usd_per_minute=(
            mpaise_to_micros_usd(
                mpaise=total_paise * 1000, usd_inr_paise=platform.usd_inr_paise
            )
            if platform.usd_inr_paise
            else None
        ),
        usd_inr_paise=platform.usd_inr_paise,
        pulse_seconds=platform.pulse_seconds,
        byok_tier=byok_tier,
        byok_uplift_micros_usd=byok_uplift_micros(byok_tier),
    )


async def price_components(
    session: AsyncSession,
    *,
    organization_id: int | None,
    slots: tuple[tuple[str, str, str], ...] | list[tuple[str, str, str]],
    at: datetime | None = None,
    marked_up: bool = True,
) -> dict[tuple[str, str, str], EstimateLine | None]:
    """Price many ``(component, provider, model)`` slots in one pass.

    For a screen that has to put a figure against every model in the catalogue.
    Calling :func:`estimate_cost_per_minute` once per model would re-resolve the
    markup, the platform rate and the exchange rate for each one — the same
    answer, forty times.

    It reuses the very same line functions the estimate is built from, so a
    model priced here and the same model priced inside a full stack estimate
    cannot disagree. That is the whole reason this is not a small independent
    loop over ``provider_rates``: every pricing defect this codebase has shipped
    was a second calculation that drifted from the first.

    ``None`` against a slot means no rate is on file for it. The caller decides
    what to do about that; showing a price of zero is never it.
    """
    at = at or datetime.now(UTC)
    markup_bps = await resolve_markup_bps(session, at=at) if marked_up else 10_000

    out: dict[tuple[str, str, str], EstimateLine | None] = {}
    for component_value, provider, model in slots:
        try:
            component = CostComponent(component_value)
        except ValueError:
            # Not a billed component — embeddings are consumed at ingest and
            # realtime is metered as LLM usage under the LLM component. Neither
            # has a per-minute line of its own under that name.
            out[(component_value, provider, model)] = None
            continue

        if component in (CostComponent.STT, CostComponent.TELEPHONY):
            line = await _per_minute_line(
                session,
                component=component,
                provider=provider,
                model=model,
                at=at,
                markup_bps=markup_bps,
            )
        else:
            rate_provider = rate_card_provider(provider)
            line = await _inference_line(
                session,
                component=component,
                provider=provider,
                rate_provider=rate_provider,
                model=model,
                at=at,
                default_units=(
                    REALTIME_TOKENS_PER_MINUTE.get(
                        rate_provider, DEFAULT_TOKENS_PER_MINUTE
                    )
                    if component is CostComponent.LLM
                    else DEFAULT_CHARACTERS_PER_MINUTE
                ),
                markup_bps=markup_bps,
            )
        out[(component_value, provider, model)] = line
    return out
