"""The measured replay (OP-6): what a run of an agent actually cost.

The Credits Plan's per-lead figures are illustrative until a real run
replaces them. This reads that run off the rows the platform already
keeps -- the ledger, the vendor pass-through, the cost items, the
Prospects list, the cards -- and hands back the numbers the plan needs:
credits charged and vendor cost, per run and per lead, with the margin,
and what the run did (prospects found, emails proposed, confirmed,
declined). Nothing here writes, estimates or assumes; a figure with no
rows behind it is zero and said to be.

Used by ``scripts/replay_prospecting.py`` against a real account, and
by the tests against fixture rows, so the two read the same way.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import (
    AgentEventModel,
    CallCostItemModel,
    ContactListModel,
    ContactModel,
    CreditLedgerModel,
    DataLookupCostModel,
    WorkflowRunModel,
)
from api.enums import AgentEventKind, CreditLedgerKind
from api.services.billing.credits import PAISE_PER_CREDIT


@dataclass
class Replay:
    workflow_id: int
    start: datetime
    end: datetime
    runs: int = 0
    #: Credits charged, by what they were for (the ledger's ref_type).
    charged_paise_by_kind: dict[str, int] = field(default_factory=dict)
    #: The vendor's side: pass-through lookups and the runs' cost items.
    vendor_paise_by_kind: dict[str, int] = field(default_factory=dict)
    lookups: int = 0
    prospects_found: int = 0
    emails_proposed: int = 0
    emails_confirmed: int = 0
    emails_declined: int = 0

    @property
    def charged_paise(self) -> int:
        return sum(self.charged_paise_by_kind.values())

    @property
    def vendor_paise(self) -> int:
        return sum(self.vendor_paise_by_kind.values())

    @property
    def credits(self) -> float:
        return round(self.charged_paise / PAISE_PER_CREDIT, 2)

    @property
    def margin_paise(self) -> int:
        return self.charged_paise - self.vendor_paise

    def per_lead(self, paise: int) -> float | None:
        return round(paise / self.prospects_found, 1) if self.prospects_found else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "window": [self.start.isoformat(), self.end.isoformat()],
            "runs": self.runs,
            "prospects_found": self.prospects_found,
            "emails": {
                "proposed": self.emails_proposed,
                "confirmed": self.emails_confirmed,
                "declined": self.emails_declined,
            },
            "charged": {
                "paise": self.charged_paise,
                "credits": self.credits,
                "by_kind": dict(sorted(self.charged_paise_by_kind.items())),
                "per_lead_credits": (
                    round(
                        self.charged_paise / PAISE_PER_CREDIT / self.prospects_found, 2
                    )
                    if self.prospects_found
                    else None
                ),
            },
            "vendor": {
                "paise": self.vendor_paise,
                "by_kind": dict(sorted(self.vendor_paise_by_kind.items())),
                "lookups": self.lookups,
                "per_lead_paise": self.per_lead(self.vendor_paise),
            },
            "margin": {
                "paise": self.margin_paise,
                "share": (
                    round(self.margin_paise / self.charged_paise, 3)
                    if self.charged_paise
                    else None
                ),
            },
        }

    def as_markdown(self) -> str:
        """The block the Credits Plan takes, as a table."""
        d = self.as_dict()
        rows = [
            ("Runs", d["runs"]),
            ("Prospects found", d["prospects_found"]),
            (
                "Emails proposed / confirmed / declined",
                f"{self.emails_proposed} / {self.emails_confirmed} / {self.emails_declined}",
            ),
            ("Credits charged", d["charged"]["credits"]),
            ("Credits per lead", d["charged"]["per_lead_credits"]),
            ("Vendor cost (₹)", round(self.vendor_paise / 100, 2)),
            (
                "Vendor cost per lead (₹)",
                round((d["vendor"]["per_lead_paise"] or 0) / 100, 2)
                if self.prospects_found
                else None,
            ),
            ("Margin share", d["margin"]["share"]),
        ]
        lines = ["| Measure | Value |", "|---|---|"]
        lines += [f"| {k} | {'—' if v is None else v} |" for k, v in rows]
        lines.append("")
        lines.append("Charged by kind (credits):")
        for kind, paise in d["charged"]["by_kind"].items():
            lines.append(f"- {kind}: {round(paise / PAISE_PER_CREDIT, 2)}")
        lines.append("Vendor by kind (₹):")
        for kind, paise in d["vendor"]["by_kind"].items():
            lines.append(f"- {kind}: {round(paise / 100, 2)}")
        return "\n".join(lines)


async def measure(
    session: AsyncSession,
    *,
    organization_id: int,
    workflow_id: int,
    start: datetime,
    end: datetime,
) -> Replay:
    """Read one agent's window. Every figure is a sum over rows; none is
    derived from another."""
    out = Replay(workflow_id=workflow_id, start=start, end=end)

    run_ids = list(
        (
            await session.scalars(
                select(WorkflowRunModel.id).where(
                    WorkflowRunModel.workflow_id == workflow_id,
                    WorkflowRunModel.created_at >= start,
                    WorkflowRunModel.created_at < end,
                )
            )
        ).all()
    )
    out.runs = len(run_ids)

    charged = await session.execute(
        select(
            func.coalesce(CreditLedgerModel.ref_type, "other"),
            func.coalesce(func.sum(-CreditLedgerModel.delta_paise), 0),
        )
        .where(
            CreditLedgerModel.organization_id == organization_id,
            CreditLedgerModel.workflow_id == workflow_id,
            CreditLedgerModel.kind == CreditLedgerKind.USAGE.value,
            CreditLedgerModel.created_at >= start,
            CreditLedgerModel.created_at < end,
        )
        .group_by(CreditLedgerModel.ref_type)
    )
    for kind, paise in charged.all():
        if int(paise):
            out.charged_paise_by_kind[str(kind)] = int(paise)

    lookups = await session.execute(
        select(
            DataLookupCostModel.provider,
            DataLookupCostModel.kind,
            func.coalesce(func.sum(DataLookupCostModel.vendor_cost_paise), 0),
            func.coalesce(func.sum(DataLookupCostModel.requests), 0),
        )
        .where(
            DataLookupCostModel.organization_id == organization_id,
            DataLookupCostModel.workflow_id == workflow_id,
            DataLookupCostModel.created_at >= start,
            DataLookupCostModel.created_at < end,
        )
        .group_by(DataLookupCostModel.provider, DataLookupCostModel.kind)
    )
    for provider, kind, paise, requests in lookups.all():
        label = f"{provider} {kind}".strip()
        out.vendor_paise_by_kind[label] = out.vendor_paise_by_kind.get(label, 0) + int(
            paise
        )
        out.lookups += int(requests)

    if run_ids:
        items = await session.execute(
            select(
                CallCostItemModel.component,
                func.coalesce(func.sum(CallCostItemModel.provider_cost_paise), 0),
            )
            .where(CallCostItemModel.workflow_run_id.in_(run_ids))
            .group_by(CallCostItemModel.component)
        )
        for component, paise in items.all():
            if int(paise):
                out.vendor_paise_by_kind[str(component)] = out.vendor_paise_by_kind.get(
                    str(component), 0
                ) + int(paise)

    out.prospects_found = int(
        await session.scalar(
            select(func.count(ContactModel.id))
            .join(ContactListModel, ContactListModel.id == ContactModel.contact_list_id)
            .where(
                ContactModel.organization_id == organization_id,
                func.lower(ContactListModel.name) == "prospects",
                ContactModel.created_at >= start,
                ContactModel.created_at < end,
            )
        )
        or 0
    )

    cards = (
        await session.scalars(
            select(AgentEventModel).where(
                AgentEventModel.organization_id == organization_id,
                AgentEventModel.workflow_id == workflow_id,
                AgentEventModel.kind == AgentEventKind.ACTION_PROPOSED.value,
                AgentEventModel.at >= start,
                AgentEventModel.at < end,
            )
        )
    ).all()
    for card in cards:
        payload = card.payload if isinstance(card.payload, dict) else {}
        if payload.get("action") != "run_tool":
            continue
        out.emails_proposed += 1
        state = payload.get("state")
        if state == "done":
            out.emails_confirmed += 1
        elif state == "declined":
            out.emails_declined += 1
    return out


__all__ = ["Replay", "measure"]
