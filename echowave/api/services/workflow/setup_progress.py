"""How far this agent is from taking real calls.

A new account meets a canvas and no sense of what "finished" means. The two
places people actually stop are both invisible from inside the editor: an agent
that has never been called, and an agent nobody can call because it is on no
number. Both look exactly like an agent that is ready.

Every step here is a database fact, not a wizard's memory of what somebody
clicked. That distinction is the whole design:

* a rail driven by clicks marks "tested" for someone who opened the test panel
  and closed it, and stays marked after the agent is rewritten;
* a rail driven by state is right when a colleague did the step, right after a
  page reload, right when the number is detached again, and cannot congratulate
  anyone for work that did not happen.

It is deliberately short. Voice and knowledge are absent because neither has an
incomplete state — every agent has a voice, and plenty of good agents need no
knowledge base — and a step that is always complete teaches people to stop
reading the rail.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select

from api.db import db_client
from api.db.models import (
    TelephonyPhoneNumberModel,
    WorkflowModel,
    WorkflowRunModel,
)


@dataclass(frozen=True)
class Step:
    """One thing to do, and whether it is done.

    ``key`` is for the UI to branch on and never shown; ``title`` and ``hint``
    are what a person reads. The hint is phrased as the next action rather than
    the missing state — "put it on a number" tells somebody what to do,
    "no number attached" tells them off.
    """

    key: str
    title: str
    hint: str
    done: bool


@dataclass(frozen=True)
class SetupProgress:
    steps: tuple[Step, ...]

    @property
    def complete(self) -> bool:
        return all(step.done for step in self.steps)

    @property
    def next_step(self) -> Step | None:
        """The first thing left to do, or None when there is nothing.

        First rather than fewest-clicks: the order is the order the work
        happens in, and pointing someone at a later step they cannot do yet is
        worse than pointing at nothing.
        """
        return next((step for step in self.steps if not step.done), None)


async def for_workflow(*, workflow_id: int, organization_id: int) -> SetupProgress:
    """The rail for one agent, scoped to the account that owns it.

    Returns every step as not-done for a workflow this organization does not
    own, rather than raising: the caller is a panel on a page, and a rail that
    500s is worse than a rail that says there is work to do. The route above it
    is what refuses a workflow the caller may not see.
    """
    async with db_client.async_session() as session:
        workflow = (
            await session.scalars(
                select(WorkflowModel).where(
                    WorkflowModel.id == workflow_id,
                    WorkflowModel.organization_id == organization_id,
                )
            )
        ).first()

        if workflow is None:
            return SetupProgress(
                steps=_steps(built=False, tested=False, live=False, on_a_number=False)
            )

        # One row is the whole question — "has this ever run" needs no count,
        # and a count over a busy agent's runs is work nobody asked for.
        tested = (
            await session.scalars(
                select(WorkflowRunModel.id)
                .where(WorkflowRunModel.workflow_id == workflow_id)
                .limit(1)
            )
        ).first() is not None

        # Either kind of attachment counts. A callback number never answers —
        # it rings back — but an agent behind one is just as reachable by a
        # member of the public, which is what this step is really asking.
        on_a_number = (
            await session.scalar(
                select(func.count(TelephonyPhoneNumberModel.id)).where(
                    TelephonyPhoneNumberModel.organization_id == organization_id,
                    (TelephonyPhoneNumberModel.inbound_workflow_id == workflow_id)
                    | (TelephonyPhoneNumberModel.callback_workflow_id == workflow_id),
                )
            )
            or 0
        ) > 0

    return SetupProgress(
        steps=_steps(
            built=True,
            tested=tested,
            live=bool(workflow.is_live),
            on_a_number=on_a_number,
        )
    )


def _steps(
    *, built: bool, tested: bool, live: bool, on_a_number: bool
) -> tuple[Step, ...]:
    return (
        Step(
            key="built",
            title="Build the agent",
            hint="Start from a template or describe your business.",
            done=built,
        ),
        Step(
            key="tested",
            title="Hear it on a call",
            hint="Call the agent yourself before anyone else does.",
            done=tested,
        ),
        Step(
            key="live",
            title="Switch it on",
            hint="A paused agent does not answer, however it is configured.",
            done=live,
        ),
        Step(
            key="on_a_number",
            title="Put it on a number",
            hint="Give customers a number that reaches it.",
            done=on_a_number,
        ),
    )
