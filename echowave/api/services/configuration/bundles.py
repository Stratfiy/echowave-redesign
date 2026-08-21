"""The Simple picker's cards: what is offered, and what each one runs on.

"Everyday", "Natural", "Premium" — one card, one price, one choice. The buyer
this is for does not know Sarvam from OpenAI and should not have to learn in
order to answer a phone.

**A bundle references tiers, not vendors**, and that indirection carries the
whole design. A customer's stored configuration names a tier, so moving a
vendor is one edit that reaches every agent already built. If a bundle named a
provider and model directly, changing one would leave every existing agent on
the old vendor and every new one on the new — precisely the drift tiers exist
to prevent. So the two edits are different jobs:

* **what a bundle runs on** — a tier edit, in ``tier_admin``.
* **what it is called, whether it is offered, in what order** — a bundle edit,
  here.

The operator screen shows both together and writes to whichever is right, so
from one place you change the vendor and the name; underneath, each lands where
it belongs.

Seeded on first read rather than by migration. A deployment that has never
opened the screen still has something to offer, and the compiled defaults stay
the single description of what ships rather than being duplicated into a
migration that then drifts from them.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import ManagedBundleModel
from api.services.configuration import managed_tiers

PIPELINE = "pipeline"
REALTIME = "realtime"


class BundleError(ValueError):
    """A bundle that could not be offered as described."""


@dataclass(frozen=True)
class BundleSeed:
    slug: str
    label: str
    blurb: str
    architecture: str
    stt_tier: str | None = None
    tts_tier: str | None = None
    llm_tier: str | None = None
    realtime_tier: str | None = None
    display_order: int = 0


#: What ships. Three cards, and the names describe **how the agent responds**
#: rather than how good it is — a quality ladder would imply Everyday is the
#: worst option, when on Indian languages it is the best one we have.
#:
#: ``llm_tier`` is deliberately null on Everyday: the customer picks the brain,
#: which is what makes it three variants of one card rather than three cards.
_SEEDS = (
    BundleSeed(
        slug="everyday",
        label="Everyday",
        blurb="Best on Indian languages, and the cheapest to run.",
        architecture=PIPELINE,
        stt_tier="default",
        tts_tier="default",
        llm_tier=None,
        display_order=10,
    ),
    BundleSeed(
        slug="natural",
        label="Natural",
        blurb="Replies the instant you stop talking.",
        architecture=REALTIME,
        realtime_tier="natural",
        display_order=20,
    ),
    BundleSeed(
        slug="premium",
        label="Premium",
        blurb="The most capable speech model. Noticeably dearer a minute.",
        architecture=REALTIME,
        realtime_tier="premium",
        display_order=30,
    ),
)


def _valid_tier(component: str, tier: str | None) -> bool:
    if tier is None:
        return True
    known = {
        "stt": managed_tiers.STT_TIERS,
        "tts": managed_tiers.TTS_TIERS,
        "llm": managed_tiers.LLM_TIERS,
        managed_tiers.REALTIME_COMPONENT: managed_tiers.REALTIME_TIERS,
    }.get(component, ())
    return tier in known


def _validate(row: ManagedBundleModel) -> None:
    """Refuse a bundle that would not compile into a working agent."""
    if row.architecture not in (PIPELINE, REALTIME):
        raise BundleError(
            f"{row.architecture!r} is not an architecture. Use "
            f"{PIPELINE!r} or {REALTIME!r}."
        )
    if not (row.slug or "").strip():
        raise BundleError("A bundle needs a slug — it is what an agent records.")
    if not (row.label or "").strip():
        raise BundleError("A bundle needs a name customers can read.")

    if row.architecture == PIPELINE:
        if not row.stt_tier or not row.tts_tier:
            raise BundleError(
                "A pipeline bundle needs a transcriber tier and a voice tier."
            )
        if row.realtime_tier:
            raise BundleError(
                "A pipeline bundle has no speech-to-speech model. Clear it, or "
                "make the bundle realtime."
            )
    else:
        if not row.realtime_tier:
            raise BundleError("A speech-to-speech bundle needs a realtime tier.")
        if row.stt_tier or row.tts_tier or row.llm_tier:
            raise BundleError(
                "A speech-to-speech bundle uses one model for the whole "
                "conversation. Clear the transcriber, voice and brain tiers."
            )

    for component, tier in (
        ("stt", row.stt_tier),
        ("tts", row.tts_tier),
        ("llm", row.llm_tier),
        (managed_tiers.REALTIME_COMPONENT, row.realtime_tier),
    ):
        if not _valid_tier(component, tier):
            raise BundleError(f"{tier!r} is not a {component} tier.")


async def ensure_seeded(session: AsyncSession) -> None:
    """Put the shipped bundles in the table if it is empty.

    Only when empty. An operator who deleted a bundle on purpose must not have
    it reappear on the next request, which is what a per-slug upsert would do.
    """
    existing = await session.scalar(select(ManagedBundleModel.id).limit(1))
    if existing is not None:
        return

    for seed in _SEEDS:
        session.add(ManagedBundleModel(**vars(seed), is_enabled=True))
    try:
        await session.flush()
    except IntegrityError:
        # Two workers seeded at once. Whoever won is right; there is nothing to
        # merge because both wrote the same rows.
        await session.rollback()


async def list_bundles(
    session: AsyncSession, *, enabled_only: bool = False
) -> list[ManagedBundleModel]:
    await ensure_seeded(session)
    query = select(ManagedBundleModel).order_by(
        ManagedBundleModel.display_order, ManagedBundleModel.id
    )
    if enabled_only:
        query = query.where(ManagedBundleModel.is_enabled.is_(True))
    return list((await session.scalars(query)).all())


def residency(row: ManagedBundleModel, *, llm_tier: str | None = None) -> dict:
    """Where this bundle processes speech and language.

    ``llm_tier`` is passed in for pipeline bundles because the brain is the
    customer's choice, not the bundle's — and it is the component that decides
    the answer: Lite is Sarvam, Normal and Smart are not.
    """
    from api.services.configuration.residency import assess

    result = assess(
        architecture=row.architecture,
        llm_tier=llm_tier or row.llm_tier,
        stt_tier=row.stt_tier,
        tts_tier=row.tts_tier,
        realtime_tier=row.realtime_tier,
    )
    return {
        "india_only": result.india_only,
        "leaves_india": list(result.leaves_india),
        "reason": result.reason,
    }


def resolved_slots(row: ManagedBundleModel) -> list[dict]:
    """What this bundle actually runs on today, tier by tier.

    The operator screen's whole reason for existing: a bundle names tiers, and
    a tier names a vendor somewhere else, so without this you would have to
    hold two screens in your head to answer "what does Natural use?".
    """
    slots: list[dict] = []
    pairs = (
        ("stt", row.stt_tier),
        ("llm", row.llm_tier),
        ("tts", row.tts_tier),
        (managed_tiers.REALTIME_COMPONENT, row.realtime_tier),
    )
    for component, tier in pairs:
        if tier is None:
            if component == "llm" and row.architecture == PIPELINE:
                # Not missing — chosen per agent. Said explicitly so the screen
                # can show "the customer picks" rather than an empty cell that
                # reads as a misconfiguration.
                slots.append(
                    {
                        "component": component,
                        "tier": None,
                        "provider": None,
                        "model": None,
                        "chosen_by_customer": True,
                    }
                )
            continue
        upstream = managed_tiers.resolve(component, tier)
        slots.append(
            {
                "component": component,
                "tier": tier,
                "provider": upstream.provider,
                "model": upstream.model,
                "chosen_by_customer": False,
            }
        )
    return slots


async def upsert_bundle(
    session: AsyncSession, *, slug: str, actor_user_id: int | None, **fields
) -> ManagedBundleModel:
    """Create or update one bundle. Validated before it is written."""
    await ensure_seeded(session)
    slug = (slug or "").strip().lower()

    row = await session.scalar(
        select(ManagedBundleModel).where(ManagedBundleModel.slug == slug)
    )
    if row is None:
        row = ManagedBundleModel(slug=slug)
        session.add(row)

    # Absence means "leave it"; an explicit ``None`` means "clear it". The
    # difference matters: switching a bundle from pipeline to speech-to-speech
    # requires clearing the transcriber and voice tiers, and a setter that
    # skipped every null would make that change impossible to express — while
    # the validation below insisted on it.
    for key, value in fields.items():
        if hasattr(row, key):
            setattr(row, key, value)
    row.slug = slug
    row.updated_by = actor_user_id

    _validate(row)
    await session.flush()
    return row
