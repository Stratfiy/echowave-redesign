"""Who else processes the data, derived from what the platform actually uses.

GDPR Art 28(2) requires a controller to know every sub-processor and to be told
when the list changes; DPDP s11(1)(c) gives a Data Principal the right to know
who their data was shared with. Both are usually answered with a hand-maintained
page, which goes stale the first time somebody adds a provider and forgets — and
a stale sub-processor list is worse than none, because it is a specific written
claim that is now false.

So this is derived. Two sources, and they answer different questions:

* **Potential** — providers the platform can route to at all, read from the
  telephony registry and the model provider registry.
* **In use** — providers this deployment holds keys for, or has actually billed
  a call against. That is the honest answer to "who has my data", because a
  provider nobody ever routed to has processed nothing.

Neither can drift from reality without the code changing too.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import CallCostItemModel, PlatformProviderCredentialModel

#: Infrastructure every deployment uses regardless of which model vendors are
#: configured. Declared rather than derived because nothing in the application
#: code enumerates its own hosting.
INFRASTRUCTURE = (
    {
        "name": "Amazon Web Services",
        "purpose": "Application hosting, database and object storage",
        "data": "All customer and call data at rest",
    },
    {
        "name": "Razorpay",
        "purpose": "Payment processing",
        "data": "Billing contact and payment metadata. Card details never reach us.",
    },
)

#: How far back "in use" looks. A provider not touched in this window is not
#: currently processing anything, whatever the configuration says.
IN_USE_WINDOW_DAYS = 90


@dataclass(frozen=True)
class Subprocessor:
    name: str
    purpose: str
    data: str
    #: configured | observed | infrastructure
    basis: str


def _purpose_for_component(component: str) -> tuple[str, str]:
    return {
        "stt": ("Speech recognition", "Call audio"),
        "llm": ("Language model inference", "Call transcript and context"),
        "tts": ("Speech synthesis", "Text the agent speaks"),
        "telephony": ("Call carriage", "Phone numbers and call audio"),
    }.get(component, ("Processing", "Call data"))


#: Receipt order — the order a call actually flows through, stt to tts — and
#: not incidentally the order ``_render`` below sentences a merged purpose in.
#: Fixed so the sentence is fixed: two components merged in the opposite order
#: must still read the same way.
_COMPONENT_ORDER: tuple[str, ...] = ("stt", "llm", "tts", "telephony")


def _component_sort_key(component: str) -> int:
    try:
        return _COMPONENT_ORDER.index(component)
    except ValueError:
        return len(_COMPONENT_ORDER)


def _merge(found: dict[str, dict], name: str, component: str, basis: str) -> None:
    """Record one (provider, component) pair against the vendor.

    One vendor can serve two components — Sarvam does both STT and TTS — and
    listing them twice reads as two companies. Accumulates the *components*
    seen for this provider rather than a rendered sentence: ``in_use`` below
    reads ``PlatformProviderCredentialModel`` with no ``ORDER BY``, so which
    (provider, component) pair reaches this function first is not something
    the database promises — and a sentence built incrementally, capitalising
    whichever purpose happened to arrive first, changed wording between
    requests depending on scan order alone. ``_render`` turns the accumulated
    set into a sentence exactly once, in a fixed order, so the wording is
    fixed too.
    """
    entry = found.setdefault(name, {"components": set(), "basis": basis})
    entry["components"].add(component)
    # Configured outranks observed: a key is installed either way.
    if basis == "configured":
        entry["basis"] = "configured"


def _render(name: str, entry: dict) -> Subprocessor:
    """One vendor's accumulated components, sentenced in a fixed order."""
    components = sorted(entry["components"], key=_component_sort_key)
    pairs = [_purpose_for_component(c) for c in components]
    purposes = [purpose for purpose, _ in pairs]
    datas: list[str] = []
    for _, data in pairs:
        if data not in datas:
            datas.append(data)
    return Subprocessor(
        name=name,
        purpose="; ".join(
            p if i == 0 else f"{p[0].lower()}{p[1:]}" for i, p in enumerate(purposes)
        ),
        data="; ".join(
            d if i == 0 else d[0].lower() + d[1:] for i, d in enumerate(datas)
        ),
        basis=entry["basis"],
    )


async def in_use(
    session: AsyncSession, *, now: datetime | None = None
) -> list[Subprocessor]:
    """Sub-processors this deployment genuinely uses.

    Configured platform keys plus anything billed in the recent window. A
    provider a customer brought their own key for still appears — their data
    still went there, and the question is about the data, not about whose
    account paid for it.
    """
    now = now or datetime.now(UTC)
    since = now - timedelta(days=IN_USE_WINDOW_DAYS)

    found: dict[str, dict] = {}

    credentials = (
        await session.scalars(
            select(PlatformProviderCredentialModel).where(
                PlatformProviderCredentialModel.is_active.is_(True)
            )
        )
    ).all()
    for credential in credentials:
        _merge(found, credential.provider, credential.component, "configured")

    observed = (
        await session.execute(
            select(CallCostItemModel.provider, CallCostItemModel.component)
            .where(
                CallCostItemModel.provider.is_not(None),
                CallCostItemModel.created_at >= since,
            )
            .distinct()
        )
    ).all()
    for provider, component in observed:
        if provider:
            _merge(found, provider, component or "", "observed")

    return _with_infrastructure(
        {name: _render(name, entry) for name, entry in found.items()}
    )


def _with_infrastructure(found: dict[str, Subprocessor]) -> list[Subprocessor]:
    """Providers first, hosting last. Both are sub-processors; only one of them
    is a choice anyone made per deployment."""
    listed = sorted(found.values(), key=lambda s: s.name.lower())
    listed.extend(
        Subprocessor(
            name=item["name"],
            purpose=item["purpose"],
            data=item["data"],
            basis="infrastructure",
        )
        for item in INFRASTRUCTURE
    )
    return listed


async def for_organization(
    session: AsyncSession, *, organization_id: int, limit_days: int = 365
) -> list[Subprocessor]:
    """Sub-processors that handled *this account's* calls.

    The specific answer to "who was my data shared with", read from the priced
    line items of that account's own calls rather than from a platform-wide
    list. A customer who only ever used one model vendor should not be told
    their data went to five.
    """
    from api.db.models import WorkflowModel, WorkflowRunModel

    since = datetime.now(UTC) - timedelta(days=limit_days)

    rows = (
        await session.execute(
            select(CallCostItemModel.provider, CallCostItemModel.component)
            .join(
                WorkflowRunModel,
                CallCostItemModel.workflow_run_id == WorkflowRunModel.id,
            )
            .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
            .where(
                WorkflowModel.organization_id == organization_id,
                WorkflowRunModel.created_at >= since,
                CallCostItemModel.provider.is_not(None),
            )
            .distinct()
        )
    ).all()

    found: dict[str, dict] = {}
    for provider, component in rows:
        if provider:
            _merge(found, provider, component or "", "observed")
    return _with_infrastructure(
        {name: _render(name, entry) for name, entry in found.items()}
    )
