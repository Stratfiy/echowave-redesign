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


#: The order components are folded into a vendor's purpose, and the reason this
#: is not left to the database.
#:
#: :func:`_merge` capitalises whichever purpose arrives first and lower-cases
#: every one after it, so the *text* of a published entry depends on the order
#: rows come back. Neither query below had an ``ORDER BY``, which means that
#: order was PostgreSQL's physical row order — stable on a quiet table and not
#: stable at all once rows churn. Sarvam serves both speech components, so one
#: deployment rendered "Speech recognition; speech synthesis" and the next
#: rendered "Speech synthesis; speech recognition" from identical data.
#:
#: That is a sub-processor list under GDPR Art 28(2), where a change to the list
#: is a thing a controller has to be told about. A diff that appears because
#: PostgreSQL chose a different scan order is a notification nobody can act on,
#: and it teaches the reader to ignore the ones that matter.
#:
#: The order is the pipeline's own — what the agent hears, thinks, says, and
#: what carries the call — because that is how every other surface in this
#: codebase describes a stack. Anything unrecognised sorts last, alphabetically,
#: rather than wherever it happened to be stored.
_COMPONENT_ORDER = ("stt", "llm", "tts", "telephony")


def _merge_order(row: tuple[str, str]) -> tuple[str, int, str]:
    """Sort key making a vendor's rendered purpose a function of the data."""
    provider, component = row
    try:
        rank = _COMPONENT_ORDER.index(component)
    except ValueError:
        rank = len(_COMPONENT_ORDER)
    return (provider, rank, component)


def _merge(found: dict[str, Subprocessor], name: str, component: str, basis: str):
    """Fold one (provider, component) pair into the list.

    One vendor can serve two components — Sarvam does both STT and TTS — and
    listing them twice reads as two companies. Merged into one entry naming both
    purposes, because the question being answered is "which companies hold my
    data", not "how many integrations are configured".
    """
    purpose, data = _purpose_for_component(component)
    existing = found.get(name)
    if existing is None:
        found[name] = Subprocessor(name=name, purpose=purpose, data=data, basis=basis)
        return
    if purpose in existing.purpose:
        return
    found[name] = Subprocessor(
        name=name,
        purpose=f"{existing.purpose}; {purpose[0].lower()}{purpose[1:]}",
        data=existing.data
        if data in existing.data
        else f"{existing.data}; {data.lower()}",
        # Configured outranks observed: a key is installed either way.
        basis="configured" if "configured" in (existing.basis, basis) else basis,
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

    found: dict[str, Subprocessor] = {}

    credentials = (
        await session.scalars(
            select(PlatformProviderCredentialModel).where(
                PlatformProviderCredentialModel.is_active.is_(True)
            )
        )
    ).all()
    # Sorted before folding, never as they arrive — see _COMPONENT_ORDER.
    for provider, component in sorted(
        ((c.provider, c.component) for c in credentials), key=_merge_order
    ):
        _merge(found, provider, component, "configured")

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
    for provider, component in sorted(
        ((p, c or "") for p, c in observed if p), key=_merge_order
    ):
        _merge(found, provider, component, "observed")

    return _with_infrastructure(found)


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

    found: dict[str, Subprocessor] = {}
    for provider, component in rows:
        if provider:
            _merge(found, provider, component or "", "observed")
    return _with_infrastructure(found)
