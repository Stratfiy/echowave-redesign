"""The founder overview (screen 29; design "Build the founder attention
queue first").

Actionable exceptions first, each with a count and the one link that
reaches it; then three metrics with their definitions and samples; then two
evidence tables. Each panel says whether it was measured: a panel whose
source failed is ``unavailable`` with the reason, never empty, and a probe
that did not answer is never green.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import (
    AgentEventModel,
    AgentTaskModel,
    BudgetIncidentModel,
    OrganizationKycModel,
    WebhookDeliveryModel,
)
from api.db.staff_models import StaffCommandModel, StaffIncidentModel, StaffRefundModel
from api.enums import AgentEventKind
from api.services import features, system_status
from api.services.staff import analytics, operations


def _item(
    key: str,
    title: str,
    count: int | None,
    href: str,
    severity: str = "warning",
    **extra,
) -> dict:
    return {
        "key": key,
        "title": title,
        "count": count,
        "href": href,
        "severity": severity,
        **extra,
    }


async def attention(session: AsyncSession, *, now: datetime) -> list[dict[str, Any]]:
    day = now - timedelta(days=1)
    items: list[dict[str, Any]] = []

    failed = await session.scalar(
        select(func.count(AgentTaskModel.id)).where(
            AgentTaskModel.ledger_state.in_(("failed", "outcome_unknown")),
            func.coalesce(AgentTaskModel.finished_at, AgentTaskModel.created_at) >= day,
        )
    )
    items.append(
        _item(
            "failed_tasks",
            "Tasks failed or with an unknown outcome (24 h)",
            int(failed or 0),
            "/superadmin/operations?tab=jobs",
            "critical",
        )
    )

    stale_cards = await session.scalar(
        select(func.count(AgentEventModel.id)).where(
            AgentEventModel.kind == AgentEventKind.ACTION_PROPOSED.value,
            AgentEventModel.payload["state"].as_string() == "proposed",
            AgentEventModel.at < day,
            AgentEventModel.at >= now - timedelta(days=14),
        )
    )
    items.append(
        _item(
            "waiting_approvals",
            "Approvals waiting more than a day",
            int(stale_cards or 0),
            "/superadmin/operations?tab=jobs",
        )
    )

    unknown_cards = await session.scalar(
        select(func.count(AgentEventModel.id)).where(
            AgentEventModel.kind == AgentEventKind.ACTION_PROPOSED.value,
            AgentEventModel.payload["state"].as_string() == "outcome_unknown",
            AgentEventModel.at >= now - timedelta(days=14),
        )
    )
    items.append(
        _item(
            "unknown_sends",
            "Sends with an unknown outcome (14 days)",
            int(unknown_cards or 0),
            "/superadmin/operations?tab=jobs",
            "critical",
        )
    )

    dead = await session.scalar(
        select(func.count(WebhookDeliveryModel.id)).where(
            WebhookDeliveryModel.status == "dead_letter",
            WebhookDeliveryModel.created_at >= day,
        )
    )
    items.append(
        _item(
            "dead_letters",
            "Deliveries given up on (24 h)",
            int(dead or 0),
            "/superadmin/operations?tab=delivery",
        )
    )

    budgets = await session.scalar(
        select(func.count(BudgetIncidentModel.id)).where(
            BudgetIncidentModel.status == "open"
        )
    )
    items.append(
        _item(
            "spend_exceptions",
            "Workspace budget alerts open",
            int(budgets or 0),
            "/superadmin/controls/policy",
        )
    )

    kyc = await session.scalar(
        select(func.count(OrganizationKycModel.id)).where(
            OrganizationKycModel.status == "submitted"
        )
    )
    items.append(
        _item(
            "kyc_waiting",
            "KYC submissions waiting for review",
            int(kyc or 0),
            "/superadmin/verification",
            "info",
        )
    )

    waiting = await session.scalar(
        select(func.count(StaffCommandModel.id)).where(
            StaffCommandModel.state == "awaiting_approval"
        )
    )
    items.append(
        _item(
            "staff_approvals",
            "Staff commands waiting for a second person",
            int(waiting or 0),
            "/superadmin/overview#commands",
        )
    )

    unknown_cmds = await session.scalar(
        select(func.count(StaffCommandModel.id)).where(
            StaffCommandModel.state == "outcome_unknown"
        )
    )
    items.append(
        _item(
            "unknown_commands",
            "Staff commands with an unknown outcome",
            int(unknown_cmds or 0),
            "/superadmin/overview#commands",
            "critical",
        )
    )

    refunds = await session.scalar(
        select(func.count(StaffRefundModel.id)).where(
            StaffRefundModel.state.in_(("pending", "outcome_unknown")),
            StaffRefundModel.created_at < day,
        )
    )
    items.append(
        _item(
            "refunds_stuck",
            "Refunds not reconciled after a day",
            int(refunds or 0),
            "/superadmin/revenue/ledger",
        )
    )

    if features.is_on("staff_incidents"):
        incidents = await session.scalar(
            select(func.count(StaffIncidentModel.id)).where(
                StaffIncidentModel.state != "resolved"
            )
        )
        items.append(
            _item(
                "open_incidents",
                "Open incidents",
                int(incidents or 0),
                "/superadmin/operations/incidents",
                "critical",
            )
        )
    else:
        items.append(
            _item(
                "open_incidents",
                "Open incidents",
                None,
                "/superadmin/operations/incidents",
                "info",
                state="disabled_by_policy",
                reason="staff_incidents is off",
            )
        )

    # Support escalations: cases past their first-response target.
    if features.is_on("support_inbox"):
        from api.services.support import tickets

        items.append(
            _item(
                "support_escalations",
                "Support cases past their first-response target",
                await tickets.overdue_count(session, now),
                "/superadmin/support",
            )
        )
    else:
        items.append(
            _item(
                "support_escalations",
                "Support escalations",
                None,
                "/superadmin/support",
                "info",
                state="disabled_by_policy",
                reason="support_inbox is off",
            )
        )
    return items


async def snapshot(session: AsyncSession) -> dict[str, Any]:
    now = datetime.now(UTC)
    out: dict[str, Any] = {"observed_at": now.isoformat()}
    try:
        out["attention"] = await attention(session, now=now)
        out["attention_state"] = "ok"
    except Exception as exc:  # noqa: BLE001 -- shown as unavailable, never empty
        logger.exception("Overview attention failed")
        await session.rollback()
        out["attention"] = []
        out["attention_state"] = "unavailable"
        out["attention_reason"] = type(exc).__name__

    try:
        out["health"] = operations.health_from_probes(await system_status.snapshot())
    except Exception as exc:  # noqa: BLE001
        out["health"] = {
            "state": "unknown",
            "signals": [],
            "reason": type(exc).__name__,
        }

    week = now - timedelta(days=7)
    try:
        outcomes = await analytics.useful_outcomes(session, since=week, until=now)
        success = await analytics.task_success(session, start=week, end=now)
        useful = await analytics.usefulness(session, start=week, end=now)
        out["metrics"] = {
            "state": "ok",
            "period": "Last 7 days",
            "weekly_useful_users": {
                "value": len({o.user_id for o in outcomes}),
                "sample": len(outcomes),
                "definition": analytics.DEFINITIONS["weekly_useful_users"],
            },
            "task_success": {
                "value": success["rate"],
                "sample": success["eligible"],
                "definition": analytics.DEFINITIONS["task_success"],
            },
            "usefulness": {
                "value": useful["rate"],
                "sample": useful["answered"],
                "definition": analytics.DEFINITIONS["usefulness"],
            },
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Overview metrics failed")
        await session.rollback()
        out["metrics"] = {"state": "unavailable", "reason": type(exc).__name__}

    try:
        out["recent_failures"] = (await operations.jobs(session, hours=72, limit=10))[
            "failing"
        ]
        rows = (
            (
                await session.execute(
                    select(StaffCommandModel)
                    .order_by(StaffCommandModel.id.desc())
                    .limit(10)
                )
            )
            .scalars()
            .all()
        )
        out["recent_commands"] = [
            {
                "id": r.id,
                "command": r.command,
                "state": r.state,
                "requested_by": r.requested_by,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
        out["evidence_state"] = "ok"
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        out["evidence_state"] = "unavailable"
        out["evidence_reason"] = type(exc).__name__
    return out
