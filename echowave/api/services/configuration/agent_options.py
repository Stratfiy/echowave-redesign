"""The model choice, in words the person buying it uses.

The Models screen names vendors and models because someone bringing their own
keys has to see them. The person this product is aimed at — a clinic owner, a
dealership, a coaching centre — does not know Sarvam from OpenAI and should not
have to learn in order to answer a phone.

So the same choice is offered twice, in two vocabularies:

* **Models screen** — provider and model, for whoever is pointing us at an
  account they pay for.
* **Here** — a voice you can listen to and a brain that is Lite, Normal or
  Smart, with one price per minute underneath.

Both write the same thing: a managed slot naming a tier, which
``managed_resolution`` turns into a vendor at call time. Nothing about the
underlying arrangement changes; only what is said about it.

**The price is the point.** A tier the customer cannot price is a tier they
will not pick, so every option carries what it costs a minute on the current
rate card, and the caller can turn a balance into an approximate number of
minutes with it.
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas.ai_model_configuration import DECIBYL_DEFAULT_VOICE
from api.services.billing.estimator import estimate_cost_per_minute
from api.services.configuration import managed_tiers, voice_catalogue
from api.services.configuration.ai_model_configuration import (
    WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY,
)
from api.services.configuration.registry import ServiceProviders


#: Voices we are happy to put in front of a first-time buyer, in the order they
#: should be shown. A short curated list beats the vendor's full catalogue:
#: seven names with no way to tell them apart is not a choice, it is a quiz.
#:
#: The character words are deliberately absent. Nobody here has listened to
#: these voices, and inventing "warm" or "authoritative" for a voice we have
#: not heard would be a claim a caller can immediately check. They belong in
#: `description` once somebody has sat with the samples — see
#: `docs/images/SHOTLIST.md` for the same discipline applied to screenshots.
@dataclass(frozen=True)
class VoiceOption:
    voice_id: str
    name: str
    gender: str | None
    description: str | None = None
    #: The one a caller hears if nobody chooses. Derived from position rather
    #: than hardcoded: the catalogue lists each model's speakers in the
    #: vendor's own order and the vendor's default is first — anushka on
    #: bulbul:v2, shubh on v3 — so this stays right when the tier moves.
    is_default: bool = False


@dataclass(frozen=True)
class BrainOption:
    tier: str
    label: str
    blurb: str


def brains() -> list[BrainOption]:
    """The language-model tiers, in the order they should be shown."""
    return [
        BrainOption(tier=tier, label=label, blurb=blurb)
        for tier, (label, blurb) in (
            (t, managed_tiers.LLM_TIER_LABELS[t]) for t in managed_tiers.LLM_TIERS
        )
    ]


def voices() -> list[VoiceOption]:
    """Managed voices, as the catalogue serves them.

    Resolved through the managed tier, so this is what a managed customer will
    really get rather than a list we maintain separately and forget to update.
    """
    catalogue = voice_catalogue.for_provider("decibyl", model=None)
    return [
        VoiceOption(
            voice_id=voice.voice_id,
            name=voice.name,
            gender=voice.gender,
            description=voice.description,
            is_default=index == 0,
        )
        for index, voice in enumerate(catalogue.voices)
    ]


def _priced(estimate, *, what: str) -> int | None:
    """The total, or ``None`` when some component of it has no rate on file.

    The estimator already reports an unpriced component; every caller here used
    to drop that and return the total anyway. A total missing its largest line
    is not a smaller price, it is a wrong one — a speech-to-speech card quoted
    Rs2.76 a minute against a real Rs25.79 that way, because the model resolved
    to no rate and what was left was the telephony and the platform fee.

    ``None`` rather than zero, and rather than a total: a screen can say "we
    cannot price this" and an operator can go and add the rate. Nothing
    downstream can mistake it for cheap.
    """
    if estimate.unpriced:
        logger.warning(
            "Refusing to quote {}: no rate on file for {}. The card will show "
            "no price until one is added at /superadmin/billing/rate-card.",
            what,
            ", ".join(estimate.unpriced),
        )
        return None
    return estimate.total_paise_per_minute


async def pipeline_estimate(
    session: AsyncSession,
    *,
    organization_id: int | None,
    brain: str,
    stt_tier: str = "default",
    tts_tier: str = "default",
    telephony_provider: str | None = None,
    marked_up: bool = True,
):
    """The itemised estimate for a managed pipeline, or ``None`` if unpriced.

    Split out from :func:`price_per_minute` because two screens want different
    depths of the same answer and pricing twice is how the two drift: the card
    needs one number, the breakdown bar beside it needs the lines that add up
    to that number. One estimate serves both.

    ``stt_tier`` and ``tts_tier`` are parameters rather than the constant
    ``"default"`` they used to be. A bundle names its own speech tiers, and
    hardcoding them here priced every bundle as though it ran the default pair
    — correct only for as long as every pipeline bundle happened to.
    """
    llm = managed_tiers.resolve("llm", brain)
    stt = managed_tiers.resolve("stt", stt_tier or "default")
    tts = managed_tiers.resolve("tts", tts_tier or "default")

    estimate = await estimate_cost_per_minute(
        session,
        organization_id=organization_id,
        stt_provider=stt.provider,
        stt_model=stt.model,
        llm_provider=llm.provider,
        llm_model=llm.model,
        tts_provider=tts.provider,
        tts_model=tts.model,
        telephony_provider=telephony_provider,
        marked_up=marked_up,
    )
    return (
        estimate if _priced(estimate, what=f"the {brain} brain") is not None else None
    )


async def price_per_minute(
    session: AsyncSession,
    *,
    organization_id: int | None,
    brain: str,
    stt_tier: str = "default",
    tts_tier: str = "default",
    telephony_provider: str | None = None,
    marked_up: bool = True,
) -> int | None:
    """Paise per minute for a managed stack on this brain tier.

    Priced against what the tier resolves to today, because that is what the
    call will actually cost. The voice does not vary the price — every managed
    voice is the same tier and the same vendor rate — so it is not a parameter.

    ``None`` when any component of the stack has no rate on file. See
    :func:`_priced`.
    """
    estimate = await pipeline_estimate(
        session,
        organization_id=organization_id,
        brain=brain,
        stt_tier=stt_tier,
        tts_tier=tts_tier,
        telephony_provider=telephony_provider,
        marked_up=marked_up,
    )
    return None if estimate is None else estimate.total_paise_per_minute


async def realtime_price_per_minute(
    session: AsyncSession,
    *,
    organization_id: int | None,
    realtime_tier: str,
    telephony_provider: str | None = None,
    marked_up: bool = True,
) -> int | None:
    """Paise per minute for a speech-to-speech stack.

    One model replaces the transcriber and the voice, so there is one vendor
    to price. It is passed as the *llm* slot because that is how a realtime
    session is metered — the vendor bills it as language-model usage and the
    rate card prices it there, which is also why the estimator's realtime
    token assumption lives under that component.

    The tier names the vendor the way ``service_factory`` needs it and the rate
    card names it the way the pipeline records it;
    ``estimator.rate_card_provider`` is what makes those the same lookup.

    ``None`` when the model has no rate on file. See :func:`_priced`.
    """
    estimate = await realtime_estimate(
        session,
        organization_id=organization_id,
        realtime_tier=realtime_tier,
        telephony_provider=telephony_provider,
        marked_up=marked_up,
    )
    return None if estimate is None else estimate.total_paise_per_minute


async def realtime_estimate(
    session: AsyncSession,
    *,
    organization_id: int | None,
    realtime_tier: str,
    telephony_provider: str | None = None,
    marked_up: bool = True,
):
    """The itemised estimate for a speech-to-speech stack, or ``None``.

    The realtime counterpart of :func:`pipeline_estimate`, and it exists for
    the same reason: the card and the breakdown beside it must be two views of
    one calculation rather than two calculations.
    """
    upstream = managed_tiers.resolve(managed_tiers.REALTIME_COMPONENT, realtime_tier)
    estimate = await estimate_cost_per_minute(
        session,
        organization_id=organization_id,
        llm_provider=upstream.provider,
        llm_model=upstream.model,
        telephony_provider=telephony_provider,
        marked_up=marked_up,
    )
    priced = _priced(estimate, what=f"the {realtime_tier} speech-to-speech tier")
    return estimate if priced is not None else None


#: The component lines a Simple-tab breakdown may name. Anything the estimator
#: itemises that is not in here is folded into "agent" rather than shown, so a
#: new line added to the estimator cannot leak a vendor onto this screen by
#: appearing on it unannounced.
_SIMPLE_LINE_LABELS: dict[str, str] = {
    "stt": "Transcription",
    "llm": "Brain",
    "tts": "Voice",
    "telephony": "Telephony",
    "platform": "Platform fee",
}


def _breakdown(estimate) -> dict | None:
    """What makes up a variant's price, with the vendors taken out.

    The Simple tab is the screen that deliberately does not name Sarvam or
    OpenAI — that is the whole reason it exists beside Advanced. So this
    carries the same figures the itemised bar on Advanced shows and none of the
    ``provider``/``model`` fields, because a breakdown that named them would
    undo the split by being the one place a vendor appears.

    Built from the estimate the card's own price came from, never a second
    call: a bar whose segments are priced separately from the headline above
    them is a bar that disagrees with it the first time a rate moves.
    """
    if estimate is None:
        return None
    lines = [
        {
            "component": line.component,
            "label": _SIMPLE_LINE_LABELS.get(line.component, "Agent"),
            "paise_per_minute": line.paise_per_minute,
        }
        for line in estimate.lines
    ]
    return {
        "agent_paise_per_minute": estimate.agent_paise_per_minute,
        "telephony_paise_per_minute": estimate.telephony_paise_per_minute,
        "platform_paise_per_minute": estimate.platform_paise_per_minute,
        "addon_paise_per_minute": estimate.addon_paise_per_minute,
        "pulse_seconds": estimate.pulse_seconds,
        "lines": lines,
    }


async def bundle_options(
    session: AsyncSession,
    *,
    organization_id: int | None,
    telephony_provider: str | None = None,
) -> list[dict]:
    """The Simple picker's cards, priced, with the residency badge on each.

    Every bundle carries **variants**, even when there is only one. A pipeline
    bundle has three — the brain is the customer's choice and it is the
    component that moves both the price and the badge — while a
    speech-to-speech bundle has exactly one. Making that uniform means the
    screen renders one shape instead of branching on architecture, and adding a
    fourth bundle later needs no new case.

    Priced through the same estimator the receipt reconciles against, never a
    second calculation. Every pricing bug found this week came from a parallel
    sum drifting from the first one.
    """
    from api.services.configuration import bundles as bundle_service
    from api.services.configuration.residency import assess

    rows = await bundle_service.list_bundles(session, enabled_only=True)
    out: list[dict] = []

    for row in rows:
        variants: list[dict] = []

        if row.architecture == bundle_service.REALTIME:
            estimate = await realtime_estimate(
                session,
                organization_id=organization_id,
                realtime_tier=row.realtime_tier,
                telephony_provider=telephony_provider,
            )
            variants.append(
                {
                    "tier": row.realtime_tier,
                    "label": row.label,
                    "blurb": "",
                    "paise_per_minute": (
                        None if estimate is None else estimate.total_paise_per_minute
                    ),
                    "breakdown": _breakdown(estimate),
                    "india_only": assess(
                        architecture="realtime", realtime_tier=row.realtime_tier
                    ).india_only,
                }
            )
        else:
            # ``llm_tier`` pinned on the row means the bundle chose the brain;
            # null means the customer does, which is the Everyday case.
            tiers = [row.llm_tier] if row.llm_tier else list(managed_tiers.LLM_TIERS)
            for tier in tiers:
                label, blurb = managed_tiers.LLM_TIER_LABELS.get(
                    tier, (tier.title(), "")
                )
                estimate = await pipeline_estimate(
                    session,
                    organization_id=organization_id,
                    brain=tier,
                    stt_tier=row.stt_tier or "default",
                    tts_tier=row.tts_tier or "default",
                    telephony_provider=telephony_provider,
                )
                variants.append(
                    {
                        "tier": tier,
                        "label": label,
                        "blurb": blurb,
                        "paise_per_minute": (
                            None
                            if estimate is None
                            else estimate.total_paise_per_minute
                        ),
                        "breakdown": _breakdown(estimate),
                        "india_only": assess(
                            architecture="pipeline",
                            llm_tier=tier,
                            stt_tier=row.stt_tier,
                            tts_tier=row.tts_tier,
                        ).india_only,
                    }
                )

        out.append(
            {
                "slug": row.slug,
                "label": row.label,
                "blurb": row.blurb,
                "architecture": row.architecture,
                # A voice is only chosen on the pipeline path: a
                # speech-to-speech model brings its own and there is nothing to
                # pick. The screen reads this rather than re-deriving it from
                # the architecture string.
                "picks_voice": row.architecture == bundle_service.PIPELINE,
                "variants": variants,
            }
        )
    return out


class SelectionError(ValueError):
    """A bundle choice that could not be saved as asked for."""


async def selected_bundle(*, organization_id: int | None) -> dict | None:
    """The Simple choice currently in force, or ``None`` if there is not one.

    Read from the account's stored managed configuration rather than kept in a
    second table. There is one answer to "what does this account run on" and it
    is the configuration the call itself resolves — a parallel record of the
    picker's last click is a record that goes stale the first time somebody
    saves from the Advanced tab.

    ``None`` when the account is on BYOK or has never saved: the Simple picker
    has nothing to restore, and it should show its defaults rather than claim a
    selection the account is not on.
    """
    from api.services.configuration.ai_model_configuration import (
        get_organization_ai_model_configuration_v2,
    )

    stored = await get_organization_ai_model_configuration_v2(organization_id)
    if stored is None or stored.mode != "decibyl" or stored.decibyl is None:
        return None
    managed = stored.decibyl
    realtime_tier = (managed.realtime_tier or "").strip()
    return {
        "bundle": managed.bundle or "",
        "tier": realtime_tier or managed.llm_tier,
        "voice": managed.voice,
    }


async def save_bundle_selection(
    session: AsyncSession,
    *,
    organization_id: int,
    bundle_slug: str,
    tier: str,
    voice: str,
) -> dict:
    """Store a Simple choice as this account's default managed stack.

    Everything the customer chose is resolved here from the bundle row rather
    than taken from the request: the client sends a slug, a tier and a voice,
    and the speech tiers behind them are looked up. A client that could name
    its own STT tier could name one nobody has priced, and the first anyone
    would know is a call billed against a rate that does not exist.

    Writes the same v2 managed shape the Advanced tab writes, so the two tabs
    remain two vocabularies for one stored answer rather than two stores.
    """
    from api.schemas.ai_model_configuration import (
        DecibylManagedAIModelConfiguration,
        OrganizationAIModelConfigurationV2,
        compile_ai_model_configuration_v2,
    )
    from api.services.configuration import bundles as bundle_service
    from api.services.configuration.ai_model_configuration import (
        get_organization_ai_model_configuration_v2,
        upsert_organization_ai_model_configuration_v2,
    )

    # The account's model gateway service key, carried forward rather than
    # rewritten. It is minted once per organization at signup and is the only
    # copy: nothing here can mint another, so writing a configuration without
    # it does not "clear a field", it destroys the credential.
    #
    # This is not hypothetical. Saving a bundle used to build a fresh managed
    # configuration and let ``api_key`` take its empty default, which passed
    # every validator — an empty key is the ordinary case for a managed slot —
    # and then refused every call the account made with "You have invalid keys
    # in your model configuration". The stack was right, the tiers were right,
    # and the credential the gateway authenticates with was gone.
    #
    # ``merge_ai_model_configuration_v2_secrets`` does not cover this. It
    # restores a key the client sent back *masked*; a key that is simply absent
    # reads as a deliberate empty value and is written as one.
    existing = await get_organization_ai_model_configuration_v2(organization_id)
    service_key = ""
    if existing is not None and existing.decibyl is not None:
        service_key = existing.decibyl.api_key or ""

    rows = await bundle_service.list_bundles(session, enabled_only=True)
    row = next((r for r in rows if r.slug == bundle_slug), None)
    if row is None:
        raise SelectionError(f"{bundle_slug!r} is not a bundle on offer.")

    chosen = (tier or "").strip()
    if row.architecture == bundle_service.REALTIME:
        # One model hears and speaks, so there is exactly one tier it can be
        # and the request does not get to name a different one.
        if chosen and chosen != row.realtime_tier:
            raise SelectionError(f"{row.label} does not offer a {chosen!r} option.")
        managed = DecibylManagedAIModelConfiguration(
            api_key=service_key,
            bundle=row.slug,
            realtime_tier=row.realtime_tier,
            # Carried so a later switch back to a pipeline bundle does not land
            # on a tier nobody chose. It is not read while realtime_tier is set.
            llm_tier="default",
            voice=voice or DECIBYL_DEFAULT_VOICE,
        )
    else:
        offered = [row.llm_tier] if row.llm_tier else list(managed_tiers.LLM_TIERS)
        if chosen not in offered:
            raise SelectionError(f"{row.label} does not offer a {chosen!r} brain.")
        if voice and voice not in {v.voice_id for v in voices()}:
            raise SelectionError(f"{voice!r} is not a voice we offer.")
        managed = DecibylManagedAIModelConfiguration(
            api_key=service_key,
            bundle=row.slug,
            llm_tier=chosen,
            stt_tier=row.stt_tier or "default",
            tts_tier=row.tts_tier or "default",
            voice=voice or DECIBYL_DEFAULT_VOICE,
        )

    configuration = OrganizationAIModelConfigurationV2(
        version=2, mode="decibyl", decibyl=managed
    )
    # Compiled before it is stored, not after. Everything above is built from a
    # bundle row an operator owns, so a combination that cannot be flattened
    # into a runnable stack is a misconfigured bundle — and the place to find
    # that out is here, as a refused save, rather than on the first call the
    # account makes.
    try:
        compile_ai_model_configuration_v2(configuration)
    except ValueError as exc:
        raise SelectionError(str(exc)) from exc

    await upsert_organization_ai_model_configuration_v2(organization_id, configuration)
    return {
        "bundle": managed.bundle,
        "tier": (managed.realtime_tier or "").strip() or managed.llm_tier,
        "voice": managed.voice,
    }


async def bundle_economics(
    session: AsyncSession, *, telephony_provider: str | None = None
) -> list[dict]:
    """The same bundles, with what each one earns.

    The operator's version of :func:`bundle_options`. It asks the estimator the
    same question twice — once with the managed markup and once without — so
    ``price`` is exactly the number quoted to a customer and ``cost`` is
    exactly the vendor bill behind it. The margin is their difference, not a
    third calculation: a margin computed independently is a margin that drifts,
    and the drift only shows up in a month-end reconciliation.

    Priced at the **list** rate, with no account attached. Pricing a bundle
    against whichever account happened to be at hand would quote one
    customer's negotiated contract as though it were everybody's.

    Returned per variant rather than per bundle because on a pipeline bundle
    the brain is the customer's choice, and Lite and Smart do not earn the
    same thing.
    """
    from api.services.configuration import bundles as bundle_service

    priced = await bundle_options(
        session, organization_id=None, telephony_provider=telephony_provider
    )
    # The speech tiers each bundle runs on, which the customer-facing payload
    # above deliberately does not carry. The cost side has to price the same
    # stack the price side did, or the margin is the difference between two
    # different bundles.
    speech = {
        row.slug: (row.stt_tier or "default", row.tts_tier or "default")
        for row in await bundle_service.list_bundles(session, enabled_only=True)
    }
    out: list[dict] = []

    for bundle in priced:
        variants: list[dict] = []
        for variant in bundle["variants"]:
            price = variant["paise_per_minute"]
            if bundle["architecture"] == "realtime":
                cost = await realtime_price_per_minute(
                    session,
                    organization_id=None,
                    realtime_tier=variant["tier"],
                    telephony_provider=telephony_provider,
                    marked_up=False,
                )
            else:
                stt_tier, tts_tier = speech.get(bundle["slug"], ("default", "default"))
                cost = await price_per_minute(
                    session,
                    organization_id=None,
                    brain=variant["tier"],
                    stt_tier=stt_tier,
                    tts_tier=tts_tier,
                    telephony_provider=telephony_provider,
                    marked_up=False,
                )
            # Either half missing means there is no margin to state. Both come
            # from the same estimator and the same rate card, so in practice
            # they are missing together — but subtracting a present cost from
            # an absent price is exactly how a bundle nobody can price would
            # have reported a margin anyway.
            priced = price is not None and cost is not None
            variants.append(
                {
                    **variant,
                    "cost_paise_per_minute": cost,
                    "margin_paise_per_minute": (price - cost) if priced else None,
                    # Expressed against the price rather than the cost, because
                    # that is the margin an investor and a discount both read.
                    # Null rather than zero when nothing is priced yet: a bundle
                    # with no rate card behind it has no margin, and 0% would
                    # read as one we chose.
                    "margin_pct": (
                        round((price - cost) / price * 100, 1)
                        if priced and price
                        else None
                    ),
                }
            )
        out.append({**bundle, "variants": variants})
    return out


def approximate_minutes(balance_paise: int, paise_per_minute: int | None) -> int | None:
    """How long a balance lasts, roughly.

    Returned as a number to *show*, not to bill against. It moves with the rate
    card and with what the agent actually says, so it is an estimate in the
    honest sense — which is why the caller is expected to render it with a
    "roughly" in front of it rather than as an entitlement.

    ``None`` when the stack cannot be priced: a zero would read as "this is
    free", which is the one thing it never means. So does a ``None`` rate,
    which is how :func:`price_per_minute` reports a component with no rate on
    file — quoting a balance as minutes at a price that is missing its largest
    line would multiply the error rather than surface it.
    """
    if paise_per_minute is None or paise_per_minute <= 0:
        return None
    return balance_paise // paise_per_minute


def managed_stack_override(
    *,
    voice: str,
    llm_tier: str,
    realtime_tier: str = "",
    stt_tier: str = "default",
    tts_tier: str = "default",
) -> dict:
    """A bundle choice, as an agent-level model override.

    Written as a v3 stack with every slot still saying ``decibyl``. That
    matters: a slot naming a tier is resolved to a vendor at call time by
    ``managed_resolution``, so this records *the product choice* rather than
    pinning a vendor model that would then not move when the tier does.

    ``realtime_tier`` selects the speech-to-speech shape instead of the
    cascade. The two are mutually exclusive by construction rather than by
    validation: one model that hears and speaks replaces the transcriber and
    the voice, so emitting both would describe an agent that cannot exist.

    Returns an empty dict when nothing was chosen, so a caller that did not ask
    — an API client posting the old three-field body — inherits the
    organization default exactly as it did before.
    """
    managed = ServiceProviders.DECIBYL.value
    realtime = (realtime_tier or "").strip()

    if realtime:
        # No stt or tts slot at all. A realtime section that also named a
        # transcriber would be two answers to one question, and the compiler
        # would have to pick one.
        stack: dict[str, object] = {
            "architecture": "realtime",
            "realtime": {"provider": managed, "model": realtime, "api_key": ""},
            # The v3 schema requires an llm section under both architectures —
            # a realtime model *is* the language model, and this is the slot
            # that records which tier serves it.
            "llm": {"provider": managed, "model": realtime, "api_key": ""},
        }
        return {
            WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY: {
                "version": 3,
                "stack": stack,
            }
        }

    if not (voice or "").strip() and not (llm_tier or "").strip():
        return {}

    stack = {
        "architecture": "pipeline",
        "llm": {
            "provider": managed,
            "model": (llm_tier or "default").strip(),
            "api_key": "",
        },
        "stt": {
            "provider": managed,
            "model": (stt_tier or "default").strip(),
            "api_key": "",
        },
        "tts": {
            "provider": managed,
            "model": (tts_tier or "default").strip(),
            "api_key": "",
            "voice": (voice or "").strip() or DECIBYL_DEFAULT_VOICE,
        },
    }
    return {
        WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY: {"version": 3, "stack": stack}
    }


async def catalogue_options(
    session: AsyncSession, *, organization_id: int | None
) -> dict[str, list[dict]]:
    """Every managed model a customer may choose, per slot, with its price.

    The answer to "what does Decibyl provide", assembled from the one place
    that says so — the catalogue — rather than from the registry, which lists
    every vendor this codebase has ever integrated including the ones we hold
    no key for and have never priced.

    ``paise_per_minute`` is that component's own contribution to a minute, with
    the managed markup already on it: what the customer pays us for choosing it.
    It is not the price of a call, which also has the other slots, telephony and
    the platform fee in it — the picker shows the difference between two models,
    and the cost bar beside it shows the total.

    Priced through ``estimator.price_components``, which is built from the same
    line functions a full estimate is, so a model priced on this screen and the
    same model inside a stack estimate cannot disagree.
    """
    from api.services.billing.estimator import price_components
    from api.services.configuration import model_catalogue

    out: dict[str, list[dict]] = {}
    for component in ("stt", "llm", "tts", "realtime"):
        entries = await model_catalogue.sellable(session, component=component)
        if not entries:
            out[component] = []
            continue

        priced = await price_components(
            session,
            organization_id=organization_id,
            slots=[(component, entry.provider, entry.model) for entry in entries],
        )
        options: list[dict] = []
        for entry in entries:
            line = priced.get((component, entry.provider, entry.model))
            options.append(
                {
                    "provider": entry.provider,
                    "model": entry.model,
                    "label": entry.label,
                    # Null rather than zero when the rate vanished between the
                    # sellable check and here. Zero would read as free, which is
                    # the one thing it never means.
                    "paise_per_minute": line.paise_per_minute if line else None,
                    # True when priced against the vendor's default rather than
                    # this model — two models then show the same number, which
                    # reads as a broken calculator unless it is said.
                    "approximate": bool(line and line.rate_is_provider_fallback),
                }
            )
        # Cheapest first. A picker ordered by what a vendor happens to return is
        # a picker nobody can compare.
        options.sort(
            key=lambda o: (o["paise_per_minute"] is None, o["paise_per_minute"])
        )
        out[component] = options
    return out
