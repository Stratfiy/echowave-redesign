"""The activation funnel: who signed up, and how far they got.

Every other analytics surface in this codebase answers questions about calls
that happened. This one answers the question nobody could answer at all —
who arrived and never made one.

Two decisions shape the whole query:

**The cohort is fixed at signup, and each step is measured for that cohort
ever after.** A step counted "in the window" instead would produce a funnel
whose stages describe different people, where a wide window makes activation
look worse simply by admitting more recent signups who have not had time yet.
Ask "of the accounts that signed up in this window, how many ever created an
agent" — that question has a stable answer that improves as they activate.

**Steps are cumulative and monotonic by construction.** Each is derived from
the previous step's set, so a later stage can never exceed an earlier one. A
funnel that can go back up is a funnel nobody trusts, and the arithmetic that
allows it is usually a join fanning out rather than a real reversal.

**Email verification is reported beside the funnel, not inside it.** Nothing
gates on it — an unverified account can build an agent and run a call — so a
verified step placed mid-funnel would either under-report agent creation (by
chaining) or let the funnel rise (by not chaining). It is a real number and it
belongs on this screen; it is not a stage of this path.
"""

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import distinct, func, select

from api.db.base_client import BaseDBClient
from api.db.models import (
    CreditLedgerModel,
    OrganizationMembershipModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.enums import CreditLedgerKind

IST = ZoneInfo("Asia/Kolkata")


def _bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """Inclusive IST days as a half-open UTC interval.

    Half-open so a run at exactly midnight is counted once, in the day it
    starts, rather than in both adjacent days or neither.
    """
    return (
        datetime.combine(start, time.min, tzinfo=IST),
        datetime.combine(end + timedelta(days=1), time.min, tzinfo=IST),
    )


class ActivationClient(BaseDBClient):
    async def activation_funnel(self, start: date, end: date) -> dict[str, Any]:
        """Signup → verified → agent → first call → first top-up.

        Returns absolute counts plus the conversion from the previous step, so
        the UI never has to divide and cannot disagree with itself about which
        denominator a rate used.
        """
        start_utc, end_utc = _bounds(start, end)

        async with self.async_session() as session:
            # The cohort. Everything below is a subset of exactly these users.
            cohort_q = select(UserModel.id).where(
                UserModel.created_at >= start_utc,
                UserModel.created_at < end_utc,
            )
            cohort_ids = [row for row in (await session.execute(cohort_q)).scalars()]
            signed_up = len(cohort_ids)

            if signed_up == 0:
                return {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "steps": _with_rates(_empty_steps()),
                    "email_verified": 0,
                    "email_verified_rate": None,
                }

            verified = (
                await session.execute(
                    select(func.count(UserModel.id)).where(
                        UserModel.id.in_(cohort_ids),
                        UserModel.email_verified_at.isnot(None),
                    )
                )
            ).scalar_one()

            # Organizations these users belong to. An agent belongs to an org,
            # not a person, so the later steps are asked of the org and then
            # counted back to the user who signed up.
            org_rows = (
                await session.execute(
                    select(
                        OrganizationMembershipModel.user_id,
                        OrganizationMembershipModel.organization_id,
                    ).where(OrganizationMembershipModel.user_id.in_(cohort_ids))
                )
            ).all()
            orgs_by_user: dict[int, set[int]] = {}
            for user_id, org_id in org_rows:
                orgs_by_user.setdefault(user_id, set()).add(org_id)

            all_org_ids = {o for orgs in orgs_by_user.values() for o in orgs}
            if not all_org_ids:
                steps = _empty_steps()
                steps[0]["count"] = signed_up
                return {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "steps": _with_rates(steps),
                    "email_verified": verified,
                    "email_verified_rate": (
                        round(verified / signed_up * 100, 1) if signed_up else None
                    ),
                }

            def orgs_reaching(org_ids: set[int]) -> set[int]:
                return {
                    user_id for user_id, orgs in orgs_by_user.items() if orgs & org_ids
                }

            # Ever, not "in the window" — see the module docstring.
            with_agent_orgs = {
                row
                for row in (
                    await session.execute(
                        select(distinct(WorkflowModel.organization_id)).where(
                            WorkflowModel.organization_id.in_(all_org_ids)
                        )
                    )
                ).scalars()
            }
            with_agent = orgs_reaching(with_agent_orgs)

            # workflow_runs has no organization_id of its own; it reaches the
            # org through its workflow. Joining rather than assuming the column
            # exists is the difference between this query and a 500.
            with_call_orgs = {
                row
                for row in (
                    await session.execute(
                        select(distinct(WorkflowModel.organization_id))
                        .select_from(WorkflowRunModel)
                        .join(
                            WorkflowModel,
                            WorkflowModel.id == WorkflowRunModel.workflow_id,
                        )
                        .where(WorkflowModel.organization_id.in_(with_agent_orgs))
                    )
                ).scalars()
            }
            with_call = orgs_reaching(with_call_orgs) & with_agent

            # Filtered on kind, not on the sign of delta_paise. A positive row
            # can also be an ADJUSTMENT (staff granting credit) or TRIAL — and
            # neither is the customer paying us, which is the whole point of
            # this step. Sign alone would count a comped account as converted.
            with_topup_orgs = {
                row
                for row in (
                    await session.execute(
                        select(distinct(CreditLedgerModel.organization_id)).where(
                            CreditLedgerModel.organization_id.in_(all_org_ids),
                            CreditLedgerModel.kind == CreditLedgerKind.TOPUP.value,
                        )
                    )
                ).scalars()
            }
            with_topup = orgs_reaching(with_topup_orgs) & with_call

            steps = _empty_steps()
            steps[0]["count"] = signed_up
            steps[1]["count"] = len(with_agent)
            steps[2]["count"] = len(with_call)
            steps[3]["count"] = len(with_topup)

            return {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "steps": _with_rates(steps),
                "email_verified": verified,
                "email_verified_rate": (
                    round(verified / signed_up * 100, 1) if signed_up else None
                ),
            }


def _empty_steps() -> list[dict[str, Any]]:
    return [
        {
            "key": "signed_up",
            "label": "Signed up",
            "count": 0,
            "hint": "Accounts created in this window.",
        },
        {
            "key": "created_agent",
            "label": "Created an agent",
            "count": 0,
            "hint": "Built something, ever — not only in this window.",
        },
        {
            "key": "first_call",
            "label": "Ran a call",
            "count": 0,
            "hint": "The first moment the product did its job.",
        },
        {
            "key": "first_topup",
            "label": "Paid",
            "count": 0,
            "hint": "A real top-up — not a staff adjustment or trial credit.",
        },
    ]


def _with_rates(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Conversion from the previous step, and from the top.

    Computed here rather than in the UI so there is one definition of each
    rate. Two screens dividing for themselves is how a funnel starts
    disagreeing with itself about which denominator it used.
    """
    top = steps[0]["count"] or 0
    for index, step in enumerate(steps):
        previous = steps[index - 1]["count"] if index else step["count"]
        step["from_previous"] = (
            round(step["count"] / previous * 100, 1) if previous else None
        )
        step["from_top"] = round(step["count"] / top * 100, 1) if top else None
        step["dropped"] = (previous - step["count"]) if index else 0
    return steps
