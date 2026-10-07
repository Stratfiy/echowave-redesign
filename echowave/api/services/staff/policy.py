"""Flags, budgets and model policy, read side (screen 43).

Each row is a configuration with its current value and where that value
comes from (console row, environment, default). Changing one is a typed
command owned by the stream that owns it -- ``flag.set`` (versioned, with
rollback) and ``cost_stop.*`` are the ops stream's -- so this module only
reads, and says ``needs_setup`` where a value has not been decided.

Free billing and operational budgets are separate groups on purpose: free
mode makes every price zero, and that must never read as unlimited work.
The daily allowances still hold in free mode (the controls stream's quotas).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api import constants
from api.db.controls_models import QuotaAllowanceModel
from api.db.models import BudgetIncidentModel
from api.services import feature_admin, features


def _allowance_defaults() -> list[dict[str, Any]]:
    return [
        {
            "kind": "model_turns",
            "per_person_per_day": constants.OPERATIONAL_QUOTA_MODEL_TURNS,
            "setting": "OPERATIONAL_QUOTA_MODEL_TURNS",
        },
        {
            "kind": "voice_minutes",
            "per_person_per_day": constants.OPERATIONAL_QUOTA_VOICE_MINUTES,
            "setting": "OPERATIONAL_QUOTA_VOICE_MINUTES",
        },
        {
            "kind": "outbound_messages",
            "per_person_per_day": constants.OPERATIONAL_QUOTA_OUTBOUND_MESSAGES,
            "setting": "OPERATIONAL_QUOTA_OUTBOUND_MESSAGES",
        },
        {
            "kind": "browser_minutes",
            "per_person_per_day": constants.OPERATIONAL_QUOTA_BROWSER_MINUTES,
            "setting": "OPERATIONAL_QUOTA_BROWSER_MINUTES",
        },
    ]


async def report(session: AsyncSession) -> dict[str, Any]:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    live_grants = await session.scalar(
        select(func.count(QuotaAllowanceModel.id)).where(
            QuotaAllowanceModel.revoked_at.is_(None),
            QuotaAllowanceModel.expires_at > now,
        )
    )
    open_budget_incidents = await session.scalar(
        select(func.count(BudgetIncidentModel.id)).where(
            BudgetIncidentModel.status == "open"
        )
    )
    from api.services.configuration.chat_presets import CHAT_PRESETS
    from api.services.routing import brain

    quotas_on = features.is_on("operational_quotas")
    return {
        "flags": await feature_admin.registry(session),
        "billing": {
            "free_mode": features.is_on("free_mode"),
            "note": "Free mode sets prices to zero. It does not lift the operational limits below.",
        },
        "operational_budgets": {
            "state": "active" if quotas_on else "disabled_by_policy",
            "reason": None
            if quotas_on
            else "operational_quotas is off: nothing is counted or refused.",
            "per_person_daily": _allowance_defaults(),
            "live_grants": int(live_grants or 0),
            "pilot_total": {
                "state": "needs_setup"
                if constants.STAFF_PILOT_BUDGET_PAISE is None
                else "ok",
                "limit_paise": constants.STAFF_PILOT_BUDGET_PAISE,
                "setting": "STAFF_PILOT_BUDGET_PAISE",
            },
            "pilot_capacity": {
                "state": "needs_setup"
                if constants.STAFF_PILOT_USER_CAPACITY is None
                else "ok",
                "people": constants.STAFF_PILOT_USER_CAPACITY,
                "setting": "STAFF_PILOT_USER_CAPACITY",
            },
            "workspace_budget_incidents_open": int(open_budget_incidents or 0),
            "cost_stop": "Per-hour spend ceilings are the ops stream's cost stop (/admin/ops/cost-stop).",
            "applies": "A change to a daily allowance applies to the next request; running calls are not cut off.",
        },
        "model_policy": {
            "routing": constants.LAYA_ROUTING,
            "routing_setting": "LAYA_ROUTING",
            "kinds": [
                {
                    "kind": kind,
                    "description": text,
                    "preset": brain.PRESET_FOR[kind],
                    "tier": next(
                        (
                            p.llm_tier
                            for p in CHAT_PRESETS
                            if p.slug == brain.PRESET_FOR[kind]
                        ),
                        None,
                    ),
                }
                for kind, text in brain.KINDS.items()
            ],
            "calls": "A call keeps one model for its length: Everyday on Auto.",
            "fallback": "The rules decide whenever Laya is unsure, slow or down.",
            "applies": "Next message; a call in progress keeps its model.",
        },
        "change": {
            "flags": "flag.set and flag.rollback (ops command, versioned, owner approval).",
            "budgets": "Daily allowance defaults are environment settings; per-person raises use allowance.grant.",
            "model_policy": "LAYA_ROUTING is an environment setting; laya.rollback / laya.restore are ops commands.",
        },
    }
